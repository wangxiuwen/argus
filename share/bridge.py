#!/usr/bin/env python3
"""Anthropic-compatible bridge for Argus.

Serves POST /v1/messages in Anthropic's Messages format and translates to the
OpenAI /v1/chat/completions endpoint that mlx-vlm exposes, so Anthropic-protocol
clients (Claude Code) can talk to a local model. Streaming is translated event
by event: OpenAI SSE deltas become message_start / content_block_delta /
message_delta / message_stop.

Only the parts real clients rely on are implemented: text and image content,
system prompts, tools (function calling), tool results, stop reasons, streaming,
and /v1/messages/count_tokens.
"""
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.request
import uuid

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
PORT = int(os.environ.get("ARGUS_BRIDGE_PORT", "8092"))


# --------------------------------------------------------------------------- #
# Anthropic  ->  OpenAI
# --------------------------------------------------------------------------- #
def _text_of(blocks):
    if isinstance(blocks, str):
        return blocks
    out = []
    for b in blocks or []:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
        elif isinstance(b, str):
            out.append(b)
    return "\n".join(out)


def to_openai_messages(req):
    msgs = []
    system = req.get("system")
    if system:
        msgs.append({"role": "system", "content": _text_of(system)})

    for m in req.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content")

        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue

        parts, tool_calls, tool_results = [], [], []
        for b in content or []:
            btype = b.get("type")
            if btype == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif btype == "image":
                src = b.get("source") or {}
                if src.get("type") == "base64":
                    url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                else:
                    url = src.get("url", "")
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif btype == "tool_use":
                tool_calls.append({
                    "id": b.get("id"),
                    "type": "function",
                    "function": {"name": b.get("name"),
                                 "arguments": json.dumps(b.get("input") or {})},
                })
            elif btype == "tool_result":
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": b.get("tool_use_id"),
                    "content": _text_of(b.get("content")) or "",
                })

        # tool results are their own OpenAI messages and must precede the rest
        msgs.extend(tool_results)
        if tool_calls:
            msgs.append({"role": "assistant",
                         "content": _text_of([p for p in parts if p["type"] == "text"]) or None,
                         "tool_calls": tool_calls})
        elif parts:
            only_text = all(p["type"] == "text" for p in parts)
            msgs.append({"role": role,
                         "content": _text_of(parts) if only_text else parts})
    return msgs


def to_openai_request(req):
    out = {
        "model": req.get("model"),
        "messages": to_openai_messages(req),
        "max_tokens": req.get("max_tokens", 4096),
        "stream": bool(req.get("stream")),
    }
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p")):
        if req.get(src) is not None:
            out[dst] = req[src]
    if req.get("stop_sequences"):
        out["stop"] = req["stop_sequences"]
    if req.get("tools"):
        out["tools"] = [{
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        } for t in req["tools"] if t.get("name")]
        choice = req.get("tool_choice") or {}
        kind = choice.get("type")
        if kind == "any":
            out["tool_choice"] = "required"
        elif kind == "tool" and choice.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
        elif kind == "none":
            out["tool_choice"] = "none"
    # thinking maps to the server's per-request switch
    if isinstance(req.get("thinking"), dict):
        out["enable_thinking"] = req["thinking"].get("type") == "enabled"
        budget = req["thinking"].get("budget_tokens")
        if budget:
            out["thinking_budget"] = budget
    return out


STOP_REASON = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


# --------------------------------------------------------------------------- #
# OpenAI  ->  Anthropic
# --------------------------------------------------------------------------- #
def to_anthropic_response(oa, model):
    choice = (oa.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks = []
    if msg.get("reasoning_content"):
        blocks.append({"type": "thinking", "thinking": msg["reasoning_content"]})
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append({"type": "tool_use", "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                       "name": fn.get("name"), "input": args})
    usage = oa.get("usage") or {}
    return {
        "id": oa.get("id") or f"msg_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks or [{"type": "text", "text": ""}],
        "stop_reason": STOP_REASON.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


class SSE:
    """Writes Anthropic streaming events to the client."""

    def __init__(self, write):
        self.write = write

    def event(self, name, data):
        self.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())


def stream_translate(upstream, model, out):
    """Translate an OpenAI SSE stream into Anthropic streaming events."""
    sse = SSE(out)
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"
    sse.event("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }})
    sse.event("ping", {"type": "ping"})

    index = -1
    open_kind = None          # "thinking" | "text" | "tool"
    tool_slots = {}           # openai tool index -> our block index
    finish = "stop"
    out_tokens = 0

    def close_block():
        nonlocal open_kind
        if open_kind is not None:
            sse.event("content_block_stop", {"type": "content_block_stop", "index": index})
            open_kind = None

    def open_block(kind, extra):
        nonlocal index, open_kind
        index += 1
        open_kind = kind
        sse.event("content_block_start",
                  {"type": "content_block_start", "index": index, "content_block": extra})

    for raw in upstream:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if choice.get("finish_reason"):
            finish = choice["finish_reason"]

        if delta.get("reasoning_content"):
            if open_kind != "thinking":
                close_block()
                open_block("thinking", {"type": "thinking", "thinking": ""})
            sse.event("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "thinking_delta", "thinking": delta["reasoning_content"]}})

        if delta.get("content"):
            if open_kind != "text":
                close_block()
                open_block("text", {"type": "text", "text": ""})
            out_tokens += 1
            sse.event("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "text_delta", "text": delta["content"]}})

        for call in delta.get("tool_calls") or []:
            slot = call.get("index", 0)
            fn = call.get("function") or {}
            if slot not in tool_slots:
                close_block()
                open_block("tool", {"type": "tool_use",
                                    "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                                    "name": fn.get("name") or "", "input": {}})
                tool_slots[slot] = index
            if fn.get("arguments"):
                sse.event("content_block_delta", {
                    "type": "content_block_delta", "index": tool_slots[slot],
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]}})

    close_block()
    sse.event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": STOP_REASON.get(finish, "end_turn"), "stop_sequence": None},
        "usage": {"output_tokens": out_tokens},
    })
    sse.event("message_stop", {"type": "message_stop"})


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, code=500, kind="api_error"):
        self._json({"type": "error", "error": {"type": kind, "message": message}}, code)

    def do_GET(self):
        if self.path in ("/health", "/v1/health"):
            self._json({"ok": True, "upstream": API})
        elif self.path == "/v1/models":
            try:
                with urllib.request.urlopen(API + "/v1/models", timeout=5) as r:
                    data = json.load(r)
                self._json({"data": [{"type": "model", "id": m["id"], "display_name": m["id"]}
                                     for m in data.get("data", [])]})
            except OSError as e:
                self._error(f"upstream unreachable: {e}", 502)
        else:
            self._error("not found", 404, "not_found_error")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._error("invalid JSON", 400, "invalid_request_error")

        if self.path.rstrip("/").endswith("count_tokens"):
            # rough estimate: clients only use it for budgeting
            text = json.dumps(req.get("messages", "")) + json.dumps(req.get("system", ""))
            return self._json({"input_tokens": max(1, len(text) // 4)})

        if not self.path.rstrip("/").endswith("messages"):
            return self._error("not found", 404, "not_found_error")

        payload = to_openai_request(req)
        model = payload.get("model") or "argus"
        body = json.dumps(payload).encode()
        upstream_req = urllib.request.Request(
            API + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})

        try:
            upstream = urllib.request.urlopen(upstream_req, timeout=900)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            return self._error(f"upstream {e.code}: {detail}", 502)
        except OSError as e:
            return self._error(f"cannot reach the model server at {API}: {e}", 502)

        if not payload["stream"]:
            try:
                oa = json.load(upstream)
            except json.JSONDecodeError:
                return self._error("bad upstream response", 502)
            return self._json(to_anthropic_response(oa, model))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def write(data):
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        try:
            stream_translate(upstream, model, write)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"Argus Anthropic bridge on http://127.0.0.1:{PORT} (upstream: {API})")
        sys.stdout.flush()
        srv.serve_forever()
