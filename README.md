# Mira

Mira is a native macOS workspace for local chat, coding agents and local media
generation on Apple Silicon. One continuous conversation can answer questions,
create images, compose music, or render videos without switching to a separate app.

Click **Start** and you get an **OpenAI-compatible API** (`/v1/chat/completions`) that understands both **text and images**, running entirely on your machine. No cloud, no telemetry, no Electron.

Mira appears in both the Dock and menu bar. The Dock uses a single-color parrot
mark on a plain white background; the menu bar uses a native monochrome bird that changes
color with the server state. Closing the chat window hides Mira from the Dock while keeping the
menu bar app and services alive; minimizing the window keeps its Dock icon.

## Features

- **Tool-calling creative agent** — write normally in one conversation. The local chat model decides when to call image, music or video generation tools; there is no mode switch.
- **Durable batch generation** — requests such as “create 100 songs” become persistent SQLite-backed batches with per-item prompts, progress, pause, resume and cancel controls. Closing the window or restarting Mira does not discard the queue.
- **Bounded self-iteration** — every creation can pass through a local quality gate that critiques and refines its prompt, then retries generation a limited number of times instead of looping forever.
- **Feedback-driven memory** — explicit likes, dislikes and saved preferences become durable context for later conversations and creations. Nothing is learned from a private result unless you choose to save it.
- **Safe source iteration** — Mira can ask a coding agent to improve an isolated checkout, run its tests and present the result as a Candidate. It never edits or replaces the running application silently.
- **Local LoRA candidates** — explicitly approved 4–5 star examples can be exported as MLX-LM chat JSONL and trained locally after a separate approval step. Adapters remain separate from the base model until you choose what to do with them.
- **Large-run safeguard** — batches over 20 items or an estimated 5 GB require explicit confirmation before they are created. A single request may contain up to 1,000 outputs.
- **Local image generation** — FLUX.2-klein 4B 4-bit creates PNG images locally. Its approximately 5 GB weights download only when first used.
- **Local music generation** — MiniMax Music 3 8-bit turns a style prompt and sectioned lyrics into playable WAV files. Its approximately 13 GB weights download only when first used.
- **Local video generation** — MiniMax H3 produces video with synchronized stereo audio. Mira handles the source download, visible progress, 4-bit conversion and inline playback.
- **Desktop chat app** — **Open Mira** in the menu bar opens a native window (AppKit + WebKit) with past conversations, images by ＋ / drag & drop / ⌘V, Agent replies and live generation task cards. Enter inserts a line break; ⌘↵ or Ctrl+Enter sends. No Electron.
- **Thinking on demand** — a Think chip next to the model picker turns reasoning on or off per request, no restart involved. Reasoning streams into a collapsible block.
- **Speed readout** — live tokens/s while generating, then a footer with tok/s, time to first token and total time. On an M2 Max, Qwen3.8-27B in bf16 runs around 6 tok/s; the 4bit variant is several times faster.
- **Launch page** — copyable commands that point curl, the OpenAI Python/Node SDKs, Codex, aider or any OpenAI-compatible app at your local server, with the current model name filled in.
- **Settings window** — model, launch at login, expose-to-network, ports, reply length limit, model location with size, and extra server flags; Save or Save & Restart.
- **Menu bar control** — start / stop / restart the server, live status icon, switch model, copy API URL, open log.
- **Model switching** — pick bf16 / 8bit / 4bit from the tray submenu or the picker next to the message box; Mira rewrites the config, restarts the server, and downloads the weights if you don't have them yet. Already-downloaded variants are marked ✓.
- **CLI** — `mira ask "what's this?" photo.png` for one-shot questions, `mira chat` for an interactive session (drop image paths straight into your message), `mira ui` for the chat page in a browser, plus `start|stop|restart|status|log|use|download|model|config`.
- **Auto-download** — no model on disk? The first start pulls it from Hugging Face automatically (default: `mlx-community/Qwen3.8-27B-bf16`, ~54 GB bf16; pick a 4bit/8bit variant if you have less RAM).
- **Resilient downloads** — Mira defaults to resumable HTTPS downloads instead of Xet/CAS chunk reconstruction, which is less reliable behind some mirrors and proxies. Set `HF_HUB_DISABLE_XET=0` when starting Mira to opt back into Xet.
- **Automatic cache cleanup** — before each start, Mira removes only superseded partials for the selected model while retaining the newest partial for resume. Other models in the shared Hugging Face cache are never touched.
- **Configurable** — model, host, port, and extra `mlx_vlm.server` flags in one config file.

## Requirements

- Apple Silicon Mac, macOS 26.2+ (required by the bundled media runtimes)
- [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) ≥ 0.6.13: `uv tool install -p 3.12 mlx-vlm` (or `pip install mlx-vlm`)
- Xcode Command Line Tools (for `swiftc` and `make`)

The release bundles pinned, checksum-verified VPIPE and mlx-serve runtimes. Model
weights are not redistributed; each media model is fetched from its public model
repository on first use.

Original media weights use the standard Hugging Face Hub cache at
`$HF_HOME/hub` (normally `~/.cache/huggingface/hub`) so other compatible tools
can reuse the same snapshots and interrupted downloads can resume. Mira keeps
only tasks and outputs in `~/Library/Application Support/Mira`; locally converted
H3 artifacts live under the same HF cache in `hub/mira-vpipe`.

## Install

```bash
git clone https://github.com/wangxiuwen/mira.git
cd mira
make install
open ~/Applications/Mira.app
```

## Usage

Click the menu bar bird → **Start Server**. The first run downloads the model; later runs load it in ~1–2 minutes. When the status says ready, the API is available:

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

### Creative Agent and batches

The desktop conversation uses the loaded local model's native OpenAI-compatible
tool calls. Mira supplies `generate_images`, `generate_music`,
`generate_videos`, task-listing and task-control tools, then executes the tool
calls through a durable local worker. No cloud Agent service or external Agent
framework is involved.

For example, type `生成 1000 首每首 2 分钟、歌词各不相同的木吉他歌曲`.
Mira first reports the estimated output size and asks for confirmation. After
confirmation it generates each song serially, creates distinct prompts and
lyrics just in time, and keeps running if the window is closed. Progress and
completed outputs reappear in the same conversation. Queue state lives at
`~/Library/Application Support/Mira/jobs.sqlite3`.

### Self-iteration safety

The desktop **Iteration Center** exposes four layers: bounded quality retries,
durable preferences, isolated source Candidates, and local LoRA training runs.
Creative refinement and remembered preferences can run automatically. Source
changes and model training cannot: Mira first creates a reviewable Candidate,
then waits for an explicit second click before applying it to its isolated source
copy or starting a training process. The running app and base model are never
silently overwritten.

An approved code Candidate can be published as a uniquely named GitHub branch
and pull request. Mira reruns the test suite, blocks changes to GitHub automation,
scans for private labels, and records the commit and PR URL. It never pushes to
`main`, merges the PR, or creates a release automatically.

Iteration artifacts live at
`~/Library/Application Support/Mira/iterations`. MLX-LM training support is
optional and can be installed with `pip install "mlx-lm[train]"`. Quantized base
models use QLoRA; resulting adapters are stored separately.

### CLI

```bash
mira start       # start (auto-downloads model on first run)
mira stop
mira restart
mira status      # ready / loading / not running
mira log         # tail the server log

mira ask "what is in this image?" shot.png   # one-shot, images optional
mira ask --think "17*23?"                    # let it reason first
mira chat        # interactive; /think toggles reasoning, /new resets
mira ui          # open the chat interface in a browser

mira use mlx-community/Qwen3.8-27B-4bit      # switch model and restart
mira download    # pre-download the configured model
mira config      # show effective settings
```

### Coding tools

Mira can launch coding agents against the local model. The compatibility
bridge keeps each client from replacing the model already loaded in memory.

```bash
mira launch                         # list supported tools and install status
mira launch codex                   # Codex CLI
mira launch aider --message "help" # aider (extra arguments pass through)
mira launch opencode                # OpenCode
mira launch claude                  # Claude Code via Anthropic Messages API
mira launch shell                   # shell with local API variables set
mira bridge status                  # inspect the bridge separately
```

The bridge listens on `127.0.0.1:8092` by default and starts automatically.
Set `BRIDGE_PORT` in `~/.config/argus/config` to change it.

Claude Code starts in `--bare` mode by default. This retains its core coding
tools while preventing a large user skill/MCP setup from overwhelming the
local model's prompt. To load your full Claude configuration instead:

```bash
ARGUS_CLAUDE_FULL=1 mira launch claude
```

OpenCode also uses an isolated local-only provider by default, so existing
cloud credentials and MCP configuration are not loaded accidentally. To keep
your normal OpenCode plugins/MCPs while still forcing inference through Mira:

```bash
ARGUS_OPENCODE_FULL=1 mira launch opencode
```

### Configuration

`~/.config/argus/config` (the legacy path is retained so existing installs keep
their settings; created on demand as `KEY=VALUE` lines):

```ini
MODEL=mlx-community/Qwen3.8-27B-bf16
PORT=8090
HOST=127.0.0.1
UI_PORT=8091
BRIDGE_PORT=8092
MAX_TOKENS=4096
EXTRA_ARGS=--kv-bits 8
```

`MAX_TOKENS` controls both Mira clients and the server-side generation cap.
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

Chat model weights live in `~/.cache/huggingface/hub`; media weights use Mira's
local media stores and `~/.mlx-serve/models`. They are intentionally left in place
when uninstalling so an app reinstall does not download them again.

## License

MIT
