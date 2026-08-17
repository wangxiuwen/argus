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
import urllib.request
import urllib.error

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".tiff"}


def get_model():
    try:
        with urllib.request.urlopen(API + "/v1/models", timeout=3) as r:
            return json.load(r)["data"][0]["id"]
    except OSError:
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


def stream_chat(messages, model):
    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": 4096}
    req = urllib.request.Request(
        API + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    out = []
    try:
        with urllib.request.urlopen(req) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                delta = json.loads(data)["choices"][0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    out.append(piece)
                    print(piece, end="", flush=True)
    except urllib.error.HTTPError as e:
        sys.exit(f"\nserver error {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    print()
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
    if not argv:
        sys.exit('usage: argus ask "prompt" [image.png ...]')
    text, images = split_images(argv)
    model = get_model()
    for p in images:
        print(f"[image] {p}", file=sys.stderr)
    stream_chat([{"role": "user", "content": build_content(text or "Describe this image.", images)}], model)


def cmd_chat():
    model = get_model()
    print(f"Argus chat — model: {model}")
    print("Drop or type image paths inside your message to attach them.")
    print("Commands: /new (reset context)  /quit\n")
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
        try:
            words = shlex.split(line)
        except ValueError:
            words = line.split()
        text, images = split_images(words)
        for p in images:
            print(f"[image] {p}")
        messages.append({"role": "user", "content": build_content(text or "Describe this image.", images)})
        reply = stream_chat(messages, model)
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "ask":
        cmd_ask(sys.argv[2:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "chat":
        cmd_chat()
    else:
        sys.exit(__doc__.strip())
