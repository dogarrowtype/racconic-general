from __future__ import annotations

import re
from typing import Optional

from .types import GenerationRequest


# Flag patterns for extraction
_PRESET_FLAGS = {
    "--dormouse": "dormouse", "-d": "dormouse",
    "--fossa": "fossa", "-f": "fossa",
    "--hippo": "hippo", "-h": "hippo",
    "--raw": None, "-r": None,  # None signals raw mode
}

_BACKEND_FLAGS = {
    "--nai": "nai", "-n": "nai",
    "--runpod": "runpod", "-rp": "runpod",
}

# Regex for valued flags
_STYLE_PATTERN = re.compile(r"(?:--style|-s)\s+(\d+)")
_SIZE_PATTERN = re.compile(r"(?:--size)\s+(\d{3,4})x(\d{3,4})")
_BATCH_PATTERN = re.compile(r"(?:--batch|-b)\s+([1-4])")


def parse(raw_input: str) -> GenerationRequest:
    """Parse raw user input into a GenerationRequest.

    Extracts GNU-style flags and valued options from the text.
    Everything remaining after extraction is the prompt text.
    """
    text = raw_input.strip()
    if not text:
        return GenerationRequest(prompt_text="")

    preset_name: Optional[str] = None
    is_raw = False
    backend_name: Optional[str] = None
    style_index: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    batch_count = 1

    # Extract style (--style N / -s N)
    m = _STYLE_PATTERN.search(text)
    if m:
        style_index = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # Extract size (--size WIDTHxHEIGHT)
    m = _SIZE_PATTERN.search(text)
    if m:
        width = int(m.group(1))
        height = int(m.group(2))
        text = text[:m.start()] + text[m.end():]

    # Extract batch (--batch N / -b N)
    m = _BATCH_PATTERN.search(text)
    if m:
        batch_count = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # Extract preset flags (must check longer flags first to avoid partial matches)
    for flag, preset in sorted(_PRESET_FLAGS.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r"(?:^|\s)" + re.escape(flag) + r"(?:\s|$)")
        if pattern.search(text):
            if preset is None:
                is_raw = True
            else:
                preset_name = preset
            text = pattern.sub(" ", text, count=1)
            break

    # Extract backend flags
    for flag, backend in sorted(_BACKEND_FLAGS.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r"(?:^|\s)" + re.escape(flag) + r"(?:\s|$)")
        if pattern.search(text):
            backend_name = backend
            text = pattern.sub(" ", text, count=1)
            break

    # Clean up whitespace
    prompt_text = " ".join(text.split())

    return GenerationRequest(
        prompt_text=prompt_text,
        preset_name=preset_name,
        backend_name=backend_name,
        style_index=style_index,
        width=width,
        height=height,
        batch_count=batch_count,
        is_raw=is_raw,
    )
