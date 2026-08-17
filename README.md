# Argus 👁️

> In Greek myth, Argus Panoptes was the hundred-eyed giant who never stopped watching.
> This Argus gives your Mac eyes — a local vision-language model, one click away in the menu bar.

Argus is a tiny macOS menu bar app + CLI that runs a **local vision-language model server** on Apple Silicon, powered by [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) and Apple's MLX framework (`mlx.fast` Metal kernels — not llama.cpp).

Click **Start** and you get an **OpenAI-compatible API** (`/v1/chat/completions`) that understands both **text and images**, running entirely on your machine. No cloud, no telemetry, no Electron.

```
🟢A   ready        🟡A   loading        ⚪️A   stopped
```

## Features

- **Desktop chat app** — **Open Argus** in the menu bar opens a native window (AppKit + WebKit) with a sidebar of past chats, images by ＋ / drag & drop / ⌘V, streaming replies and collapsible thinking. While the model works you get pulsing dots and an elapsed-seconds counter, and the send button turns into Stop. No Electron: the whole app is one Swift file.
- **Thinking on demand** — a Think chip next to the model picker turns reasoning on or off per request, no restart involved. Reasoning streams into a collapsible block.
- **Speed readout** — live tokens/s while generating, then a footer with tok/s, time to first token and total time. On an M2 Max, Qwen3.8-27B in bf16 runs around 6 tok/s; the 4bit variant is several times faster.
- **Launch page** — copyable commands that point curl, the OpenAI Python/Node SDKs, Codex, aider or any OpenAI-compatible app at your local server, with the current model name filled in.
- **Settings window** — model, launch at login, expose-to-network, ports, reply length limit, model location with size, and extra server flags; Save or Save & Restart.
- **Menu bar control** — start / stop / restart the server, live status icon, switch model, copy API URL, open log.
- **Model switching** — pick bf16 / 8bit / 4bit from the tray submenu or the picker next to the message box; Argus rewrites the config, restarts the server, and downloads the weights if you don't have them yet. Already-downloaded variants are marked ✓.
- **CLI** — `argus ask "what's this?" photo.png` for one-shot questions, `argus chat` for an interactive session (drop image paths straight into your message), `argus ui` for the chat page in a browser, plus `start|stop|restart|status|log|use|download|model|config`.
- **Auto-download** — no model on disk? The first start pulls it from Hugging Face automatically (default: `mlx-community/Qwen3.8-27B-bf16`, ~54 GB bf16; pick a 4bit/8bit variant if you have less RAM).
- **Configurable** — model, host, port, and extra `mlx_vlm.server` flags in one config file.

## Requirements

- Apple Silicon Mac (MLX requirement), macOS 13+
- [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) ≥ 0.6.13: `uv tool install -p 3.12 mlx-vlm` (or `pip install mlx-vlm`)
- Xcode Command Line Tools (for `swiftc` and `make`)

## Install

```bash
git clone https://github.com/wangxiuwen/argus.git
cd argus
make install
open ~/Applications/Argus.app
```

## Usage

Click the menu bar icon → **Start Server**. The first run downloads the model; later runs load it in ~1–2 minutes. When the icon turns 🟢 the API is ready:

```bash
curl http://127.0.0.1:8090/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "mlx-community/Qwen3.8-27B-bf16",
  "messages": [{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
    {"type": "text", "text": "What is in this image?"}
  ]}]
}'
```

Any OpenAI SDK works — point `base_url` at `http://127.0.0.1:8090/v1`.

### CLI

```bash
argus start       # start (auto-downloads model on first run)
argus stop
argus restart
argus status      # ready / loading / not running
argus log         # tail the server log

argus ask "what is in this image?" shot.png   # one-shot, images optional
argus ask --think "17*23?"                    # let it reason first
argus chat        # interactive; /think toggles reasoning, /new resets
argus ui          # open the chat interface in a browser

argus use mlx-community/Qwen3.8-27B-4bit      # switch model and restart
argus download    # pre-download the configured model
argus config      # show effective settings
```

### Coding tools

Argus can launch coding agents against the local model. The compatibility
bridge keeps each client from replacing the model already loaded in memory.

```bash
argus launch                         # list supported tools and install status
argus launch codex                   # Codex CLI
argus launch aider --message "help" # aider (extra arguments pass through)
argus launch opencode                # OpenCode
argus launch claude                  # Claude Code via Anthropic Messages API
argus launch shell                   # shell with local API variables set
argus bridge status                  # inspect the bridge separately
```

The bridge listens on `127.0.0.1:8092` by default and starts automatically.
Set `BRIDGE_PORT` in `~/.config/argus/config` to change it.

Claude Code starts in `--bare` mode by default. This retains its core coding
tools while preventing a large user skill/MCP setup from overwhelming the
local model's prompt. To load your full Claude configuration instead:

```bash
ARGUS_CLAUDE_FULL=1 argus launch claude
```

OpenCode also uses an isolated local-only provider by default, so existing
cloud credentials and MCP configuration are not loaded accidentally. To keep
your normal OpenCode plugins/MCPs while still forcing inference through Argus:

```bash
ARGUS_OPENCODE_FULL=1 argus launch opencode
```

### Configuration

`~/.config/argus/config` (created on demand, `KEY=VALUE` lines):

```ini
MODEL=mlx-community/Qwen3.8-27B-bf16
PORT=8090
HOST=127.0.0.1
UI_PORT=8091
BRIDGE_PORT=8092
MAX_TOKENS=4096
EXTRA_ARGS=--kv-bits 8
```

`MAX_TOKENS` controls both Argus clients and the server-side generation cap.
`EXTRA_ARGS` is passed straight to `mlx_vlm.server` — see
`mlx_vlm.server --help` for KV-cache quantization, thinking budget, draft
models and more.

### Picking a model

Any model supported by mlx-vlm works. For Qwen3.8-27B on different RAM budgets:

| Variant | Size | Fits comfortably in |
|---|---|---|
| `mlx-community/Qwen3.8-27B-bf16` | ~54 GB | 96 GB+ |
| `mlx-community/Qwen3.8-27B-8bit` | ~29 GB | 48 GB+ |
| `mlx-community/Qwen3.8-27B-4bit` | ~15 GB | 32 GB+ |

## Uninstall

```bash
make uninstall
```

Model weights live in `~/.cache/huggingface/hub` — remove them there if you want the disk back.

## License

MIT
