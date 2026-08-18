#!/usr/bin/env python3
"""Mira 的本地图片生成服务，驱动随应用分发的 mlx-serve。"""
import base64
import json
import os
import pathlib
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "Runpod/FLUX.2-klein-4B-mflux-4bit"
EXPECTED_BYTES = 4_619_705_913
MLX_BASE = os.environ.get("MIRA_MLX_API", "http://127.0.0.1:11234")
BUNDLE = pathlib.Path(os.environ.get("MIRA_BUNDLE", pathlib.Path(__file__).parent))
MLX_BINARY = pathlib.Path(os.environ.get(
    "MIRA_MLX_SERVE", BUNDLE / "Contents" / "Helpers" / "mlx-serve" / "mlx-serve"))
MODEL_DIR = pathlib.Path.home() / ".mlx-serve" / "models" / MODEL
READY_MARKER = MODEL_DIR / ".mira-ready"
OUT = pathlib.Path.home() / "Pictures" / "Mira"
PORT = int(os.environ.get("MIRA_IMAGE_PORT", "9880"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"stage": "idle", "error": None, "output": None, "progress": None,
         "job_id": None}
lock = threading.Lock()


def tree_bytes(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def model_ready():
    """A pull creates the directory/config first; only substantial weights count."""
    return READY_MARKER.is_file() and tree_bytes(MODEL_DIR) >= int(EXPECTED_BYTES * .85)


def mlx_ready():
    try:
        with urllib.request.urlopen(MLX_BASE + "/health", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def ensure_mlx_server():
    if mlx_ready():
        return True
    if not MLX_BINARY.is_file():
        return False
    subprocess.Popen([str(MLX_BINARY), "serve", "--host", "127.0.0.1", "--port", "11234"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(80):
        if mlx_ready():
            return True
        time.sleep(.25)
    return False


def post_json(path, payload, timeout=1800):
    req = urllib.request.Request(MLX_BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def prepare_model():
    def worker():
        with lock:
            state.update(stage="preparing", error=None, progress=None)
        try:
            if not MLX_BINARY.is_file():
                raise RuntimeError("mlx-serve 没有包含在 Mira 中")
            downloader = shutil.which("hf") or shutil.which("huggingface-cli")
            if downloader:
                env = dict(os.environ)
                env.setdefault("HF_HUB_DISABLE_XET", "1")
                code = subprocess.call([downloader, "download", MODEL, "--local-dir", str(MODEL_DIR)],
                                       env=env)
            else:
                code = subprocess.call([str(MLX_BINARY), "pull", MODEL])
            if code or tree_bytes(MODEL_DIR) < int(EXPECTED_BYTES * .85):
                raise RuntimeError(f"图片模型下载失败（退出码 {code}）")
            READY_MARKER.touch()
            if ensure_mlx_server():
                try:
                    post_json("/v1/models/rescan", {})
                except OSError:
                    pass
            with lock:
                state.update(stage="ready", error=None)
        except Exception as error:  # noqa: BLE001
            with lock:
                state.update(stage="failed", error=str(error))
    threading.Thread(target=worker, daemon=True).start()


def generate(body):
    def worker():
        started = time.time()
        try:
            if not model_ready():
                raise RuntimeError("图片模型尚未准备")
            if not ensure_mlx_server():
                raise RuntimeError("mlx-serve 启动失败")
            try:
                post_json("/v1/models/rescan", {})
            except OSError:
                pass
            payload = {
                "model": MODEL,
                "prompt": str(body.get("prompt", "")).strip(),
                "size": str(body.get("size", "1024x1024")),
                "steps": max(1, min(50, int(body.get("steps", 4)))),
                "seed": int(body.get("seed", int(time.time()) & 0xFFFFFFFF)),
            }
            if not payload["prompt"]:
                raise RuntimeError("提示词不能为空")
            result = post_json("/v1/images/generations", payload)
            encoded = result.get("data", [{}])[0].get("b64_json")
            if not encoded:
                raise RuntimeError("图片引擎没有返回 PNG")
            png = base64.b64decode(encoded, validate=True)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("图片引擎返回了无效文件")
            name = time.strftime("%Y%m%d-%H%M%S") + ".png"
            path = OUT / name
            path.write_bytes(png)
            with lock:
                state.update(stage="ready", error=None, output=f"/files/{name}",
                             progress={"step": payload["steps"], "total": payload["steps"],
                                       "elapsed": round(time.time() - started)})
        except Exception as error:  # noqa: BLE001
            with lock:
                state.update(stage="failed", error=str(error), progress=None)
        finally:
            if mlx_ready():
                try:
                    post_json("/v1/unload-model", {"model": MODEL}, timeout=120)
                except (OSError, ValueError):
                    pass
    threading.Thread(target=worker, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send_bytes(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(obj).encode())

    def do_GET(self):
        route = urllib.parse.urlparse(self.path).path
        if route == "/api/status":
            with lock:
                snapshot = dict(state)
            ready = model_ready()
            if snapshot["stage"] not in ("preparing", "generating", "failed"):
                snapshot["stage"] = "ready" if ready else "not_prepared"
            snapshot.update(model_ready=ready,
                            running=snapshot["stage"] in ("preparing", "generating"),
                            server_ready=mlx_ready(), model=MODEL, output_dir=str(OUT))
            if snapshot["stage"] == "preparing":
                have = tree_bytes(MODEL_DIR)
                snapshot["download"] = {"bytes": have, "total": EXPECTED_BYTES,
                                        "percent": min(99, int(have * 100 / EXPECTED_BYTES))}
            self.send_json(snapshot)
        elif route == "/api/list":
            files = sorted(OUT.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
            self.send_json([{"name": p.name, "url": f"/files/{p.name}", "size": p.stat().st_size}
                            for p in files[:50]])
        elif route.startswith("/files/"):
            name = os.path.basename(urllib.parse.unquote(route[7:]))
            path = OUT / name
            if not path.is_file():
                return self.send_json({}, 404)
            self.send_bytes(200, path.read_bytes(), "image/png")
        else:
            self.send_json({}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self.send_json({"error": "无效的 Content-Length"}, 400)
        if not 0 <= length <= 1_000_000:
            return self.send_json({"error": "请求体过大"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.send_json({"error": "无效 JSON"}, 400)
        with lock:
            if state["stage"] in ("preparing", "generating"):
                return self.send_json({"error": "已有任务正在运行"}, 409)
        if self.path == "/api/prepare":
            prepare_model()
            return self.send_json({"ok": True}, 202)
        if self.path == "/api/generate":
            if not model_ready():
                return self.send_json({"error": "图片模型尚未准备"}, 409)
            job_id = uuid.uuid4().hex
            with lock:
                state.update(stage="generating", error=None, output=None, progress=None,
                             job_id=job_id)
            generate(body)
            return self.send_json({"ok": True, "job_id": job_id}, 202)
        self.send_json({}, 404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
