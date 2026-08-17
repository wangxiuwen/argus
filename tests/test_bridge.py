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
        return Response(json.dumps({"loaded_model": model}).encode())

    def test_loaded_model_coalesces_nearby_requests(self):
        with mock.patch.object(bridge.time, "monotonic", side_effect=[10.0, 10.1]), \
             mock.patch.object(bridge.urllib.request, "urlopen",
                               return_value=self.model_response("local/model")) as get:
            self.assertEqual(bridge.loaded_model(), "local/model")
            self.assertEqual(bridge.loaded_model(), "local/model")
        get.assert_called_once()
        self.assertTrue(get.call_args.args[0].endswith("/health"))

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

    def test_request_never_falls_back_to_client_alias_when_server_is_down(self):
        with mock.patch.object(bridge, "loaded_model", return_value=None):
            translated = bridge.to_openai_request({
                "model": "gpt-5-codex",
                "messages": [{"role": "user", "content": "hello"}],
            })
        self.assertIsNone(translated["model"])


class StreamingTranslationTests(unittest.TestCase):
    @staticmethod
    def translate(chunks):
        upstream = io.BytesIO(b"".join(
            b"data: " + json.dumps(chunk).encode() + b"\n\n"
            for chunk in chunks
        ) + b"data: [DONE]\n\n")
        output = []
        bridge.stream_translate(upstream, "local/model", output.append)
        events = []
        for packet in output:
            lines = packet.decode().strip().splitlines()
            events.append((lines[0].removeprefix("event: "),
                           json.loads(lines[1].removeprefix("data: "))))
        return events

    def test_text_stream_has_a_complete_content_block(self):
        events = self.translate([
            {"choices": [{"delta": {"content": "hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}],
             "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        self.assertEqual([name for name, _ in events], [
            "message_start", "ping", "content_block_start",
            "content_block_delta", "content_block_delta", "content_block_stop",
            "message_delta", "message_stop",
        ])
        self.assertEqual(events[-2][1]["usage"]["output_tokens"], 2)

    def test_interleaved_tool_fragments_are_emitted_as_sequential_blocks(self):
        events = self.translate([
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_a", "function": {"name": "Read", "arguments": "{\"file_"}},
                {"index": 1, "id": "call_b", "function": {"name": "Glob", "arguments": "{\"pat"}},
            ]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "path\":\"README.md\"}"}},
                {"index": 1, "function": {"arguments": "tern\":\"*.py\"}"}},
            ]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])

        block_events = [(name, data) for name, data in events
                        if name.startswith("content_block_")]
        self.assertEqual([name for name, _ in block_events], [
            "content_block_start", "content_block_delta", "content_block_stop",
            "content_block_start", "content_block_delta", "content_block_stop",
        ])
        starts = [data["content_block"] for name, data in block_events
                  if name == "content_block_start"]
        deltas = [data["delta"]["partial_json"] for name, data in block_events
                  if name == "content_block_delta"]
        self.assertEqual([(b["id"], b["name"]) for b in starts],
                         [("call_a", "Read"), ("call_b", "Glob")])
        self.assertEqual(deltas, [
            '{"file_path":"README.md"}', '{"pattern":"*.py"}',
        ])
        self.assertEqual(events[-2][1]["delta"]["stop_reason"], "tool_use")

    def test_upstream_stream_error_becomes_an_anthropic_error_event(self):
        events = self.translate([
            {"error": {"message": "generation failed", "type": "server_error"}},
        ])
        self.assertEqual(events[-1], ("error", {
            "type": "error",
            "error": {"type": "api_error", "message": "generation failed"},
        }))
        self.assertNotIn("message_stop", [name for name, _ in events])


if __name__ == "__main__":
    unittest.main()
