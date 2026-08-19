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
        self.attrs_by_id = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
            self.attrs_by_id[attrs["id"]] = attrs
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

    def test_settings_token_range_matches_backend_validation(self):
        parser = PageParser()
        parser.feed((ROOT / "share" / "settings.html").read_text())
        control = parser.attrs_by_id["maxTokens"]
        self.assertEqual(control["min"], "128")
        self.assertEqual(control["max"], "131072")

    def test_iteration_controls_do_not_depend_on_unhandled_webkit_dialogs(self):
        page = (ROOT / "share" / "ui.html").read_text()
        self.assertIn('id="iterationBtn"', page)
        self.assertIn("再次点击确认", page)
        self.assertIn("批准发布 GitLab MR", page)
        self.assertIn("打开 GitLab MR", page)
        self.assertIn("feedback-input", page)
        self.assertNotIn("prompt(", page)
        self.assertNotIn("confirm(", page)

    def test_model_picker_preserves_scroll_and_rejects_stale_searches(self):
        page = (ROOT / "share" / "ui.html").read_text()
        self.assertIn("catalogChanged", page)
        self.assertIn("renderList($(\"find\").value, true)", page)
        self.assertIn("generation !== searchGeneration", page)
        self.assertIn("hubSearchCache", page)
        self.assertIn("overscroll-behavior: contain", page)

    def test_composer_shrinks_without_horizontal_page_overflow(self):
        page = (ROOT / "share" / "ui.html").read_text()
        self.assertIn("overflow: hidden", page)
        self.assertIn("#picker { position: relative; margin-left: auto; min-width: 0", page)
        self.assertIn("flex: 1 1 100px", page)
        self.assertIn("#composer { padding: 12px 22px 18px", page)
        self.assertIn("overflow: hidden", page)
        self.assertNotIn('<span class="agent-mode">', page)

    def test_model_picker_distinguishes_selected_local_and_remote_models(self):
        page = (ROOT / "share" / "ui.html").read_text()
        self.assertIn('v.id === model ? "✓" : (v.downloaded ? "本地" : "↓")', page)
        self.assertIn("replace(/\\s*\\([^)]*\\)\\s*$/, \"\")", page)
        self.assertNotIn('mark.textContent = v.downloaded ? "✓"', page)


if __name__ == "__main__":
    unittest.main()
