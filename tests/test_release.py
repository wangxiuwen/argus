from pathlib import Path
import plistlib
import re
import unittest


ROOT = Path(__file__).parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_source_bundle_version_matches_makefile_default(self):
        makefile = (ROOT / "Makefile").read_text()
        version = re.search(r"^VERSION\s*=\s*(\S+)", makefile, re.MULTILINE).group(1)
        with (ROOT / "Info.plist").open("rb") as f:
            info = plistlib.load(f)
        self.assertEqual(info["CFBundleShortVersionString"], version)
        self.assertEqual(info["CFBundleVersion"], version)

    def test_build_verifies_arm64_before_packaging(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("lipo $(OUT) -verify_arch arm64", makefile)

    def test_desktop_app_has_dock_identity_and_packaged_icon(self):
        with (ROOT / "Info.plist").open("rb") as f:
            info = plistlib.load(f)
        self.assertNotIn("LSUIElement", info)
        self.assertEqual(info["CFBundleIconFile"], "Mira")
        self.assertTrue((ROOT / "assets" / "Mira.icns").is_file())
        swift = (ROOT / "Mira" / "main.swift").read_text()
        self.assertIn("setActivationPolicy(.regular)", swift)
        self.assertIn("applicationShouldHandleReopen", swift)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("Contents/Resources/Mira.icns", makefile)

    def test_release_notes_heredoc_stays_inside_yaml_run_block(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        notes = workflow.split("--notes \"$(cat <<'EOF'\n", 1)[1].split("\n          EOF", 1)[0]
        for line in notes.splitlines():
            if line:
                self.assertTrue(line.startswith("          "), repr(line))


if __name__ == "__main__":
    unittest.main()
