#!/usr/bin/env python3
"""Durable local generation queue for Mira's image, music and video tools."""
import json
import os
import pathlib
import sqlite3
from contextlib import contextmanager
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MIRA_JOBS_PORT", "9881"))
CHAT_API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
BASES = {
    "image": os.environ.get("MIRA_IMAGE_API", "http://127.0.0.1:9880"),
    "music": os.environ.get("MIRA_MUSIC_API", "http://127.0.0.1:9879"),
    "video": os.environ.get("MIRA_VIDEO_API", "http://127.0.0.1:9877"),
}
DB = pathlib.Path(os.environ.get(
    "MIRA_JOBS_DB", pathlib.Path.home() / "Library" / "Application Support" / "Mira" / "jobs.sqlite3"))
DB.parent.mkdir(parents=True, exist_ok=True)
db_lock = threading.RLock()
wake = threading.Event()
agent_lock = threading.Lock()
agent_inflight = {}
agent_results = {}

AGENT_SYSTEM = """You are Mira, a local creative agent. Image, music and video generation are
tools, not separate chat modes. Use a tool whenever the user asks you to create media. For
multiple outputs, create one durable batch. A tool may return confirmation_required for a
large batch; explain the estimated size and ask for confirmation. When the user confirms,
call the same tool again with confirmed=true. Never claim a task was created unless the tool
returned a batch id. Answer in the user's language and keep status summaries concise."""


def generation_tool(name, kind, description, extra_properties):
    properties = {
        "prompt": {"type": "string", "description": "Creative concept and constraints"},
        "count": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 1},
        "confirmed": {"type": "boolean", "description": "True only after user confirms a large batch"},
    }
    properties.update(extra_properties)
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": ["prompt"]}}}


AGENT_TOOLS = [
    generation_tool("generate_images", "image", "Queue one or many image generations", {
        "size": {"type": "string", "enum": ["1024x1024", "1344x768", "768x1344"]}}),
    generation_tool("generate_music", "music", "Queue one or many complete songs", {
        "duration_seconds": {"type": "integer", "minimum": 1, "maximum": 360},
        "lyrics": {"type": "string", "description": "Optional exact lyrics; omit to let the agent create distinct lyrics"}}),
    generation_tool("generate_videos", "video", "Queue one or many videos with synchronized audio", {
        "duration_seconds": {"type": "integer", "enum": [1, 5]},
        "size": {"type": "string", "enum": ["960x544", "512x288"]}}),
    {"type": "function", "function": {"name": "list_generation_tasks",
     "description": "List recent generation batches and their progress",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "control_generation_task",
     "description": "Pause, resume, or cancel a generation batch",
     "parameters": {"type": "object", "properties": {
         "batch_id": {"type": "string"}, "action": {"type": "string", "enum": ["pause", "resume", "cancel"]}},
         "required": ["batch_id", "action"]}}},
]


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


@contextmanager
def database():
    db = connect()
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
          total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0, spec TEXT NOT NULL,
          request_id TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
          position INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
          prompt TEXT, lyrics TEXT, output TEXT, error TEXT,
          FOREIGN KEY(batch_id) REFERENCES batches(id)
        );
        CREATE INDEX IF NOT EXISTS items_batch_status ON items(batch_id,status,position);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(batches)")}
        if "request_id" not in columns:
            db.execute("ALTER TABLE batches ADD COLUMN request_id TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS batches_request_id "
                   "ON batches(request_id) WHERE request_id IS NOT NULL")


def estimate_bytes(kind, count, spec):
    if kind == "music":
        return count * int(spec.get("duration_seconds", 60)) * 176_400
    if kind == "video":
        return count * int(spec.get("duration_seconds", 5)) * 400_000
    return count * 3_000_000


def create_batch(body):
    kind = str(body.get("kind", ""))
    if kind not in BASES:
        raise ValueError("kind must be image, music, or video")
    count = int(body.get("count", 1))
    if not 1 <= count <= 1000:
        raise ValueError("count must be between 1 and 1000")
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt is required")
    request_id = str(body.get("request_id", "")).strip() or None
    if request_id and len(request_id) > 200:
        raise ValueError("request_id is too long")
    if request_id:
        with database() as db:
            existing = db.execute("SELECT id FROM batches WHERE request_id=?",
                                  (request_id,)).fetchone()
        if existing:
            return batch(existing["id"]), 202
    estimated = estimate_bytes(kind, count, body)
    if (count > 20 or estimated > 5_000_000_000) and body.get("confirmed") is not True:
        return {"confirmation_required": True, "kind": kind, "count": count,
                "estimated_gb": round(estimated / 1e9, 1)}, 409
    batch_id = uuid.uuid4().hex[:12]
    now = time.time()
    spec = dict(body); spec.pop("confirmed", None)
    with db_lock, database() as db:
        db.execute("INSERT INTO batches(id,kind,status,total,spec,request_id,created,updated) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (batch_id, kind, "queued", count, json.dumps(spec, ensure_ascii=False),
                    request_id, now, now))
        db.executemany("INSERT INTO items(batch_id,position) VALUES(?,?)",
                       ((batch_id, position) for position in range(1, count + 1)))
    wake.set()
    return batch(batch_id), 202


def batch(batch_id, include_items=True):
    with database() as db:
        row = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            return None
        out = dict(row); out["spec"] = json.loads(out["spec"])
        if include_items:
            out["items"] = [dict(item) for item in db.execute(
                "SELECT position,status,prompt,output,error FROM items WHERE batch_id=? ORDER BY position LIMIT 100",
                (batch_id,))]
        return out


def batches():
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT id,kind,status,total,completed,failed,error,created,updated FROM batches ORDER BY created DESC LIMIT 100")]


def action(batch_id, name):
    target = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}.get(name)
    if not target:
        raise ValueError("unknown action")
    with db_lock, database() as db:
        current = db.execute("SELECT status FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not current:
            return None
        allowed = {
            "pause": {"queued", "running"},
            "resume": {"paused"},
            "cancel": {"queued", "running", "paused"},
        }
        if current["status"] not in allowed[name]:
            raise ValueError(f"cannot {name} a {current['status']} batch")
        db.execute("UPDATE batches SET status=?,updated=? WHERE id=?",
                   (target, time.time(), batch_id))
        if target == "cancelled":
            db.execute("UPDATE items SET status='cancelled' WHERE batch_id=? AND status='queued'", (batch_id,))
    wake.set()
    return batch(batch_id)


def execute_agent_tool(name, arguments, request_id=None):
    if name in ("generate_images", "generate_music", "generate_videos"):
        kind = {"generate_images": "image", "generate_music": "music",
                "generate_videos": "video"}[name]
        payload = dict(arguments); payload["kind"] = kind
        if request_id:
            payload["request_id"] = request_id
        result, code = create_batch(payload)
        return result, code
    if name == "list_generation_tasks":
        return {"tasks": batches()}, 200
    if name == "control_generation_task":
        result = action(str(arguments.get("batch_id", "")), str(arguments.get("action", "")))
        return result or {"error": "task not found"}, 200 if result else 404
    return {"error": f"unknown tool {name}"}, 400


def agent_chat(body):
    incoming = body.get("messages", [])
    if not isinstance(incoming, list):
        raise ValueError("messages must be an array")
    messages = [{"role": "system", "content": AGENT_SYSTEM}]
    for message in incoming[-40:]:
        if isinstance(message, dict) and message.get("role") in ("user", "assistant"):
            content = message.get("content", "")
            messages.append({"role": message["role"], "content": content})
    created = []
    for _ in range(8):
        health = request_json(CHAT_API + "/health", timeout=5)
        model = health.get("loaded_model")
        if not model:
            raise RuntimeError("local chat model is not ready")
        response = request_json(CHAT_API + "/v1/chat/completions", {
            "model": model, "messages": messages, "tools": AGENT_TOOLS,
            "tool_choice": "auto", "stream": False, "max_tokens": 2048,
            "enable_thinking": bool(body.get("enable_thinking", False)),
        }, timeout=900)
        assistant = response["choices"][0]["message"]
        calls = assistant.get("tool_calls") or []
        if not calls:
            return {"content": assistant.get("content") or "", "tasks": created,
                    "model": model}
        messages.append({"role": "assistant", "content": assistant.get("content") or "",
                         "tool_calls": calls})
        for call_index, call in enumerate(calls):
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
                request_id = str(body.get("request_id", "")).strip()
                tool_request_id = f"{request_id}:{call_index}" if request_id else None
                result, code = execute_agent_tool(call["function"]["name"], arguments,
                                                  tool_request_id)
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                result, code = {"error": str(error)}, 400
            if result.get("id") and result["id"] not in created:
                created.append(result["id"])
            messages.append({"role": "tool", "tool_call_id": call.get("id"),
                             "name": call["function"]["name"],
                             "content": json.dumps({"status": code, **result}, ensure_ascii=False)})
        if created:
            return {"content": "任务已创建，正在后台生成。", "tasks": created,
                    "model": model}
    return {"content": "工具调用次数过多，已停止这一轮。", "tasks": created}


def agent_chat_idempotent(body):
    """Coalesce retries from a reopened WebView under one durable request id."""
    request_id = str(body.get("request_id", "")).strip()
    if not request_id:
        return agent_chat(body)
    if len(request_id) > 160:
        raise ValueError("request_id is too long")
    with agent_lock:
        cached = agent_results.get(request_id)
        if cached is not None:
            return cached
        state = agent_inflight.get(request_id)
        leader = state is None
        if leader:
            state = {"event": threading.Event(), "result": None, "error": None}
            agent_inflight[request_id] = state
    if not leader:
        if not state["event"].wait(920):
            raise RuntimeError("Agent request is still running")
        if state["error"]:
            raise RuntimeError(state["error"])
        return state["result"]
    try:
        result = agent_chat(body)
        state["result"] = result
        with agent_lock:
            agent_results[request_id] = result
            while len(agent_results) > 200:
                agent_results.pop(next(iter(agent_results)))
        return result
    except Exception as error:  # noqa: BLE001
        state["error"] = str(error)
        raise
    finally:
        state["event"].set()
        with agent_lock:
            agent_inflight.pop(request_id, None)


def request_json(url, payload=None, timeout=1800):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error)
        except (ValueError, json.JSONDecodeError):
            detail = {}
        raise RuntimeError(detail.get("error", f"HTTP {error.code}")) from error


def model_json(instruction):
    health = request_json(CHAT_API + "/health", timeout=5)
    model = health.get("loaded_model")
    if not model:
        raise RuntimeError("local chat model is not ready")
    result = request_json(CHAT_API + "/v1/chat/completions", {
        "model": model,
        "messages": [{"role": "system", "content": "Return only valid JSON, without markdown."},
                     {"role": "user", "content": instruction}],
        "stream": False, "max_tokens": 1200, "temperature": 0.9,
    }, timeout=600)
    text = result["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


def materialize(kind, spec, position, total):
    prompt = str(spec.get("prompt", ""))
    lyrics = str(spec.get("lyrics", ""))
    if total == 1 and (kind != "music" or lyrics.strip()):
        return prompt, lyrics
    if kind == "music":
        instruction = (f"Create song {position} of {total} from this concept: {prompt}. "
                       "Make it distinct from neighboring songs. Return an object with keys "
                       "prompt (English production/style prompt) and lyrics (complete lyrics "
                       "using [verse] and [chorus] markers).")
        generated = model_json(instruction)
        return str(generated["prompt"]), str(generated["lyrics"])
    generated = model_json(
        f"Create distinct {kind} prompt {position} of {total} from this concept: {prompt}. "
        "Return an object with one key named prompt.")
    return str(generated["prompt"]), ""


def media_payload(kind, spec, prompt, lyrics):
    if kind == "image":
        return {"prompt": prompt, "size": spec.get("size", "1024x1024"),
                "steps": int(spec.get("steps", 4))}
    if kind == "music":
        return {"prompt": prompt, "lyrics": lyrics,
                "duration_seconds": max(1, min(360, int(spec.get("duration_seconds", 60))))}
    size = str(spec.get("size", "960x544")).split("x")
    seconds = max(1, min(10, int(spec.get("duration_seconds", 5))))
    return {"prompt": prompt, "width": int(size[0]), "height": int(size[1]),
            "frames": 22 if seconds == 1 else 120, "steps": int(spec.get("steps", 6)), "seed": position_seed(prompt)}


def position_seed(prompt):
    return abs(hash(prompt)) % (2**31)


def wait_media(kind, job_id=None):
    base = BASES[kind]
    while True:
        status = request_json(base + "/api/status")
        if status.get("error"):
            raise RuntimeError(status["error"])
        if job_id and status.get("job_id") and status["job_id"] != job_id:
            raise RuntimeError("media task was replaced")
        if not status.get("running"):
            output = status.get("output")
            if kind == "video":
                name = pathlib.Path(str(output or "")).name
                output = f"/outputs/{urllib.parse.quote(name)}" if name else None
            if not output:
                listing = request_json(base + ("/api/outputs" if kind == "video" else "/api/list"))
                first = listing[0] if listing else None
                output = first.get("url") if isinstance(first, dict) else (f"/files/{urllib.parse.quote(first)}" if first else None)
            if not output:
                raise RuntimeError("generation finished without an output")
            return output if str(output).startswith("http") else base + output
        time.sleep(2)


def run_media(kind, payload):
    base = BASES[kind]
    try:
        accepted = request_json(base + "/api/generate", payload)
    except RuntimeError as error:
        if "not prepared" not in str(error).lower() and "尚未准备" not in str(error):
            raise
        request_json(base + "/api/prepare", {"accept_license": True})
        while True:
            status = request_json(base + "/api/status")
            if status.get("error"):
                raise RuntimeError(status["error"])
            if status.get("model_ready") and not status.get("running"):
                break
            time.sleep(3)
        accepted = request_json(base + "/api/generate", payload)
    return wait_media(kind, accepted.get("job_id"))


def finish_item(selected, output, prompt=None, lyrics=None):
    with db_lock, database() as db:
        changed = db.execute(
            "UPDATE items SET status='complete',prompt=COALESCE(?,prompt),"
            "lyrics=COALESCE(?,lyrics),output=? WHERE id=? AND status='running'",
            (prompt, lyrics, output, selected["item_id"])).rowcount
        if changed:
            db.execute("UPDATE batches SET completed=completed+1,updated=? WHERE id=?",
                       (time.time(), selected["id"]))
    finalize_batch(selected["id"])


def finalize_batch(batch_id):
    with db_lock, database() as db:
        remaining = db.execute(
            "SELECT count(*) FROM items WHERE batch_id=? AND status IN ('queued','running')",
            (batch_id,)).fetchone()[0]
        current = db.execute("SELECT status,failed FROM batches WHERE id=?",
                             (batch_id,)).fetchone()
        if remaining == 0 and current["status"] not in ("cancelled", "paused"):
            db.execute("UPDATE batches SET status=?,updated=? WHERE id=?",
                       ("failed" if current["failed"] else "complete", time.time(), batch_id))


def recover_running():
    """Adopt media still computing after the task daemon was restarted."""
    with database() as db:
        rows = [dict(row) for row in db.execute("""
          SELECT i.id item_id,i.position,b.* FROM items i JOIN batches b ON b.id=i.batch_id
          WHERE i.status='running' AND b.status='running' ORDER BY b.created,i.position""")]
    for selected in rows:
        try:
            status = request_json(BASES[selected["kind"]] + "/api/status", timeout=5)
        except Exception:  # service disappeared; safely retry the item
            status = {}
        if not status.get("running"):
            with db_lock, database() as db:
                db.execute("UPDATE items SET status='queued' WHERE id=? AND status='running'",
                           (selected["item_id"],))
                db.execute("UPDATE batches SET status='queued',updated=? WHERE id=? AND status='running'",
                           (time.time(), selected["id"]))
            wake.set()
            continue
        try:
            output = wait_media(selected["kind"], status.get("job_id"))
            spec = json.loads(selected["spec"])
            finish_item(selected, output, str(spec.get("prompt", "")),
                        str(spec.get("lyrics", "")))
        except Exception as error:  # noqa: BLE001
            with db_lock, database() as db:
                db.execute("UPDATE items SET status='failed',error=? WHERE id=? AND status='running'",
                           (str(error), selected["item_id"]))
                db.execute("UPDATE batches SET failed=failed+1,error=?,updated=? WHERE id=?",
                           (str(error), time.time(), selected["id"]))
            finalize_batch(selected["id"])


def worker():
    while True:
        selected = None
        with db_lock, database() as db:
            row = db.execute("""
              SELECT i.id item_id,i.position,b.* FROM items i JOIN batches b ON b.id=i.batch_id
              WHERE i.status='queued' AND b.status IN ('queued','running')
              ORDER BY b.created,i.position LIMIT 1""").fetchone()
            if row:
                selected = dict(row)
                db.execute("UPDATE items SET status='running' WHERE id=?", (row["item_id"],))
                db.execute("UPDATE batches SET status='running',updated=? WHERE id=?",
                           (time.time(), row["id"]))
        if not selected:
            wake.wait(2); wake.clear(); continue
        spec = json.loads(selected["spec"])
        try:
            prompt, lyrics = materialize(selected["kind"], spec, selected["position"], selected["total"])
            output = run_media(selected["kind"], media_payload(selected["kind"], spec, prompt, lyrics))
            finish_item(selected, output, prompt, lyrics)
        except Exception as error:  # noqa: BLE001
            with db_lock, database() as db:
                db.execute("UPDATE items SET status='failed',error=? WHERE id=?",
                           (str(error), selected["item_id"]))
                db.execute("UPDATE batches SET failed=failed+1,error=?,updated=? WHERE id=?",
                           (str(error), time.time(), selected["id"]))
            finalize_batch(selected["id"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def send_json(self, body, code=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 50_000_000: raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"{}")
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/jobs": return self.send_json(batches())
        if path.startswith("/api/jobs/"):
            result = batch(path.rsplit("/", 1)[-1])
            return self.send_json(result or {"error": "not found"}, 200 if result else 404)
        if path == "/health": return self.send_json({"ok": True})
        self.send_json({"error": "not found"}, 404)
    def do_POST(self):
        try:
            if self.path == "/api/agent":
                return self.send_json(agent_chat_idempotent(self.read_json()))
            if self.path == "/api/jobs":
                result, code = create_batch(self.read_json()); return self.send_json(result, code)
            if self.path.startswith("/api/jobs/"):
                parts = self.path.strip("/").split("/")
                result = action(parts[2], parts[3]) if len(parts) == 4 else None
                return self.send_json(result or {"error": "not found"}, 200 if result else 404)
        except (ValueError, TypeError, RuntimeError, json.JSONDecodeError, urllib.error.URLError) as error:
            return self.send_json({"error": str(error)}, 400)
        self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=recover_running, daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
