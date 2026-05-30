from __future__ import annotations

from typing import Type

from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper


class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("command_prefix")
        helper.copy("default_backend")
        helper.copy("rate_limit_seconds")
        helper.copy("room_whitelist")

        # LLM settings
        helper.copy("llm.api_key")
        helper.copy("llm.base_url")
        helper.copy("llm.default_preset")
        helper.copy("llm.max_retries")
        helper.copy("llm.max_tokens")
        helper.copy_dict("llm.presets")

        # Common prompt settings
        helper.copy("prompt.prefix_prompt")
        helper.copy("prompt.suffix_prompt")
        helper.copy("prompt.banned_words")

        # NovelAI backend
        helper.copy("nai.enabled")
        helper.copy("nai.api_key")
        helper.copy("nai.model")
        helper.copy("nai.default_size")
        helper.copy("nai.steps")
        helper.copy("nai.scale")
        helper.copy("nai.sampler")
        helper.copy("nai.scheduler")
        helper.copy("nai.quality_toggle")
        helper.copy("nai.decrisper")
        helper.copy("nai.sm")
        helper.copy("nai.sm_dyn")
        helper.copy("nai.seed")
        helper.copy("nai.uc_preset")
        helper.copy("nai.cfg_rescale")
        helper.copy("nai.negative_prompt")
        helper.copy("nai.max_pixels")
        helper.copy("nai.max_steps")
        helper.copy("nai.style_text")
        helper.copy("nai.prefix_prompt")
        helper.copy("nai.suffix_prompt")
        helper.copy("nai.img2img_strength")
        helper.copy("nai.img2img_noise")
        helper.copy("nai.img2img_wait_seconds")

        # RunPod backend
        helper.copy("runpod.enabled")
        helper.copy("runpod.api_key")
        helper.copy("runpod.worker_id")
        helper.copy("runpod.default_size")
        helper.copy("runpod.batch_mode")
        helper.copy("runpod.max_batch")
        helper.copy("runpod.poll_interval")
        helper.copy("runpod.poll_timeout")
        helper.copy("runpod.comfyui_workflow_file")
        helper.copy("runpod.style_text")
        helper.copy("runpod.prefix_prompt")
        helper.copy("runpod.suffix_prompt")
