from __future__ import annotations

import random
import re
from typing import Optional

from .types import StyleChunk


def parse_style_chunks(style_text: str) -> list[StyleChunk]:
    """Parse style text into chunks with weights.

    Format: each line is a style chunk.  Optionally ends with :weight.
        tag1, tag2, tag3 :5
        tag4, tag5           (weight defaults to 1)
    """
    if not style_text:
        return []

    chunks: list[StyleChunk] = []
    lines = [line.strip() for line in style_text.split("\n") if line.strip()]
    if not lines:
        return []

    for line in lines:
        # Check for weight suffix (:number at end)
        if ":" in line and line.rsplit(":", 1)[-1].strip().isdigit():
            parts = line.rsplit(":", 1)
            tags_part = parts[0].strip()
            weight = int(parts[1].strip())
        else:
            tags_part = line
            weight = 1

        tags = [t.strip() for t in tags_part.split(",") if t.strip()]
        if tags:
            chunks.append(StyleChunk(tags=tags, weight=weight))

    return chunks


def select_random(style_text: str) -> list[str]:
    """Select a random style chunk using weighted probabilities.

    10 % chance of no style, 90 % chance of one weighted-random chunk.
    """
    chunks = parse_style_chunks(style_text)
    if not chunks:
        return []

    if random.random() <= 0.1:
        return []

    weighted_pool: list[int] = []
    for i, chunk in enumerate(chunks):
        weighted_pool.extend([i] * chunk.weight)

    if not weighted_pool:
        return []

    idx = random.choice(weighted_pool)
    return chunks[idx].tags


def select_specific(style_text: str, style_index: int) -> list[str]:
    """Select a specific style by 1-based index. Returns [] on out-of-bounds."""
    chunks = parse_style_chunks(style_text)
    if not chunks:
        return []

    zero = style_index - 1
    if zero < 0 or zero >= len(chunks):
        return []
    return chunks[zero].tags


def list_styles(style_text: str) -> list[tuple[int, str, int]]:
    """Return (1-based index, comma-joined tags, weight) for display."""
    chunks = parse_style_chunks(style_text)
    return [(i + 1, ", ".join(c.tags), c.weight) for i, c in enumerate(chunks)]


def build_style_string(
    style_text: str,
    raw_prompt: str,
    style_index: Optional[int],
) -> str:
    """Choose the appropriate style tags and return as a comma-joined string.

    Returns empty string when no style should be applied.
    """
    if style_index is not None:
        if style_index == 0:
            return ""
        tags = select_specific(style_text, style_index)
        return ", ".join(tags)

    # Auto-detect: skip random style if prompt already contains style tags
    if style_text and raw_prompt:
        chunks = parse_style_chunks(style_text)
        all_tags = [t for c in chunks for t in c.tags]
        prompt_lower = raw_prompt.lower()
        for tag in all_tags:
            if re.search(r"\b" + re.escape(tag.lower()) + r"\b", prompt_lower):
                return ""

    tags = select_random(style_text)
    return ", ".join(tags)
