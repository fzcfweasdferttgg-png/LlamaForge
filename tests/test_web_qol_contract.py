import os, unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class WebQolContractTest(unittest.TestCase):
    def test_scan_ui_exposes_persistent_folder_list(self):
        src = read("web/js/setup.js")
        self.assertIn('id="scan-roots"', src)
        self.assertIn('model_dirs', src)
        self.assertIn('/api/config', src)

    def test_llama_editor_has_registry_only_unregister_action(self):
        src = read("web/js/models.js")
        self.assertIn('data-act="unregister"', src)
        self.assertIn('/api/models/unregister', src)
        self.assertIn('does not delete', src)

    def test_closed_logs_are_not_polled(self):
        src = read("web/js/main.js")
        self.assertIn('router-log-details', src)
        self.assertIn('.open', src)
        self.assertNotIn('models.refreshRouterLog();\nmodels.refreshVllmLog();', src)

    def test_no_blend_mode_overlays(self):
        # A fixed full-viewport layer with mix-blend-mode repaints the whole
        # page on every scroll. The CRT scanline overlay is gone; keep any
        # future overlay (and everything else) off blend modes.
        src = read("web/index.html")
        self.assertNotIn("mix-blend-mode", src)
        if "body::after" in src:
            rule = src.split("body::after", 1)[1].split("}", 1)[0]
            self.assertNotIn("mix-blend-mode", rule)

    def test_bootstrap_scripts_prompt_for_existing_checkout(self):
        for rel in ("bootstrap.ps1", "bootstrap.sh"):
            src = read(rel)
            self.assertIn("LLAMAFORGE_LLAMA_SRC", src, rel)
            self.assertIn("bootstrap_config.py", src, rel)


if __name__ == "__main__":
    unittest.main()


class IndexHeadTest(unittest.TestCase):
    """Text inside <head> makes the parser open <body> early, so every later
    <link>/<style> lands in body as a grid item and the layout falls apart."""

    def test_head_holds_no_stray_text(self):
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__(); self.where = []; self.stray = []
            def handle_starttag(self, tag, attrs):
                if tag in ("head", "style", "script", "title"): self.where.append(tag)
            def handle_endtag(self, tag):
                if self.where and self.where[-1] == tag: self.where.pop()
            def handle_data(self, data):
                if self.where == ["head"] and data.strip(): self.stray.append(data.strip()[:40])

        p = P(); p.feed(read("web/index.html"))
        self.assertEqual(p.stray, [])

    def test_no_font_cdn(self):
        # a local panel must render offline and tell no third party it was opened
        src = read("web/index.html")
        for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
            self.assertNotIn(host, src)
        self.assertIn('/web/css/fonts.css', src)
        css = read("web/css/fonts.css")
        for name in __import__("re").findall(r"/web/fonts/([\w.-]+\.woff2)", css):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "web", "fonts", name)), name)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "web", "fonts", "OFL.txt")))
