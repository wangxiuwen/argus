from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).parents[1]


class CliConfigTests(unittest.TestCase):
    def run_argus(self, home, *args):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run(
            ["zsh", str(ROOT / "bin" / "argus"), *args], env=env,
            text=True, capture_output=True, check=False)

    def test_config_is_parsed_as_data_not_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config_dir = home / ".config" / "argus"
            config_dir.mkdir(parents=True)
            marker = home / "must-not-exist"
            (config_dir / "config").write_text(
                f"MODEL=safe/model\nUNKNOWN=$(touch {marker})\n"
                f"EXTRA_ARGS=$(touch {marker})\nMAX_TOKENS=8192\n")
            result = self.run_argus(home, "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn("MODEL=safe/model", result.stdout)
            self.assertIn("MAX_TOKENS=8192", result.stdout)

    def test_start_uses_stable_http_downloads_by_default(self):
        """Xet/CAS failures must not leave a first model download dead overnight."""
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            bin_dir = home / ".local" / "bin"
            config_dir = home / ".config" / "argus"
            bin_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)
            marker = home / "download-backend"
            server = bin_dir / "mlx_vlm.server"
            server.write_text(
                "#!/bin/sh\n"
                f"printf '%s' \"${{HF_HUB_DISABLE_XET-unset}}\" > {marker!s}\n"
                "sleep 30\n")
            server.chmod(0o755)
            (config_dir / "config").write_text("PORT=65433\n")
            env = dict(os.environ, HOME=str(home))
            env.pop("HF_HUB_DISABLE_XET", None)
            result = subprocess.run(
                ["zsh", str(ROOT / "bin" / "argus"), "start"], env=env,
                text=True, capture_output=True, check=False)
            try:
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                for _ in range(100):
                    if marker.exists():
                        break
                    time.sleep(0.05)
                self.assertEqual(marker.read_text(), "1")
            finally:
                pidfile = home / ".local" / "state" / "argus" / "server.pid"
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), 15)
                    except (ProcessLookupError, ValueError):
                        pass

    def test_start_auto_prunes_only_superseded_partials_for_current_model(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            bin_dir = home / ".local" / "bin"
            share_dir = home / ".local" / "share" / "argus"
            config_dir = home / ".config" / "argus"
            blobs = home / ".cache" / "huggingface" / "hub" / \
                "models--org--model" / "blobs"
            for directory in (bin_dir, share_dir, config_dir, blobs):
                directory.mkdir(parents=True)
            shutil.copy(ROOT / "share" / "prune.py", share_dir / "prune.py")
            server = bin_dir / "mlx_vlm.server"
            server.write_text("#!/bin/sh\nsleep 30\n")
            server.chmod(0o755)
            (config_dir / "config").write_text("MODEL=org/model\nPORT=65434\n")
            superseded = blobs / "weights.old.incomplete"
            resumable = blobs / "weights.new.incomplete"
            superseded.write_bytes(b"old")
            resumable.write_bytes(b"new")
            old = time.time() - 2 * 60 * 60
            os.utime(superseded, (old, old))
            result = self.run_argus(home, "start")
            try:
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertFalse(superseded.exists())
                self.assertTrue(resumable.exists())
            finally:
                pidfile = home / ".local" / "state" / "argus" / "server.pid"
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), 15)
                    except (ProcessLookupError, ValueError):
                        pass

    def test_model_update_treats_metacharacters_as_text(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            result = self.run_argus(home, "model", "org/model&variant")
            self.assertEqual(result.returncode, 0, result.stderr)
            config = home / ".config" / "argus" / "config"
            self.assertEqual(config.read_text(), "MODEL=org/model&variant\n")

    def test_stale_pidfile_never_kills_an_unrelated_process(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        try:
            with tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                config_dir = home / ".config" / "argus"
                state_dir = home / ".local" / "state" / "argus"
                config_dir.mkdir(parents=True)
                state_dir.mkdir(parents=True)
                (config_dir / "config").write_text("PORT=65431\n")
                (state_dir / "server.pid").write_text(str(sleeper.pid))
                result = self.run_argus(home, "stop")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "not running")
                self.assertIsNone(sleeper.poll())
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=5)

    def test_status_reports_an_unexpected_server_exit_as_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            state_dir = home / ".local" / "state" / "argus"
            state_dir.mkdir(parents=True)
            (state_dir / "server.pid").write_text("99999999")
            result = self.run_argus(home, "status")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed", result.stdout)
            self.assertIn("fermi log", result.stdout)

    def test_stale_pidfile_never_kills_another_mlx_server(self):
        other = subprocess.Popen([
            sys.executable, "-c", "import time; time.sleep(30)",
            "mlx_vlm.server", "--port", "65432",
        ])
        try:
            with tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                config_dir = home / ".config" / "argus"
                state_dir = home / ".local" / "state" / "argus"
                config_dir.mkdir(parents=True)
                state_dir.mkdir(parents=True)
                (config_dir / "config").write_text("PORT=65431\n")
                (state_dir / "server.pid").write_text(str(other.pid))
                result = self.run_argus(home, "stop")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), "not running")
                self.assertIsNone(other.poll())
        finally:
            if other.poll() is None:
                other.terminate()
            other.wait(timeout=5)

    def test_ui_restart_and_stop_manage_the_real_listener(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config_dir = home / ".config" / "argus"
            share_dir = home / ".local" / "share" / "argus"
            config_dir.mkdir(parents=True)
            share_dir.mkdir(parents=True)
            (config_dir / "config").write_text(f"UI_PORT={port}\nPORT=65431\n")
            for name in ("ui.py", "ui.html", "settings.html"):
                shutil.copy(ROOT / "share" / name, share_dir / name)
            started = self.run_argus(home, "ui", "restart")
            self.assertEqual(started.returncode, 0, started.stderr + started.stdout)
            self.assertIn(f"http://127.0.0.1:{port}", started.stdout)
            stopped = self.run_argus(home, "ui", "stop")
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertEqual(stopped.stdout.strip(), "UI stopped")

    def test_ui_start_moves_a_managed_process_to_a_new_saved_port(self):
        with socket.socket() as first_probe, socket.socket() as second_probe:
            first_probe.bind(("127.0.0.1", 0))
            first = first_probe.getsockname()[1]
            second_probe.bind(("127.0.0.1", 0))
            second = second_probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config_dir = home / ".config" / "argus"
            share_dir = home / ".local" / "share" / "argus"
            config_dir.mkdir(parents=True)
            share_dir.mkdir(parents=True)
            config = config_dir / "config"
            config.write_text(f"UI_PORT={first}\nPORT=65431\nBRIDGE_PORT=65432\n")
            for name in ("ui.py", "ui.html", "settings.html"):
                shutil.copy(ROOT / "share" / name, share_dir / name)
            self.assertEqual(self.run_argus(home, "ui", "start").returncode, 0)
            old_pid = int((home / ".local" / "state" / "argus" / "ui.pid").read_text())
            config.write_text(f"UI_PORT={second}\nPORT=65431\nBRIDGE_PORT=65432\n")
            moved = self.run_argus(home, "ui", "start")
            self.assertEqual(moved.returncode, 0, moved.stderr + moved.stdout)
            new_pid = int((home / ".local" / "state" / "argus" / "ui.pid").read_text())
            self.assertNotEqual(old_pid, new_pid)
            with socket.create_connection(("127.0.0.1", second), timeout=2):
                pass
            self.run_argus(home, "ui", "stop")

    def test_bridge_start_moves_a_managed_process_to_a_new_saved_port(self):
        with socket.socket() as first_probe, socket.socket() as second_probe:
            first_probe.bind(("127.0.0.1", 0))
            first = first_probe.getsockname()[1]
            second_probe.bind(("127.0.0.1", 0))
            second = second_probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config_dir = home / ".config" / "argus"
            share_dir = home / ".local" / "share" / "argus"
            config_dir.mkdir(parents=True)
            share_dir.mkdir(parents=True)
            config = config_dir / "config"
            config.write_text(f"BRIDGE_PORT={first}\nPORT=65430\nUI_PORT=65431\n")
            shutil.copy(ROOT / "share" / "bridge.py", share_dir / "bridge.py")
            started = self.run_argus(home, "bridge", "start")
            self.assertEqual(started.returncode, 0, started.stderr + started.stdout)
            pidfile = home / ".local" / "state" / "argus" / "bridge.pid"
            old_pid = int(pidfile.read_text())
            config.write_text(f"BRIDGE_PORT={second}\nPORT=65430\nUI_PORT=65431\n")
            moved = self.run_argus(home, "bridge", "start")
            self.assertEqual(moved.returncode, 0, moved.stderr + moved.stdout)
            new_pid = int(pidfile.read_text())
            self.assertNotEqual(old_pid, new_pid)
            with socket.create_connection(("127.0.0.1", second), timeout=2):
                pass
            self.run_argus(home, "bridge", "stop")


if __name__ == "__main__":
    unittest.main()
