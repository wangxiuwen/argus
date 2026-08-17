from html.parser import HTMLParser
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "script" and "src" not in attrs:
            self._script = []

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None


class HtmlTests(unittest.TestCase):
    def test_pages_have_unique_ids_and_valid_javascript(self):
        for name in ("ui.html", "settings.html"):
            with self.subTest(page=name):
                parser = PageParser()
                parser.feed((ROOT / "share" / name).read_text())
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                result = subprocess.run(
                    ["node", "--check", "-"], input="\n".join(parser.scripts),
                    text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
