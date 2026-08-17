import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "argus_bridge", Path(__file__).parents[1] / "share" / "bridge.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class LoadedModelTests(unittest.TestCase):
    def setUp(self):
        bridge._loaded.update(id=None, at=0.0)

    @staticmethod
    def model_response(model):
        return Response(json.dumps({"data": [{"id": model}]}).encode())

    def test_loaded_model_coalesces_nearby_requests(self):
        with mock.patch.object(bridge.time, "monotonic", side_effect=[10.0, 10.1]), \
             mock.patch.object(bridge.urllib.request, "urlopen",
                               return_value=self.model_response("local/model")) as get:
            self.assertEqual(bridge.loaded_model(), "local/model")
            self.assertEqual(bridge.loaded_model(), "local/model")
        get.assert_called_once()

    def test_loaded_model_refreshes_quickly_after_a_switch(self):
        replies = [self.model_response("old/model"), self.model_response("new/model")]
        with mock.patch.object(bridge.time, "monotonic", side_effect=[10.0, 11.0]), \
             mock.patch.object(bridge.urllib.request, "urlopen", side_effect=replies):
            self.assertEqual(bridge.loaded_model(), "old/model")
            self.assertEqual(bridge.loaded_model(), "new/model")

    def test_failed_refresh_does_not_return_stale_model(self):
        bridge._loaded.update(id="old/model", at=1.0)
        with mock.patch.object(bridge.time, "monotonic", return_value=10.0), \
             mock.patch.object(bridge.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(bridge.loaded_model())


class TranslationTests(unittest.TestCase):
    def test_system_messages_are_merged_at_the_front(self):
        request = {
            "system": [{"type": "text", "text": "top-level"}],
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "system", "content": "client-added"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        self.assertEqual(bridge.to_openai_messages(request), [
            {"role": "system", "content": "top-level\n\nclient-added"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])

    def test_tool_result_precedes_the_following_user_text(self):
        request = {"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "done"},
            {"type": "text", "text": "continue"},
        ]}]}
        self.assertEqual(bridge.to_openai_messages(request), [
            {"role": "tool", "tool_call_id": "call_1", "content": "done"},
            {"role": "user", "content": "continue"},
        ])

    def test_request_uses_loaded_model_instead_of_client_alias(self):
        with mock.patch.object(bridge, "loaded_model", return_value="local/model"):
            translated = bridge.to_openai_request({
                "model": "claude-sonnet-4-5",
                "messages": [{"role": "user", "content": "hello"}],
            })
        self.assertEqual(translated["model"], "local/model")


if __name__ == "__main__":
    unittest.main()
