import conftest_paths  # noqa: F401
import os, tempfile, unittest
import docs


class TestDocsRoutesModel(unittest.TestCase):
    """Route handlers are thin wrappers over docs.*; assert the data layer the
    routes return, plus image path-safety, without spinning an HTTP server."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "img"))
        with open(os.path.join(self.dir, "intro.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Intro\nsection: getting-started\norder: 1\n---\n# Hi")
        open(os.path.join(self.dir, "img", "a.png"), "wb").write(b"\x89PNG")
        self._orig = docs.content_dir
        docs.content_dir = lambda: self.dir

    def tearDown(self):
        docs.content_dir = self._orig

    def test_manifest_shape(self):
        m = docs.manifest()
        self.assertEqual(m["sections"][0]["id"], "getting-started")
        self.assertEqual(m["sections"][0]["pages"][0]["slug"], "intro")

    def test_section_titles_are_written_not_derived(self):
        # "whats-new".title() would give "Whats New", "faq" would give "Faq"
        self.assertEqual(docs.section_title("whats-new"), "What's New")
        self.assertEqual(docs.section_title("faq"), "FAQ")
        self.assertEqual(docs.section_title("getting-started"), "Getting Started")
        self.assertEqual(docs.section_title("some-new-section"), "Some New Section")
        self.assertEqual(docs.manifest()["sections"][0]["title"], "Getting Started")

    def test_search_index_covers_body_text(self):
        with open(os.path.join(self.dir, "flags.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: Flags\nsection: guides\n---\n# Flags\nSet `--ctx-size` to **8192**.\n")
        hit = next(p for p in docs.search_index() if p["slug"] == "flags")
        self.assertIn("--ctx-size", hit["text"])
        self.assertNotIn("**", hit["text"])          # markup is stripped, words stay
        self.assertEqual(hit["text"], hit["text"].lower())

    def test_page_and_missing(self):
        self.assertEqual(docs.page("intro")["title"], "Intro")
        self.assertIsNone(docs.page("ghost"))

    def test_img_safe(self):
        self.assertTrue(os.path.exists(docs._safe_img("a.png")))
        with self.assertRaises(ValueError):
            docs._safe_img("../secret")


if __name__ == "__main__":
    unittest.main()
