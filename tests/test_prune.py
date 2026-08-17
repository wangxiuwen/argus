import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "argus_prune", Path(__file__).parents[1] / "share" / "prune.py")
prune = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prune)


class PruneTests(unittest.TestCase):
    def test_yes_deletes_only_superseded_stale_partial_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            old_hub = prune.HUB
            prune.HUB = temp
            try:
                blobs = Path(temp) / "models--org--model" / "blobs"
                blobs.mkdir(parents=True)
                stale = blobs / "weights.old.incomplete"
                active = blobs / "weights.new.incomplete"
                complete = blobs / "finished"
                for path in (stale, active, complete):
                    path.write_bytes(b"data")
                old = time.time() - 2 * 60 * 60
                os.utime(stale, (old, old))
                with mock.patch.object(sys, "argv", ["argus prune", "--yes"]), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(prune.main(), 0)
                self.assertFalse(stale.exists())
                self.assertTrue(active.exists())
                self.assertTrue(complete.exists())
            finally:
                prune.HUB = old_hub

    def test_latest_partial_is_kept_for_resume_even_when_old(self):
        with tempfile.TemporaryDirectory() as temp:
            old_hub = prune.HUB
            prune.HUB = temp
            try:
                partial = Path(temp) / "models--org--model" / "blobs" / "weights.incomplete"
                partial.parent.mkdir(parents=True)
                partial.write_bytes(b"resumable")
                old = time.time() - 2 * 60 * 60
                os.utime(partial, (old, old))
                with mock.patch.object(sys, "argv", ["argus prune", "--yes"]), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(prune.main(), 0)
                self.assertTrue(partial.exists())
            finally:
                prune.HUB = old_hub

    def test_repo_filter_never_touches_another_shared_cache_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            old_hub = prune.HUB
            prune.HUB = temp
            try:
                old = time.time() - 2 * 60 * 60
                paths = []
                for repo in ("models--org--target", "models--org--other"):
                    blobs = Path(temp) / repo / "blobs"
                    blobs.mkdir(parents=True)
                    first = blobs / "weights.old.incomplete"
                    latest = blobs / "weights.new.incomplete"
                    first.write_bytes(b"old")
                    latest.write_bytes(b"new")
                    os.utime(first, (old, old))
                    paths.append(first)
                with mock.patch.object(
                        sys, "argv", ["argus prune", "--yes", "--repo", "org/target"]), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(prune.main(), 0)
                self.assertFalse(paths[0].exists())
                self.assertTrue(paths[1].exists())
            finally:
                prune.HUB = old_hub

    def test_invalid_negative_age_is_rejected(self):
        with mock.patch.object(sys, "argv", ["argus prune", "--min-age", "-1"]), \
                mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                prune.main()
        self.assertEqual(stopped.exception.code, 2)

    def test_file_that_resumed_after_scan_is_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            old_hub = prune.HUB
            prune.HUB = temp
            try:
                partial = Path(temp) / "models--org--model" / "blobs" / "x.incomplete"
                partial.parent.mkdir(parents=True)
                partial.write_bytes(b"new data")
                stale_snapshot = [(str(partial), 1, 120)]
                with mock.patch.object(prune, "scan", return_value=(stale_snapshot, [])), \
                        mock.patch.object(sys, "argv", ["argus prune", "--yes"]), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(prune.main(), 0)
                self.assertTrue(partial.exists())
            finally:
                prune.HUB = old_hub


if __name__ == "__main__":
    unittest.main()
