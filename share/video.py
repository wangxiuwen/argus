#!/usr/bin/env python3
import http.server, json, os, pathlib, shutil, subprocess, threading, time, urllib.parse, uuid

HERE = pathlib.Path(__file__).parent
BUNDLE = pathlib.Path(os.environ.get("MIRA_BUNDLE", HERE))
PORT = int(os.environ.get("MIRA_VIDEO_PORT", "9877"))
DEFAULT_WORK = pathlib.Path.home() / "Library" / "Application Support" / "Mira" / "video"
WORK = pathlib.Path(os.environ.get("MIRA_VIDEO_WORK", DEFAULT_WORK))
HF_HOME = pathlib.Path(os.environ.get("HF_HOME", pathlib.Path.home() / ".cache" / "huggingface"))
HF_HUB = pathlib.Path(os.environ.get("HF_HUB_CACHE", HF_HOME / "hub"))
MODEL_LINKS = WORK / "models"
DERIVED_MODELS = HF_HUB / "mira-vpipe" / "local"
MODELS = DERIVED_MODELS / "MiniMax-H3-FL2VA-4bit"
OUTPUTS = WORK / "outputs"
LOG = WORK / "video.log"
ENGINE = pathlib.Path(os.environ.get(
    "MIRA_VPIPE", BUNDLE / "Contents" / "Helpers" / "vpipe"))
PIPELINES = pathlib.Path(os.environ.get(
    "MIRA_VIDEO_PIPELINES", BUNDLE / "Contents" / "Resources" / "video-pipelines"))
SOURCE_REPO = "Comfy-Org/MiniMax-H3"
LORA_REPO = "larryvrh/MiniMax-H3-Turbo-Lora"
SOURCE_MODEL_FILES = {
    "diffusion_models/minimax_h3_fl2va_bf16.safetensors": 66280487368,
    "text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors": 51506295256,
    "vae/minimax_h3_audio_vae_fp32.safetensors": 605254808,
    "vae/minimax_h3_video_vae_fp16.safetensors": 5207808496,
}
LORA_FILES = ["minimax_h3_turbo_v4_step600_ema.safetensors"]
state = {"process": None, "stage": "idle", "error": None, "output": None,
         "cancel_requested": False, "job_id": None}
lock = threading.Lock()

def model_ready():
    return MODELS.is_dir() and sum(p.stat().st_size for p in MODELS.rglob("*") if p.is_file()) > 30e9

def repo_cache(repo):
    return HF_HUB / ("models--" + repo.replace("/", "--"))

def ensure_model_layout():
    """Keep task data in Application Support and model bytes in the HF cache."""
    MODEL_LINKS.mkdir(parents=True, exist_ok=True)
    DERIVED_MODELS.mkdir(parents=True, exist_ok=True)
    local = MODEL_LINKS / "local"
    if not local.exists() and not local.is_symlink():
        local.symlink_to(DERIVED_MODELS, target_is_directory=True)

def link_snapshot(repo, snapshot):
    snapshot = pathlib.Path(snapshot).resolve()
    expected = repo_cache(repo).resolve()
    if expected not in snapshot.parents:
        raise RuntimeError(f"hf returned a snapshot outside its cache: {snapshot}")
    destination = MODEL_LINKS.joinpath(*repo.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        raise RuntimeError(f"model link path is occupied: {destination}")
    destination.symlink_to(snapshot, target_is_directory=True)

def hf_command(repo, files, token=""):
    executable = os.environ.get("MIRA_HF") or shutil.which("hf")
    if not executable:
        raise RuntimeError("Hugging Face CLI is required: pip install huggingface_hub")
    command = [executable, "download", repo, *files, "--cache-dir", str(HF_HUB),
               "--max-workers", "4", "--quiet"]
    if token:
        command.extend(["--token", token])
    return command

def download_env(files):
    """Xet-backed files beyond the mirror's regular-download limit must come
    straight from huggingface.co with the hf_xet backend enabled — the mirror's
    plain-HTTPS path makes huggingface_hub refuse files this large."""
    if sum(SOURCE_MODEL_FILES.get(f, 0) for f in files) < 5e9:
        return None
    env = dict(os.environ)
    env.pop("HF_ENDPOINT", None)
    env.pop("HF_HUB_DISABLE_XET", None)
    return env

def download_repo(repo, files, token, log):
    command = hf_command(repo, files, token)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log, text=True,
                            env=download_env(files))
    with lock: state["process"] = proc
    output, _ = proc.communicate()
    with lock: cancelled = state["cancel_requested"]
    if cancelled:
        raise InterruptedError("cancelled")
    if proc.returncode:
        raise RuntimeError(f"hf download failed with exit code {proc.returncode}")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("hf download did not return a snapshot path")
    link_snapshot(repo, lines[-1])

def external_download_active():
    candidates = []
    for root in (repo_cache(SOURCE_REPO), repo_cache(LORA_REPO)):
        if root.exists():
            candidates.extend(root.rglob("*.incomplete"))
    cutoff = time.time() - 180
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime >= cutoff:
                return True
        except OSError:
            pass
    return False

def download_progress():
    total = sum(SOURCE_MODEL_FILES.values())
    blobs = repo_cache(SOURCE_REPO) / "blobs"
    files = [path for path in blobs.iterdir() if path.is_file()] if blobs.exists() else []
    downloaded = min(total, sum(path.stat().st_size for path in files))
    complete = 0 if any(path.name.endswith(".incomplete") for path in files) else min(
        len(SOURCE_MODEL_FILES), len(files))
    return {"downloaded_bytes": downloaded, "total_bytes": total,
            "percent": round(downloaded * 100 / total, 1),
            "files_complete": complete, "files_total": len(SOURCE_MODEL_FILES)}

def status():
    with lock:
        proc = state["process"]
        running = bool(proc and proc.poll() is None)
        external = external_download_active()
        idle_stage = "external_downloading" if external else ("ready" if model_ready() else "not_prepared")
        return {"stage": state["stage"] if running else idle_stage,
                "running": running, "model_ready": model_ready(), "error": state["error"],
                "external_download": external,
                "download": download_progress() if external or state["stage"] == "preparing" else None,
                "output": state["output"], "work_dir": str(WORK),
                "model_cache": str(HF_HUB)}

def output_items():
    if not OUTPUTS.exists():
        return []
    items = []
    for path in OUTPUTS.iterdir():
        if path.is_file() and path.suffix.lower() == ".mp4":
            stat = path.stat()
            items.append({"name": path.name, "size": stat.st_size,
                          "modified": int(stat.st_mtime),
                          "url": "/outputs/" + urllib.parse.quote(path.name)})
    return sorted(items, key=lambda item: item["modified"], reverse=True)

def pipeline(name):
    return json.loads((PIPELINES / name).read_text())

def write_job(spec, name):
    jobs = WORK / "jobs"; jobs.mkdir(parents=True, exist_ok=True)
    path = jobs / name; path.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    path.chmod(0o600)
    return path

def set_stage(spec, stage_id, **values):
    stage = next(s for s in spec["stages"] if s["id"] == stage_id)
    stage.setdefault("config", {}).update(values)

def generation_spec(prompt, width, height, frames, steps, seed, output):
    spec = pipeline("minimax-h3-text-to-video-turbo.vpipeline")
    set_stage(spec, "text-prompt", text=prompt)
    set_stage(spec, "generate-video", width=width, height=height, frames=frames,
              steps=steps, seed=seed, i8_gemm=False)
    set_stage(spec, "save-video", output_url=str(output))
    return spec

def run_commands(stage, commands, output=None, attempts=1, job_id=None, downloads=()):
    def worker():
        WORK.mkdir(parents=True, exist_ok=True); OUTPUTS.mkdir(parents=True, exist_ok=True)
        with lock: state.update(stage=stage, error=None, output=str(output) if output else None,
                                cancel_requested=False, job_id=job_id)
        try:
            with LOG.open("ab", buffering=0) as log:
                ensure_model_layout()
                for repo, files, token in downloads:
                    download_repo(repo, files, token, log)
                for attempt in range(1, attempts + 1):
                    code = 0
                    for command in commands:
                        proc = subprocess.Popen(command, cwd=WORK, stdout=log, stderr=subprocess.STDOUT)
                        with lock: state["process"] = proc
                        code = proc.wait()
                        with lock: cancelled = state["cancel_requested"]
                        if cancelled: raise InterruptedError("cancelled")
                        if code: break
                    complete = model_ready() if stage == "preparing" else bool(output and output.is_file())
                    if code == 0 and complete: break
                    if attempt == attempts:
                        detail = f"exit code {code}" if code else "expected output was not created"
                        raise RuntimeError(f"VPIPE failed after {attempts} attempts: {detail}")
                    log.write(f"\n[Fermi Video] attempt {attempt}/{attempts} incomplete; retrying in 3 seconds\n".encode())
                    time.sleep(3)
            with lock: state.update(stage="ready", process=None)
        except InterruptedError:
            with lock: state.update(stage="idle", error=None, process=None)
        except Exception as e:
            with lock: state.update(stage="failed", error=str(e), process=None)
        finally:
            if stage == "preparing":
                try: (WORK / "jobs" / "prepare-h3.vpipeline").unlink()
                except OSError: pass
    threading.Thread(target=worker, daemon=True).start()

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
    def send_output(self, name):
        name = urllib.parse.unquote(name)
        if not name or pathlib.Path(name).name != name:
            return self.send({"error":"not found"}, 404)
        path = OUTPUTS / name
        if path.suffix.lower() != ".mp4" or not path.is_file():
            return self.send({"error":"not found"}, 404)
        size = path.stat().st_size
        start, end, code = 0, max(0, size - 1), 200
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                first, last = range_header[6:].split("-", 1)
                start = int(first) if first else 0
                end = int(last) if last else end
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1); code = 206
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk: break
                self.wfile.write(chunk); remaining -= len(chunk)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/": self.send(b'{"service":"Fermi video"}')
        elif parsed.path == "/api/status": self.send(status())
        elif parsed.path == "/api/outputs": self.send(output_items())
        elif parsed.path == "/api/log":
            data = LOG.read_bytes()[-60000:] if LOG.exists() else b""
            self.send(data, ctype="text/plain; charset=utf-8")
        elif parsed.path.startswith("/outputs/"):
            self.send_output(parsed.path[len("/outputs/"):])
        else: self.send({"error":"not found"}, 404)
    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send({"error":"cross-site requests are not allowed"}, 403)
        if self.path == "/api/prepare":
            if status()["running"]: return self.send({"error":"busy"}, 409)
            if external_download_active():
                return self.send({"error":"external model download is still active"}, 409)
            data = self.body()
            if data.get("accept_license") is not True:
                return self.send({"error":"MiniMax H3 license acceptance is required"}, 400)
            token = data.get("token", "").strip()
            base = pipeline("prepare-minimax-h3-4bit.vpipeline")
            if token: set_stage(base, "fetch", hf_token=token)
            base_path = write_job(base, "prepare-h3.vpipeline")
            lora = PIPELINES / "prepare-minimax-h3-turbo-lora.vpipeline"
            run_commands("preparing", [[str(ENGINE), "--launch", str(base_path)],
                                        [str(ENGINE), "--launch", str(lora)]], attempts=20,
                         downloads=((SOURCE_REPO, tuple(SOURCE_MODEL_FILES), token),
                                    (LORA_REPO, tuple(LORA_FILES), token)))
            self.send({"ok":True}, 202)
        elif self.path == "/api/generate":
            if status()["running"]: return self.send({"error":"busy"}, 409)
            if not model_ready(): return self.send({"error":"model not prepared"}, 409)
            d=self.body(); prompt=str(d.get("prompt","")).strip()
            if not prompt: return self.send({"error":"prompt required"}, 400)
            OUTPUTS.mkdir(parents=True, exist_ok=True)
            out=OUTPUTS/(time.strftime("%Y%m%d-%H%M%S")+".mp4")
            spec=generation_spec(prompt,int(d.get("width",960)),int(d.get("height",544)),
                 int(d.get("frames",120)),int(d.get("steps",6)),int(d.get("seed",6)),out)
            job=write_job(spec,"latest-generate.vpipeline")
            job_id=uuid.uuid4().hex
            run_commands("generating", [[str(ENGINE),"--launch",str(job)]], out, job_id=job_id)
            self.send({"ok":True,"output":str(out),"job_id":job_id},202)
        elif self.path == "/api/cancel":
            with lock:
                state["cancel_requested"] = True
                if state["process"] and state["process"].poll() is None: state["process"].terminate()
            self.send({"ok":True})
        else: self.send({"error":"not found"},404)

if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    ensure_model_layout()
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
