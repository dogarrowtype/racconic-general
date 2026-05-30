from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..types import GenerationResult


class ImageBackend(ABC):
    """Abstract base for image generation backends."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative: str,
        width: int,
        height: int,
        batch: int,
        source_image: Optional[bytes] = None,
        strength: Optional[float] = None,
        noise: Optional[float] = None,
    ) -> GenerationResult:
        ...

    @abstractmethod
    def default_size(self) -> tuple[int, int]:
        """Return (width, height) defaults for this backend."""
        ...
