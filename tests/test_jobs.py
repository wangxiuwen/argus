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
        self.old_iteration_db = jobs.iteration.DB
        jobs.DB = Path(self.temp.name) / "jobs.sqlite3"
        jobs.iteration.DB = jobs.DB
        jobs.init_db()
        jobs.iteration.init_db()

    def tearDown(self):
        jobs.DB = self.old_db
        jobs.iteration.DB = self.old_iteration_db
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

    def test_request_id_makes_retried_batch_creation_idempotent(self):
        body = {"kind": "music", "prompt": "one song", "request_id": "chat-1:0"}
        first, first_code = jobs.create_batch(body)
        second, second_code = jobs.create_batch(body)
        self.assertEqual((first_code, second_code), (202, 202))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(jobs.batches()), 1)

    def test_restart_requeues_interrupted_items(self):
        result, _ = jobs.create_batch({"kind": "video", "prompt": "forest"})
        with jobs.database() as db:
            db.execute("UPDATE batches SET status='running' WHERE id=?", (result["id"],))
            db.execute("UPDATE items SET status='running' WHERE batch_id=?", (result["id"],))
        jobs.init_db()
        with mock.patch.object(jobs, "request_json", return_value={"running": False}):
            jobs.recover_running()
        stored = jobs.batch(result["id"])
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["items"][0]["status"], "queued")

    def test_restart_adopts_media_that_is_still_generating(self):
        result, _ = jobs.create_batch({"kind": "music", "prompt": "song", "lyrics": "la"})
        with jobs.database() as db:
            db.execute("UPDATE batches SET status='running' WHERE id=?", (result["id"],))
            db.execute("UPDATE items SET status='running' WHERE batch_id=?", (result["id"],))
        replies = [
            {"running": True, "job_id": "job-1"},
            {"running": False, "job_id": "job-1", "output": "/files/song.wav"},
        ]
        with mock.patch.object(jobs, "request_json", side_effect=replies):
            jobs.recover_running()
        stored = jobs.batch(result["id"])
        self.assertEqual(stored["status"], "complete")
        self.assertEqual(stored["completed"], 1)
        self.assertEqual(stored["items"][0]["output"],
                         "http://127.0.0.1:9879/files/song.wav")

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

    def test_retry_requeues_failed_items_of_a_finished_batch(self):
        result, _ = jobs.create_batch({"kind": "video", "count": 1, "prompt": "fog forest"})
        batch_id = result["id"]
        with jobs.database() as db:
            db.execute("UPDATE items SET status='failed',error='hf download failed' WHERE batch_id=?",
                       (batch_id,))
            db.execute("UPDATE batches SET status='failed',error='hf download failed',failed=1 WHERE id=?",
                       (batch_id,))
        retried = jobs.action(batch_id, "retry")
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["failed"], 0)
        self.assertIsNone(retried["error"])
        self.assertEqual([item["status"] for item in retried["items"]], ["queued"])
        running, _ = jobs.create_batch({"kind": "image", "count": 1, "prompt": "x"})
        with jobs.database() as db:
            db.execute("UPDATE batches SET status='running' WHERE id=?", (running["id"],))
        with self.assertRaises(ValueError):
            jobs.action(running["id"], "retry")


class AgentLoopTests(unittest.TestCase):
    def test_agent_can_propose_but_cannot_approve_self_changes(self):
        names = {tool["function"]["name"] for tool in jobs.AGENT_TOOLS}
        self.assertIn("propose_self_update", names)
        self.assertIn("prepare_lora_training", names)
        self.assertNotIn("apply_code_candidate", names)
        self.assertNotIn("start_training", names)

    def test_created_task_returns_immediately_without_a_second_model_round(self):
        replies = [
            {"loaded_model": "local/model"},
            {"choices": [{"message": {"role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "generate_images", "arguments": json.dumps({"prompt": "cat"})}}]}}]},
        ]
        with mock.patch.object(jobs, "request_json", side_effect=replies), \
                mock.patch.object(jobs, "execute_agent_tool",
                                  return_value=({"id": "batch123"}, 202)):
            result = jobs.agent_chat({"messages": [{"role": "user", "content": "画一只猫"}]})
        self.assertIn("已创建", result["content"])
        self.assertEqual(result["tasks"], ["batch123"])

    def test_reopened_webview_reuses_the_same_agent_request(self):
        request = {"request_id": "request-1", "messages": []}
        jobs.agent_results.pop("request-1", None)
        expected = {"content": "任务已创建。", "tasks": ["batch123"]}
        with mock.patch.object(jobs, "agent_chat", return_value=expected) as run:
            self.assertEqual(jobs.agent_chat_idempotent(request), expected)
            self.assertEqual(jobs.agent_chat_idempotent(request), expected)
        self.assertEqual(run.call_count, 1)
        jobs.agent_results.pop("request-1", None)


if __name__ == "__main__":
    unittest.main()
