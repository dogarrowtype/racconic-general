from __future__ import annotations

import asyncio
import io
import logging
import math
import random
import zipfile
from typing import Optional

import aiohttp

from ..types import GenerationResult
from . import ImageBackend

log = logging.getLogger("racconic.nai")

DIMENSION_MULTIPLE = 64
NAI_API_URL = "https://image.novelai.net/ai/generate-image"
MAX_RETRIES = 10
RATE_LIMIT_RETRY_DELAY_RANGE = (1, 5)
QUEUE_PROCESSING_DELAY = 1.0


class NovelAIBackend(ImageBackend):
    """NovelAI image generation backend."""

    def __init__(self, http: aiohttp.ClientSession, config: dict) -> None:
        self.http = http
        self.cfg = config
        self._lock = asyncio.Lock()

    def default_size(self) -> tuple[int, int]:
        return _parse_dimensions(self.cfg.get("default_size", "1216x832"))

    # -- public API ----------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        negative: str,
        width: int,
        height: int,
        batch: int,
    ) -> GenerationResult:
        async with self._lock:
            return await self._generate_locked(prompt, negative, width, height, batch)

    async def _generate_locked(
        self,
        prompt: str,
        negative: str,
        width: int,
        height: int,
        batch: int,
    ) -> GenerationResult:
        api_key = self.cfg.get("api_key", "")
        if not api_key:
            return GenerationResult(images=[], prompt_used=prompt, backend="nai",
                                    error="NovelAI API key is not configured")

        max_pixels = self.cfg.get("max_pixels", 1048576)
        max_steps = self.cfg.get("max_steps", 28)
        steps = self.cfg.get("steps", 28)

        width, height, steps = _apply_anlas_guard(width, height, steps, max_pixels, max_steps)

        payload = {
            "input": prompt,
            "model": self.cfg.get("model", "nai-diffusion-4-5-full"),
            "action": "generate",
            "parameters": {
                "params_version": 3,
                "width": width,
                "height": height,
                "scale": self.cfg.get("scale", 5.0),
                "sampler": self.cfg.get("sampler", "k_euler_ancestral"),
                "steps": steps,
                "seed": _resolve_seed(self.cfg.get("seed", -1)),
                "n_samples": 1,
                "ucPreset": self.cfg.get("uc_preset", 2),
                "qualityToggle": self.cfg.get("quality_toggle", True),
                "sm": self.cfg.get("sm", False),
                "sm_dyn": self.cfg.get("sm_dyn", False),
                "dynamic_thresholding": self.cfg.get("decrisper", False),
                "controlnet_strength": 1,
                "legacy": False,
                "add_original_image": True,
                "cfg_rescale": self.cfg.get("cfg_rescale", 0.0),
                "noise_schedule": self.cfg.get("scheduler", "karras"),
                "legacy_v3_extend": False,
                "skip_cfg_above_sigma": None,
                "use_coords": False,
                "normalize_reference_strength_multiple": True,
                "v4_prompt": {
                    "caption": {
                        "base_caption": prompt,
                        "char_captions": [],
                    },
                    "use_coords": False,
                    "use_order": True,
                },
                "v4_negative_prompt": {
                    "caption": {
                        "base_caption": negative,
                        "char_captions": [],
                    },
                },
                "characterPrompts": [],
                "negative_prompt": negative,
                "deliberate_euler_ancestral_bug": False,
                "prefer_brownian": True,
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.http.post(NAI_API_URL, headers=headers, json=payload) as resp:
                    if resp.status in (429, 520):
                        delay = random.uniform(*RATE_LIMIT_RETRY_DELAY_RANGE)
                        log.info(
                            "NovelAI %s (attempt %d/%d), retrying in %.1fs",
                            resp.status, attempt, MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status != 200:
                        text = await resp.text()
                        log.error("NovelAI error %s: %s", resp.status, text)
                        return GenerationResult(
                            images=[], prompt_used=prompt, backend="nai",
                            error=f"NovelAI API returned {resp.status}",
                        )

                    archive_data = await resp.read()
                    image = _extract_png_from_zip(archive_data)
                    if not image:
                        return GenerationResult(
                            images=[], prompt_used=prompt, backend="nai",
                            error="Failed to extract PNG from NovelAI response",
                        )

                    # Delay before allowing next request to avoid rate limits
                    await asyncio.sleep(QUEUE_PROCESSING_DELAY)
                    return GenerationResult(images=[image], prompt_used=prompt, backend="nai")

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("NovelAI request failed (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(random.uniform(*RATE_LIMIT_RETRY_DELAY_RANGE))

            except Exception as exc:
                log.exception("NovelAI generation failed")
                return GenerationResult(
                    images=[], prompt_used=prompt, backend="nai",
                    error=str(exc),
                )

        log.error("NovelAI: all %d attempts exhausted", MAX_RETRIES)
        return GenerationResult(
            images=[], prompt_used=prompt, backend="nai",
            error="NovelAI rate limit: all retries exhausted",
        )


# -- helpers -----------------------------------------------------------------


def _resolve_seed(seed: int) -> int:
    """Return the configured seed, or a random one if seed is -1."""
    if seed < 0:
        return random.randint(0, 9999999999)
    return seed


def _parse_dimensions(size_str: str) -> tuple[int, int]:
    try:
        w, h = map(int, size_str.lower().split("x"))
        return w, h
    except (ValueError, AttributeError):
        return 1216, 832


def _apply_anlas_guard(
    width: int, height: int, steps: int,
    max_pixels: int, max_steps: int,
) -> tuple[int, int, int]:
    if width * height > max_pixels:
        ratio = math.sqrt(max_pixels / (width * height))
        width = int(width * ratio) // DIMENSION_MULTIPLE * DIMENSION_MULTIPLE
        height = int(height * ratio) // DIMENSION_MULTIPLE * DIMENSION_MULTIPLE
        while width * height > max_pixels:
            if width > height:
                width -= DIMENSION_MULTIPLE
            else:
                height -= DIMENSION_MULTIPLE
        log.info("Anlas guard: clamped to %dx%d", width, height)

    # Ensure dimensions are multiples of 64
    width = max(DIMENSION_MULTIPLE, (width // DIMENSION_MULTIPLE) * DIMENSION_MULTIPLE)
    height = max(DIMENSION_MULTIPLE, (height // DIMENSION_MULTIPLE) * DIMENSION_MULTIPLE)

    if steps > max_steps:
        log.info("Anlas guard: clamped steps %d → %d", steps, max_steps)
        steps = max_steps

    return width, height, steps


def _extract_png_from_zip(archive_bytes: bytes) -> Optional[bytes]:
    try:
        buf = io.BytesIO(archive_bytes)
        with zipfile.ZipFile(buf) as zf:
            for name in zf.namelist():
                if name.endswith(".png"):
                    return zf.read(name)
    except Exception:
        log.exception("Failed to extract PNG from ZIP")
    return None
