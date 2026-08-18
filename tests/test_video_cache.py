import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "mira_video_cache_test", Path(__file__).parents[1] / "share" / "video.py")
video = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(video)


class VideoCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old = (video.WORK, video.HF_HUB, video.MODEL_LINKS,
                    video.DERIVED_MODELS, video.MODELS)
        video.WORK = root / "Application Support" / "Mira" / "video"
        video.HF_HUB = root / ".cache" / "huggingface" / "hub"
        video.MODEL_LINKS = video.WORK / "models"
        video.DERIVED_MODELS = video.HF_HUB / "mira-vpipe" / "local"
        video.MODELS = video.DERIVED_MODELS / "MiniMax-H3-FL2VA-4bit"

    def tearDown(self):
        (video.WORK, video.HF_HUB, video.MODEL_LINKS,
         video.DERIVED_MODELS, video.MODELS) = self.old
        self.temp.cleanup()

    def test_vpipe_model_links_point_to_hf_snapshot_and_derived_cache(self):
        snapshot = (video.repo_cache(video.SOURCE_REPO) / "snapshots" / "revision")
        snapshot.mkdir(parents=True)
        video.ensure_model_layout()
        video.link_snapshot(video.SOURCE_REPO, snapshot)
        self.assertEqual((video.MODEL_LINKS / "local").resolve(), video.DERIVED_MODELS.resolve())
        self.assertEqual((video.MODEL_LINKS / "Comfy-Org" / "MiniMax-H3").resolve(),
                         snapshot.resolve())

    def test_hf_download_uses_canonical_cache_and_not_a_local_model_dir(self):
        with mock.patch.object(video.shutil, "which", return_value="/usr/bin/hf"):
            command = video.hf_command(video.SOURCE_REPO, tuple(video.SOURCE_MODEL_FILES))
        self.assertIn(str(video.HF_HUB), command)
        self.assertIn("--cache-dir", command)
        self.assertNotIn("--local-dir", command)
        self.assertNotIn(str(video.WORK / "models"), command)

    def test_cancelled_hf_partial_is_counted_but_never_deleted(self):
        partial = video.repo_cache(video.SOURCE_REPO) / "blobs" / "abc.incomplete"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        with mock.patch.object(video.time, "time", return_value=partial.stat().st_mtime + 1):
            self.assertTrue(video.external_download_active())
        self.assertTrue(partial.exists())


if __name__ == "__main__":
    unittest.main()
