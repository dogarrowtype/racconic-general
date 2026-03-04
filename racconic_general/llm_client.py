from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

import aiohttp

from .types import LLMPreset

log = logging.getLogger("racconic.llm")


class LLMClient:
    """OpenRouter API client for LLM-based prompt generation."""

    def __init__(self, http: aiohttp.ClientSession, api_key: str, base_url: str,
                 max_retries: int = 3, max_tokens: int = 400) -> None:
        self.http = http
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = max_retries
        self.max_tokens = max_tokens

    async def generate_prompt(self, preset: LLMPreset, user_text: str) -> Optional[str]:
        """Send user_text to the LLM and return the generated prompt string."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            #"X-OpenRouter-Title": "racconic",
        }

        payload: dict = {
            "model": preset.model,
            "messages": [
                {"role": "system", "content": preset.system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Chat history starts here:\n{user_text}\n"
                        "Chat history ends. Please complete the assigned prompt creation task now."
                    ),
                },
            ],
            "temperature": preset.temperature,
            "max_tokens": preset.max_tokens or self.max_tokens,
        }

        if preset.reasoning_enabled:
            payload["reasoning"] = {"enabled": True}

        for attempt in range(1, self.max_retries + 1):
            try:
                async with self.http.post(self.base_url, headers=headers, json=payload) as resp:
                    if resp.status == 429 or resp.status >= 500:
                        delay = random.uniform(1, 5)
                        log.warning(
                            "OpenRouter %s (attempt %d/%d), retrying in %.1fs",
                            resp.status, attempt, self.max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status != 200:
                        error = await resp.text()
                        log.error("OpenRouter error %s: %s", resp.status, error)
                        return None

                    data = await resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "").strip()
                        return content if content else None
                    return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("OpenRouter request failed (attempt %d/%d): %s", attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    await asyncio.sleep(random.uniform(1, 3))

        log.error("OpenRouter: all %d attempts exhausted", self.max_retries)
        return None
