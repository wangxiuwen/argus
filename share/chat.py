#!/usr/bin/env python3
"""Argus CLI chat client — talks to the local mlx-vlm OpenAI-compatible server.

Usage (normally invoked via `argus ask` / `argus chat`):
  chat.py ask "prompt" [image.png ...]
  chat.py chat                      # interactive REPL, drop image paths into your message
"""
import base64
import json
import mimetypes
import os
import shlex
import sys
import time
import urllib.request
import urllib.error

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
CONFIGURED_MODEL = os.environ.get("ARGUS_MODEL")
try:
    MAX_TOKENS = int(os.environ.get("ARGUS_MAX_TOKENS", "4096"))
except ValueError:
    MAX_TOKENS = 4096
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".tiff"}


def get_model():
    try:
        with urllib.request.urlopen(API + "/health", timeout=3) as r:
            return json.load(r)["loaded_model"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        if CONFIGURED_MODEL:
            return CONFIGURED_MODEL
        sys.exit(f"server not reachable at {API} — start it with: argus start")


def img_part(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def build_content(text, images):
    if not images:
        return text
    return [img_part(p) for p in images] + [{"type": "text", "text": text}]


def stream_chat(messages, model, thinking=False):
    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS,
               "enable_thinking": thinking}
    req = urllib.request.Request(
        API + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    out = []
    tps = 0.0
    t0 = time.monotonic()
    ttft = 0.0
    # let the user know we are waiting rather than wedged; cleared on the first token
    print("\033[2m…\033[0m", end="", flush=True)
    try:
        with urllib.request.urlopen(req) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                speed = (chunk.get("timings") or {}).get("predicted_per_second")
                if speed:
                    tps = speed
                delta = chunk["choices"][0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    if not out:
                        print("\r\033[K", end="", flush=True)
                        ttft = time.monotonic() - t0
                    out.append(piece)
                    print(piece, end="", flush=True)
    except urllib.error.HTTPError as e:
        print("\r\033[K", end="")
        sys.exit(f"server error {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    except urllib.error.URLError as e:
        print("\r\033[K", end="")
        reason = getattr(e, "reason", e)
        sys.exit(f"server not reachable at {API}: {reason} — start it with: argus start")
    except KeyboardInterrupt:
        print("\r\033[K(stopped)")
        return "".join(out)
    if not out:
        print("\r\033[K", end="")
    print()
    if out:
        bits = []
        if tps:
            bits.append(f"{tps:.1f} tok/s")
        if ttft:
            bits.append(f"first token {ttft:.1f}s")
        bits.append(f"{time.monotonic() - t0:.1f}s total")
        print(f"\033[2m{'  ·  '.join(bits)}\033[0m", file=sys.stderr)
    return "".join(out)


def split_images(words):
    """Separate existing image files from the rest of the words."""
    images, text_words = [], []
    for w in words:
        p = os.path.expanduser(w)
        if os.path.isfile(p) and os.path.splitext(p)[1].lower() in IMG_EXT:
            images.append(p)
        else:
            text_words.append(w)
    return " ".join(text_words), images


def cmd_ask(argv):
    thinking = False
    argv = list(argv)
    for flag in ("--think", "--thinking"):
        if flag in argv:
            argv.remove(flag)
            thinking = True
    if not argv:
        sys.exit('usage: argus ask [--think] "prompt" [image.png ...]')
    text, images = split_images(argv)
    model = get_model()
    for p in images:
        print(f"[image] {p}", file=sys.stderr)
    stream_chat([{"role": "user", "content": build_content(text or "Describe this image.", images)}],
                model, thinking)


def cmd_chat():
    model = get_model()
    thinking = False
    print(f"Argus chat — model: {model}")
    print("Drop or type image paths inside your message to attach them.")
    print("Commands: /new (reset context)  /think (toggle thinking)  /quit\n")
    messages = []
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line == "/new":
            messages = []
            print("(context cleared)")
            continue
        if line == "/think":
            thinking = not thinking
            print(f"(thinking {'on' if thinking else 'off'})")
            continue
        try:
            words = shlex.split(line)
        except ValueError:
            words = line.split()
        text, images = split_images(words)
        for p in images:
            print(f"[image] {p}")
        messages.append({"role": "user", "content": build_content(text or "Describe this image.", images)})
        reply = stream_chat(messages, model, thinking)
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "ask":
        cmd_ask(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "chat":
        cmd_chat()
    else:
        sys.exit(__doc__.strip())
