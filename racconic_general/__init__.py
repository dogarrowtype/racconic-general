from __future__ import annotations

import time
from typing import Optional, Type

from maubot import Plugin, MessageEvent
from maubot.handlers import command
from mautrix.util.config import BaseProxyConfig

from .config import Config
from . import input_parser
from . import image_sender
from . import style_engine
from .llm_client import LLMClient
from .prompt_pipeline import PromptPipeline
from .backends.novelai import NovelAIBackend
from .backends.runpod import RunPodBackend


class RacconicBot(Plugin):
    config: Config
    _llm: Optional[LLMClient]
    _pipeline: Optional[PromptPipeline]
    _backends: dict[str, NovelAIBackend | RunPodBackend]
    _room_cooldowns: dict[str, float]

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self._room_cooldowns = {}
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
            await evt.respond(
                "~Usage: `!{prefix} [flags] <prompt text>` or reply to a message.\n"
                "Try `!{prefix} help` for details.".format(prefix=self.config["command_prefix"])
            )
            return

        # Check for reply context when text is also provided (flags only, no prompt text)
        request = input_parser.parse(raw_input)
        if not request.prompt_text:
            reply_to = evt.content.get_reply_to()
            if reply_to:
                try:
                    replied_evt = await self.client.get_event(evt.room_id, reply_to)
                    request.prompt_text = replied_evt.content.body or ""
                except Exception:
                    self.log.warning("Failed to fetch replied-to event %s", reply_to)

        if not request.prompt_text:
            await evt.respond("~No prompt text provided. Provide text or reply to a message.")
            return

        # Rate limit
        remaining = self._check_cooldown(evt.room_id)
        if remaining is not None:
            await evt.respond(f"~Please wait {remaining:.0f}s before generating again.")
            return
        self._room_cooldowns[evt.room_id] = time.monotonic()

        # Resolve backend
        backend_key, backend = self._resolve_backend(request.backend_name)
        if backend is None:
            await evt.respond("~No image generation backends are enabled. Check config.")
            return

        # Resolve size
        if request.width and request.height:
            width, height = request.width, request.height
        else:
            width, height = backend.default_size()

        # Progress indicator
        await evt.react("\u23f3")  # hourglass

        try:
            # Generate prompt
            final_prompt = await self._pipeline.generate(request, backend_key)

            # Resolve negative prompt (NAI-specific)
            negative = ""
            if backend_key == "nai":
                negative = self.config["nai.negative_prompt"] or ""

            # Generate images
            result = await backend.generate(
                prompt=final_prompt,
                negative=negative,
                width=width,
                height=height,
                batch=request.batch_count,
            )

            if result.error:
                await evt.react("\u274c")  # red X
                await evt.respond(f"~Generation failed: {result.error}")
                return

            if not result.images:
                await evt.react("\u274c")
                await evt.respond("~No images were generated.")
                return

            await evt.react("\u2705")  # green check
            await image_sender.send_images(self.client, evt, result)

        except Exception:
            self.log.exception("Unhandled error during generation")
            await evt.react("\u274c")
            await evt.respond("~An unexpected error occurred. Check logs for details.")

    @racc.subcommand(help="Show usage information")
    async def help(self, evt: MessageEvent) -> None:
        prefix = self.config["command_prefix"]
        await evt.respond(
            f"~**Racconic Image Generator**\n\n"
            f"**Generate an image:**\n"
            f"`!{prefix} [flags] <prompt text>`\n"
            f"Or reply to a message with `!{prefix}`\n\n"
            f"**Flags:**\n"
            f"- Preset: `--dormouse` / `-d`, `--fossa` / `-f`, `--hippo` / `-h`, `--raw` / `-r`\n"
            f"- Backend: `--nai` / `-n`, `--runpod` / `-rp`\n"
            f"- Style: `--style N` / `-s N` (1-based, `0` = none)\n"
            f"- Size: `--size WIDTHxHEIGHT`\n"
            f"- Batch: `--batch N` / `-b N` (1-4, RunPod only)\n\n"
            f"**Subcommands:**\n"
            f"`!{prefix} styles [nai|runpod]` — list styles\n"
            f"`!{prefix} presets` — list LLM presets\n"
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
                await evt.respond(f"~Unknown backend: {backend_name}")
                return
        else:
            for key in ("nai", "runpod"):
                if self.config[f"{key}.enabled"]:
                    targets.append((key, self.config.get(f"{key}.style_text", "") or ""))

        if not targets:
            await evt.respond("~No backends enabled.")
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

        await evt.respond("\n\n".join(lines))

    @racc.subcommand(help="List LLM presets")
    async def presets(self, evt: MessageEvent) -> None:
        presets = self.config["llm.presets"]
        default = self.config["llm.default_preset"]
        lines = ["~**LLM Presets:**"]
        for name, p in presets.items():
            marker = " (default)" if name == default else ""
            lines.append(f"- **{name}**{marker}: model `{p['model']}`, temp {p.get('temperature', 1.0)}")
        await evt.respond("\n\n".join(lines))

    @racc.subcommand(help="Show backend status")
    async def status(self, evt: MessageEvent) -> None:
        lines = ["~**Backend Status:**"]
        default = self.config["default_backend"]
        for key in ("nai", "runpod"):
            enabled = self.config[f"{key}.enabled"]
            is_default = " (default)" if key == default else ""
            status = "enabled" if enabled else "disabled"
            lines.append(f"- **{key}**: {status}{is_default}")
        await evt.respond("\n\n".join(lines))
