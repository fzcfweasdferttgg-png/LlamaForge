import conftest_paths  # noqa: F401
import datetime as dt, io, os, shutil, subprocess, tempfile, unittest
from unittest import mock

from embers import sources

NOW = dt.datetime(2026, 10, 5, 2, 0)

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:evt-1@example\r
DTSTART;TZID=Europe/London:20261006T090000\r
DTEND;TZID=Europe/London:20261006T100000\r
SUMMARY:Acme kickoff\\, round 2\r
LOCATION:Room 4\r
DESCRIPTION:Bring the signed SOW.\\nAsk Sam about\r
  the budget.\r
LAST-MODIFIED:20261001T120000Z\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:evt-old@example\r
DTSTART;VALUE=DATE:20250101\r
SUMMARY:Ancient\r
END:VEVENT\r
END:VCALENDAR\r
"""

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Qwen 4 &amp; friends</title><link>https://x/1</link><guid>g1</guid>
<description>&lt;p&gt;New &lt;b&gt;models&lt;/b&gt;&lt;/p&gt;</description><pubDate>Fri, 02 Oct 2026</pubDate></item>
<item><title>Second</title><link>https://x/2</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><id>tag:1</id><title>Paper one</title><link href="https://arxiv.org/abs/1"/>
<updated>2026-10-01T00:00:00Z</updated><summary>We study things.</summary></entry></feed>"""


class FakeResp(io.BytesIO):
    pass


def opener_for(data):
    def opener(req, timeout=None):
        return FakeResp(data)
    return opener


class ClipTest(unittest.TestCase):
    def test_collapses_and_caps_bytes(self):
        self.assertEqual(sources.clip("a  \t b\r\n\n\n\nc"), "a b\n\nc")
        out = sources.clip("\u00e9" * 100, cap=11)
        self.assertEqual(out, "\u00e9" * 5)     # never splits a UTF-8 sequence


class FolderTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.write("a.md", "Alpha note")
        self.write("sub/b.txt", "Beta note")
        self.write("img.png", "binary")
        self.write(".obsidian/c.md", "config")

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_reads_text_files_and_skips_dot_dirs(self):
        items, cursor = sources.fetch("folder", self.root, {}, NOW)
        self.assertEqual([i["ref"] for i in items], ["a.md", "sub/b.txt"])
        self.assertTrue(items[0]["text"].startswith("File: a.md\n\nAlpha note"))
        self.assertEqual(set(cursor), {"a.md", "sub/b.txt"})

    def test_cursor_skips_unchanged(self):
        _, cursor = sources.fetch("folder", self.root, {}, NOW)
        self.write("a.md", "Alpha note, longer now")
        items, _ = sources.fetch("folder", self.root, cursor, NOW)
        self.assertEqual([i["ref"] for i in items], ["a.md"])

    def test_caps(self):
        self.write("big.md", "x" * (40 * 1024))
        items, _ = sources.fetch("folder", self.root, {}, NOW)
        big = [i for i in items if i["ref"] == "big.md"][0]
        self.assertLessEqual(len(big["text"].encode("utf-8")), sources.MAX_ITEM_BYTES)
        with mock.patch.object(sources, "MAX_FILES", 1):
            items, cursor = sources.fetch("folder", self.root, {}, NOW)
        self.assertEqual(len(cursor), 1)

    def test_symlink_outside_root_is_skipped(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "secret.md"), "w", encoding="utf-8") as f:
            f.write("secret")
        try:
            os.symlink(os.path.join(outside, "secret.md"), os.path.join(self.root, "link.md"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges on this machine")
        items, _ = sources.fetch("folder", self.root, {}, NOW)
        self.assertNotIn("link.md", [i["ref"] for i in items])

    def test_missing_folder(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch("folder", os.path.join(self.root, "nope"), {}, NOW)


class IcsTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".ics")
        os.close(fd)
        self.addCleanup(os.remove, self.path)
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(ICS)

    def test_window_unfolding_and_unescape(self):
        items, cursor = sources.fetch("ics", self.path, {}, NOW)
        self.assertEqual(len(items), 1)
        text = items[0]["text"]
        self.assertIn("Event: Acme kickoff, round 2", text)
        self.assertIn("Where: Room 4", text)
        self.assertIn("Bring the signed SOW.\nAsk Sam about the budget.", text)
        self.assertEqual(cursor, {"evt-1@example": "20261001T120000Z"})

    def test_cursor_skips_unmodified(self):
        _, cursor = sources.fetch("ics", self.path, {}, NOW)
        self.assertEqual(sources.fetch("ics", self.path, cursor, NOW)[0], [])

    def test_url_and_scheme(self):
        items, _ = sources.fetch_ics("https://cal.example/x.ics", {}, NOW, opener=opener_for(ICS.encode()))
        self.assertEqual(len(items), 1)
        with self.assertRaises(sources.SourceError):
            sources.fetch_ics("file:///etc/passwd", {}, NOW)

    def test_not_a_calendar(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch_ics("https://x", {}, NOW, opener=opener_for(b"<html>"))


class RssTest(unittest.TestCase):
    def test_rss2(self):
        items, cursor = sources.fetch_rss("https://x/feed", {}, NOW, opener=opener_for(RSS))
        self.assertEqual([i["title"] for i in items], ["Qwen 4 & friends", "Second"])
        self.assertIn("New models", items[0]["text"])
        self.assertNotIn("<b>", items[0]["text"])
        self.assertEqual(cursor["seen"], ["g1", "https://x/2"])
        again, _ = sources.fetch_rss("https://x/feed", cursor, NOW, opener=opener_for(RSS))
        self.assertEqual(again, [])

    def test_atom(self):
        items, _ = sources.fetch_rss("https://x/atom", {}, NOW, opener=opener_for(ATOM))
        self.assertEqual(items[0]["ref"], "https://arxiv.org/abs/1")
        self.assertIn("We study things.", items[0]["text"])

    def test_rejects_dtd(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(sources.SourceError):
            sources.fetch_rss("https://x", {}, NOW, opener=opener_for(evil))

    def test_bad_xml_and_cap(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch_rss("https://x", {}, NOW, opener=opener_for(b"<rss><oops"))
        many = b"<rss><channel>" + b"".join(b"<item><guid>%d</guid><title>t</title></item>" % i
                                            for i in range(150)) + b"</channel></rss>"
        items, _ = sources.fetch_rss("https://x", {}, NOW, opener=opener_for(many))
        self.assertEqual(len(items), sources.MAX_FEED_ITEMS)


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q")
        self.commit("first: add notes")

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True)

    def commit(self, msg):
        self.git("commit", "-q", "--allow-empty", "-m", msg)

    def test_commits_and_cursor(self):
        now = dt.datetime.now()
        items, cursor = sources.fetch("git", self.repo, {}, now)
        self.assertEqual([i["title"] for i in items], ["first: add notes"])
        self.commit("second: fix bug\n\nLonger body.")
        items, cursor2 = sources.fetch("git", self.repo, cursor, now)
        self.assertEqual([i["title"] for i in items], ["second: fix bug"])
        self.assertIn("Longer body.", items[0]["text"])
        self.assertNotEqual(cursor2["last"], cursor["last"])

    def test_not_a_repo(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch("git", os.path.join(self.repo, "nope"), {}, NOW)


class LlamacppTest(unittest.TestCase):
    RELS = [{"tag_name": f"b{n}", "body": f"model : add Foo{n} support (#{n})",
             "published_at": "2026-10-01T00:00:00Z", "html_url": f"https://x/{n}"} for n in range(7000, 7040)]

    def test_first_run_is_capped_then_only_new(self):
        items, cursor = sources.fetch_llamacpp("", {}, NOW, releases=self.RELS)
        self.assertEqual(len(items), 30)
        self.assertEqual(items[0]["ref"], "b7039")
        self.assertEqual(cursor, {"build": 7039})
        newer = [{"tag_name": "b7040", "body": "model : add Bar support (#1)", "published_at": "", "html_url": ""}]
        items, cursor = sources.fetch_llamacpp("", cursor, NOW, releases=newer + self.RELS)
        self.assertEqual([i["ref"] for i in items], ["b7040"])


class DispatchTest(unittest.TestCase):
    def test_unknown_type(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch("imap", "x", {}, NOW)



class HardeningTest(unittest.TestCase):
    def test_only_http_schemes(self):
        for url in ("ftp://x/feed", "file:///etc/hosts", "gopher://x"):
            with self.assertRaises(sources.SourceError):
                sources.fetch_rss(url, {}, NOW, opener=opener_for(RSS))

    def test_timeout_always_passed(self):
        seen = {}

        def opener(req, timeout=None):
            seen["timeout"] = timeout
            return FakeResp(RSS)
        sources.fetch_rss("https://x/feed", {}, NOW, opener=opener)
        self.assertTrue(seen["timeout"])

    def test_download_cap_enforced(self):
        reads = []

        class Big(io.BytesIO):
            def read(self, n=-1):
                reads.append(n)
                return super().read(n)
        with mock.patch.object(sources, "MAX_DOWNLOAD", 100):
            with self.assertRaises(sources.SourceError):
                sources.fetch_rss("https://x", {}, NOW, opener=lambda r, timeout=None: Big(b"x" * 500))
        self.assertEqual(reads, [101])

    def test_redirect_to_non_http_refused(self):
        import urllib.request
        h = sources._SafeRedirect()
        req = urllib.request.Request("https://x/a")
        for target in ("file:///etc/passwd", "ftp://x/y"):
            with self.assertRaises(sources.SourceError):
                h.redirect_request(req, None, 302, "Found", {}, target)
        self.assertIsNotNone(h.redirect_request(req, None, 302, "Found", {}, "https://x/b"))

    def test_bad_bytes_are_replaced_not_raised(self):
        text = ICS.encode().replace(b"Room 4", b"Room \xff\xfe")
        items, _ = sources.fetch_ics("https://x", {}, NOW, opener=opener_for(text))
        self.assertIn("Room", items[0]["text"])

    def test_entity_anywhere_and_utf16_rejected(self):
        for evil in (b'<rss><!ENTITY a "b"><channel/></rss>',
                     b'<rss><!entity a "b"><channel/></rss>',
                     '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE r><rss/>'.encode("utf-16")):
            with self.assertRaises(sources.SourceError):
                sources.fetch_rss("https://x", {}, NOW, opener=opener_for(evil))

    def test_ics_missing_and_invalid_dtstart(self):
        text = ("BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nSUMMARY:No start\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:b\nDTSTART:99999999\nSUMMARY:Bad\nEND:VEVENT\n"
                "BEGIN:VEVENT\nUID:c\nDTSTART:garbage\nEND:VEVENT\nEND:VCALENDAR\n")
        items, cursor = sources.fetch_ics("https://x", {}, NOW, opener=opener_for(text.encode()))
        self.assertEqual((items, cursor), ([], {}))

    def test_wrong_type_cursors_tolerated(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "a.md"), "w") as f:
            f.write("hi")
        for bad in ("junk", None, 5, ["x"]):
            self.assertEqual(len(sources.fetch("folder", root, bad, NOW)[0]), 1)
            self.assertEqual(len(sources.fetch("rss", "https://x", bad, NOW, opener=opener_for(RSS))[0]), 2)
            self.assertEqual(len(sources.fetch("ics", "https://x", bad, NOW, opener=opener_for(ICS.encode()))[0]), 1)
        self.assertEqual(len(sources.fetch("folder", root, {"a.md": 7}, NOW)[0]), 1)
        self.assertEqual(len(sources.fetch("rss", "https://x", {"seen": "abc"}, NOW, opener=opener_for(RSS))[0]), 2)
        self.assertEqual(len(sources.fetch("rss", "https://x", {"seen": [1, None]}, NOW, opener=opener_for(RSS))[0]), 2)
        rels = LlamacppTest.RELS
        for bad in ({"build": "x"}, {"build": None}, {"build": [1]}, "junk"):
            self.assertEqual(len(sources.fetch("llamacpp", "", bad, NOW, releases=rels)[0]), 30)

    def test_folder_symlinked_dir_outside_root_skipped(self):
        root, outside = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "s.md"), "w") as f:
            f.write("secret")
        try:
            os.symlink(outside, os.path.join(root, "dir"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks need privileges on this machine")
        self.assertEqual(sources.fetch("folder", root, {}, NOW)[0], [])

    def test_folder_non_utf8_replaced(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        with open(os.path.join(root, "a.md"), "wb") as f:
            f.write(b"ok \xff\xfe done")
        self.assertIn("done", sources.fetch("folder", root, {}, NOW)[0][0]["text"])


class GitHardeningTest(unittest.TestCase):
    def test_dash_path_rejected(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch_git("--upload-pack=evil", {}, NOW, run=mock.Mock())

    def test_argv_env_and_safety_flags(self):
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        calls = []

        def run(args, **kw):
            calls.append((args, kw))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        sources.fetch_git(repo, {"last": 5}, NOW, run=run)
        args, kw = calls[0]
        self.assertIsInstance(args, list)
        self.assertFalse(kw.get("shell"))
        self.assertEqual(args[args.index("-C") + 1], repo)
        self.assertIn("core.fsmonitor=false", args)
        self.assertIn("core.hooksPath=", args)
        self.assertIn("--no-pager", args)
        self.assertTrue(kw["timeout"])
        self.assertEqual(kw["errors"], "replace")
        self.assertEqual(kw["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kw["env"]["GIT_PAGER"], "cat")

    def test_relative_dash_path_made_safe(self):
        with self.assertRaises(sources.SourceError):
            sources.fetch_git("-x", {}, NOW, run=mock.Mock())


