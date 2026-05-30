from __future__ import annotations

import asyncio
import io
import math
import time
from typing import Optional, Type

from maubot import Plugin, MessageEvent
from maubot.handlers import command, event
from mautrix.types import EventType, MessageType, RoomID, UserID
from mautrix.util.config import BaseProxyConfig

from .config import Config
from . import input_parser
from . import image_sender
from . import style_engine
from .llm_client import LLMClient
from .prompt_pipeline import PromptPipeline
from .types import GenerationRequest
from .backends.novelai import NovelAIBackend
from .backends.runpod import RunPodBackend

DIMENSION_MULTIPLE = 64
DEFAULT_PIXEL_BUDGET = 1216 * 832  # NAI's default total pixel count


class RacconicBot(Plugin):
    config: Config
    _llm: Optional[LLMClient]
    _pipeline: Optional[PromptPipeline]
    _backends: dict[str, NovelAIBackend | RunPodBackend]
    _room_cooldowns: dict[str, float]
    _pending_img_requests: dict[tuple[RoomID, UserID], asyncio.Future]

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self._room_cooldowns = {}
        self._pending_img_requests = {}
        await self._init_components()

    async def _init_components(self) -> None:
        cfg = self.config

        llm_cfg = cfg["llm"]
        self._llm = LLMClient(
            http=self.http,
            api_key=llm_cfg["api_key"],
            base_url=llm_cfg["base_url"],
            max_retries=llm_cfg.get("max_retries", 3),
            max_tokens=llm_cfg.get("max_tokens", 400),
        )

        # Build a plain-dict snapshot for the pipeline (needs llm, prompt, nai, runpod sections)
        pipeline_cfg = {
            "llm": dict(llm_cfg),
            "prompt": dict(cfg["prompt"]),
            "nai": dict(cfg["nai"]),
            "runpod": dict(cfg["runpod"]),
        }
        self._pipeline = PromptPipeline(self._llm, pipeline_cfg)

        self._backends = {}
        if cfg["nai.enabled"]:
            self._backends["nai"] = NovelAIBackend(self.http, dict(cfg["nai"]))
        if cfg["runpod.enabled"]:
            backend = RunPodBackend(self.http, dict(cfg["runpod"]))
            await backend.load_workflow()
            self._backends["runpod"] = backend

    def on_external_config_update(self) -> None:
        self.config.load_and_update()
        self.loop.create_task(self._init_components())

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config

    # -- helpers -------------------------------------------------------------

    def _get_command_name(self) -> str:
        return self.config["command_prefix"]

    def _resolve_backend(self, name: Optional[str]) -> tuple[Optional[str], Optional[NovelAIBackend | RunPodBackend]]:
        if name and name in self._backends:
            return name, self._backends[name]
        default = self.config["default_backend"]
        if default in self._backends:
            return default, self._backends[default]
        # Fallback to first available
        for k, v in self._backends.items():
            return k, v
        return None, None

    def _is_room_allowed(self, room_id: str) -> bool:
        """Check if room is in the whitelist (empty whitelist = all allowed)."""
        whitelist = self.config["room_whitelist"]
        if not whitelist:
            return True
        return room_id in whitelist

    async def _try_react(self, evt: MessageEvent, emoji: str) -> None:
        """Send a reaction, swallowing errors so responses aren't blocked."""
        try:
            await evt.react(emoji)
        except Exception:
            self.log.debug("Failed to react with %s", emoji)

    def _check_cooldown(self, room_id: str) -> Optional[float]:
        """Return seconds remaining if on cooldown, else None."""
        limit = self.config["rate_limit_seconds"]
        if limit <= 0:
            return None
        last = self._room_cooldowns.get(room_id, 0)
        remaining = limit - (time.monotonic() - last)
        return remaining if remaining > 0 else None

    # -- commands ------------------------------------------------------------

    @command.new(name=_get_command_name, require_subcommand=False,
                 arg_fallthrough=False, must_consume_args=False)
    @command.argument("raw_input", pass_raw=True, required=False)
    async def racc(self, evt: MessageEvent, raw_input: str = "") -> None:
        if not self._is_room_allowed(evt.room_id):
            return

        raw_input = (raw_input or "").strip()

        if not raw_input:
            # Check for reply-based context
            reply_to = evt.content.get_reply_to()
            if reply_to:
                try:
                    replied_evt = await self.client.get_event(evt.room_id, reply_to)
                    raw_input = replied_evt.content.body or ""
                except Exception:
                    self.log.warning("Failed to fetch replied-to event %s", reply_to)

        if not raw_input:
            await evt.reply(
                "~Usage: `!{prefix} [flags] <prompt text>` or reply to a message.\n"
                "Try `!{prefix} help` for details.".format(prefix=self.config["command_prefix"])
            )
            return

        # Check for reply context when text is also provided (flags only, no prompt text)
        request = input_parser.parse(raw_input)

        if await self._reject_on_errors(evt, request):
            return

        if not request.prompt_text:
            reply_to = evt.content.get_reply_to()
            if reply_to:
                try:
                    replied_evt = await self.client.get_event(evt.room_id, reply_to)
                    request.prompt_text = replied_evt.content.body or ""
                except Exception:
                    self.log.warning("Failed to fetch replied-to event %s", reply_to)

        if not request.prompt_text:
            await evt.reply("~No prompt text provided. Provide text or reply to a message.")
            return

        # img2img source resolution (only for text-event entry)
        source_image: Optional[bytes] = None
        source_dims: Optional[tuple[int, int]] = None
        if request.is_img2img:
            source_image, source_dims = await self._acquire_img2img_source(evt)
            if source_image is None:
                return

        if not await self._claim_cooldown(evt):
            return

        await self._run_generation(evt, request, source_image=source_image, source_dims=source_dims)

    async def _reject_on_errors(self, evt: MessageEvent, request: GenerationRequest) -> bool:
        if not request.errors:
            return False
        prefix = self.config["command_prefix"]
        error_lines = "\n\n".join(f"- {e}" for e in request.errors)
        await evt.reply(
            f"~**Oops!** Something looks off:\n\n{error_lines}\n\n"
            f"Try `!{prefix} help` for usage info."
        )
        return True

    async def _claim_cooldown(self, evt: MessageEvent) -> bool:
        remaining = self._check_cooldown(evt.room_id)
        if remaining is not None:
            await evt.reply(f"~Please wait {remaining:.0f}s before generating again.")
            return False
        self._room_cooldowns[evt.room_id] = time.monotonic()
        return True

    async def _acquire_img2img_source(
        self, evt: MessageEvent,
    ) -> tuple[Optional[bytes], Optional[tuple[int, int]]]:
        """Resolve the source image for an `-i` request from a text-event.

        Tries reply-to-image first, then waits up to img2img_wait_seconds for
        the next image from the same user in the same room. Returns (None, None)
        and replies to the user on failure.
        """
        # Reply path
        reply_to = evt.content.get_reply_to()
        if reply_to:
            try:
                replied_evt = await self.client.get_event(evt.room_id, reply_to)
                if replied_evt and getattr(replied_evt.content, "msgtype", None) == MessageType.IMAGE:
                    data, dims = await self._download_image_event(replied_evt)
                    if data is not None:
                        return data, dims
            except Exception:
                self.log.warning("Failed to fetch replied-to event %s", reply_to)

        # Follow-up path
        wait_seconds = float(self.config.get("nai.img2img_wait_seconds", 10))
        key = (evt.room_id, evt.sender)
        if key in self._pending_img_requests:
            await evt.reply("~Already waiting for an image from you \u2014 send one or wait for the previous request to time out.")
            return None, None

        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_img_requests[key] = fut
        prompt_event_id = await evt.reply(f"~Send your reference image within {int(wait_seconds)} seconds\u2026")
        try:
            image_evt = await asyncio.wait_for(fut, timeout=wait_seconds)
        except asyncio.TimeoutError:
            await evt.reply(f"~Timed out: no image received within {int(wait_seconds)} seconds.")
            return None, None
        finally:
            self._pending_img_requests.pop(key, None)

        # Got the image \u2014 redact the "send your image" prompt to keep the chat clean.
        try:
            await self.client.redact(evt.room_id, prompt_event_id)
        except Exception:
            self.log.debug("Failed to redact img2img prompt notice", exc_info=True)

        return await self._download_image_event(image_evt)

    async def _download_image_event(
        self, image_evt: MessageEvent,
    ) -> tuple[Optional[bytes], Optional[tuple[int, int]]]:
        url = getattr(image_evt.content, "url", None)
        if not url:
            self.log.warning("Image event %s has no mxc url (encrypted?)", image_evt.event_id)
            return None, None
        try:
            data = await self.client.download_media(url)
        except Exception:
            self.log.exception("Failed to download mxc media %s", url)
            return None, None
        info = getattr(image_evt.content, "info", None)
        dims: Optional[tuple[int, int]] = None
        if info is not None:
            w = getattr(info, "width", None) or getattr(info, "w", None)
            h = getattr(info, "height", None) or getattr(info, "h", None)
            if w and h:
                dims = (int(w), int(h))
        return data, dims

    async def _run_generation(
        self,
        evt: MessageEvent,
        request: GenerationRequest,
        source_image: Optional[bytes] = None,
        source_dims: Optional[tuple[int, int]] = None,
    ) -> None:
        # Resolve backend
        backend_key, backend = self._resolve_backend(request.backend_name)
        if backend is None:
            await evt.reply("~No image generation backends are enabled. Check config.")
            return

        if request.is_img2img and backend_key != "nai":
            await evt.reply("~`-i`/`--img2img` requires NovelAI. Enable the NAI backend or use `-n`.")
            return

        # For img2img: decode the source first so we use its true dimensions
        # (Matrix's `info.w`/`h` is set by the sender's client and often missing
        # or wrong — only the actual pixels can be trusted).
        source_png: Optional[bytes] = None
        if request.is_img2img and source_image is not None:
            try:
                true_w, true_h = _read_image_dims(source_image)
            except Exception:
                self.log.exception("Failed to decode source image for img2img")
                await evt.reply("~Could not decode your reference image. Send a JPG/PNG/WebP.")
                return
            source_dims = (true_w, true_h)
            self.log.debug("img2img source true dims: %dx%d (info said %s)",
                           true_w, true_h, source_dims)

        # Resolve target dims. For img2img, match source aspect ratio unless
        # the user supplied --size explicitly.
        if request.width and request.height:
            width, height = request.width, request.height
        elif request.is_img2img and source_dims is not None:
            width, height = _aspect_fit_dims(*source_dims, DEFAULT_PIXEL_BUDGET)
        else:
            width, height = backend.default_size()

        # Resize source to target dims and re-encode as PNG
        if request.is_img2img and source_image is not None:
            try:
                source_png = _prepare_source_image(source_image, width, height)
            except Exception:
                self.log.exception("Failed to prepare source image for img2img")
                await evt.reply("~Could not decode your reference image. Send a JPG/PNG/WebP.")
                return

        # Progress indicator
        await self._try_react(evt, "\u23f3")  # hourglass

        try:
            final_prompt = await self._pipeline.generate(request, backend_key)

            negative = ""
            if backend_key == "nai":
                negative = self.config["nai.negative_prompt"] or ""

            result = await backend.generate(
                prompt=final_prompt,
                negative=negative,
                width=width,
                height=height,
                batch=request.batch_count,
                source_image=source_png,
                strength=request.strength,
                noise=request.noise,
            )

            if result.error:
                await self._try_react(evt, "\u274c")  # red X
                await evt.reply(f"~Generation failed: {result.error}")
                return

            if not result.images:
                await self._try_react(evt, "\u274c")
                await evt.reply("~No images were generated.")
                return

            await self._try_react(evt, "\u2705")  # green check
            await image_sender.send_images(self.client, evt, result)

        except Exception:
            self.log.exception("Unhandled error during generation")
            await self._try_react(evt, "\u274c")
            await evt.reply("~An unexpected error occurred. Check logs for details.")

    # -- passive image listener ----------------------------------------------

    @event.on(EventType.ROOM_MESSAGE)
    async def _on_image(self, evt: MessageEvent) -> None:
        if getattr(evt.content, "msgtype", None) != MessageType.IMAGE:
            return
        if evt.sender == self.client.mxid:
            return
        if not self._is_room_allowed(evt.room_id):
            return

        # Inline path: image event whose body invokes the bot.
        body = (evt.content.body or "").strip()
        prefix = f"!{self.config['command_prefix']}"
        if body == prefix or body.startswith(prefix + " "):
            raw_input = body[len(prefix):].strip()
            await self._handle_inline_img2img(evt, raw_input)
            return

        # Follow-up path: resolve a pending future for this user/room.
        key = (evt.room_id, evt.sender)
        fut = self._pending_img_requests.get(key)
        if fut and not fut.done():
            fut.set_result(evt)

    async def _handle_inline_img2img(self, evt: MessageEvent, raw_input: str) -> None:
        """Image event with `!racc ...` in its body \u2014 image and prompt arrived together."""
        if not raw_input:
            await evt.reply("~Add a prompt after `!{prefix}` to use this image.".format(
                prefix=self.config["command_prefix"]))
            return

        request = input_parser.parse(raw_input)
        if await self._reject_on_errors(evt, request):
            return
        if not request.prompt_text:
            await evt.reply("~No prompt text provided.")
            return

        # The user attached an image with a caption \u2014 treat as img2img regardless of -i.
        request.is_img2img = True

        source_image, source_dims = await self._download_image_event(evt)
        if source_image is None:
            await evt.reply("~Could not download the attached image.")
            return

        if not await self._claim_cooldown(evt):
            return

        await self._run_generation(evt, request, source_image=source_image, source_dims=source_dims)

    @racc.subcommand(help="Show usage information")
    async def help(self, evt: MessageEvent) -> None:
        prefix = self.config["command_prefix"]
        await evt.reply(
            f"~**Racconic Image Generator**\n\n"
            f"**How to use:**\n\n"
            f"`!{prefix} <prompt>` — describe what you want to see\n\n"
            f"`!{prefix} -f a raccoon eating pizza` — use a preset + your prompt\n\n"
            f"You can also reply to any message with `!{prefix}` to use that message as the prompt.\n\n"
            f"**Presets** (changes how the AI rewrites your prompt):\n\n"
            f"`-d` or `--dormouse` — dormouse style\n\n"
            f"`-f` or `--fossa` — fossa style\n\n"
            f"`-h` or `--hippo` — hippo style\n\n"
            f"`-r` or `--raw` — skip the AI, send your prompt exactly as-is\n\n"
            f"**Backend** (which image generator to use):\n\n"
            f"`-n` or `--nai` — use NovelAI\n\n"
            f"`-rp` or `--runpod` — use RunPod/ComfyUI\n\n"
            f"**Extra options:**\n\n"
            f"`-s N` or `--style N` — pick a specific style by number (use `0` for no style)\n\n"
            f"`--size WIDTHxHEIGHT` — set image size, e.g. `--size 1024x768`\n\n"
            f"`-b N` or `--batch N` — generate multiple images at once (1-4, RunPod only)\n\n"
            f"**Image-to-image** (NovelAI only):\n\n"
            f"`-i` or `--img2img` — use a reference image. Three ways:\n\n"
            f"  • Send an image with `!{prefix} -i <prompt>` as its caption.\n\n"
            f"  • Reply to an image with `!{prefix} -i <prompt>`.\n\n"
            f"  • Send `!{prefix} -i <prompt>`, then post your image within 10s.\n\n"
            f"`--strength X.XX` — denoise strength (default 0.70, range 0.01–0.99). Lower = closer to source.\n\n"
            f"`--noise X.XX` — extra noise (default 0.00, range 0.00–0.99).\n\n"
            f"**Subcommands:**\n\n"
            f"`!{prefix} styles [nai|runpod]` — list available styles\n\n"
            f"`!{prefix} presets` — list LLM presets\n\n"
            f"`!{prefix} status` — show backend status"
        )

    @racc.subcommand(help="List available styles")
    @command.argument("backend_name", required=False)
    async def styles(self, evt: MessageEvent, backend_name: str = "") -> None:
        backend_name = (backend_name or "").strip()
        targets: list[tuple[str, str]] = []

        if backend_name:
            st = self.config.get(f"{backend_name}.style_text", "")
            if st is not None:
                targets.append((backend_name, st))
            else:
                await evt.reply(f"~Unknown backend: {backend_name}")
                return
        else:
            for key in ("nai", "runpod"):
                if self.config[f"{key}.enabled"]:
                    targets.append((key, self.config.get(f"{key}.style_text", "") or ""))

        if not targets:
            await evt.reply("~No backends enabled.")
            return

        lines: list[str] = []
        for name, st_text in targets:
            lines.append(f"~**{name} styles:**")
            entries = style_engine.list_styles(st_text)
            if not entries:
                lines.append("  (no styles configured)")
            else:
                for idx, tags, weight in entries:
                    lines.append(f"  `{idx}`. {tags} (weight {weight})")

        await evt.reply("\n\n".join(lines))

    @racc.subcommand(help="List LLM presets")
    async def presets(self, evt: MessageEvent) -> None:
        presets = self.config["llm.presets"]
        default = self.config["llm.default_preset"]
        lines = ["~**LLM Presets:**"]
        for name, p in presets.items():
            marker = " (default)" if name == default else ""
            lines.append(f"- **{name}**{marker}: model `{p['model']}`, temp {p.get('temperature', 1.0)}")
        await evt.reply("\n\n".join(lines))

    @racc.subcommand(help="Show backend status")
    async def status(self, evt: MessageEvent) -> None:
        lines = ["~**Backend Status:**"]
        default = self.config["default_backend"]
        for key in ("nai", "runpod"):
            enabled = self.config[f"{key}.enabled"]
            is_default = " (default)" if key == default else ""
            status = "enabled" if enabled else "disabled"
            lines.append(f"- **{key}**: {status}{is_default}")
        await evt.reply("\n\n".join(lines))


# -- helpers -----------------------------------------------------------------


def _aspect_fit_dims(sw: int, sh: int, pixel_budget: int) -> tuple[int, int]:
    """Scale (sw, sh) to roughly `pixel_budget` pixels, preserving aspect, snapped to multiples of 64."""
    if sw <= 0 or sh <= 0:
        return 1216, 832
    scale = math.sqrt(pixel_budget / (sw * sh))
    w = max(DIMENSION_MULTIPLE, int(sw * scale) // DIMENSION_MULTIPLE * DIMENSION_MULTIPLE)
    h = max(DIMENSION_MULTIPLE, int(sh * scale) // DIMENSION_MULTIPLE * DIMENSION_MULTIPLE)
    return w, h


def _read_image_dims(data: bytes) -> tuple[int, int]:
    """Decode an image just enough to read its true (width, height)."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.width, im.height


def _prepare_source_image(data: bytes, width: int, height: int) -> bytes:
    """Decode arbitrary image bytes, resize to (width, height), return PNG bytes."""
    from PIL import Image  # local import so import errors surface at use, not plugin load
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im = im.resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="PNG")
    return out.getvalue()
