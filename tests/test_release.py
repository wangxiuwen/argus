from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_status_item_symbols_resolve_at_runtime(self):
        """An invalid SF Symbol renders the tray as an invisible empty slot —
        a string assertion cannot catch that, so ask AppKit for real."""
        swift = (ROOT / "Fermi" / "main.swift").read_text()
        names = sorted(set(re.findall(r'systemSymbolName: "([a-zA-Z.]+)"', swift))
                       | set(re.findall(r'symbol: "([a-zA-Z.]+)"', swift)))
        self.assertTrue(names, "no tray symbols found to check")
        probe = ["import AppKit"]
        for name in names:
            probe.append(
                f'print("{name}:\\(NSImage(systemSymbolName: "{name}", '
                'accessibilityDescription: nil) != nil)")')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "probe.swift"
            path.write_text("\n".join(probe) + "\n")
            run = subprocess.run(["swift", str(path)], capture_output=True,
                                 text=True, timeout=180)
        self.assertEqual(run.returncode, 0, run.stderr)
        bad = [line for line in run.stdout.splitlines() if line.endswith("false")]
        self.assertEqual([], bad, f"SF Symbols that do not resolve: {bad}")

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
        self.assertEqual(info["CFBundleName"], "Fermi")
        self.assertEqual(info["CFBundleExecutable"], "Fermi")
        self.assertEqual(info["CFBundleIconFile"], "Fermi")
        self.assertTrue((ROOT / "assets" / "Fermi.icns").is_file())
        self.assertTrue((ROOT / "share" / "FermiIcon.png").is_file())
        swift = (ROOT / "Fermi" / "main.swift").read_text()
        self.assertIn("setActivationPolicy(.regular)", swift)
        self.assertIn("applicationShouldHandleReopen", swift)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("Contents/Resources/Fermi.icns", makefile)

    def test_branding_uses_fermi_orbital_mark(self):
        swift = (ROOT / "Fermi" / "main.swift").read_text()
        page = (ROOT / "share" / "ui.html").read_text()
        readme = (ROOT / "README.md").read_text()
        self.assertIn('symbol: String = "atom"', swift)
        self.assertNotIn('systemSymbolName: "bird', swift)
        self.assertNotIn('systemSymbolName: "eye', swift)
        self.assertIn("hero-mark", page)
        self.assertIn('<img src="/mira/icon.png" alt="">', page)
        self.assertNotIn("🦜", page)
        self.assertNotIn("#f1ecff", page)
        self.assertNotIn("#55b9ee", page)
        self.assertNotIn("#fb7185", page)
        self.assertIn("--bg: #ffffff", page)
        self.assertNotIn("hero-eye", page)
        self.assertNotIn("brand-eye", page)
        self.assertIn("fermi", readme.lower())

        backend = (ROOT / "share" / "ui.py").read_text()
        self.assertIn('route == "/mira/icon.png"', backend)
        self.assertIn('self._serve_png("FermiIcon.png")', backend)

    def test_close_hides_dock_but_minimize_keeps_it(self):
        swift = (ROOT / "Fermi" / "main.swift").read_text()
        self.assertIn("NSWindowDelegate", swift)
        self.assertIn("win.delegate = self", swift)
        self.assertIn("func windowWillClose", swift)
        self.assertIn("setActivationPolicy(.accessory)", swift)
        self.assertIn("func openChat", swift)
        self.assertIn("setActivationPolicy(.regular)", swift)
        self.assertNotIn("windowDidMiniaturize", swift)

    def test_release_notes_heredoc_stays_inside_yaml_run_block(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()
        notes = workflow.split("--notes \"$(cat <<'EOF'\n", 1)[1].split("\n          EOF", 1)[0]
        for line in notes.splitlines():
            if line:
                self.assertTrue(line.startswith("          "), repr(line))


if __name__ == "__main__":
    unittest.main()
