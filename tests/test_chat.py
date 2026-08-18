import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "argus_chat", Path(__file__).parents[1] / "share" / "chat.py")
chat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chat)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ChatTests(unittest.TestCase):
    def test_model_comes_from_health(self):
        response = Response(json.dumps({"loaded_model": "local/model"}).encode())
        with mock.patch.object(chat.urllib.request, "urlopen", return_value=response):
            self.assertEqual(chat.get_model(), "local/model")

    def test_existing_image_paths_are_separated_from_prompt_text(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            text, images = chat.split_images(["describe", image.name])
        self.assertEqual(text, "describe")
        self.assertEqual(images, [image.name])

    def test_connection_failure_is_a_clean_cli_error(self):
        with mock.patch.object(
                chat.urllib.request, "urlopen",
                side_effect=urllib.error.URLError("connection refused")), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                chat.stream_chat([{"role": "user", "content": "hello"}], "local/model")
        self.assertIn("server not reachable", str(stopped.exception))
        self.assertIn("mira start", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
