# Racconic General

A [maubot](https://github.com/maubot/maubot) plugin for AI-powered image generation in Matrix rooms. Supports both **NovelAI** and **RunPod/ComfyUI** backends simultaneously, with LLM-based prompt expansion via OpenRouter.

## Features

- **Dual backend support** — NovelAI and RunPod/ComfyUI can run side-by-side, selectable per request
- **LLM prompt generation** — Three built-in presets (Dormouse, Fossa, Hippo) expand short descriptions into detailed image prompts via OpenRouter
- **Per-model max_tokens** — Each LLM preset can specify its own `max_tokens` limit
- **Raw mode** — Bypass the LLM and pass your text directly as the image prompt
- **Reply-based context** — Reply to any message with `!racc` to use that message as input
- **Weighted style system** — Define style tag pools with weights; styles are randomly selected or chosen by index
- **Batch generation** — Generate 1–4 images per request (RunPod backend, sequential or native mode)
- **Custom sizing** — Override image dimensions per request
- **Per-room rate limiting** — Configurable cooldown between generation requests
- **NovelAI rate limit handling** — Automatic retry on 429/520 errors with serialized request queue (one request at a time)
- **Progress feedback** — Reaction indicators while generating (hourglass → checkmark/X)
- **Bot-safe output** — All text responses are prefixed with `~` so other bots ignore them

## Installation

Build the plugin and upload to your maubot instance:

```bash
mbc build
# or build and upload directly:
mbc build -u
```

Then create a plugin instance in the maubot management UI and configure it.

## Usage

### Basic Generation

```
!racc a fox sitting in a forest
```

### Reply to a Message

Reply to any message with `!racc` to use that message's content as input for image generation.

### Flags

All flags are optional and can appear in any order before the prompt text:

| Flag | Short | Description |
|------|-------|-------------|
| `--dormouse` | `-d` | Use the Dormouse LLM preset |
| `--fossa` | `-f` | Use the Fossa LLM preset |
| `--hippo` | `-h` | Use the Hippo LLM preset |
| `--raw` | `-r` | Skip LLM — use text as-is for the prompt |
| `--nai` | `-n` | Use the NovelAI backend |
| `--runpod` | `-rp` | Use the RunPod backend |
| `--style N` | `-s N` | Select style by index (1-based, `0` = none) |
| `--size WxH` | | Set image dimensions (e.g. `--size 1024x768`) |
| `--batch N` | `-b N` | Generate N images, 1–4 (RunPod only) |

### Examples

```
!racc a cyberpunk cityscape at sunset
!racc -f --nai a fox in a forest
!racc -s 3 --size 1024x768 -b 2 a cat on a rooftop
!racc -r exact prompt tags here, detailed, masterpiece
!racc -s 0 a landscape with no style applied
```

### Subcommands

```
!racc help                 Show usage information
!racc styles [nai|runpod]  List configured styles for a backend
!racc presets              List available LLM presets
!racc status               Show which backends are enabled
```

## Configuration

All settings are managed through the maubot config editor. Key sections:

### General

| Key | Default | Description |
|-----|---------|-------------|
| `command_prefix` | `racc` | Command name (e.g. `!racc`) |
| `default_backend` | `runpod` | Default backend when none specified |
| `rate_limit_seconds` | `10` | Per-room cooldown between requests |
| `room_whitelist` | `[]` | Room IDs where the bot is allowed (empty = all rooms) |

### LLM (OpenRouter)

```yaml
llm:
    api_key: "sk-or-..."
    base_url: "https://openrouter.ai/api/v1/chat/completions"
    default_preset: dormouse
    max_retries: 3
    max_tokens: 400                  # Global default
    presets:
        dormouse:
            model: "google/gemini-2.0-flash-001"
            system_prompt: "..."
            temperature: 1.0
            max_tokens: 400          # Per-preset override
            reasoning_enabled: false
        fossa:
            model: "anthropic/claude-3.5-sonnet"
            system_prompt: "..."
            temperature: 1.2
            max_tokens: 400
            reasoning_enabled: false
        hippo:
            model: "openai/gpt-4o"
            system_prompt: "..."
            temperature: 0.9
            max_tokens: 400
            reasoning_enabled: false
```

The `max_tokens` setting can be specified per preset to override the global default. This is useful when different models need different token limits (e.g. a reasoning model may need more tokens).

The system prompts ship as placeholders. Replace them with your own prompts that describe how you want the LLM to generate image descriptions.

### Common Prompt Settings

```yaml
prompt:
    prefix_prompt: ""       # Prepended to every generated prompt
    suffix_prompt: ""       # Appended to every generated prompt
    banned_words: []        # Words filtered from all prompts
```

### NovelAI Backend

```yaml
nai:
    enabled: false
    api_key: ""
    model: "nai-diffusion-4-5-full"
    default_size: "1216x832"
    steps: 28
    scale: 5.0
    sampler: "k_euler_ancestral"
    scheduler: "karras"
    quality_toggle: true
    decrisper: false
    uc_preset: 2
    cfg_rescale: 0.0
    negative_prompt: ""
    max_pixels: 1048576       # Anlas guard pixel limit
    max_steps: 28             # Anlas guard step limit
    style_text: ""            # Backend-specific styles
    prefix_prompt: ""         # Appended to common prefix
    suffix_prompt: ""         # Appended to common suffix
```

The **anlas guard** automatically clamps oversized dimensions and step counts to stay within NovelAI's free-tier limits.

NovelAI requests are **serialized** — only one request is in-flight at a time (per API key limitation). If the API returns 429 (rate limit) or 520 (server error), the plugin retries up to 10 times with a random 1–5s delay between attempts.

### RunPod/ComfyUI Backend

```yaml
runpod:
    enabled: true
    api_key: ""
    worker_id: ""
    default_size: "1024x1024"
    batch_mode: "sequential"   # "sequential" or "native"
    max_batch: 4
    poll_interval: 2.0         # Seconds between status polls
    poll_timeout: 120.0        # Max wait time for a job
    comfyui_workflow_file: ""  # Absolute path to external ComfyUI workflow JSON
    style_text: ""
    prefix_prompt: ""
    suffix_prompt: ""
```

The `comfyui_workflow_file` should point to an absolute filesystem path containing a ComfyUI workflow (API format) with `{prompt}` as a placeholder in the text input node. The plugin automatically randomises seeds and updates dimensions/batch size in latent image nodes.

**Batch modes:**
- `sequential` — Makes N parallel single-image requests (faster overall)
- `native` — Single request with `batch_size=N` in the workflow (fewer API calls)

### Style Text Format

Styles are defined per backend. Each line is a style chunk, optionally ending with `:weight`:

```yaml
style_text: |
    colored pencil (medium), traditional media :3
    impressionism, pastel (artwork), painting :2
    digital art, sharp details, vibrant colors
```

- Higher weight = higher selection probability
- Lines without `:N` default to weight 1
- 90% chance a random style is applied, 10% chance none is
- Use `--style 0` / `-s 0` to force no style
- If the LLM-generated prompt already contains a style tag, random selection is skipped

### Prompt Construction

The final prompt is assembled as:

```
[common prefix_prompt], [backend prefix_prompt]
[LLM output or raw text]
[selected style tags]
[common suffix_prompt], [backend suffix_prompt]
```

## Architecture

```
racconic_general/
    __init__.py          Main plugin — commands, rate limiting, orchestration
    config.py            Config class (BaseProxyConfig)
    types.py             Dataclasses (GenerationRequest, GenerationResult, etc.)
    input_parser.py      GNU-style flag extraction from raw input
    style_engine.py      Weighted random style selection
    llm_client.py        OpenRouter API client with retry logic
    prompt_pipeline.py   LLM → clean → ban words → prefix + style + suffix
    image_sender.py      Upload to Matrix media repo, send m.image events
    backends/
        __init__.py      ImageBackend ABC
        novelai.py       NovelAI backend (anlas guard, v4 payload, ZIP extraction, serialized queue)
        runpod.py        RunPod/ComfyUI backend (workflow injection, polling, batch)
```

## License

MIT
