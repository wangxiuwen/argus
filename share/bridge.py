#!/usr/bin/env python3
"""Anthropic-compatible bridge for Fermi.

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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
PORT = int(os.environ.get("ARGUS_BRIDGE_PORT", "8092"))
try:
    MAX_TOKENS = int(os.environ.get("ARGUS_MAX_TOKENS", "4096"))
except ValueError:
    MAX_TOKENS = 4096

DEBUG = os.environ.get("ARGUS_BRIDGE_DEBUG") == "1"

# Only coalesce requests arriving at nearly the same time.  A longer cache can
# resurrect the previous model immediately after `argus use` restarts the
# server, causing mlx-vlm to swap back to the old weights.
MODEL_CACHE_SECONDS = 0.5
_loaded = {"id": None, "at": 0.0}


def openai_passthrough_route(route):
    return route.endswith((
        "/chat/completions", "/completions", "/embeddings",
        "/responses", "/responses/input_tokens",
    ))


def trusted_browser_origin(origin, port, fetch_site=None):
    """Block web pages from spending local inference resources via CSRF."""
    if fetch_site == "cross-site":
        return False
    if not origin:
        return True
    try:
        parsed = urllib.parse.urlsplit(origin)
        return (parsed.scheme == "http"
                and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                and parsed.port == int(port))
    except (TypeError, ValueError):
        return False


def debug(*parts):
    if DEBUG:
        print("[bridge]", *parts, file=sys.stderr, flush=True)


def loaded_model():
    """The model the server currently has in memory.

    Clients send their own model names ("claude-sonnet-4-5", "gpt-5-codex").
    mlx_vlm.server reads that field as an instruction to unload and load that
    repo instead, which either stalls for minutes or fails outright, so every
    request the bridge forwards is pinned to what is already loaded.
    """
    now = time.monotonic()
    if _loaded["id"] and now - _loaded["at"] < MODEL_CACHE_SECONDS:
        return _loaded["id"]
    try:
        with urllib.request.urlopen(API + "/health", timeout=5) as r:
            model = json.load(r)["loaded_model"]
            if not isinstance(model, str) or not model:
                raise ValueError("upstream returned an empty model id")
            _loaded["id"] = model
            _loaded["at"] = now
    except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        # Never pin a stale model after a server restart or model switch.
        _loaded["id"] = None
        _loaded["at"] = 0.0
    return _loaded["id"]


def codex_model_info(model_id):
    """Return the strict model-catalog shape expected by current Codex."""
    return {
        "slug": model_id,
        "display_name": model_id,
        "description": "Local model served by Fermi",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 1,
        "model_messages": {
            "instructions_template": (
                "You are a capable coding assistant. Follow the user's "
                "instructions and use the available tools."
            ),
            "instructions_variables": None,
        },
        "default_reasoning_summary": "none",
        "support_verbosity": False,
        "default_verbosity": "medium",
        "apply_patch_tool_type": "freeform",
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": True,
        "context_window": 65536,
        "max_context_window": 65536,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text", "image"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "tool_mode": "code_mode_only",
        "multi_agent_version": "v2",
    }


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
    # the upstream server rejects a system message that is not the first one, and
    # clients do put system-role entries inside messages, so collect them all and
    # emit a single leading system message
    system_parts = []
    if req.get("system"):
        system_parts.append(_text_of(req["system"]))

    for m in req.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content")

        if role == "system":
            system_parts.append(_text_of(content))
            continue

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

    system = "\n\n".join(p for p in system_parts if p)
    if system:
        msgs.insert(0, {"role": "system", "content": system})
    return msgs


def to_openai_request(req, model=None):
    out = {
        "model": model or loaded_model(),
        "messages": to_openai_messages(req),
        "max_tokens": req.get("max_tokens", MAX_TOKENS),
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
    # Anthropic content blocks cannot be interleaved: a block must stop before
    # the next one starts.  OpenAI, however, can interleave argument fragments
    # for several tool calls in the same chunks.  Buffer calls and emit each as
    # one complete, sequential block after text/reasoning has finished.
    tool_slots = {}           # openai tool index -> accumulated call
    finish = "stop"
    out_tokens = 0
    reported_out_tokens = None

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
        if chunk.get("error"):
            close_block()
            error = chunk["error"]
            message = error.get("message", "upstream stream failed") \
                if isinstance(error, dict) else str(error)
            sse.event("error", {"type": "error", "error": {
                "type": "api_error", "message": message,
            }})
            return
        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens") is not None:
            reported_out_tokens = usage["completion_tokens"]
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
            out_tokens += 1

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
            is_new = slot not in tool_slots
            if is_new:
                tool_slots[slot] = {
                    "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:16]}",
                    "name": fn.get("name") or "",
                    "arguments": [],
                }
            elif call.get("id"):
                tool_slots[slot]["id"] = call["id"]
            if fn.get("name") and not is_new:
                tool_slots[slot]["name"] += fn["name"]
            if fn.get("arguments"):
                tool_slots[slot]["arguments"].append(fn["arguments"])
                out_tokens += 1

    close_block()
    for tool in tool_slots.values():
        open_block("tool", {"type": "tool_use", "id": tool["id"],
                            "name": tool["name"], "input": {}})
        arguments = "".join(tool["arguments"])
        if arguments:
            sse.event("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": arguments}})
        close_block()
    sse.event("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": STOP_REASON.get(finish, "end_turn"), "stop_sequence": None},
        "usage": {"output_tokens": reported_out_tokens
                  if reported_out_tokens is not None else out_tokens},
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
        debug("error", code, kind, message[:400])
        self._json({"type": "error", "error": {"type": kind, "message": message}}, code)

    @property
    def route(self):
        return self.path.split("?", 1)[0].rstrip("/")

    def do_GET(self):
        if self.route in ("/health", "/v1/health"):
            model = loaded_model()
            self._json({"ok": bool(model), "upstream": API, "model": model})
        elif self.route == "/v1/models":
            try:
                with urllib.request.urlopen(API + "/v1/models", timeout=5) as r:
                    data = json.load(r)
                rows = [{"type": "model", "id": m["id"], "display_name": m["id"]}
                        for m in data.get("data", [])]
                # OpenAI-compatible SDKs expect `data`; current Codex clients
                # additionally refresh a richer `models` catalog.  Return both
                # shapes so neither client has to fall back with a decode error.
                self._json({
                    "data": rows,
                    "models": [codex_model_info(m["id"]) for m in rows],
                })
            except OSError as e:
                self._error(f"upstream unreachable: {e}", 502)
        else:
            self._error(f"no route for GET {self.route}", 404, "not_found_error")

    def _passthrough_openai(self, req):
        """OpenAI-protocol clients (codex, aider, …) also send their own model
        names, so the same pinning applies; otherwise forward untouched."""
        model = loaded_model()
        if not model:
            return self._error("the model server is not ready", 503, "overloaded_error")
        req["model"] = model
        body = json.dumps(req).encode()
        upstream_req = urllib.request.Request(
            API + self.path, data=body, headers={"Content-Type": "application/json"})
        try:
            upstream = urllib.request.urlopen(upstream_req, timeout=900)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        except OSError as e:
            return self._error(f"cannot reach the model server at {API}: {e}", 502)

        ctype = upstream.headers.get("Content-Type", "application/json")
        self.send_response(upstream.status)
        self.send_header("Content-Type", ctype)
        if "text/event-stream" in ctype:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for line in upstream:
                    self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            data = upstream.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self):
        if not trusted_browser_origin(self.headers.get("Origin"), PORT,
                                      self.headers.get("Sec-Fetch-Site")):
            return self._error("cross-site requests are not allowed", 403,
                               "permission_error")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._error("invalid Content-Length", 400, "invalid_request_error")
        if not 0 <= length <= 50_000_000:
            return self._error("request body is too large", 413, "invalid_request_error")
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._error("invalid JSON", 400, "invalid_request_error")
        if not isinstance(req, dict):
            return self._error("request body must be an object", 400,
                               "invalid_request_error")

        # OpenAI-shaped endpoints are proxied with the model pinned
        if openai_passthrough_route(self.route):
            return self._passthrough_openai(req)

        if self.route.endswith("count_tokens"):
            # rough estimate: clients only use it for budgeting
            text = json.dumps(req.get("messages", "")) + json.dumps(req.get("system", ""))
            return self._json({"input_tokens": max(1, len(text) // 4)})

        if not self.route.endswith("messages"):
            return self._error(f"no route for POST {self.route}", 404, "not_found_error")

        debug("POST", self.path, "client model:", req.get("model"),
              "stream:", bool(req.get("stream")), "tools:", len(req.get("tools") or []),
              "msgs:", len(req.get("messages") or []))
        model = loaded_model()
        if not model:
            return self._error("the model server is not ready", 503, "overloaded_error")
        payload = to_openai_request(req, model)
        debug("roles:", [m["role"] for m in payload["messages"]])
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

    def handle_error(self, request, client_address):
        # CLI users cancel generations routinely.  Do not fill argus.log with a
        # socketserver traceback after the bridge has already handled the abort.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            debug("client disconnected", client_address[0])
            return
        super().handle_error(request, client_address)


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"Fermi Anthropic bridge on http://127.0.0.1:{PORT} (upstream: {API})")
        sys.stdout.flush()
        srv.serve_forever()
