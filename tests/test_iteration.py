import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "mira_iteration_test", Path(__file__).parents[1] / "share" / "iteration.py")
iteration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(iteration)


class IterationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db, self.old_root = iteration.DB, iteration.ROOT
        iteration.DB = Path(self.temp.name) / "jobs.sqlite3"
        iteration.ROOT = Path(self.temp.name) / "iterations"
        iteration.init_db()
        with iteration.database() as db:
            db.executescript("""
              CREATE TABLE batches(id TEXT PRIMARY KEY,kind TEXT,spec TEXT);
              CREATE TABLE items(batch_id TEXT,position INTEGER,prompt TEXT,lyrics TEXT,output TEXT);
            """)
            db.execute("INSERT INTO batches VALUES(?,?,?)",
                       ("batch1", "music", json.dumps({"prompt": "warm song"})))
            db.execute("INSERT INTO items VALUES(?,?,?,?,?)",
                       ("batch1", 1, "acoustic", "[verse] hello", "song.wav"))

    def tearDown(self):
        iteration.DB, iteration.ROOT = self.old_db, self.old_root
        self.temp.cleanup()

    def test_explicit_preference_is_available_to_future_quality_gates(self):
        iteration.remember("vocal", "soft female vocal")
        self.assertIn("soft female vocal", iteration.memory_context())

    def test_feedback_is_learnable_only_when_explicitly_enabled(self):
        iteration.record_feedback({"batch_id": "batch1", "position": 1,
                                   "rating": 5, "note": "more acoustic", "learn": True})
        iteration.record_feedback({"batch_id": "batch1", "position": 1,
                                   "rating": 2, "note": "one-off", "learn": False})
        summary = iteration.feedback_summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["learnable"], 1)
        self.assertIn("more acoustic", iteration.memory_context())
        self.assertNotIn("one-off", iteration.memory_context())

    def test_quality_gate_refines_until_score_passes(self):
        replies = iter([
            {"score": 6, "issues": ["generic"], "improved_prompt": "specific", "improved_lyrics": "v2"},
            {"score": 9, "issues": [], "improved_prompt": "specific cinematic", "improved_lyrics": "v3"},
        ])
        result = iteration.refine_creation("music", "plain", "v1", lambda _prompt: next(replies),
                                           "batch1", 1, 2)
        self.assertEqual(result["score"], 9)
        self.assertEqual(result["prompt"], "specific cinematic")

    def test_lora_candidate_requires_twenty_approved_examples(self):
        first = iteration.record_feedback({"batch_id": "batch1", "position": 1,
                                           "rating": 5, "learn": True})
        candidate = iteration.prepare_training_candidate("local/model", 50)
        self.assertEqual(candidate["status"], "insufficient_data")
        for _ in range(19):
            iteration.record_feedback({"batch_id": "batch1", "position": 1,
                                       "rating": 5, "learn": True})
        candidate = iteration.prepare_training_candidate("local/model", 50)
        self.assertEqual(candidate["status"], "ready")
        self.assertTrue((Path(candidate["path"]) / "train.jsonl").is_file())

    def test_candidate_cannot_run_without_explicit_approval(self):
        now = 1.0
        with iteration.database() as db:
            db.execute("""INSERT INTO candidates
              (id,kind,status,goal,path,command,created,updated)
              VALUES(?,?,?,?,?,?,?,?)""",
                       ("ready-lora", "lora", "ready", "Adapt local/model",
                        self.temp.name, "[]", now, now))
        with mock.patch.object(iteration.shutil, "which") as which:
            with self.assertRaises(ValueError):
                iteration.start_training("ready-lora", confirmed=False)
            which.assert_not_called()

    def test_code_candidate_cannot_apply_without_explicit_approval(self):
        now = 1.0
        with iteration.database() as db:
            db.execute("""INSERT INTO candidates
              (id,kind,status,goal,path,created,updated)
              VALUES(?,?,?,?,?,?,?)""",
                       ("ready-code", "code", "ready", "Improve tests",
                        self.temp.name, now, now))
        with mock.patch.object(iteration.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                iteration.apply_code_candidate("ready-code", confirmed=False)
            run.assert_not_called()

    def test_code_candidate_cannot_publish_without_explicit_approval(self):
        now = 1.0
        with iteration.database() as db:
            db.execute("""INSERT INTO candidates
              (id,kind,status,goal,path,created,updated)
              VALUES(?,?,?,?,?,?,?)""",
                       ("publish-code", "code", "ready", "Improve tests",
                        self.temp.name, now, now))
        with mock.patch.object(iteration.shutil, "which") as which:
            with self.assertRaises(ValueError):
                iteration.publish_code_candidate("publish-code", confirmed=False)
            which.assert_not_called()

    def test_publication_creates_branch_commit_and_pr_without_touching_main(self):
        root = Path(self.temp.name)
        origin, seed, candidate_path = root / "origin.git", root / "seed", root / "candidate"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", origin], check=True,
                       capture_output=True)
        subprocess.run(["git", "init", "--initial-branch=main", seed], check=True,
                       capture_output=True)
        (seed / "Makefile").write_text("test:\n\t@true\n")
        (seed / "app.txt").write_text("base\n")
        subprocess.run(["git", "-C", seed, "add", "."], check=True)
        subprocess.run(["git", "-C", seed, "-c", "user.name=Test", "-c",
                        "user.email=test@example.com", "commit", "-m", "base"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", seed, "remote", "add", "origin", str(origin)], check=True)
        subprocess.run(["git", "-C", seed, "push", "origin", "main"], check=True,
                       capture_output=True)
        main_before = subprocess.run(["git", "--git-dir", origin, "rev-parse", "main"],
                                     check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "clone", str(origin), candidate_path], check=True,
                       capture_output=True)
        (candidate_path / "app.txt").write_text("improved\n")
        now = 1.0
        with iteration.database() as db:
            db.execute("""INSERT INTO candidates
              (id,kind,status,goal,path,branch,created,updated)
              VALUES(?,?,?,?,?,?,?,?)""",
                       ("publish-ok", "code", "publishing", "Improve app",
                        str(candidate_path), "mira/iteration-publish-ok", now, now))
        old_push = iteration.PUBLIC_PUSH_REPO
        iteration.PUBLIC_PUSH_REPO = str(origin)
        try:
            with mock.patch.object(iteration, "_create_or_find_pr",
                                   return_value="https://github.com/example/mira/pull/1"):
                iteration._publication_worker("publish-ok", "mira/iteration-publish-ok")
        finally:
            iteration.PUBLIC_PUSH_REPO = old_push
        published = iteration._candidate("publish-ok")
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["pr_url"], "https://github.com/example/mira/pull/1")
        branch_commit = subprocess.run(
            ["git", "--git-dir", origin, "rev-parse", "refs/heads/mira/iteration-publish-ok"],
            check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch_commit, published["published_commit"])
        main_after = subprocess.run(["git", "--git-dir", origin, "rev-parse", "main"],
                                    check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(main_before, main_after)

    def test_publication_rejects_github_workflow_changes(self):
        path = Path(self.temp.name) / "protected"
        subprocess.run(["git", "init", "--initial-branch=main", path], check=True,
                       capture_output=True)
        (path / "Makefile").write_text("test:\n\t@true\n")
        subprocess.run(["git", "-C", path, "add", "."], check=True)
        subprocess.run(["git", "-C", path, "-c", "user.name=Test", "-c",
                        "user.email=test@example.com", "commit", "-m", "base"],
                       check=True, capture_output=True)
        workflow = path / ".github" / "workflows" / "unsafe.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("on: push\n")
        now = 1.0
        with iteration.database() as db:
            db.execute("""INSERT INTO candidates
              (id,kind,status,goal,path,branch,created,updated)
              VALUES(?,?,?,?,?,?,?,?)""",
                       ("publish-blocked", "code", "publishing", "Change automation",
                        str(path), "mira/iteration-blocked", now, now))
        iteration._publication_worker("publish-blocked", "mira/iteration-blocked")
        blocked = iteration._candidate("publish-blocked")
        self.assertEqual(blocked["status"], "publish_failed")
        self.assertIn("protected automation", blocked["error"])


if __name__ == "__main__":
    unittest.main()
