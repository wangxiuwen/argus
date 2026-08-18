import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "mira_jobs", Path(__file__).parents[1] / "share" / "jobs.py")
jobs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jobs)


class DurableQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = jobs.DB
        jobs.DB = Path(self.temp.name) / "jobs.sqlite3"
        jobs.init_db()

    def tearDown(self):
        jobs.DB = self.old_db
        self.temp.cleanup()

    def test_batch_is_persisted_as_individual_queued_items(self):
        result, code = jobs.create_batch({
            "kind": "image", "count": 3, "prompt": "three distinct cats"})
        self.assertEqual(code, 202)
        stored = jobs.batch(result["id"])
        self.assertEqual(stored["total"], 3)
        self.assertEqual([item["status"] for item in stored["items"]],
                         ["queued", "queued", "queued"])

    def test_large_batch_requires_explicit_confirmation(self):
        result, code = jobs.create_batch({
            "kind": "music", "count": 1000, "prompt": "ambient songs",
            "duration_seconds": 120})
        self.assertEqual(code, 409)
        self.assertTrue(result["confirmation_required"])
        self.assertGreater(result["estimated_gb"], 1)
        self.assertEqual(jobs.batches(), [])

    def test_confirmed_batch_can_queue_one_thousand_items(self):
        result, code = jobs.create_batch({
            "kind": "music", "count": 1000, "prompt": "ambient songs",
            "duration_seconds": 120, "confirmed": True})
        self.assertEqual(code, 202)
        self.assertEqual(result["total"], 1000)
        with jobs.database() as db:
            count = db.execute("SELECT count(*) FROM items WHERE batch_id=?",
                               (result["id"],)).fetchone()[0]
        self.assertEqual(count, 1000)

    def test_restart_requeues_interrupted_items(self):
        result, _ = jobs.create_batch({"kind": "video", "prompt": "forest"})
        with jobs.database() as db:
            db.execute("UPDATE batches SET status='running' WHERE id=?", (result["id"],))
            db.execute("UPDATE items SET status='running' WHERE batch_id=?", (result["id"],))
        jobs.init_db()
        stored = jobs.batch(result["id"])
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["items"][0]["status"], "queued")

    def test_pause_resume_and_cancel_are_durable_state_transitions(self):
        result, _ = jobs.create_batch({"kind": "image", "count": 2, "prompt": "cats"})
        batch_id = result["id"]
        self.assertEqual(jobs.action(batch_id, "pause")["status"], "paused")
        self.assertEqual(jobs.action(batch_id, "resume")["status"], "queued")
        cancelled = jobs.action(batch_id, "cancel")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual([item["status"] for item in cancelled["items"]],
                         ["cancelled", "cancelled"])
        with self.assertRaises(ValueError):
            jobs.action(batch_id, "resume")


class AgentLoopTests(unittest.TestCase):
    def test_model_tool_call_creates_a_task_and_returns_it_to_model(self):
        replies = [
            {"loaded_model": "local/model"},
            {"choices": [{"message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "generate_images", "arguments": json.dumps({"prompt": "cat"})}}]}}]},
            {"loaded_model": "local/model"},
            {"choices": [{"message": {"role": "assistant", "content": "已创建。"}}]},
        ]
        with mock.patch.object(jobs, "request_json", side_effect=replies), \
                mock.patch.object(jobs, "execute_agent_tool",
                                  return_value=({"id": "batch123"}, 202)):
            result = jobs.agent_chat({"messages": [{"role": "user", "content": "画一只猫"}]})
        self.assertEqual(result["content"], "已创建。")
        self.assertEqual(result["tasks"], ["batch123"])


if __name__ == "__main__":
    unittest.main()
