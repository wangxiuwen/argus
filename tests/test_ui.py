import importlib.util
import io
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "argus_ui", Path(__file__).parents[1] / "share" / "ui.py")
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class MediaDownloadTests(unittest.TestCase):
    def test_download_copies_a_local_video_into_downloads_and_reveals_it(self):
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(ui, "DOWNLOAD_DIR", Path(temp)), \
                mock.patch.object(ui.urllib.request, "urlopen",
                                  return_value=Response(b"video")) as get, \
                mock.patch.object(ui.subprocess, "Popen") as launch:
            destination = ui.download_media(
                "video", "http://127.0.0.1:9877/outputs/result.mp4")
            self.assertEqual(destination.name, "result.mp4")
            self.assertEqual(destination.read_bytes(), b"video")
            self.assertEqual(get.call_args.args[0],
                             "http://127.0.0.1:9877/outputs/result.mp4")
            self.assertEqual(launch.call_args.args[0][:2], ["open", "-R"])

    def test_download_rejects_external_and_wrong_extension_urls(self):
        for value in ("https://evil.example/result.mp4", "/outputs/result.txt"):
            with self.assertRaises(ValueError):
                ui.media_download_url("video", value)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_config = ui.CONFIG
        ui.CONFIG = str(Path(self.temp.name) / "config")

    def tearDown(self):
        ui.CONFIG = self.old_config
        self.temp.cleanup()

    def test_defaults_include_every_runtime_port_and_reply_limit(self):
        cfg = ui.read_config()
        self.assertEqual(cfg["UI_PORT"], "8091")
        self.assertEqual(cfg["BRIDGE_PORT"], "8092")
        self.assertEqual(cfg["MAX_TOKENS"], "4096")

    def test_legacy_max_tokens_is_migrated_out_of_extra_args_on_write(self):
        Path(ui.CONFIG).write_text("EXTRA_ARGS=--kv-bits 8 --max-tokens 8192\n")
        self.assertEqual(ui.read_config()["MAX_TOKENS"], "8192")
        cfg = ui.write_config({})
        self.assertEqual(cfg["MAX_TOKENS"], "8192")
        self.assertEqual(cfg["EXTRA_ARGS"], "--kv-bits 8")

    def test_write_is_complete_and_preserves_bridge_port(self):
        cfg = ui.write_config({"BRIDGE_PORT": "9002", "MAX_TOKENS": 16384})
        self.assertEqual(cfg["BRIDGE_PORT"], "9002")
        self.assertEqual(cfg["MAX_TOKENS"], "16384")
        written = Path(ui.CONFIG).read_text()
        self.assertIn("BRIDGE_PORT=9002\n", written)
        self.assertIn("MAX_TOKENS=16384\n", written)

    def test_control_characters_and_port_collisions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "control character"):
            ui.write_config({"MODEL": "safe/model\nBAD=value"})
        with self.assertRaisesRegex(ValueError, "must be different"):
            ui.write_config({"UI_PORT": "8090"})


class ModelCatalogTests(unittest.TestCase):
    def test_known_variant_metadata_wins_over_an_incomplete_cache_entry(self):
        q4 = "mlx-community/Qwen3.8-27B-4bit"
        cached = {"id": q4, "label": "Qwen3.8-27B-4bit (0.0 GB)",
                  "gb": 0.0, "downloaded": False, "local": True}
        known = {"id": q4, "label": "Qwen3.8-27B 4bit (~16 GB)",
                 "gb": 16.1, "downloaded": False}
        with mock.patch.object(ui, "local_models", return_value=[cached]), \
             mock.patch.object(ui, "variant_list", return_value=[known]), \
             mock.patch.object(ui, "current_model", return_value=q4):
            catalog = ui.model_catalog()
        self.assertEqual(catalog, {"current": q4, "variants": [known]})

    def test_partial_known_variant_is_visible_but_not_marked_downloaded(self):
        with mock.patch.object(ui, "blob_bytes", return_value=(0, 2_000_000_000, 0)):
            q4 = next(v for v in ui.variant_list() if v["id"].endswith("-4bit"))
        self.assertEqual(q4["label"], "Qwen3.8-27B 4bit (~16 GB)")
        self.assertFalse(q4["downloaded"])


class BrowserOriginTests(unittest.TestCase):
    def test_cli_and_same_origin_browser_requests_are_allowed(self):
        self.assertTrue(ui.trusted_browser_origin(None, 8091))
        self.assertTrue(ui.trusted_browser_origin("http://127.0.0.1:8091", 8091))
        self.assertTrue(ui.trusted_browser_origin("http://localhost:8091", 8091))

    def test_cross_site_and_lookalike_origins_are_rejected(self):
        self.assertFalse(ui.trusted_browser_origin("https://evil.example", 8091))
        self.assertFalse(ui.trusted_browser_origin("http://127.0.0.1.evil.example:8091", 8091))
        self.assertFalse(ui.trusted_browser_origin("http://127.0.0.1:9999", 8091))
        self.assertFalse(ui.trusted_browser_origin("null", 8091))
        self.assertFalse(ui.trusted_browser_origin(None, 8091, "cross-site"))


class ProcessTests(unittest.TestCase):
    def test_failed_start_is_distinct_from_an_intentional_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "server.pid"
            log = Path(temp) / "argus.log"
            pidfile.write_text("99999999")
            log.write_text("RuntimeError: CAS Client Error\nApplication startup failed. Exiting.\n")
            failure = ui.server_startup_failure(str(pidfile), str(log))
        self.assertEqual(failure["stage"], "failed")
        self.assertIn("download", failure["error"])

    def test_intentional_stop_has_no_startup_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            failure = ui.server_startup_failure(
                str(Path(temp) / "missing.pid"), str(Path(temp) / "missing.log"))
        self.assertIsNone(failure)

    def test_failure_ignores_errors_from_an_older_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "server.pid"
            log = Path(temp) / "argus.log"
            pidfile.write_text("99999999")
            log.write_text("CAS Client Error\n[argus] starting org/new-model\nboom\n")
            failure = ui.server_startup_failure(str(pidfile), str(log))
        self.assertEqual(failure["error"], "model server exited unexpectedly")

    def test_stale_pid_reused_by_an_unrelated_process_is_not_alive(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "server.pid"
            pidfile.write_text(str(os.getpid()))
            result = subprocess.CompletedProcess([], 0, stdout="python unittest", stderr="")
            with mock.patch.object(ui.subprocess, "run", return_value=result):
                self.assertFalse(ui.server_process_alive(str(pidfile)))

    def test_matching_server_process_is_alive(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "server.pid"
            pidfile.write_text(str(os.getpid()))
            result = subprocess.CompletedProcess(
                [], 0, stdout="python mlx_vlm.server --port 8090", stderr="")
            with mock.patch.object(ui.subprocess, "run", return_value=result):
                self.assertTrue(ui.server_process_alive(str(pidfile), 8090))

    def test_server_on_another_port_is_not_owned(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "server.pid"
            pidfile.write_text(str(os.getpid()))
            result = subprocess.CompletedProcess(
                [], 0, stdout="python mlx_vlm.server --port 9000", stderr="")
            with mock.patch.object(ui.subprocess, "run", return_value=result):
                self.assertFalse(ui.server_process_alive(str(pidfile), 8090))


if __name__ == "__main__":
    unittest.main()
