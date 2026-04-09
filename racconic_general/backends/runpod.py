from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import random
from typing import Optional

import aiohttp

from ..types import GenerationResult
from . import ImageBackend

log = logging.getLogger("racconic.runpod")


class RunPodBackend(ImageBackend):
    """RunPod serverless + ComfyUI image generation backend."""

    def __init__(self, http: aiohttp.ClientSession, config: dict) -> None:
        self.http = http
        self.cfg = config
        self._workflow_cache: Optional[dict] = None
        self._queue_lock = asyncio.Lock()

    def default_size(self) -> tuple[int, int]:
        return _parse_dimensions(self.cfg.get("default_size", "1216x832"))

    async def load_workflow(self) -> None:
        """Load the ComfyUI workflow JSON from an external file path."""
        filepath = self.cfg.get("comfyui_workflow_file", "")
        if not filepath:
            log.error("No comfyui_workflow_file path configured")
            return
        try:
            with open(filepath, "r") as f:
                wf = json.load(f)
        except FileNotFoundError:
            log.error("Workflow file not found: %s", filepath)
            return
        except json.JSONDecodeError as exc:
            log.error("Workflow file %s is not valid JSON: %s", filepath, exc)
            return
        except Exception as exc:
            log.error("Failed to read workflow file %s: %s", filepath, exc)
            return

        if not _validate_workflow(wf):
            return
        self._workflow_cache = wf
        log.info("ComfyUI workflow loaded from %s (%d nodes)", filepath, len(wf))

    # -- public API ----------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        negative: str,
        width: int,
        height: int,
        batch: int,
    ) -> GenerationResult:
        api_key = self.cfg.get("api_key", "")
        worker_id = self.cfg.get("worker_id", "")
        if not api_key or not worker_id:
            return GenerationResult(
                images=[], prompt_used=prompt, backend="runpod",
                error="RunPod API key or worker ID not configured",
            )
        if self._workflow_cache is None:
            return GenerationResult(
                images=[], prompt_used=prompt, backend="runpod",
                error="ComfyUI workflow not loaded",
            )

        max_batch = self.cfg.get("max_batch", 4)
        batch = max(1, min(batch, max_batch))
        batch_mode = self.cfg.get("batch_mode", "sequential")

        if batch_mode == "sequential":
            return await self._generate_batch(prompt, width, height, batch, api_key, worker_id, parallel=False)
        return await self._generate_batch(prompt, width, height, batch, api_key, worker_id, parallel=True)

    # -- internal ------------------------------------------------------------

    async def _generate_batch(
        self, prompt: str, width: int, height: int, batch: int,
        api_key: str, worker_id: str, *, parallel: bool,
    ) -> GenerationResult:
        """Generate N single-image requests, either sequentially or in parallel.

        Sequential: acquires global lock, sends one request at a time (cheaper, one worker).
        Parallel: sends all requests concurrently (faster, spins up multiple workers).
        """
        if parallel:
            tasks = [
                self._generate_single(prompt, width, height, 1, api_key, worker_id)
                for _ in range(batch)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        else:
            results = []
            async with self._queue_lock:
                for _ in range(batch):
                    try:
                        results.append(await self._generate_single(prompt, width, height, 1, api_key, worker_id))
                    except Exception as exc:
                        results.append(exc)

        images: list[bytes] = []
        errors: list[str] = []
        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r))
            elif isinstance(r, GenerationResult):
                images.extend(r.images)
                if r.error:
                    errors.append(r.error)

        if not images:
            return GenerationResult(
                images=[], prompt_used=prompt, backend="runpod",
                error=f"All {batch} batch requests failed: {'; '.join(errors)}",
            )
        return GenerationResult(images=images, prompt_used=prompt, backend="runpod",
                                width=width, height=height)

    async def _generate_single(
        self, prompt: str, width: int, height: int, batch_size: int,
        api_key: str, worker_id: str,
    ) -> GenerationResult:
        """Submit one RunPod job and collect images."""
        workflow = _inject_workflow(
            copy.deepcopy(self._workflow_cache),
            prompt, width, height, batch_size,
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {"input": {"workflow": workflow}}
        api_url = f"https://api.runpod.ai/v2/{worker_id}/runsync"

        try:
            async with self.http.post(api_url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    log.error("RunPod error %s: %s", resp.status, text)
                    return GenerationResult(
                        images=[], prompt_used=prompt, backend="runpod",
                        error=f"RunPod API returned {resp.status}",
                    )

                data = await resp.json()

            status = data.get("status", "")

            # If the job is still running, poll for completion
            if status == "IN_PROGRESS" or status == "IN_QUEUE":
                job_id = data.get("id", "")
                if job_id:
                    data = await self._poll_job(job_id, api_key, worker_id)
                    status = data.get("status", "")

            if status != "COMPLETED":
                return GenerationResult(
                    images=[], prompt_used=prompt, backend="runpod",
                    error=f"RunPod job status: {status}",
                )

            output = data.get("output", {})
            raw_images = output.get("images", [])
            if not raw_images:
                return GenerationResult(
                    images=[], prompt_used=prompt, backend="runpod",
                    error="No images in RunPod response",
                )

            images: list[bytes] = []
            for img in raw_images:
                b64 = img.get("data") or img.get("image")
                if b64:
                    try:
                        images.append(base64.b64decode(b64))
                    except Exception:
                        log.warning("Failed to decode base64 image")

            if not images:
                return GenerationResult(
                    images=[], prompt_used=prompt, backend="runpod",
                    error="Failed to decode any images",
                )
            return GenerationResult(images=images, prompt_used=prompt, backend="runpod",
                                width=width, height=height)

        except Exception as exc:
            log.exception("RunPod generation failed")
            return GenerationResult(
                images=[], prompt_used=prompt, backend="runpod",
                error=str(exc),
            )

    async def _poll_job(self, job_id: str, api_key: str, worker_id: str) -> dict:
        """Poll RunPod status endpoint until job completes or times out."""
        url = f"https://api.runpod.ai/v2/{worker_id}/status/{job_id}"
        headers = {"Authorization": f"Bearer {api_key}"}
        interval = self.cfg.get("poll_interval", 2.0)
        timeout = self.cfg.get("poll_timeout", 120.0)
        elapsed = 0.0

        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                async with self.http.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    status = data.get("status", "")
                    if status in ("COMPLETED", "FAILED", "CANCELLED"):
                        return data
            except Exception:
                log.warning("Poll request failed, retrying")

        return {"status": "TIMEOUT"}


# -- helpers -----------------------------------------------------------------

def _validate_workflow(workflow: dict) -> bool:
    """Check that a workflow dict looks like a valid ComfyUI API workflow."""
    if not isinstance(workflow, dict) or not workflow:
        log.error("Workflow is empty or not a dict")
        return False
    has_nodes = any(
        isinstance(v, dict) and "class_type" in v for v in workflow.values()
    )
    if not has_nodes:
        log.error("Workflow does not contain valid ComfyUI nodes")
        return False
    if "{prompt}" not in json.dumps(workflow):
        log.warning("Workflow has no {prompt} placeholder — prompt will not be inserted")
    return True


def _parse_dimensions(size_str: str) -> tuple[int, int]:
    try:
        w, h = map(int, size_str.lower().split("x"))
        return w, h
    except (ValueError, AttributeError):
        return 1216, 832


def _inject_workflow(
    workflow: dict, prompt: str, width: int, height: int, batch_size: int,
) -> dict:
    """Replace placeholders in the ComfyUI workflow."""
    # Replace {prompt} placeholder via string replacement
    wf_str = json.dumps(workflow)
    prompt_escaped = json.dumps(prompt)[1:-1]  # strip surrounding quotes
    wf_str = wf_str.replace("{prompt}", prompt_escaped)
    workflow = json.loads(wf_str)

    seed = random.randint(0, 2**64 - 1)

    for node_id, node_data in workflow.items():
        if not isinstance(node_data, dict) or "inputs" not in node_data:
            continue

        class_type = node_data.get("class_type", "")
        inputs = node_data["inputs"]

        # Randomise seed in sampler nodes
        if "KSampler" in class_type or "Sampler" in class_type:
            if "seed" in inputs:
                inputs["seed"] = seed
            if "noise_seed" in inputs:
                inputs["noise_seed"] = seed

        # Update size and batch in latent image nodes
        latent_types = ("EmptySD3LatentImage", "EmptyLatentImage", "LatentImage", "Latent")
        if any(lt in class_type for lt in latent_types):
            if "width" in inputs:
                inputs["width"] = width
            if "height" in inputs:
                inputs["height"] = height
            if "batch_size" in inputs:
                inputs["batch_size"] = batch_size

    return workflow
