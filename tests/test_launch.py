import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "argus_launch", Path(__file__).parents[1] / "share" / "launch.py")
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)


class CommandArgsTests(unittest.TestCase):
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

    def test_aider_receives_bridge_base_url(self):
        args = launch.command_args(
            "aider", "/bin/aider", launch.TOOLS["aider"],
            "local/model", "http://127.0.0.1:8092/v1", ["--yes"])
        self.assertIn("http://127.0.0.1:8092/v1", args)
        self.assertEqual(args[-1], "--yes")


if __name__ == "__main__":
    unittest.main()
