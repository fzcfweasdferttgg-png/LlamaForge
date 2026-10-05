import conftest_paths  # noqa: F401
import unittest

from embers import view, wikifs

SHA = "0123456789ab"
SHA2 = "ba9876543210"


def page_md(items_md, summary="A summary."):
    text = f"# Project Atlas\n\n{summary}\n\n"
    return wikifs.replace_region(text, "items", items_md)


class ItemsTest(unittest.TestCase):
    def items(self):
        return [
            {"id": "it-aaaa1111", "text": "Send the [draft] to Sam", "owner": "Sam", "due": "Friday",
             "status": "open", "verified": True},
            {"id": "it-bbbb2222", "text": "Maybe call <Bob>", "status": "open", "verified": False},
            {"id": "it-cccc3333", "text": "Booked the venue", "status": "closed", "verified": True},
        ]

    def evidence(self):
        return {"it-aaaa1111": [{"raw": SHA, "quote": 'Sam said "send the draft" by Friday'}],
                "it-cccc3333": [{"raw": SHA2, "quote": "venue <booked>"}]}

    def render(self):
        return view.render(page_md(wikifs.render_items(self.items(), self.evidence())))

    def test_items_get_classes_ids_and_boxes(self):
        html = self.render()
        self.assertIn('class="ef-item" data-item="it-aaaa1111"', html)
        self.assertIn('class="ef-item ef-unverified" data-item="it-bbbb2222"', html)
        self.assertIn('class="ef-item ef-closed" data-item="it-cccc3333"', html)
        self.assertNotIn("^it-", html)                 # Obsidian block ids are not shown
        self.assertNotIn("*(unverified)*", html)
        self.assertNotIn("(unverified)</em>", html)

    def test_region_markers_are_not_shown(self):
        html = self.render()
        self.assertNotIn("ember:items", html)
        self.assertIn("A summary.", html)

    def test_escapes_read_as_plain_text(self):
        html = self.render()
        self.assertIn("Send the [draft] to Sam", html)
        self.assertIn("Maybe call &lt;Bob&gt;", html)   # shown, not a tag
        self.assertNotIn("\\[", html)
        self.assertNotIn("&amp;lt;", html)

    def test_footnotes_become_quote_buttons_and_sources(self):
        html = self.render()
        self.assertIn(f'data-raw="{SHA}"', html)
        self.assertIn("title=\"Sam said &#x27;send the draft&#x27; by Friday\"", html)
        self.assertIn('<details class="ef-sources">', html)
        self.assertIn("venue &lt;booked&gt;", html)
        self.assertNotIn("[^r-", html)
        self.assertNotIn("raw/" + SHA + ".txt](", html)

    def test_footnotes_are_numbered_in_order(self):
        html = self.render()
        self.assertLess(html.index(f'data-raw="{SHA}"'), html.index(f'data-raw="{SHA2}"'))
        self.assertIn(">1</button>", html)
        self.assertIn(">2</button>", html)


class BriefTest(unittest.TestCase):
    def test_page_links_become_in_app_links(self):
        md = ("# Morning: Monday\n\nTwo things.\n\n## Today\n\n"
              f"- Send the draft ([projects/atlas](../pages/projects/atlas.md#^it-aaaa1111)) [^r-{SHA}]\n\n"
              f'[^r-{SHA}]: [raw/{SHA}.txt](../raw/{SHA}.txt) "send the draft"\n')
        html = view.render(md)
        self.assertIn('class="ef-link" data-page="projects/atlas" data-item="it-aaaa1111"', html)
        self.assertNotIn("../pages/", html)
        self.assertIn(f'data-raw="{SHA}"', html)

    def test_index_links(self):
        html = view.render("## projects\n- [Atlas](pages/projects/atlas.md): the plan (2 open)\n")
        self.assertIn('class="ef-link" data-page="projects/atlas"', html)

    def test_raw_links(self):
        html = view.render(f"see [raw/{SHA}.txt](../../raw/{SHA}.txt)")
        self.assertIn(f'class="ef-rawlink" data-raw="{SHA}"', html)


class HostileTest(unittest.TestCase):
    def test_html_and_images_are_inert(self):
        html = view.render("<script>alert(1)</script>\n\n![x](https://evil.example/p.png)\n")
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)

    def test_placeholder_characters_in_input_are_dropped(self):
        html = view.render("text \ue000F0\ue001 and \ue000E5b\ue001 \ue000I0\ue001\n")
        self.assertNotIn("<button", html)
        self.assertNotIn("\ue000", html)
        self.assertNotIn("\ue001", html)

    def test_forged_footnote_with_quote_cannot_break_out(self):
        md = (f'x [^r-{SHA}]\n\n[^r-{SHA}]: [raw/{SHA}.txt](../raw/{SHA}.txt) "a\\" onmouseover=\\"x"\n')
        html = view.render(md)
        self.assertNotIn('" onmouseover', html)

    def test_escaped_footnote_syntax_stays_text(self):
        html = view.render(f"look \\[\\^r-{SHA}\\] here\n")
        self.assertNotIn("<button", html)
        self.assertIn(f"[^r-{SHA}]", html)

    def test_javascript_links_neutralised(self):
        self.assertNotIn("javascript:", view.render("[x](javascript:alert(1))"))


class PlainTest(unittest.TestCase):
    def test_plain_text_unescape(self):
        self.assertEqual(view.plain("Call \\[Bob\\] &lt;now&gt; \\#1"), "Call [Bob] <now> #1")

    def test_empty(self):
        self.assertEqual(view.render(""), "")


if __name__ == "__main__":
    unittest.main()
