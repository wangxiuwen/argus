#!/usr/bin/env python3
"""Argus web chat UI — serves ui.html and proxies /v1/* to the local server.

The proxy keeps everything same-origin so no CORS setup is needed on the
mlx-vlm server. SSE streaming responses are forwarded line by line.
"""
import http.server
import json
import os
import re
import shlex
import socketserver
import subprocess
import sys
import tempfile
import threading
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


def model_catalog():
    """Models shown by the picker, with curated metadata taking precedence.

    A known variant can have a snapshot/config on disk while all weight blobs
    are still incomplete.  In that case local_models() reports a misleading
    0.0 GB entry; list curated variants first so their expected size and label
    survive de-duplication.
    """
    cur = current_model()
    seen, variants = set(), []
    for model in variant_list() + local_models():
        if model["id"] in seen:
            continue
        seen.add(model["id"])
        variants.append(model)
    if cur not in seen:
        variants.insert(0, {"label": cur, "id": cur, "downloaded": False})
    return {"current": cur, "variants": variants}


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
    "BRIDGE_PORT": "8092",
    "MAX_TOKENS": "4096",
    "EXTRA_ARGS": "",
}

PORT_KEYS = ("PORT", "UI_PORT", "BRIDGE_PORT")


def trusted_browser_origin(origin, port, fetch_site=None):
    """Allow local UI mutations and non-browser clients, reject CSRF/rebinding.

    CLI clients do not send Origin or Sec-Fetch-Site.  Browsers do, so a page
    on the public web must not be able to switch models, rewrite config, or
    invoke local desktop actions through Argus's loopback HTTP server.
    """
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


def clean_config_value(key, value):
    if not isinstance(value, (str, int)):
        raise ValueError(f"{key} must be text or a number")
    value = str(value).strip()
    if any(c in value for c in ("\n", "\r", "\0")):
        raise ValueError(f"{key} contains an invalid control character")
    if key == "MODEL":
        if not value:
            raise ValueError("MODEL cannot be empty")
    elif key == "HOST":
        if value not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("HOST must be 127.0.0.1 or 0.0.0.0")
    elif key in PORT_KEYS:
        try:
            number = int(value)
        except ValueError as e:
            raise ValueError(f"{key} must be a port number") from e
        if not 1024 <= number <= 65535:
            raise ValueError(f"{key} must be between 1024 and 65535")
        value = str(number)
    elif key == "MAX_TOKENS":
        try:
            number = int(value)
        except ValueError as e:
            raise ValueError("MAX_TOKENS must be a number") from e
        if not 128 <= number <= 131072:
            raise ValueError("MAX_TOKENS must be between 128 and 131072")
        value = str(number)
    return value


def strip_legacy_max_tokens(extra):
    """Move the old EXTRA_ARGS --max-tokens setting to MAX_TOKENS."""
    return re.sub(r"(?:^|\s)--max-tokens(?:=|\s+)\d+(?=\s|$)", " ", extra).strip()


def read_config():
    cfg = dict(DEFAULTS)
    seen = set()
    try:
        with open(CONFIG) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() in cfg:
                        key = k.strip()
                        try:
                            cfg[key] = clean_config_value(key, v)
                            seen.add(key)
                        except ValueError:
                            pass
    except OSError:
        pass
    if "MAX_TOKENS" not in seen:
        match = re.search(r"(?:^|\s)--max-tokens(?:=|\s+)(\d+)(?=\s|$)",
                          cfg["EXTRA_ARGS"])
        if match:
            try:
                cfg["MAX_TOKENS"] = clean_config_value("MAX_TOKENS", match.group(1))
            except ValueError:
                pass
    return cfg


def write_config(updates):
    if not isinstance(updates, dict):
        raise ValueError("config update must be an object")
    unknown = set(updates) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config key: {sorted(unknown)[0]}")
    cfg = read_config()
    for k, v in updates.items():
        cfg[k] = clean_config_value(k, v)
    if len({cfg[k] for k in PORT_KEYS}) != len(PORT_KEYS):
        raise ValueError("API, UI, and bridge ports must be different")
    cfg["EXTRA_ARGS"] = strip_legacy_max_tokens(cfg["EXTRA_ARGS"])
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".config.", dir=os.path.dirname(CONFIG), text=True)
    try:
        with os.fdopen(fd, "w") as f:
            for k in DEFAULTS:
                f.write(f"{k}={cfg[k]}\n")
        os.replace(temp_path, CONFIG)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
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


def server_process_alive(pidfile=None, expected_port=None):
    """A pidfile is only valid while it still names the mlx-vlm process.

    PIDs are reused by the OS. Checking only kill(pid, 0) can therefore leave
    the UI permanently claiming that a stopped server is loading when the PID
    now belongs to an unrelated process.
    """
    pidfile = pidfile or os.path.expanduser("~/.local/state/argus/server.pid")
    try:
        with open(pidfile) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True,
            text=True, timeout=2, check=False)
        if result.returncode != 0 or "mlx_vlm.server" not in result.stdout:
            return False
        expected_port = str(expected_port or read_config()["PORT"])
        try:
            args = shlex.split(result.stdout)
        except ValueError:
            return False
        return any(
            arg == f"--port={expected_port}"
            or (arg == "--port" and i + 1 < len(args) and args[i + 1] == expected_port)
            for i, arg in enumerate(args)
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def launch_argus_later(*args, delay=0.4):
    """Let the HTTP response flush before a command restarts this UI process."""
    def launch():
        subprocess.Popen([ARGUS, *args], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    timer = threading.Timer(delay, launch)
    timer.daemon = True
    timer.start()


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

    def _read_json(self, max_bytes=1_000_000):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError as e:
            raise ValueError("invalid Content-Length") from e
        if not 0 <= length <= max_bytes:
            raise ValueError("request body is too large")
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError("invalid JSON") from e

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
            alive = server_process_alive()
            ready, model = False, None
            try:
                with urllib.request.urlopen(API + "/health", timeout=2) as r:
                    model = json.load(r).get("loaded_model")
                    ready = bool(model)
            except (OSError, TypeError, json.JSONDecodeError):
                pass
            runtime_cfg = read_config()
            out = {"alive": alive, "ready": ready, "model": model,
                   "max_tokens": int(runtime_cfg["MAX_TOKENS"]),
                   "api_host": runtime_cfg["HOST"], "api_port": runtime_cfg["PORT"]}
            if alive and not ready:
                out.update(startup_stage(current_model()))
                out["target"] = current_model()
            self._send_json(out)
        elif self.path == "/argus/models":
            self._send_json(model_catalog())
        elif self.path.startswith("/argus/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            self._send_json({"results": search_hub(q) if len(q) >= 2 else []})
        elif self.path.startswith("/v1/"):
            self._proxy()
        else:
            self.send_error(404)

    def do_POST(self):
        if not trusted_browser_origin(self.headers.get("Origin"), PORT,
                                      self.headers.get("Sec-Fetch-Site")):
            self._send_json({"error": "cross-site requests are not allowed"}, 403)
            return
        if self.path == "/argus/use":
            try:
                body = self._read_json()
                if not isinstance(body, dict):
                    raise ValueError("request body must be an object")
                model = clean_config_value("MODEL", body.get("model", ""))
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return
            subprocess.Popen([ARGUS, "use", model],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            self._send_json({"ok": True})
        elif self.path == "/argus/config":
            try:
                body = self._read_json()
                if not isinstance(body, dict):
                    raise ValueError("request body must be an object")
                restart = bool(body.pop("restart", False))
                cfg = write_config(body)
            except ValueError as e:
                self._send_json({"error": str(e)}, 400)
                return
            if restart:
                launch_argus_later("restart")
            self._send_json({"ok": True, "config": cfg, "restarting": restart,
                             "ui_url": f'http://127.0.0.1:{cfg["UI_PORT"]}'})
        elif self.path == "/argus/reveal":
            subprocess.Popen(["open", cache_info()["path"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send_json({"ok": True})
        elif self.path == "/argus/openlog":
            subprocess.Popen(["open", os.path.expanduser("~/Library/Logs/argus.log")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._send_json({"ok": True})
        elif self.path.startswith("/v1/"):
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._send_json({"error": "invalid Content-Length"}, 400)
                return
            if not 0 <= length <= 50_000_000:
                self._send_json({"error": "request body is too large"}, 413)
                return
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
