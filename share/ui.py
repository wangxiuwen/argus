#!/usr/bin/env python3
"""Argus web chat UI — serves ui.html and proxies /v1/* to the local server.

The proxy keeps everything same-origin so no CORS setup is needed on the
mlx-vlm server. SSE streaming responses are forwarded line by line.
"""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import urllib.request
import urllib.error

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
PORT = int(os.environ.get("ARGUS_UI_PORT", "8091"))
HERE = os.path.dirname(os.path.abspath(__file__))
ARGUS = os.path.expanduser("~/.local/bin/argus")
CONFIG = os.path.expanduser("~/.config/argus/config")

VARIANTS = [
    {"label": "Qwen3.8-27B bf16 (~54 GB)", "id": "mlx-community/Qwen3.8-27B-bf16"},
    {"label": "Qwen3.8-27B 8bit (~29 GB)", "id": "mlx-community/Qwen3.8-27B-8bit"},
    {"label": "Qwen3.8-27B 4bit (~15 GB)", "id": "mlx-community/Qwen3.8-27B-4bit"},
]


def is_downloaded(repo):
    d = os.path.expanduser("~/.cache/huggingface/hub/models--" + repo.replace("/", "--"))
    return os.path.isdir(d)


DEFAULTS = {
    "MODEL": VARIANTS[0]["id"],
    "PORT": "8090",
    "HOST": "127.0.0.1",
    "UI_PORT": "8091",
    "EXTRA_ARGS": "",
}


def read_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in cfg:
                        cfg[k.strip()] = v.strip()
    except OSError:
        pass
    return cfg


def write_config(updates):
    cfg = read_config()
    for k, v in updates.items():
        if k in cfg:
            cfg[k] = str(v).strip()
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        for k in DEFAULTS:
            f.write(f"{k}={cfg[k]}\n")
    return cfg


def cache_info():
    path = os.path.expanduser("~/.cache/huggingface/hub")
    try:
        out = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=8).stdout
        gb = int(out.split()[0]) / 1024 / 1024
        size = f"{gb:.1f} GB"
    except Exception:
        size = "—"
    return {"path": path, "size": size}


def current_model():
    return read_config()["MODEL"]


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _serve_html(self, name="ui.html"):
        with open(os.path.join(HERE, name), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, data=None):
        req = urllib.request.Request(API + self.path, data=data)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            upstream = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except OSError:
            body = json.dumps({"error": f"server not reachable at {API} — run: argus start"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        ctype = upstream.headers.get("Content-Type", "application/json")
        self.send_response(upstream.status)
        self.send_header("Content-Type", ctype)
        if "text/event-stream" in ctype:
            # forward SSE line by line so tokens appear as they are generated
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
            body = upstream.read()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._serve_html()
        elif self.path == "/settings":
            self._serve_html("settings.html")
        elif self.path == "/argus/config":
            variants = [dict(v) for v in VARIANTS]
            for v in variants:
                v["downloaded"] = is_downloaded(v["id"])
            self._send_json({"config": read_config(), "variants": variants, "cache": cache_info()})
        elif self.path == "/argus/health":
            # distinguish "not running" from "running but busy generating": /v1/models
            # blocks while the single-worker server is mid-generation
            pidfile = os.path.expanduser("~/.local/state/argus/server.pid")
            alive = False
            try:
                with open(pidfile) as f:
                    os.kill(int(f.read().strip()), 0)
                alive = True
            except (OSError, ValueError):
                alive = False
            ready, model = False, None
            try:
                with urllib.request.urlopen(API + "/v1/models", timeout=2) as r:
                    model = json.load(r)["data"][0]["id"]
                    ready = True
            except OSError:
                pass
            self._send_json({"alive": alive, "ready": ready, "model": model})
        elif self.path == "/argus/models":
            cur = current_model()
            variants = [dict(v) for v in VARIANTS]
            if cur not in [v["id"] for v in variants]:
                variants.insert(0, {"label": cur, "id": cur})
            for v in variants:
                v["downloaded"] = is_downloaded(v["id"])
            self._send_json({"current": cur, "variants": variants})
        elif self.path.startswith("/v1/"):
            self._proxy()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/argus/use":
            length = int(self.headers.get("Content-Length", 0))
            model = json.loads(self.rfile.read(length)).get("model", "")
            if not model:
                self._send_json({"error": "model required"}, 400)
                return
            subprocess.Popen([ARGUS, "use", model],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            self._send_json({"ok": True})
        elif self.path == "/argus/config":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            restart = bool(body.pop("restart", False))
            cfg = write_config(body)
            if restart:
                subprocess.Popen([ARGUS, "restart"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            self._send_json({"ok": True, "config": cfg})
        elif self.path == "/argus/reveal":
            subprocess.Popen(["open", cache_info()["path"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send_json({"ok": True})
        elif self.path == "/argus/openlog":
            subprocess.Popen(["open", os.path.expanduser("~/Library/Logs/argus.log")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send_json({"ok": True})
        elif self.path.startswith("/v1/"):
            length = int(self.headers.get("Content-Length", 0))
            self._proxy(self.rfile.read(length))
        else:
            self.send_error(404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as srv:
        print(f"Argus UI on http://127.0.0.1:{PORT} (API: {API})")
        sys.stdout.flush()
        srv.serve_forever()
