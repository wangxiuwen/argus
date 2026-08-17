#!/usr/bin/env python3
"""Argus web chat UI — serves ui.html and proxies /v1/* to the local server.

The proxy keeps everything same-origin so no CORS setup is needed on the
mlx-vlm server. SSE streaming responses are forwarded line by line.
"""
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("ARGUS_API", "http://127.0.0.1:8090")
PORT = int(os.environ.get("ARGUS_UI_PORT", "8091"))
HERE = os.path.dirname(os.path.abspath(__file__))
ARGUS = os.path.expanduser("~/.local/bin/argus")
CONFIG = os.path.expanduser("~/.config/argus/config")

VARIANTS = [
    {"name": "Qwen3.8-27B bf16", "id": "mlx-community/Qwen3.8-27B-bf16", "gb": 54.7},
    {"name": "Qwen3.8-27B 8bit", "id": "mlx-community/Qwen3.8-27B-8bit", "gb": 29.5},
    {"name": "Qwen3.8-27B 4bit", "id": "mlx-community/Qwen3.8-27B-4bit", "gb": 16.1},
]


def repo_dir(repo):
    return os.path.expanduser("~/.cache/huggingface/hub/models--" + repo.replace("/", "--"))


def blob_bytes(repo):
    """Bytes on disk that count toward this repo: finished blobs plus the newest
    partial per blob. Interrupted downloads leave extra .incomplete files with a
    different random suffix for the same blob, which must not be counted twice."""
    blobs = os.path.join(repo_dir(repo), "blobs")
    done, partial = 0, {}
    try:
        entries = os.scandir(blobs)
    except OSError:
        return 0, 0, 0
    stale = 0
    for e in entries:
        try:
            st = e.stat()
        except OSError:
            continue
        if e.name.endswith(".incomplete"):
            sha = e.name.split(".")[0]
            prev = partial.get(sha)
            if prev is None or st.st_mtime > prev[1]:
                if prev:
                    stale += prev[0]
                partial[sha] = (st.st_size, st.st_mtime)
            else:
                stale += st.st_size
        else:
            done += st.st_size
    return done, sum(v[0] for v in partial.values()), stale


def variant_list():
    """Known quantizations of the default model, kept as quick picks."""
    out = []
    for v in VARIANTS:
        done, partial, _ = blob_bytes(v["id"])
        total = v["gb"] * 1e9
        out.append({
            "id": v["id"],
            "label": f'{v["name"]} (~{v["gb"]:.0f} GB)',
            "gb": v["gb"],
            # only "downloaded" when the weights are actually all there
            "downloaded": partial == 0 and done >= 0.95 * total,
        })
    return out


def local_models():
    """Every model already in the Hugging Face cache that mlx-vlm could load.

    Argus is not Qwen-specific: anything mlx-vlm supports (gemma, mistral,
    pixtral, internvl, glm4v, minicpm-v, moondream, llava, smolvlm, …) shows up
    here once it is on disk.
    """
    out = []
    hub = os.path.expanduser("~/.cache/huggingface/hub")
    try:
        entries = sorted(os.scandir(hub), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if not e.name.startswith("models--"):
            continue
        repo = e.name[len("models--"):].replace("--", "/")
        snaps = os.path.join(e.path, "snapshots")
        has_config = False
        try:
            for snap in os.scandir(snaps):
                if os.path.exists(os.path.join(snap.path, "config.json")):
                    has_config = True
                    break
        except OSError:
            continue
        if not has_config:
            continue
        done, partial, _ = blob_bytes(repo)
        out.append({
            "id": repo,
            "label": f"{repo.split('/')[-1]} ({done / 1e9:.1f} GB)",
            "gb": round(done / 1e9, 1),
            "downloaded": partial == 0,
            "local": True,
        })
    return out


def search_hub(query, limit=20):
    """Search Hugging Face for MLX-format models matching the query."""
    q = urllib.parse.quote(query)
    url = (f"https://huggingface.co/api/models?search={q}"
           f"&filter=mlx&limit={limit}&sort=downloads&direction=-1")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.load(r)
    except (OSError, json.JSONDecodeError):
        return []
    have = {m["id"] for m in local_models()}
    out = []
    for m in data:
        rid = m.get("id")
        if not rid:
            continue
        out.append({
            "id": rid,
            "label": rid,
            "downloads": m.get("downloads", 0),
            "downloaded": rid in have,
            "remote": True,
        })
    return out


def is_downloaded(repo):
    for v in variant_list():
        if v["id"] == repo:
            return v["downloaded"]
    return os.path.isdir(repo_dir(repo))


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


def startup_stage(model):
    """What the server is doing while it is not answering yet.

    Progress is measured from the blobs on disk against the repo's known size,
    which is more honest than the huggingface tqdm bar (it only ticks when a
    whole file lands, so it reads 0% for many minutes on multi-GB shards).
    """
    total = next((v["gb"] * 1e9 for v in VARIANTS if v["id"] == model), 0)
    done, partial, stale = blob_bytes(model)
    have = done + partial
    stage = "downloading" if partial else ("loading" if have else "starting")
    out = {
        "stage": stage,
        "downloaded_gb": round(have / 1e9, 1),
        "total_gb": round(total / 1e9, 1) if total else None,
        "percent": int(min(99, have * 100 / total)) if total else None,
        "stale_gb": round(stale / 1e9, 1),
    }
    return out


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
            body = json.dumps({"error": "the model server is not answering yet — "
                                        "wait for the status dot to turn green, or run: argus start"}).encode()
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
            self._send_json({"config": read_config(), "variants": variant_list(),
                             "cache": cache_info()})
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
            out = {"alive": alive, "ready": ready, "model": model}
            if alive and not ready:
                out.update(startup_stage(current_model()))
                out["target"] = current_model()
            self._send_json(out)
        elif self.path == "/argus/models":
            cur = current_model()
            seen, variants = set(), []
            for v in local_models() + variant_list():
                if v["id"] in seen:
                    continue
                seen.add(v["id"])
                variants.append(v)
            if cur not in seen:
                variants.insert(0, {"label": cur, "id": cur, "downloaded": False})
            self._send_json({"current": cur, "variants": variants})
        elif self.path.startswith("/argus/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            self._send_json({"results": search_hub(q) if len(q) >= 2 else []})
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
