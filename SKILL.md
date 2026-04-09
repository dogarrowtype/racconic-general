# Racconic Image Generator — Agent Skill

This document describes how to interact with the **Racconic** Maubot plugin as a Matrix room participant. The bot listens for commands prefixed with `!racc` (the prefix may differ per deployment — check `!racc status` to confirm the bot is active).

---

## Core Command

```
!racc [flags] <prompt>
```

Generates one or more images from a natural-language prompt. The prompt is optionally rewritten by an LLM before being sent to the image generation backend.

You may also **reply to any existing message** with `!racc` (with optional flags) to use that message's text as the prompt.

---

## Flags

All flags are GNU-style. Order does not matter. Flags and prompt text can be intermixed.

### Preset flags (LLM persona / style of prompt rewriting)

| Flag | Long form | Description |
|------|-----------|-------------|
| `-d` | `--dormouse` | Dormouse preset |
| `-f` | `--fossa` | Fossa preset |
| `-h` | `--hippo` | Hippo preset |
| `-r` | `--raw` | Skip LLM rewriting entirely — send prompt verbatim to the backend |

Only one preset flag may be used per command.

### Backend selection

| Flag | Long form | Description |
|------|-----------|-------------|
| `-n` | `--nai` | Force NovelAI backend |
| `-rp` | `--runpod` | Force RunPod/ComfyUI backend |

If omitted, the configured default backend is used.

### Valued options

| Flag | Argument | Description |
|------|----------|-------------|
| `-s N` | `--style N` | Select style by number (use `0` for no style). See `!racc styles` for available indices. |
| `--size WxH` | e.g. `1024x768` | Override output image dimensions (WIDTHxHEIGHT). |
| `-b N` | `--batch N` | Generate N images in one request (1–4). RunPod only. |

---

## Subcommands

| Command | Description |
|---------|-------------|
| `!racc help` | Show full usage information in-room |
| `!racc styles` | List all styles for all enabled backends |
| `!racc styles nai` | List styles for the NovelAI backend |
| `!racc styles runpod` | List styles for the RunPod backend |
| `!racc presets` | List available LLM presets and their models |
| `!racc status` | Show which backends are enabled and which is the default |

---

## Examples

```
!racc a raccoon in a forest at dusk
```
Generate an image with the default backend and default LLM preset.

```
!racc -f a raccoon eating pizza
```
Use the fossa LLM preset to rewrite the prompt, then generate.

```
!racc -r highly detailed photograph of a red panda, soft lighting
```
Send the prompt verbatim to the backend (no LLM rewriting).

```
!racc --nai -s 2 a fox wearing a scarf
```
Use NovelAI, style index 2.

```
!racc --runpod --size 1280x720 -b 4 cinematic shot of a raccoon city
```
Generate 4 images at 1280×720 on RunPod.

```
!racc --hippo --style 0 a cozy cabin in the woods
```
Use hippo preset, no style applied.

```
# (while replying to a message containing "a cat in the rain")
!racc -f
```
Use the fossa preset with the replied-to message as the prompt.

---

## Style Selection Behaviour

- By default, a style is **randomly selected** from the configured pool with weighted probability (90% chance a style is applied, 10% chance none is).
- If the LLM-generated prompt already contains a style tag, automatic selection is skipped.
- Use `-s 0` / `--style 0` to explicitly force **no style**.
- Use `-s N` to pin a specific style by index (see `!racc styles` for the list).

---

## Prompt Assembly Order

The final prompt sent to the backend is constructed as:

```
[common prefix], [backend-specific prefix]
[LLM output — or raw text if --raw]
[selected style tags]
[common suffix], [backend-specific suffix]
```

---

## Bot Output Convention

All text responses from the bot are prefixed with `~`. This is intentional — it marks responses as bot-generated so other bots ignore them. When parsing bot replies, strip the leading `~` if needed.

---

## Bot Reactions

The bot uses Matrix reactions to signal progress:

| Reaction | Meaning |
|----------|---------|
| ⏳ | Generation started |
| ✅ | Images generated successfully |
| ❌ | Generation failed (error message follows) |

---

## Error Handling

The parser is strict and will report errors for malformed input rather than silently ignoring them. Common mistakes it will catch:

- `--f` instead of `-f` (double-dash on short flags)
- `-style` instead of `--style` (single-dash on long flags)
- `-s4` instead of `-s 4` (missing space before value)
- `--size 1024` instead of `--size 1024x768` (missing height)
- `-b 5` (batch out of range — must be 1–4)
- Multiple conflicting flags of the same type
- Unknown flags

On error, the bot replies with a human-readable explanation and suggests the correct form.

---

## Rate Limiting

The bot enforces a per-room cooldown between generations. If you send a command too soon after the previous one, the bot will reply with the number of seconds remaining. Wait and retry.

---

## Interaction Patterns for Agents

1. **Discovery** — run `!racc status` first to confirm the bot is active and learn which backends are available.
2. **Style exploration** — run `!racc styles` to see available style indices before using `-s N`.
3. **Preset exploration** — run `!racc presets` to see available LLM presets and their names.
4. **Simple generation** — `!racc <description>` is sufficient for most use cases.
5. **Exact control** — use `--raw` when the prompt should not be modified by the LLM.
6. **Reply workflow** — reply to a user's message with `!racc [flags]` to generate from their text without retyping it.
7. **Cooldown awareness** — if the bot returns a "please wait Ns" message, parse the number of seconds and retry after that delay.
8. **Error recovery** — if the bot returns an "Oops!" message, parse the listed errors, correct the flags, and resubmit.
