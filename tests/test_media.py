import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "share" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image = load("image")
music = load("music")


class MediaModelReadinessTests(unittest.TestCase):
    def test_image_directory_without_completed_download_marker_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "weight").write_bytes(b"x" * 100)
            with mock.patch.object(image, "MODEL_DIR", root), \
                    mock.patch.object(image, "READY_MARKER", root / ".mira-ready"), \
                    mock.patch.object(image, "EXPECTED_BYTES", 100):
                self.assertFalse(image.model_ready())
                (root / ".mira-ready").touch()
                self.assertTrue(image.model_ready())

    def test_partial_music_weights_are_not_reported_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            weight = root / "weight"
            weight.write_bytes(b"x" * 50)
            with mock.patch.object(music, "MODEL_DIR", root), \
                    mock.patch.object(music, "EXPECTED_BYTES", 100):
                self.assertFalse(music.model_ready())
                weight.write_bytes(b"x" * 100)
                self.assertTrue(music.model_ready())


class UnifiedConversationTests(unittest.TestCase):
    def test_media_modes_share_the_chat_composer(self):
        html = (ROOT / "share" / "ui.html").read_text()
        for mode in ("chat", "image", "music", "video"):
            self.assertIn(f'<option value="{mode}">', html)
        self.assertNotIn('id="videoView"', html)
        self.assertNotIn('id="musicView"', html)
        self.assertIn("messages.push(user, record)", html)
        self.assertIn("renderMedia(card, m)", html)

    def test_composer_uses_newline_enter_and_modified_enter_to_send(self):
        html = (ROOT / "share" / "ui.html").read_text()
        self.assertIn('e.key === "Enter" && (e.metaKey || e.ctrlKey)', html)
        self.assertNotIn('e.key === "Enter" && !e.shiftKey', html)


if __name__ == "__main__":
    unittest.main()
