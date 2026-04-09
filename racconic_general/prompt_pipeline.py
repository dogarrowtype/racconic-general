from __future__ import annotations

import logging
import re
from typing import Optional

from .llm_client import LLMClient
from .style_engine import build_style_string
from .types import GenerationRequest, LLMPreset

log = logging.getLogger("racconic.pipeline")


def clean_prompt(prompt: str) -> str:
    """Strip markdown artefacts and explanation lines from LLM output."""
    # Remove code blocks and inline code
    prompt = re.sub(r"```.*?```", "", prompt, flags=re.DOTALL)
    prompt = re.sub(r"`.*?`", "", prompt)
    # Replace underscores with spaces, remove stray backslashes
    prompt = re.sub(r"_", " ", prompt)
    prompt = re.sub(r"\\", "", prompt)

    # Drop lines that look like explanations; strip leading bullet markers first
    # so that "- golden fur, sharp claws" keeps its content instead of being dropped.
    lines = prompt.split("\n")
    kept = []
    for line in lines:
        stripped = re.sub(r"^\s*[-*]\s+", "", line)  # remove leading bullet
        if not stripped.lstrip().startswith(("Here", "This", "I", "Note:")):
            kept.append(stripped)
    if kept:
        prompt = " ".join(kept)
    return prompt.strip()


def filter_banned_words(text: str, banned_words: list[str]) -> str:
    """Remove banned words from text via word-boundary regex."""
    if not banned_words:
        return text
    words = [w.strip() for w in banned_words if w.strip()]
    if not words:
        return text
    pattern = r"\b(?:" + "|".join(map(re.escape, words)) + r")\b"
    return re.sub(pattern, "", text)


def build_final_prompt(
    raw_prompt: str,
    style_text: str,
    common_prefix: str,
    common_suffix: str,
    backend_prefix: str,
    backend_suffix: str,
    style_index: Optional[int],
) -> str:
    """Assemble: prefix + prompt + style + suffix."""
    style_str = build_style_string(style_text, raw_prompt, style_index)

    parts: list[str] = []
    # Combine common + backend-specific prefixes
    combined_prefix = ", ".join(p for p in [common_prefix, backend_prefix] if p)
    if combined_prefix:
        parts.append(combined_prefix)

    parts.append(raw_prompt)

    if style_str:
        parts.append(style_str)

    # Combine common + backend-specific suffixes
    combined_suffix = ", ".join(s for s in [common_suffix, backend_suffix] if s)
    if combined_suffix:
        parts.append(combined_suffix)

    return "\n".join(parts)


class PromptPipeline:
    """Orchestrates the full prompt generation chain."""

    def __init__(self, llm_client: LLMClient, config: dict) -> None:
        self.llm = llm_client
        self.config = config

    def _resolve_preset(self, name: Optional[str]) -> LLMPreset:
        llm_cfg = self.config["llm"]
        preset_name = name or llm_cfg["default_preset"]
        presets = llm_cfg["presets"]
        if preset_name not in presets:
            log.warning("Unknown preset %r, falling back to default", preset_name)
            preset_name = llm_cfg["default_preset"]
        p = presets[preset_name]
        return LLMPreset(
            name=preset_name,
            model=p["model"],
            system_prompt=p["system_prompt"],
            temperature=p["temperature"],
            max_tokens=p.get("max_tokens") or llm_cfg.get("max_tokens", 400),
            reasoning_enabled=p.get("reasoning_enabled", False),
        )

    async def generate(self, request: GenerationRequest, backend_key: str) -> str:
        """Run the full pipeline and return the final prompt string."""
        # Raw mode: skip LLM and all post-processing, send text as-is
        if request.is_raw:
            return request.prompt_text

        backend_cfg = self.config[backend_key]
        prompt_cfg = self.config["prompt"]

        preset = self._resolve_preset(request.preset_name)
        raw = await self.llm.generate_prompt(preset, request.prompt_text)
        if not raw:
            log.warning("LLM returned nothing; using raw text as fallback")
            raw = request.prompt_text

        raw = clean_prompt(raw)
        raw = filter_banned_words(raw, prompt_cfg.get("banned_words", []))

        return build_final_prompt(
            raw_prompt=raw,
            style_text=backend_cfg.get("style_text", ""),
            common_prefix=prompt_cfg.get("prefix_prompt", ""),
            common_suffix=prompt_cfg.get("suffix_prompt", ""),
            backend_prefix=backend_cfg.get("prefix_prompt", ""),
            backend_suffix=backend_cfg.get("suffix_prompt", ""),
            style_index=request.style_index,
        )
