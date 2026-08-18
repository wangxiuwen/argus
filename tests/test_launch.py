import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "argus_launch", Path(__file__).parents[1] / "share" / "launch.py")
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class CommandArgsTests(unittest.TestCase):
    def test_codex_receives_loaded_model_explicitly(self):
        args = launch.command_args(
            "codex", "/bin/codex", launch.TOOLS["codex"],
            "mlx-community/Qwen3.8-27B-4bit", "http://127.0.0.1:8092/v1", [])
        self.assertEqual(args[-2:], [
            "--model", "mlx-community/Qwen3.8-27B-4bit"])
        self.assertIn('model_provider="mira"', args)
        self.assertIn('model_providers.mira.wire_api="responses"', args)
        self.assertIn(
            'model_providers.mira.base_url="http://127.0.0.1:8092/v1"', args)

    def test_codex_preserves_explicit_model_override(self):
        args = launch.command_args(
            "codex", "/bin/codex", launch.TOOLS["codex"],
            "local/model", "http://127.0.0.1:8092/v1", ["-m", "custom/model"])
        self.assertEqual(args[-2:], ["-m", "custom/model"])
        self.assertIn('model_provider="mira"', args)

    def test_server_model_uses_health_not_downloaded_model_list_order(self):
        context = mock.MagicMock()
        context.__enter__.return_value = io.BytesIO(
            json.dumps({"loaded_model": "local/bf16"}).encode())
        with mock.patch.object(launch.urllib.request, "urlopen", return_value=context) as get:
            self.assertEqual(launch.server_model(), "local/bf16")
        self.assertTrue(get.call_args.args[0].endswith("/health"))

    def test_server_context_limit_uses_effective_health_value(self):
        context = mock.MagicMock()
        context.__enter__.return_value = io.BytesIO(json.dumps({
            "loaded_context_size": 65536,
            "effective_context_limit": 32768,
        }).encode())
        with mock.patch.object(launch.urllib.request, "urlopen", return_value=context):
            self.assertEqual(launch.server_context_limit(), 32768)

    def test_claude_uses_bare_mode_by_default(self):
        args = launch.command_args(
            "claude", "/bin/claude", launch.TOOLS["claude"],
            "local/model", "http://127.0.0.1:8092", ["--verbose"])
        self.assertEqual(args, ["/bin/claude", "--bare", "--verbose"])

    def test_explicit_bare_is_not_duplicated(self):
        args = launch.command_args(
            "claude", "/bin/claude", launch.TOOLS["claude"],
            "local/model", "http://127.0.0.1:8092", ["--bare", "-p", "hi"])
        self.assertEqual(args.count("--bare"), 1)

    def test_full_claude_mode_can_be_requested(self):
        args = launch.command_args(
            "claude", "/bin/claude", launch.TOOLS["claude"],
            "local/model", "http://127.0.0.1:8092", [], claude_full=True)
        self.assertEqual(args, ["/bin/claude"])

    def test_opencode_uses_pure_mode_by_default(self):
        args = launch.command_args(
            "opencode", "/bin/opencode", launch.TOOLS["opencode"],
            "local/model", "http://127.0.0.1:8092/v1", [])
        self.assertEqual(args, ["/bin/opencode", "--pure"])

    def test_opencode_provider_is_local_and_uses_a_stable_alias(self):
        config = launch.opencode_config(
            "org/model", "http://127.0.0.1:8092/v1", context_limit=65536)
        self.assertEqual(config["model"], "mira/local")
        provider = config["provider"]["mira"]
        self.assertEqual(provider["options"]["baseURL"], "http://127.0.0.1:8092/v1")
        self.assertEqual(provider["models"]["local"]["name"], "org/model")
        self.assertEqual(provider["models"]["local"]["limit"]["context"], 65536)

    def test_opencode_main_executes_with_isolated_local_configuration(self):
        captured = {}

        def capture_exec(binary, args, env):
            captured.update(binary=binary, args=args, env=env)
            raise RuntimeError("exec intercepted")

        output = io.StringIO()
        with mock.patch.object(launch, "server_model", return_value="org/model"), \
                mock.patch.object(launch, "server_context_limit", return_value=65536), \
                mock.patch.object(launch, "ensure_bridge", return_value=True), \
                mock.patch.object(launch.shutil, "which", return_value="/bin/opencode"), \
                mock.patch.object(launch.os, "execve", side_effect=capture_exec), \
                mock.patch("sys.stdout", output):
            with self.assertRaisesRegex(RuntimeError, "exec intercepted"):
                launch.main(["opencode", "run", "hello"])

        config = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["model"], "mira/local")
        self.assertEqual(config["provider"]["mira"]["models"]["local"]["limit"]["context"],
                         65536)
        self.assertEqual(
            config["provider"]["mira"]["options"]["baseURL"],
            "http://127.0.0.1:8092/v1")
        self.assertEqual(captured["env"]["OPENCODE_AUTH_CONTENT"], "{}")
        self.assertTrue(captured["env"]["XDG_CONFIG_HOME"].endswith("/argus/opencode/config"))
        self.assertEqual(captured["env"]["OPENCODE_CONFIG_DIR"],
                         captured["env"]["XDG_CONFIG_HOME"])
        self.assertEqual(captured["args"],
                         ["/bin/opencode", "--pure", "run", "hello"])

    def test_full_opencode_keeps_local_provider_without_isolating_user_config(self):
        captured = {}

        def capture_exec(binary, args, env):
            captured.update(args=args, env=env)
            raise RuntimeError("exec intercepted")

        with mock.patch.dict(os.environ, {"ARGUS_OPENCODE_FULL": "1"}, clear=True), \
                mock.patch.object(launch, "server_model", return_value="org/model"), \
                mock.patch.object(launch, "server_context_limit", return_value=65536), \
                mock.patch.object(launch, "ensure_bridge", return_value=True), \
                mock.patch.object(launch.shutil, "which", return_value="/bin/opencode"), \
                mock.patch.object(launch.os, "execve", side_effect=capture_exec), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "exec intercepted"):
                launch.main(["opencode", "run", "hello"])

        config = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(config["model"], "mira/local")
        self.assertNotIn("XDG_CONFIG_HOME", captured["env"])
        self.assertNotIn("--pure", captured["args"])

    def test_aider_receives_bridge_base_url(self):
        args = launch.command_args(
            "aider", "/bin/aider", launch.TOOLS["aider"],
            "local/model", "http://127.0.0.1:8092/v1", ["--yes"])
        self.assertIn("http://127.0.0.1:8092/v1", args)
        self.assertEqual(args[-1], "--yes")


if __name__ == "__main__":
    unittest.main()
