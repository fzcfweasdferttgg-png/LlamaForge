import conftest_paths  # noqa: F401
import unittest
import docs


class TestRenderInline(unittest.TestCase):
    def test_heading_with_id(self):
        self.assertEqual(docs.render("# Hello World"),
                         '<h1 id="hello-world">Hello World</h1>')

    def test_paragraph_and_spans(self):
        html = docs.render("A **bold** and *italic* and `code` and [t](u).")
        self.assertEqual(
            html,
            '<p>A <strong>bold</strong> and <em>italic</em> and '
            '<code>code</code> and <a href="u">t</a>.</p>')

    def test_image(self):
        self.assertEqual(docs.render("![alt](docs/img/x.png)"),
                         '<p><img alt="alt" src="docs/img/x.png"></p>')

    def test_images_off_renders_a_link_and_fetches_nothing(self):
        md = "\n\n".join([
            "![pixel](https://x.example/p.png)",
            "- ![](https://x.example/q.png) in a list",
            "> ![q](https://x.example/r.png)",
            "| a |\n|---|\n| ![t](https://x.example/s.png) |"])
        html = docs.render(md, images=False)
        self.assertNotIn("<img", html)
        self.assertIn('<a href="https://x.example/p.png">pixel</a>', html)
        self.assertIn('<a href="https://x.example/q.png">image</a>', html)
        self.assertIn('<a href="https://x.example/r.png">q</a>', html)
        self.assertIn('<a href="https://x.example/s.png">t</a>', html)

    def test_images_off_still_neutralises_urls(self):
        html = docs.render("![x](javascript:alert(1))", images=False)
        self.assertNotIn("javascript:", html)

    def test_hr(self):
        self.assertEqual(docs.render("---"), "<hr>")

    def test_raw_html_is_escaped(self):
        self.assertEqual(docs.render("<script>x</script>"),
                         "<p>&lt;script&gt;x&lt;/script&gt;</p>")

    def test_code_span_content_escaped(self):
        self.assertEqual(docs.render("`<b>`"), "<p><code>&lt;b&gt;</code></p>")

    def test_quote_in_image_alt_escaped(self):
        html = docs.render('![x"onerror="alert(1)](u)')
        self.assertNotIn('"onerror="', html)      # quote cannot break out of the attribute

    def test_quote_in_link_escaped(self):
        html = docs.render('[t"x](u)')
        self.assertNotIn('t"x', html)

    def test_javascript_url_neutralized(self):
        out = docs.render("[x](javascript:alert(1))")
        self.assertNotIn("javascript:", out)
        self.assertIn('href="#"', out)

    def test_relative_and_http_urls_preserved(self):
        self.assertIn('href="install.md"', docs.render("[a](install.md)"))
        self.assertIn('src="docs/img/x.png"', docs.render("![a](docs/img/x.png)"))
        self.assertIn('href="https://x.io"', docs.render("[a](https://x.io)"))


class TestRenderBlocks(unittest.TestCase):
    def test_fenced_code_with_lang(self):
        self.assertEqual(docs.render("```bash\nls -la\n```"),
                         '<pre><code class="lang-bash">ls -la</code></pre>')

    def test_fenced_code_escaped(self):
        self.assertEqual(docs.render("```\n<a>\n```"),
                         "<pre><code>&lt;a&gt;</code></pre>")

    def test_unordered_list(self):
        self.assertEqual(docs.render("- one\n- two"),
                         "<ul><li>one</li><li>two</li></ul>")

    def test_ordered_list(self):
        self.assertEqual(docs.render("1. a\n2. b"),
                         "<ol><li>a</li><li>b</li></ol>")

    def test_nested_list(self):
        self.assertEqual(docs.render("- a\n  - b"),
                         "<ul><li>a<ul><li>b</li></ul></li></ul>")

    def test_table(self):
        self.assertEqual(
            docs.render("| A | B |\n| --- | --- |\n| 1 | 2 |"),
            "<table><thead><tr><th>A</th><th>B</th></tr></thead>"
            "<tbody><tr><td>1</td><td>2</td></tr></tbody></table>")

    def test_blockquote(self):
        self.assertEqual(docs.render("> quoted"),
                         "<blockquote><p>quoted</p></blockquote>")

    def test_admonition(self):
        self.assertEqual(docs.render("> [!NOTE] heads up"),
                         '<div class="admon admon-note"><p>heads up</p></div>')


import os, tempfile


class TestContentModel(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "img"))
        self._orig = docs.content_dir
        docs.content_dir = lambda: self.dir

    def tearDown(self):
        docs.content_dir = self._orig

    def _w(self, name, text):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_frontmatter(self):
        meta, body = docs.parse_frontmatter(
            "---\ntitle: Hi\nsection: reference\norder: 2\n---\n# Body")
        self.assertEqual(meta["title"], "Hi")
        self.assertEqual(meta["section"], "reference")
        self.assertEqual(body.strip(), "# Body")

    def test_list_pages_ordering_and_skip_underscore(self):
        self._w("b.md", "---\ntitle: B\nsection: guides\norder: 2\n---\nx")
        self._w("a.md", "---\ntitle: A\nsection: getting-started\norder: 1\n---\nx")
        self._w("_style.md", "---\ntitle: S\nsection: guides\norder: 0\n---\nx")
        slugs = [p["slug"] for p in docs.list_pages()]
        self.assertEqual(slugs, ["a", "b"])          # getting-started before guides; _ skipped

    def test_page_toc_and_missing(self):
        self._w("p.md", "---\ntitle: P\nsection: guides\norder: 1\n---\n# H1\n## H2")
        pg = docs.page("p")
        self.assertEqual(pg["title"], "P")
        self.assertEqual([t["text"] for t in pg["toc"]], ["H1", "H2"])
        self.assertIsNone(docs.page("nope"))

    def test_safe_img_rejects_traversal(self):
        for bad in ("../x", "a/b", "a\\b", "/etc/x"):
            with self.assertRaises(ValueError):
                docs._safe_img(bad)
        self.assertTrue(docs._safe_img("ok.png").endswith(os.path.join("img", "ok.png")))

    def test_safe_img_rejects_windows_drive_and_ads(self):
        for bad in ("C:evil", "c:\\x", "a:b", "x:stream"):
            with self.assertRaises(ValueError):
                docs._safe_img(bad)

    def test_non_numeric_order_does_not_crash(self):
        self._w("bad.md", "---\ntitle: Bad\nsection: guides\norder: two\n---\nx")
        self._w("ok.md", "---\ntitle: OK\nsection: guides\norder: 1\n---\nx")
        slugs = [p["slug"] for p in docs.list_pages()]   # must not raise
        self.assertIn("bad", slugs)
        self.assertIn("ok", slugs)
        bad = next(p for p in docs.list_pages() if p["slug"] == "bad")
        self.assertEqual(bad["order"], 0)                # malformed order falls back to 0


if __name__ == "__main__":
    unittest.main()
