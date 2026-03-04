from __future__ import annotations

import re
from typing import Optional

from .types import GenerationRequest


# -- flag definitions --------------------------------------------------------

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

# Valued flag regexes (with leading boundary)
_STYLE_PATTERN = re.compile(r"(?:^|\s)(?:--style|-s)\s+(\d+)")
_SIZE_PATTERN = re.compile(r"(?:^|\s)(?:--size)\s+(\d{3,5})x(\d{3,5})")
_BATCH_PATTERN = re.compile(r"(?:^|\s)(?:--batch|-b)\s+([1-4])")

# All known flags
_ALL_KNOWN_LONG = {"--dormouse", "--fossa", "--hippo", "--raw", "--nai", "--runpod", "--style", "--size", "--batch"}
_ALL_KNOWN_SHORT = {"-d", "-f", "-h", "-r", "-n", "-rp", "-s", "-b"}
_ALL_KNOWN_FLAGS = _ALL_KNOWN_LONG | _ALL_KNOWN_SHORT

# -- error detection patterns ------------------------------------------------

# Double-dash with single-letter: --f → -f
_DOUBLE_DASH_SHORT = {
    "--f": "-f (fossa)", "--d": "-d (dormouse)", "--h": "-h (hippo)",
    "--r": "-r (raw)", "--n": "-n (nai)", "--b": "-b N (batch)",
    "--s": "-s N (style)",
}

# Single-dash long flags: -style → --style
_SINGLE_DASH_LONG = {
    "-style": "--style N", "-size": "--size WxH", "-batch": "--batch N",
    "-dormouse": "--dormouse", "-fossa": "--fossa", "-hippo": "--hippo",
    "-raw": "--raw", "-nai": "--nai", "-runpod": "--runpod",
}

# Bare word + digit: s4, b3 → -s 4, -b 3
_BARE_VALUED = re.compile(r"(?:^|\s)([sb])(\d+)(?:\s|$)")

# Missing space: -s4, -b3
_MISSING_SPACE_SHORT = re.compile(r"(?:^|\s)(-[sb])(\d+)(?:\s|$)")
# Missing space: --style4, --batch3
_MISSING_SPACE_LONG = re.compile(r"(?:^|\s)(--style|--batch)(\d+)(?:\s|$)")
# Missing space: --size1024x832
_MISSING_SPACE_SIZE = re.compile(r"(?:^|\s)(--size)(\d+x?\d*)(?:\s|$)")
# Spaced dash: "- s", "- d", etc.
_SPACED_DASH = re.compile(r"(?:^|\s)-\s+([a-z]{1,2})(?:\s|$)")

# Batch out of range
_BATCH_OOB = re.compile(r"(?:--batch|-b)\s+(\d+)")
# --size present but followed by something (for malformed size detection)
_SIZE_PRESENT = re.compile(r"(?:^|\s)--size(?:\s+(\S+))?")
# -s or -b not followed by a digit
_VALUED_FLAG_NO_ARG = re.compile(r"(?:^|\s)(-s|-b)(?:\s+[^0-9]|\s*$)")

# Stray dashes: bare -, --, ---, etc.
_STRAY_DASH = re.compile(r"(?:^|\s)(-{1,3})(?:\s|$)")

# Any flag-like token (for unknown flag detection)
_FLAG_TOKEN = re.compile(r"(?:(?<=\s)|^)(--?[a-z][\w-]*)(?=\s|$)")


def _find_flag_matches(text: str, flags: dict[str, str | None]) -> list[tuple[str, str | None]]:
    """Find all occurrences of flags in text, returning (flag, value) pairs."""
    found: list[tuple[str, str | None]] = []
    seen_positions: set[int] = set()
    for flag, value in sorted(flags.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r"(?:(?<=\s)|^)" + re.escape(flag) + r"(?=\s|$)")
        for m in pattern.finditer(text):
            if m.start() not in seen_positions:
                found.append((flag, value))
                seen_positions.add(m.start())
    return found


def parse(raw_input: str) -> GenerationRequest:
    """Parse raw user input into a GenerationRequest.

    Extracts GNU-style flags and valued options from the text.
    Everything remaining after extraction is the prompt text.
    Validates strictly — malformed or duplicate flags produce errors.

    """
    text = raw_input.strip()
    if not text:
        return GenerationRequest(prompt_text="")

    errors: list[str] = []

    # ── Phase 1: detect typos/malformed flags ────────────────────────────

    # Double-dash short flags: --f → -f
    for bad, suggestion in _DOUBLE_DASH_SHORT.items():
        pattern = re.compile(r"(?:^|\s)" + re.escape(bad) + r"(?:\s|$)")
        if pattern.search(text):
            errors.append(f"Unknown flag `{bad}` — did you mean `{suggestion}`?")

    # Single-dash long flags: -style → --style
    for bad, suggestion in _SINGLE_DASH_LONG.items():
        pattern = re.compile(r"(?:^|\s)" + re.escape(bad) + r"(?:\s|$)")
        if pattern.search(text):
            errors.append(f"`{bad}` uses one dash — did you mean `{suggestion}`?")

    # Missing space on short valued flags: -s4, -b3
    for m in _MISSING_SPACE_SHORT.finditer(text):
        flag, val = m.group(1), m.group(2)
        errors.append(f"`{flag}{val}` needs a space: `{flag} {val}`.")

    # Missing space on long valued flags: --style4, --batch3
    for m in _MISSING_SPACE_LONG.finditer(text):
        flag, val = m.group(1), m.group(2)
        errors.append(f"`{flag}{val}` needs a space: `{flag} {val}`.")

    # Missing space on size: --size1024x832
    for m in _MISSING_SPACE_SIZE.finditer(text):
        flag, val = m.group(1), m.group(2)
        errors.append(f"`{flag}{val}` needs a space: `{flag} {val}`.")

    # Spaced dash: "- s" → "-s"
    for m in _SPACED_DASH.finditer(text):
        letter = m.group(1)
        errors.append(f"`- {letter}` has an extra space — did you mean `-{letter}`?")

    # Bare word + digit: "s4" → "-s 4", "b3" → "-b 3"
    for m in _BARE_VALUED.finditer(text):
        letter, val = m.group(1), m.group(2)
        errors.append(f"`{letter}{val}` looks like a flag — did you mean `-{letter} {val}`?")

    # Batch out of range
    if not _BATCH_PATTERN.search(text):
        m = _BATCH_OOB.search(text)
        if m:
            val = int(m.group(1))
            if val < 1 or val > 4:
                errors.append(f"Batch count `{val}` is out of range — must be 1-4.")

    # Malformed --size: present but doesn't match valid WxH
    # (skip if already caught by missing-space check)
    if not _SIZE_PATTERN.search(text) and not _MISSING_SPACE_SIZE.search(text):
        m = _SIZE_PRESENT.search(text)
        if m:
            arg = m.group(1) or ""
            if not arg:
                errors.append("`--size` needs a value — e.g. `--size 1024x768`.")
            elif re.match(r"^\d+$", arg):
                # Just a number, no x: --size 1024
                errors.append(f"`--size {arg}` is missing the height — use `--size {arg}x768` (WIDTHxHEIGHT).")
            elif re.match(r"^\d+x$", arg) or re.match(r"^x\d+$", arg):
                # Incomplete: --size 1024x or --size x832
                errors.append(f"`--size {arg}` is incomplete — use format `--size WIDTHxHEIGHT`.")
            elif re.match(r"^\d+\s+\d+$", arg):
                # Space instead of x: --size 1024 832
                nums = arg.split()
                errors.append(f"Size uses `x` not space — did you mean `--size {nums[0]}x{nums[1]}`?")
            else:
                errors.append(f"`--size {arg}` is not valid — use format `--size WIDTHxHEIGHT` (e.g. `--size 1024x768`).")

    # Valued flag with no argument: "-s" or "-b" not followed by a number
    for m in _VALUED_FLAG_NO_ARG.finditer(text):
        flag = m.group(1)
        name = "style index" if flag == "-s" else "batch count"
        errors.append(f"`{flag}` needs a number — e.g. `{flag} 2` ({name}).")

    # Stray dashes: bare -, --, ---
    if not errors:
        for m in _STRAY_DASH.finditer(text):
            dashes = m.group(1)
            errors.append(f"Stray `{dashes}` — not a valid flag.")

    if errors:
        return GenerationRequest(prompt_text="", errors=errors)

    # ── Phase 2: extract valued flags (with duplicate detection) ──────────

    style_index: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    batch_count = 1

    style_matches = list(_STYLE_PATTERN.finditer(text))
    if len(style_matches) > 1:
        errors.append("Multiple `--style`/`-s` flags — pick one.")
    elif style_matches:
        m = style_matches[0]
        style_index = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    size_matches = list(_SIZE_PATTERN.finditer(text))
    if len(size_matches) > 1:
        errors.append("Multiple `--size` flags — pick one.")
    elif size_matches:
        m = size_matches[0]
        width = int(m.group(1))
        height = int(m.group(2))
        text = text[:m.start()] + text[m.end():]

    batch_matches = list(_BATCH_PATTERN.finditer(text))
    if len(batch_matches) > 1:
        errors.append("Multiple `--batch`/`-b` flags — pick one.")
    elif batch_matches:
        m = batch_matches[0]
        batch_count = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    if errors:
        return GenerationRequest(prompt_text="", errors=errors)

    # ── Phase 3: extract and validate boolean flags ──────────────────────

    preset_matches = _find_flag_matches(text, _PRESET_FLAGS)
    backend_matches = _find_flag_matches(text, _BACKEND_FLAGS)

    # Check for duplicate preset flags
    if len(preset_matches) > 1:
        flags_used = [f"`{f}`" for f, _ in preset_matches]
        errors.append(f"Multiple preset flags: {', '.join(flags_used)} — pick one.")

    # Check for duplicate backend flags
    if len(backend_matches) > 1:
        flags_used = [f"`{f}`" for f, _ in backend_matches]
        errors.append(f"Multiple backend flags: {', '.join(flags_used)} — pick one.")

    # Extract preset
    preset_name: Optional[str] = None
    is_raw = False
    if preset_matches and not errors:
        flag, preset_val = preset_matches[0]
        if preset_val is None:
            is_raw = True
        else:
            preset_name = preset_val
        text = re.compile(r"(?:(?<=\s)|^)" + re.escape(flag) + r"(?=\s|$)").sub("", text, count=1)

    # Extract backend
    backend_name: Optional[str] = None
    if backend_matches and not errors:
        flag, backend_val = backend_matches[0]
        backend_name = backend_val
        text = re.compile(r"(?:(?<=\s)|^)" + re.escape(flag) + r"(?=\s|$)").sub("", text, count=1)

    # ── Phase 4: check for unknown leftover flags ────────────────────────

    for m in _FLAG_TOKEN.finditer(text):
        token = m.group(1)
        if token in _ALL_KNOWN_FLAGS:
            continue
        errors.append(f"Unknown flag `{token}`. See `help` for available flags.")

    if errors:
        return GenerationRequest(prompt_text="", errors=errors)

    # ── Phase 5: clean up and return ─────────────────────────────────────

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
