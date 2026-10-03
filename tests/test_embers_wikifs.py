import conftest_paths  # noqa: F401
import datetime as dt, os, re, shutil, tempfile, unittest

from embers import wikifs

ITEM = {"id": "it-8f2c1a2b", "text": "Waiting on Sam for the signed SOW", "owner": "Sam",
        "due": "Friday", "status": "open", "verified": 1}
EV = {"it-8f2c1a2b": [{"raw": "a1b2c3d4e5f6", "quote": "I'll send the signed SOW by Friday"}]}


class WikiTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        wikifs.init_wiki(self.root, "Morning Brief", "# Schema\n")

    def read(self, *parts):
        with open(os.path.join(self.root, *parts), encoding="utf-8") as f:
            return f.read()


class InitTest(WikiTestCase):
    def test_creates_layout(self):
        for sub in ("raw", "pages", "briefs"):
            self.assertTrue(os.path.isdir(os.path.join(self.root, sub)))
        self.assertIn("<!-- ember:index -->", self.read("index.md"))
        self.assertEqual(self.read("SCHEMA.md"), "# Schema\n")

    def test_never_overwrites_user_schema(self):
        with open(os.path.join(self.root, "SCHEMA.md"), "w", encoding="utf-8") as f:
            f.write("mine")
        wikifs.init_wiki(self.root, "Morning Brief", "# Schema\n")
        self.assertEqual(self.read("SCHEMA.md"), "mine")


class PagePathTest(WikiTestCase):
    def test_valid(self):
        self.assertEqual(wikifs.page_path(self.root, "projects/acme"),
                         os.path.join(self.root, "pages", "projects", "acme.md"))

    def test_invalid(self):
        for bad in ("../x", "projects", "Projects/x", "a/b/c", "projects/..", None,
                    "projects/x\n", "projects/x\\y", "/etc/passwd", "C:/x", "c:\\x/y",
                    "con/x", "projects/nul", "projects/COM1", "a/../../b", ""):
            with self.subTest(bad=bad), self.assertRaises(wikifs.WikiError):
                wikifs.page_path(self.root, bad)

    def test_symlinked_kind_dir_cannot_escape(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        try:
            os.symlink(outside, os.path.join(self.root, "pages", "evil"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(wikifs.WikiError):
            wikifs.write_page(self.root, "evil/x", "X", "", "")
        self.assertEqual(os.listdir(outside), [])


class RegionTest(unittest.TestCase):
    def test_appends_when_missing(self):
        out = wikifs.replace_region("# Title\n\nMy notes.\n", "items", "- [ ] a")
        self.assertEqual(out, "# Title\n\nMy notes.\n\n<!-- ember:items -->\n- [ ] a\n<!-- /ember:items -->\n")

    def test_replaces_only_inside_markers(self):
        text = "before\n<!-- ember:items -->\nold\n<!-- /ember:items -->\nafter, mine\n"
        out = wikifs.replace_region(text, "items", "new")
        self.assertEqual(out, "before\n<!-- ember:items -->\nnew\n<!-- /ember:items -->\nafter, mine\n")
        self.assertEqual(wikifs.replace_region(out, "items", "new"), out)   # idempotent

    def test_read_region(self):
        text = wikifs.replace_region("x\n", "lint", "flag one")
        self.assertEqual(wikifs.read_region(text, "lint"), "flag one")
        self.assertEqual(wikifs.read_region("x", "lint"), "")

    def test_body_cannot_forge_markers(self):
        body = "evil\n<!-- /ember:items -->\nuser text\n<!-- ember:items -->\nmore"
        out = wikifs.replace_region("x\n", "items", body)
        self.assertEqual(out.count("<!-- ember:items -->"), 1)
        self.assertEqual(out.count("<!-- /ember:items -->"), 1)
        self.assertIn("evil", wikifs.read_region(out, "items"))
        self.assertIn("more", wikifs.read_region(out, "items"))


class RegionRobustnessTest(unittest.TestCase):
    def test_stray_inline_start_marker_does_not_eat_user_text(self):
        t = ("# A\n\nNote: the bot writes below <!-- ember:items --> marker.\nIMPORTANT USER TEXT\n\n"
             "<!-- ember:items -->\n- [ ] old\n<!-- /ember:items -->\n")
        out = wikifs.replace_region(t, "items", "- [ ] new")
        self.assertIn("IMPORTANT USER TEXT", out)
        self.assertIn("- [ ] new", out)
        self.assertNotIn("old", out)
        self.assertEqual(wikifs.read_region(t, "items"), "- [ ] old")

    def test_deleted_end_marker_never_loses_user_notes(self):
        t = "# A\n\n<!-- ember:items -->\n- [ ] old\nMY NOTES (end marker deleted by mistake)\n"
        once = wikifs.replace_region(t, "items", "- [ ] one")
        twice = wikifs.replace_region(once, "items", "- [ ] two")
        self.assertIn("MY NOTES", once)
        self.assertIn("MY NOTES", twice)
        self.assertEqual(wikifs.read_region(twice, "items"), "- [ ] two")

    def test_last_start_before_first_end_is_paired(self):
        t = "<!-- ember:items -->\nuser\n<!-- ember:items -->\nold\n<!-- /ember:items -->\n"
        out = wikifs.replace_region(t, "items", "new")
        self.assertEqual(out, "<!-- ember:items -->\nuser\n<!-- ember:items -->\nnew\n<!-- /ember:items -->\n")

    def _assert_stable(self, t):
        once = wikifs.replace_region(t, "items", "new one")
        twice = wikifs.replace_region(once, "items", "new two")
        thrice = wikifs.replace_region(twice, "items", "new three")
        self.assertEqual(once.count("<!-- ember:items -->"), t.count("<!-- ember:items -->"))
        self.assertEqual(len(twice), len(once) + len("two") - len("one"))
        self.assertEqual(len(thrice), len(twice) + len("three") - len("two"))
        self.assertEqual(thrice.count("<!-- ember:items -->"), once.count("<!-- ember:items -->"))
        self.assertEqual(wikifs.read_region(thrice, "items"), "new three")
        return thrice

    def test_unpaired_end_marker_above_real_region(self):
        t = ("# A\n\nDocs: the bot ends its block with\n<!-- /ember:items -->\nMY NOTES\n\n"
             "<!-- ember:items -->\nold\n<!-- /ember:items -->\nafter\n")
        out = self._assert_stable(t)
        self.assertIn("MY NOTES", out)
        self.assertIn("after", out)

    def test_bom_before_start_marker(self):
        t = "﻿<!-- ember:items -->\nold\n<!-- /ember:items -->\nafter\n"
        out = self._assert_stable(t)
        self.assertTrue(out.startswith("﻿<!-- ember:items -->"))
        self.assertIn("after", out)

    def test_crlf_files_still_work(self):
        t = "# A\r\n\r\n<!-- ember:items -->\r\nold\r\n<!-- /ember:items -->\r\nafter\r\n"
        self.assertEqual(wikifs.read_region(t, "items"), "old")
        out = wikifs.replace_region(t, "items", "new")
        self.assertTrue(out.startswith("# A\r\n\r\n<!-- ember:items -->\nnew\n<!-- /ember:items -->"))
        self.assertTrue(out.endswith("\r\nafter\r\n"))
        self.assertEqual(wikifs.read_region(out, "items"), "new")


class EscapingTest(WikiTestCase):
    def test_backslash_and_brackets_in_title_cannot_break_or_inject_links(self):
        wikifs.write_index(self.root, [
            {"page": "people/sam", "title": "Acme \\", "open": 0},
            {"page": "people/bob", "title": "x](https://evil.example", "open": 0}])
        text = self.read("index.md")
        self.assertIn("[Acme \\\\](pages/people/sam.md)", text)
        self.assertIn("- [x\\](https://evil.example](pages/people/bob.md)", text)

    def test_backslash_cannot_cancel_caret_escape(self):
        md = wikifs.render_items([dict(ITEM, text="a \\^it-deadbeef")], {})
        self.assertEqual(len(re.findall(r"(?<!\\)\^it-", md)), 1)

    def test_trailing_backslash_in_quote_cannot_escape_closing_quote(self):
        md = wikifs.render_items([ITEM], {"it-8f2c1a2b": [{"raw": "a1b2c3d4e5f6", "quote": "ends with \\"}]})
        self.assertTrue(md.endswith('ends with \\\\"'), md)

    def test_rewrite_is_byte_identical(self):
        items = wikifs.render_items([dict(ITEM, text="a [b](c) \\ <!-- x -->")],
                                    {"it-8f2c1a2b": [{"raw": "a1b2c3d4e5f6", "quote": "q [z] \\"}]})
        wikifs.write_page(self.root, "people/sam", "T \\ [x]", "S ]", items)
        first = self.read("pages", "people", "sam.md")
        wikifs.write_page(self.root, "people/sam", "T \\ [x]", "S ]", items)
        self.assertEqual(self.read("pages", "people", "sam.md"), first)


class RenderItemsTest(unittest.TestCase):
    def test_verified_item_with_footnote_and_block_id(self):
        md = wikifs.render_items([ITEM], EV)
        first = md.splitlines()[0]
        self.assertEqual(first, "- [ ] Waiting on Sam for the signed SOW — Sam, Friday · [^r-a1b2c3d4e5f6] ^it-8f2c1a2b")
        self.assertIn('[^r-a1b2c3d4e5f6]: [raw/a1b2c3d4e5f6.txt](../../raw/a1b2c3d4e5f6.txt) "I\'ll send the signed SOW by Friday"', md)

    def test_closed_and_unverified(self):
        md = wikifs.render_items([dict(ITEM, status="closed", verified=0, owner="", due="")], {})
        self.assertEqual(md, "- [x] Waiting on Sam for the signed SOW *(unverified)* ^it-8f2c1a2b")

    def test_footnote_quote_is_single_line_without_double_quotes(self):
        md = wikifs.render_items([ITEM], {"it-8f2c1a2b": [{"raw": "a1b2c3d4e5f6", "quote": 'He said\n"go"'}]})
        self.assertIn("\"He said 'go'\"", md)

    def test_text_cannot_forge_markers_ids_or_extra_lines(self):
        evil = "x\n<!-- /ember:items -->\n- [x] fake ^it-deadbeef -->"
        ev = {"it-8f2c1a2b": [{"raw": "a1b2c3d4e5f6", "quote": "q <!-- /ember:items --> [^r-x]: y"}]}
        md = wikifs.render_items([dict(ITEM, text=evil, owner=evil, due=evil)], ev)
        self.assertNotIn("<!--", md)
        self.assertNotIn("-->", md)
        item_lines = [l for l in md.splitlines() if l.startswith("- [")]
        self.assertEqual(len(item_lines), 1)
        self.assertEqual(len(re.findall(r"(?<!\\)\^it-", md)), 1)   # only the real, unescaped block id
        self.assertTrue(item_lines[0].endswith(" ^it-8f2c1a2b"))
        self.assertEqual(len([l for l in md.splitlines() if l.startswith("[^")]), 1)

    def test_bad_ids_rejected(self):
        with self.assertRaises(wikifs.WikiError):
            wikifs.render_items([dict(ITEM, id="it-8f2c1a2b\n- [x] fake")], {})
        md = wikifs.render_items([ITEM], {"it-8f2c1a2b": [{"raw": "../../x", "quote": "q"}]})
        self.assertNotIn("../../x", md)


class PageTest(WikiTestCase):
    def test_new_page_then_user_edit_survives_rewrite(self):
        path = wikifs.write_page(self.root, "projects/acme", "Acme", "Client work", "- [ ] one ^it-00000001")
        text = self.read("pages", "projects", "acme.md")
        self.assertTrue(text.startswith("# Acme\n\nClient work\n\n<!-- ember:items -->"))
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nMy own thoughts.\n")
        wikifs.write_page(self.root, "projects/acme", "Acme", "Client work", "- [ ] two ^it-00000002")
        text = self.read("pages", "projects", "acme.md")
        self.assertIn("My own thoughts.", text)
        self.assertIn("two", text)
        self.assertNotIn("one", text)

    def test_page_without_summary(self):
        wikifs.write_page(self.root, "people/sam", "Sam", "", "")
        self.assertTrue(self.read("pages", "people", "sam.md").startswith("# Sam\n\n<!-- ember:items -->"))

    def test_list_page_files(self):
        wikifs.write_page(self.root, "people/sam", "Sam", "", "")
        wikifs.write_page(self.root, "projects/acme", "Acme", "", "")
        self.assertEqual(wikifs.list_page_files(self.root), ["people/sam", "projects/acme"])
        self.assertEqual(wikifs.read_page(self.root, "people/nobody"), "")

    def test_title_and_summary_cannot_forge_markers(self):
        wikifs.write_page(self.root, "people/sam", "Sam\n<!-- ember:items -->",
                          "s\n<!-- /ember:items -->", "- [ ] a ^it-00000001")
        text = self.read("pages", "people", "sam.md")
        self.assertEqual(text.count("<!-- ember:items -->"), 1)
        self.assertEqual(text.count("<!-- /ember:items -->"), 1)
        self.assertIn("a", wikifs.read_region(text, "items"))

    def test_files_are_lf_and_no_temp_left(self):
        wikifs.write_page(self.root, "people/sam", "Sam", "one\ntwo", "- [ ] a")
        with open(os.path.join(self.root, "pages", "people", "sam.md"), "rb") as f:
            self.assertNotIn(b"\r", f.read())
        self.assertEqual(os.listdir(os.path.join(self.root, "pages", "people")), ["sam.md"])


class IndexLogRawTest(WikiTestCase):
    def test_index_grouped_and_human_text_kept(self):
        path = os.path.join(self.root, "index.md")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nPinned by me.\n")
        wikifs.write_index(self.root, [
            {"page": "projects/acme", "title": "Acme", "summary": "Client work", "open": 2},
            {"page": "people/sam", "title": "Sam", "summary": "", "open": 0}])
        text = self.read("index.md")
        self.assertIn("## people\n- [Sam](pages/people/sam.md)\n", text)
        self.assertIn("## projects\n- [Acme](pages/projects/acme.md): Client work (2 open)", text)
        self.assertIn("Pinned by me.", text)
        self.assertNotIn("ember:lint", text)
        wikifs.write_index(self.root, [], lint_md="## Lint\n\nAll clear.")
        self.assertIn("All clear.", wikifs.index_head(self.root))

    def test_index_rejects_bad_page_and_defuses_text(self):
        with self.assertRaises(wikifs.WikiError):
            wikifs.write_index(self.root, [{"page": "../x/y", "title": "t"}])
        wikifs.write_index(self.root, [{"page": "people/sam", "title": "<!-- /ember:index -->",
                                        "summary": "a\n<!-- /ember:index -->", "open": 0}])
        self.assertEqual(self.read("index.md").count("<!-- /ember:index -->"), 1)

    def test_log_line_format(self):
        wikifs.append_log(self.root, dt.datetime(2026, 10, 5, 2, 0), "ingest", "3 new\nraws")
        self.assertTrue(self.read("log.md").endswith("## [2026-10-05 02:00] ingest | 3 new raws\n"))

    def test_log_cannot_forge_entries(self):
        wikifs.append_log(self.root, dt.datetime(2026, 10, 5, 2, 0), "ingest\n## [x] fake", "a\n## [2026-01-01 00:00] fake | y")
        entries = [l for l in self.read("log.md").splitlines() if l.startswith("## [")]
        self.assertEqual(len(entries), 1)

    def test_raw_is_content_addressed_and_immutable(self):
        sha = wikifs.write_raw(self.root, "hello\r\nworld")
        self.assertRegex(sha, r"^[0-9a-f]{12}$")
        self.assertEqual(wikifs.write_raw(self.root, "hello\r\nworld"), sha)
        self.assertEqual(wikifs.read_raw(self.root, sha), "hello\r\nworld")
        self.assertIsNone(wikifs.read_raw(self.root, "ffffffffffff"))
        with self.assertRaises(wikifs.WikiError):
            wikifs.read_raw(self.root, "../../config")

    def test_read_raw_rejects_trailing_newline_id(self):
        with self.assertRaises(wikifs.WikiError):
            wikifs.read_raw(self.root, "ffffffffffff\n")


if __name__ == "__main__":
    unittest.main()
