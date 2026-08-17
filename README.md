# Argus 👁️

> In Greek myth, Argus Panoptes was the hundred-eyed giant who never stopped watching.
> This Argus gives your Mac eyes — a local vision-language model, one click away in the menu bar.

Argus is a tiny macOS menu bar app + CLI that runs a **local vision-language model server** on Apple Silicon, powered by [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) and Apple's MLX framework (`mlx.fast` Metal kernels — not llama.cpp).

Click **Start** and you get an **OpenAI-compatible API** (`/v1/chat/completions`) that understands both **text and images**, running entirely on your machine. No cloud, no telemetry, no Electron.

```
🟢A   ready        🟡A   loading        ⚪️A   stopped
```

## Features

- **Menu bar control** — start / stop / restart, live status icon, copy API URL, open log, launch at login. Native Swift/AppKit, a single source file, zero dependencies.
- **CLI twin** — `argus start|stop|restart|status|log|download|model|config` for terminal folks. The tray and the CLI manage the same server.
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
argus download    # pre-download the configured model
argus model mlx-community/Qwen3.8-27B-8bit   # switch model
argus config      # show effective settings
```

### Configuration

`~/.config/argus/config` (created on demand, `KEY=VALUE` lines):

```ini
MODEL=mlx-community/Qwen3.8-27B-bf16
PORT=8090
HOST=127.0.0.1
EXTRA_ARGS=--max-tokens 4096
```

`EXTRA_ARGS` is passed straight to `mlx_vlm.server` — see `mlx_vlm.server --help` for KV-cache quantization, thinking budget, draft models and more.

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
