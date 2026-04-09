from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMPreset:
    name: str
    model: str
    system_prompt: str
    temperature: float
    max_tokens: int
    reasoning_enabled: bool = False


@dataclass
class StyleChunk:
    tags: list[str]
    weight: int


@dataclass
class GenerationRequest:
    prompt_text: str
    preset_name: Optional[str] = None
    backend_name: Optional[str] = None
    style_index: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    batch_count: int = 1
    is_raw: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    images: list[bytes]
    prompt_used: str
    backend: str
    error: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
