import importlib.util
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
