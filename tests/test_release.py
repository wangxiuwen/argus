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


if __name__ == "__main__":
    unittest.main()
