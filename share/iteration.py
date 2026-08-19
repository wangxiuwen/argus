#!/usr/bin/env python3
"""Fermi's durable self-iteration runtime.

The public interface keeps quality, preferences, code candidates, and LoRA
training behind one approval-aware module. Callers provide local-model and
process adapters; this module owns policy and durable state.
"""
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager


ROOT = pathlib.Path(os.environ.get(
    "MIRA_ITERATION_ROOT",
    pathlib.Path.home() / "Library" / "Application Support" / "Mira" / "iterations"))
DB = pathlib.Path(os.environ.get(
    "MIRA_JOBS_DB",
    pathlib.Path.home() / "Library" / "Application Support" / "Mira" / "jobs.sqlite3"))
PUBLIC_REPO = "https://github.com/wangxiuwen/mira.git"
PUBLIC_PUSH_REPO = os.environ.get(
    "MIRA_PUBLIC_PUSH_REPO", "ssh://git@ssh.github.com:443/wangxiuwen/mira.git")
PUBLIC_GH_REPO = os.environ.get("MIRA_PUBLIC_GH_REPO", "wangxiuwen/mira")
FERMI = pathlib.Path.home() / ".local" / "bin" / "fermi"
lock = threading.RLock()


@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    ROOT.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS preferences (
          id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, value TEXT NOT NULL,
          confidence REAL NOT NULL, source TEXT NOT NULL,
          created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback (
          id TEXT PRIMARY KEY, batch_id TEXT, position INTEGER, rating INTEGER NOT NULL,
          note TEXT, learn INTEGER NOT NULL, example TEXT,
          created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS quality_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, position INTEGER,
          attempt INTEGER NOT NULL, score REAL, issues TEXT, prompt TEXT,
          created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS candidates (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
          goal TEXT NOT NULL, path TEXT, summary TEXT, log TEXT, error TEXT,
          command TEXT, created REAL NOT NULL, updated REAL NOT NULL
        );
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(candidates)")}
        for name in ("branch", "published_commit", "pr_url"):
            if name not in columns:
                db.execute(f"ALTER TABLE candidates ADD COLUMN {name} TEXT")


def remember(name, value, source="user", confidence=1.0):
    name, value = str(name).strip(), str(value).strip()
    if not name or not value:
        raise ValueError("preference name and value are required")
    if len(name) > 120 or len(value) > 4000:
        raise ValueError("preference is too long")
    confidence = max(0.0, min(1.0, float(confidence)))
    now = time.time()
    with lock, database() as db:
        existing = db.execute("SELECT id,created FROM preferences WHERE name=?", (name,)).fetchone()
        pref_id = existing["id"] if existing else uuid.uuid4().hex[:12]
        created = existing["created"] if existing else now
        db.execute("""INSERT OR REPLACE INTO preferences
          (id,name,value,confidence,source,created,updated) VALUES(?,?,?,?,?,?,?)""",
                   (pref_id, name, value, confidence, source, created, now))
    return preference(pref_id)


def preference(pref_id):
    with database() as db:
        row = db.execute("SELECT * FROM preferences WHERE id=?", (pref_id,)).fetchone()
    return dict(row) if row else None


def preferences(limit=30):
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM preferences ORDER BY confidence DESC,updated DESC LIMIT ?", (limit,))]


def memory_context():
    rows = preferences(20)
    if not rows:
        return "No durable user preferences yet."
    return "Durable user preferences:\n" + "\n".join(
        f"- {row['name']}: {row['value']}" for row in rows)


def creation_example(batch_id, position):
    with database() as db:
        batch = db.execute("SELECT kind,spec FROM batches WHERE id=?", (batch_id,)).fetchone()
        item = db.execute(
            "SELECT prompt,lyrics,output FROM items WHERE batch_id=? AND position=?",
            (batch_id, position)).fetchone()
    if not batch or not item:
        raise ValueError("creation not found")
    spec = json.loads(batch["spec"])
    answer = {"kind": batch["kind"], "prompt": item["prompt"],
              "lyrics": item["lyrics"], "output": item["output"]}
    return {"messages": [
        {"role": "system", "content": "Plan high-quality local media generation."},
        {"role": "user", "content": str(spec.get("prompt", ""))},
        {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
    ]}


def record_feedback(body):
    rating = int(body.get("rating", 0))
    if rating not in (1, 2, 3, 4, 5):
        raise ValueError("rating must be between 1 and 5")
    batch_id = str(body.get("batch_id", "")).strip()
    position = int(body.get("position", 1))
    note = str(body.get("note", "")).strip()
    learn = bool(body.get("learn", True))
    example = creation_example(batch_id, position) if batch_id else None
    feedback_id, now = uuid.uuid4().hex[:12], time.time()
    with lock, database() as db:
        db.execute("""INSERT INTO feedback
          (id,batch_id,position,rating,note,learn,example,created) VALUES(?,?,?,?,?,?,?,?)""",
                   (feedback_id, batch_id or None, position, rating, note, int(learn),
                    json.dumps(example, ensure_ascii=False) if example else None, now))
    if learn and note:
        remember(f"feedback-{feedback_id}", note, "explicit-feedback",
                 1.0 if rating >= 4 else .8)
    return {"id": feedback_id, "rating": rating, "learn": learn}


def feedback_summary():
    with database() as db:
        row = db.execute("""SELECT count(*) total,
          coalesce(sum(CASE WHEN learn=1 THEN 1 ELSE 0 END),0) learnable,
          round(avg(rating),2) average FROM feedback""").fetchone()
    return dict(row)


def refine_creation(kind, prompt, lyrics, model_json, batch_id=None, position=1,
                    max_attempts=2):
    """Run a bounded preflight Quality Gate; model failure never blocks Creation."""
    current_prompt, current_lyrics = str(prompt), str(lyrics or "")
    best = {"prompt": current_prompt, "lyrics": current_lyrics, "score": None, "issues": []}
    for attempt in range(1, max(1, min(3, int(max_attempts))) + 1):
        instruction = f"""Act as a strict {kind} creative director. Evaluate this generation plan.
{memory_context()}
Prompt: {current_prompt}
Lyrics: {current_lyrics}
Return JSON with score (0-10), issues (array), improved_prompt, improved_lyrics.
Preserve the user's intent. A score of 8 or higher passes."""
        try:
            result = model_json(instruction)
            score = max(0.0, min(10.0, float(result.get("score", 0))))
            issues = [str(value) for value in result.get("issues", [])][:10]
            improved_prompt = str(result.get("improved_prompt") or current_prompt)
            improved_lyrics = str(result.get("improved_lyrics") or current_lyrics)
            best = {"prompt": improved_prompt, "lyrics": improved_lyrics,
                    "score": score, "issues": issues}
            with database() as db:
                db.execute("""INSERT INTO quality_events
                  (batch_id,position,attempt,score,issues,prompt,created) VALUES(?,?,?,?,?,?,?)""",
                           (batch_id, position, attempt, score,
                            json.dumps(issues, ensure_ascii=False), improved_prompt, time.time()))
            if score >= 8:
                break
            current_prompt, current_lyrics = improved_prompt, improved_lyrics
        except Exception:
            break
    return best


def _candidate(candidate_id):
    with database() as db:
        row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def candidates():
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM candidates ORDER BY created DESC LIMIT 50")]


def _update_candidate(candidate_id, **values):
    values["updated"] = time.time()
    keys = list(values)
    with lock, database() as db:
        db.execute(f"UPDATE candidates SET {','.join(key+'=?' for key in keys)} WHERE id=?",
                   tuple(values[key] for key in keys) + (candidate_id,))


def propose_code_candidate(goal):
    goal = str(goal).strip()
    if not goal:
        raise ValueError("goal is required")
    candidate_id, now = uuid.uuid4().hex[:12], time.time()
    path = ROOT / "code" / candidate_id
    with database() as db:
        db.execute("""INSERT INTO candidates
          (id,kind,status,goal,path,created,updated) VALUES(?,?,?,?,?,?,?)""",
                   (candidate_id, "code", "queued", goal, str(path), now, now))
    threading.Thread(target=_build_code_candidate,
                     args=(candidate_id, goal, path), daemon=True).start()
    return _candidate(candidate_id)


def _source_checkout():
    source = ROOT / "source"
    if not (source / ".git").exists():
        subprocess.run(["git", "clone", "--depth", "1", PUBLIC_REPO, str(source)],
                       check=True, capture_output=True, text=True, timeout=600)
    else:
        subprocess.run(["git", "-C", str(source), "fetch", "origin", "main"],
                       check=True, capture_output=True, text=True, timeout=180)
        subprocess.run(["git", "-C", str(source), "reset", "--hard", "origin/main"],
                       check=True, capture_output=True, text=True, timeout=60)
    return source


def _build_code_candidate(candidate_id, goal, path):
    log_path = path.parent / f"{candidate_id}.log"
    try:
        _update_candidate(candidate_id, status="preparing")
        source = _source_checkout()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(source), "worktree", "add", "--detach", str(path), "HEAD"],
                       check=True, capture_output=True, text=True, timeout=120)
        prompt = ("Improve Fermi for this approved goal: " + goal +
                  "\nWork only in this isolated candidate. Preserve privacy, add regression tests, "
                  "run make test, and do not publish, install, or modify any other checkout.")
        command = [str(FERMI), "launch", "codex", "exec", "--sandbox", "workspace-write",
                   "--approve-for-me", "--ephemeral", "-C", str(path), prompt]
        _update_candidate(candidate_id, status="running", command=json.dumps(command))
        with open(log_path, "w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                       text=True, timeout=7200)
        tests = subprocess.run(["make", "test"], cwd=path, capture_output=True,
                               text=True, timeout=1800)
        changed = subprocess.run(["git", "status", "--short"], cwd=path,
                                 capture_output=True, text=True, timeout=30).stdout
        summary = changed.strip() or "Candidate produced no source changes."
        status = "ready" if completed.returncode == 0 and tests.returncode == 0 and changed.strip() else "failed"
        _update_candidate(candidate_id, status=status, summary=summary,
                          log=str(log_path), error=None if status == "ready" else
                          f"codex={completed.returncode}, tests={tests.returncode}")
    except Exception as error:  # noqa: BLE001
        _update_candidate(candidate_id, status="failed", error=str(error), log=str(log_path))


def apply_code_candidate(candidate_id, confirmed=False):
    candidate = _candidate(candidate_id)
    if not candidate:
        raise ValueError("candidate not found")
    if candidate["kind"] != "code" or candidate["status"] != "ready":
        raise ValueError("code candidate is not ready")
    if confirmed is not True:
        raise ValueError("explicit approval is required")
    path = pathlib.Path(candidate["path"])
    source = _source_checkout()
    patch = subprocess.run(["git", "diff", "--binary"], cwd=path,
                           check=True, capture_output=True, timeout=60).stdout
    check = subprocess.run(["git", "apply", "--check", "-"], cwd=source, input=patch,
                           capture_output=True, timeout=60)
    if check.returncode:
        raise RuntimeError(check.stderr.decode("utf-8", "replace"))
    subprocess.run(["git", "apply", "-"], cwd=source, input=patch,
                   check=True, timeout=60)
    _update_candidate(candidate_id, status="applied", summary=candidate["summary"])
    return _candidate(candidate_id)


def _publication_changes(path):
    changed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"], cwd=path,
        check=True, capture_output=True, timeout=60).stdout.split(b"\0")
    names = [value.decode("utf-8", "strict") for value in changed if value]
    protected = [name for name in names if name.startswith((".github/workflows/", ".github/actions/"))
                 or name == ".gitmodules"]
    if protected:
        raise RuntimeError("self-published Candidates cannot change protected automation: " +
                           ", ".join(protected))
    private_values = [pathlib.Path.home().name, str(pathlib.Path.home()), "company/"]
    private_values.extend(os.environ.get("MIRA_PRIVATE_LABELS", "").split(","))
    sensitive = tuple(value.strip().lower().encode() for value in private_values if value.strip())
    for name in names:
        file_path = path / name
        if file_path.is_symlink():
            raise RuntimeError(f"self-published Candidates cannot add symlinks: {name}")
        if file_path.is_file() and file_path.stat().st_size <= 2_000_000:
            content = file_path.read_bytes().lower()
            if any(value in content for value in sensitive):
                raise RuntimeError(f"privacy scan rejected {name}")
    return names


def _create_or_find_pr(branch, title, body):
    existing = subprocess.run(
        ["gh", "pr", "view", branch, "--repo", PUBLIC_GH_REPO,
         "--json", "url", "--jq", ".url"], capture_output=True, text=True, timeout=60)
    if existing.returncode == 0 and existing.stdout.strip():
        return existing.stdout.strip()
    created = subprocess.run(
        ["gh", "pr", "create", "--repo", PUBLIC_GH_REPO, "--base", "main",
         "--head", branch, "--title", title, "--body", body],
        check=True, capture_output=True, text=True, timeout=120)
    return created.stdout.strip().splitlines()[-1]


def publish_code_candidate(candidate_id, confirmed=False):
    candidate = _candidate(candidate_id)
    if not candidate:
        raise ValueError("candidate not found")
    if candidate["kind"] != "code" or candidate["status"] not in (
            "ready", "applied", "publish_failed"):
        raise ValueError("code candidate is not publishable")
    if confirmed is not True:
        raise ValueError("explicit publication approval is required")
    if not shutil.which("gh"):
        raise RuntimeError("GitHub CLI is required to publish a PR")
    auth = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=30)
    if auth.returncode:
        raise RuntimeError("GitHub CLI is not authenticated")
    branch = candidate.get("branch") or f"mira/iteration-{candidate_id}"
    _update_candidate(candidate_id, status="publishing", branch=branch, error=None)
    threading.Thread(target=_publication_worker,
                     args=(candidate_id, branch), daemon=True).start()
    return _candidate(candidate_id)


def _publication_worker(candidate_id, branch):
    candidate = _candidate(candidate_id)
    path = pathlib.Path(candidate["path"])
    try:
        tests = subprocess.run(["make", "test"], cwd=path, capture_output=True,
                               text=True, timeout=1800)
        if tests.returncode:
            raise RuntimeError(f"publication tests failed ({tests.returncode})")
        subprocess.run(["git", "checkout", "-B", branch], cwd=path,
                       check=True, capture_output=True, text=True, timeout=60)
        subprocess.run(["git", "add", "-A"], cwd=path, check=True,
                       capture_output=True, timeout=60)
        names = _publication_changes(path)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=path,
                                timeout=60)
        title = "Fermi iteration: " + " ".join(candidate["goal"].split())[:60]
        if staged.returncode != 0:
            subprocess.run(
                ["git", "-c", "user.name=Fermi Iteration",
                 "-c", "user.email=fermi-iteration@users.noreply.github.com",
                 "commit", "-m", title], cwd=path, check=True,
                capture_output=True, text=True, timeout=120)
        elif not candidate.get("published_commit"):
            raise RuntimeError("Candidate has no changes to publish")
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True,
                                capture_output=True, text=True, timeout=30).stdout.strip()
        _update_candidate(candidate_id, published_commit=commit)
        subprocess.run(["git", "push", PUBLIC_PUSH_REPO, f"HEAD:refs/heads/{branch}"],
                       cwd=path, check=True, capture_output=True, text=True, timeout=300)
        body = (f"Automated Candidate `{candidate_id}` for an explicitly approved Fermi Iteration.\n\n"
                f"Goal: {candidate['goal']}\n\n"
                f"Local `make test` passed before publication. Changed files: {len(names)}.\n\n"
                "This PR was not merged automatically.")
        pr_url = _create_or_find_pr(branch, title, body)
        _update_candidate(candidate_id, status="published", pr_url=pr_url, error=None)
    except Exception as error:  # noqa: BLE001
        _update_candidate(candidate_id, status="publish_failed", error=str(error))


def prepare_training_candidate(model, iters=100):
    candidate_id, now = uuid.uuid4().hex[:12], time.time()
    path = ROOT / "training" / candidate_id
    path.mkdir(parents=True, exist_ok=True)
    with database() as db:
        examples = [json.loads(row[0]) for row in db.execute(
            "SELECT example FROM feedback WHERE learn=1 AND rating>=4 AND example IS NOT NULL ORDER BY created")]
    split = max(1, int(len(examples) * .9)) if examples else 0
    for name, rows in (("train.jsonl", examples[:split]), ("valid.jsonl", examples[split:])):
        with open(path / name, "w", encoding="utf-8") as output:
            for example in rows:
                output.write(json.dumps(example, ensure_ascii=False) + "\n")
    command = ["mlx_lm.lora", "--model", str(model), "--train", "--data", str(path),
               "--adapter-path", str(path / "adapter"), "--iters", str(max(10, min(5000, int(iters)))),
               "--batch-size", "1", "--num-layers", "4", "--max-seq-length", "1024"]
    status = "ready" if len(examples) >= 20 else "insufficient_data"
    with database() as db:
        db.execute("""INSERT INTO candidates
          (id,kind,status,goal,path,summary,command,created,updated) VALUES(?,?,?,?,?,?,?,?,?)""",
                   (candidate_id, "lora", status, f"Adapt {model}", str(path),
                    f"已批准 {len(examples)} 条样本；至少需要 20 条", json.dumps(command), now, now))
    return _candidate(candidate_id)


def start_training(candidate_id, confirmed=False):
    candidate = _candidate(candidate_id)
    if not candidate:
        raise ValueError("candidate not found")
    if candidate["kind"] != "lora" or candidate["status"] != "ready":
        raise ValueError("training candidate is not ready")
    if confirmed is not True:
        raise ValueError("explicit approval is required")
    if not shutil.which("mlx_lm.lora"):
        raise RuntimeError('install training support first: pip install "mlx-lm[train]"')
    command = json.loads(candidate["command"])
    _update_candidate(candidate_id, status="running")
    threading.Thread(target=_training_worker,
                     args=(candidate_id, command, pathlib.Path(candidate["path"])), daemon=True).start()
    return _candidate(candidate_id)


def _training_worker(candidate_id, command, path):
    log_path = path / "training.log"
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    text=True, timeout=86_400)
        _update_candidate(candidate_id, status="complete" if result.returncode == 0 else "failed",
                          log=str(log_path), error=None if result.returncode == 0 else
                          f"training exited {result.returncode}")
    except Exception as error:  # noqa: BLE001
        _update_candidate(candidate_id, status="failed", error=str(error), log=str(log_path))


def overview():
    return {"preferences": preferences(), "feedback": feedback_summary(),
            "candidates": candidates()}


init_db()
