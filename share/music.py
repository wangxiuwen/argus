#!/usr/bin/env python3
"""Fermi 内部的 MiniMax Music 3 本地任务服务。"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "ddalcu/MiniMax-Music3-MLX-Serve-8bit"
EXPECTED_BYTES = 13_552_891_864
MLX_BASE = os.environ.get("MIRA_MLX_API", "http://127.0.0.1:11234")
MLX = MLX_BASE + "/v1/audio/music-generations"
BUNDLE = pathlib.Path(os.environ.get("MIRA_BUNDLE", pathlib.Path(__file__).parent))
MLX_BINARY = pathlib.Path(os.environ.get(
    "MIRA_MLX_SERVE", BUNDLE / "Contents" / "Helpers" / "mlx-serve" / "mlx-serve"))
MODEL_DIR = pathlib.Path.home() / ".mlx-serve" / "models" / MODEL
LEGACY_OUT = pathlib.Path.home() / "Music" / "minimax3"
OUT = str(LEGACY_OUT if LEGACY_OUT.exists() else pathlib.Path.home() / "Music" / "Fermi")
PORT = int(os.environ.get("MIRA_MUSIC_PORT", "9879"))
os.makedirs(OUT, exist_ok=True)
state = {"stage": "idle", "error": None, "output": None, "progress": None,
         "job_id": None, "started_at": None}
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
    return MODEL_DIR.is_dir() and tree_bytes(MODEL_DIR) >= int(EXPECTED_BYTES * .98)


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
    for _ in range(40):
        if mlx_ready():
            return True
        time.sleep(.25)
    return False


def prepare_model():
    def worker():
        with lock: state.update(stage="preparing", error=None)
        try:
            if not MLX_BINARY.is_file():
                raise RuntimeError("mlx-serve is not bundled")
            code = subprocess.call([str(MLX_BINARY), "pull", MODEL])
            if code or not model_ready():
                raise RuntimeError(f"model download failed (exit {code})")
            with lock: state.update(stage="ready", error=None)
        except Exception as error:
            with lock: state.update(stage="failed", error=str(error))
    threading.Thread(target=worker, daemon=True).start()


def generate_music(body):
    def worker():
        started = time.time()
        try:
            if not model_ready():
                raise RuntimeError("音乐模型尚未准备")
            if not ensure_mlx_server():
                raise RuntimeError("mlx-serve did not start")
            payload = json.dumps({
                "model": MODEL,
                "prompt": body.get("prompt", ""),
                "lyrics": body.get("lyrics", ""),
                "duration_seconds": max(1, min(360, int(body.get("duration_seconds", 60)))),
            }).encode()
            if not str(body.get("lyrics", "")).strip():
                raise RuntimeError("MiniMax Music 3 需要歌词")
            req = urllib.request.Request(MLX, data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1800) as resp:
                wav = resp.read()
            if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
                raise RuntimeError("音乐引擎返回了无效 WAV")
            name = time.strftime("%Y%m%d-%H%M%S") + ".wav"
            with open(os.path.join(OUT, name), "wb") as f:
                f.write(wav)
            with lock:
                state.update(stage="ready", error=None, output=f"/files/{name}",
                             progress={"elapsed": round(time.time() - started)})
        except Exception as error:  # noqa: BLE001
            with lock:
                state.update(stage="failed", error=str(error), progress=None)
        finally:
            if mlx_ready():
                try:
                    req = urllib.request.Request(
                        MLX_BASE + "/v1/unload-model",
                        data=json.dumps({"model": MODEL}).encode(),
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=120).read()
                except OSError:
                    pass
    threading.Thread(target=worker, daemon=True).start()

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MiniMax Music 3 · 本地(MLX)</title>
<style>
:root{color-scheme:light dark;font-family:-apple-system,sans-serif}
body{max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.5}
label{display:block;margin:.8rem 0 .25rem;font-size:.9rem;color:gray}
textarea,input{width:100%;box-sizing:border-box;padding:.5rem;border:1px solid #8884;border-radius:8px;font:inherit;background:transparent}
textarea{min-height:7rem}
button{margin-top:1rem;padding:.6rem 1.4rem;border:0;border-radius:8px;background:#3b82f6;color:#fff;font-size:1rem;cursor:pointer}
button:disabled{opacity:.5}
.row{display:flex;gap:1rem}.row>div{flex:1}
#status{margin-top:.8rem;font-size:.9rem;color:gray;white-space:pre-wrap}
.item{display:flex;align-items:center;gap:.8rem;padding:.5rem 0;border-top:1px solid #8883}
.item span{font-size:.85rem;color:gray;min-width:11rem}
audio{width:100%}
</style></head><body>
<h2>MiniMax Music 3 <small style="color:gray;font-size:.6em">本地 · Apple Silicon · 8bit</small></h2>
<label>音乐描述</label>
<textarea id="prompt" style="min-height:3.5rem">Genre: acoustic pop. BPM: 96. Warm and intimate. Vocals: soft female lead. Arrangement: fingerpicked guitar and soft piano.</textarea>
<label>歌词（[verse] / [chorus] 单独成行，必填）</label>
<textarea id="lyrics">[verse]
晨光洒在松树间
每条安静的街道属于你我
[chorus]
世界轻轻开始呼吸</textarea>
<div class="row"><div><label>时长（秒，1–360）</label><input id="dur" type="number" value="60"></div></div>
<button id="go">生成</button>
<div id="status"></div>
<h3 style="margin-top:2rem">历史</h3><div id="list"></div>
<script>
const $=id=>document.getElementById(id);
async function refresh(){
  const r=await fetch('/api/list');const files=await r.json();
  $('list').innerHTML=files.map(f=>`<div class="item"><span>${f}</span><audio controls preload="none" src="/files/${encodeURIComponent(f)}"></audio></div>`).join('')||'<p style="color:gray">还没有生成过</p>';
}
$('go').onclick=async()=>{
  $('go').disabled=true;const t0=Date.now();
  $('status').textContent='生成中…（一分钟的歌大约要跑一两分钟，别关页面）';
  const tick=setInterval(()=>{$('status').textContent=`生成中… ${Math.round((Date.now()-t0)/1000)}s`},1000);
  try{
    const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt:$('prompt').value,lyrics:$('lyrics').value,duration_seconds:Number($('dur').value)||60})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||r.status);
    $('status').textContent=`完成，用时 ${Math.round((Date.now()-t0)/1000)}s → ${d.file}`;
    await refresh();
  }catch(e){$('status').textContent='失败：'+e.message}
  finally{clearInterval(tick);$('go').disabled=false}
};
refresh();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, b'{"service":"Fermi music"}')
        elif self.path == "/api/status":
            with lock:
                stage, error = state["stage"], state["error"]
            ready = model_ready()
            if stage not in ("preparing", "generating", "failed"):
                stage = "ready" if ready else "not_prepared"
            result = {
                "stage": stage, "running": stage in ("preparing", "generating"),
                "model_ready": ready, "server_ready": mlx_ready(), "error": error,
                "model": MODEL, "output_dir": OUT, "output": state.get("output"),
                "progress": state.get("progress"),
            }
            if stage == "generating" and state.get("started_at"):
                result["progress"] = {"elapsed": round(time.time() - state["started_at"])}
            if stage == "preparing":
                have = tree_bytes(MODEL_DIR)
                result["download"] = {"bytes": have, "total": EXPECTED_BYTES,
                                      "percent": min(99, int(have * 100 / EXPECTED_BYTES))}
            self._send(200, json.dumps(result).encode())
        elif self.path == "/api/list":
            files = sorted((f for f in os.listdir(OUT) if f.endswith(".wav")), reverse=True)
            self._send(200, json.dumps(files[:50]).encode())
        elif self.path.startswith("/files/"):
            name = os.path.basename(urllib.request.unquote(self.path[7:]))
            p = os.path.join(OUT, name)
            if not os.path.isfile(p):
                self._send(404, b"{}")
                return
            with open(p, "rb") as f:
                data = f.read()
            self._send(200, data, "audio/wav")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if self.path == "/api/prepare":
            with lock:
                if state["stage"] in ("preparing", "generating"):
                    return self._send(409, b'{"error":"busy"}')
            prepare_model()
            return self._send(202, b'{"ok":true}')
        if self.path != "/api/generate":
            self._send(404, b"{}")
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._send(400, b'{"error":"invalid Content-Length"}')
        if not 0 <= n <= 1_000_000:
            return self._send(413, b'{"error":"request body is too large"}')
        try:
            body = json.loads(self.rfile.read(n))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._send(400, b'{"error":"invalid JSON"}')
        if not model_ready():
            return self._send(409, b'{"error":"model not prepared"}')
        job_id = uuid.uuid4().hex
        with lock:
            if state["stage"] in ("preparing", "generating"):
                return self._send(409, b'{"error":"busy"}')
            state.update(stage="generating", error=None, output=None, progress=None,
                         job_id=job_id, started_at=time.time())
        generate_music(body)
        self._send(202, json.dumps({"ok": True, "job_id": job_id}).encode())


if __name__ == "__main__":
    print(f"Fermi music service on http://127.0.0.1:{PORT}  (mlx-serve on :11234)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
