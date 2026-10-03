import conftest_paths  # noqa: F401
import datetime as dt, os, shutil, tempfile, unittest

from embers.store import Store

NOW = dt.datetime(2026, 10, 5, 2, 0)


class StoreTestCase(unittest.TestCase):
    fts = True

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.st = Store(os.path.join(self.dir, "embers.db"), fts=self.fts)
        self.addCleanup(self.st.close)

    def add(self, page="projects/acme", text="Waiting on Sam for the signed SOW", verified=1, when=NOW):
        with self.st.db:
            self.st.ensure_page(page, page.split("/")[1].title(), "", when)
            iid = self.st.new_item_id(page, text, when)
            self.st.add_item(iid, page, "loop", text, "", "", verified, when)
            self.st.index_page(page)
        return iid


class RunsTest(StoreTestCase):
    def test_lifecycle(self):
        with self.st.db:
            rid = self.st.start_run("ingest", NOW)
        self.assertIsNone(self.st.last_run("ingest"))       # still running
        with self.st.db:
            self.st.finish_run(rid, NOW, "ok", tokens_in=10, tokens_out=2, detail={"raws": 1})
        last = self.st.last_run("ingest")
        self.assertEqual((last["status"], last["tokens_in"], last["started"]), ("ok", 10, "2026-10-05T02:00:00"))
        self.assertEqual(self.st.recent_runs()[0]["id"], rid)


class RawsTest(StoreTestCase):
    def test_dedupe_pending_and_status(self):
        with self.st.db:
            self.assertTrue(self.st.add_raw("a1b2c3d4e5f6", "notes", "a.md", "a.md", NOW, 10))
            self.assertFalse(self.st.add_raw("a1b2c3d4e5f6", "notes", "a.md", "a.md", NOW, 10))
        self.assertEqual([r["sha"] for r in self.st.pending_raws()], ["a1b2c3d4e5f6"])
        with self.st.db:
            self.st.mark_raws(["a1b2c3d4e5f6"], "done")
        self.assertEqual(self.st.pending_raws(), [])
        self.assertEqual(self.st.raw_status("a1b2c3d4e5f6"), "done")
        self.assertIsNone(self.st.raw_status("ffffffffffff"))
        self.assertEqual(len(self.st.all_raws()), 1)


class ItemsTest(StoreTestCase):
    def test_add_update_evidence(self):
        iid = self.add()
        self.assertRegex(iid, r"^it-[0-9a-f]{8}$")
        with self.st.db:
            self.st.add_evidence(iid, "a1b2c3d4e5f6", "I'll send the signed SOW", NOW)
            self.st.add_evidence(iid, "a1b2c3d4e5f6", "I'll send the signed SOW", NOW)   # ignored
            self.st.update_item(iid, NOW + dt.timedelta(days=1), status="closed", owner="Sam")
        it = self.st.get_item(iid)
        self.assertEqual((it["status"], it["owner"], it["updated"]), ("closed", "Sam", "2026-10-06T02:00:00"))
        self.assertEqual(self.st.evidence_for([iid]), {iid: [{"raw": "a1b2c3d4e5f6", "quote": "I'll send the signed SOW"}]})
        self.assertEqual(self.st.evidence_for([]), {})
        self.assertEqual(self.st.referenced_raws(), {"a1b2c3d4e5f6"})
        self.assertEqual(self.st.all_evidence()[0]["page"], "projects/acme")
        self.assertIn(iid, self.st.known_item_ids())

    def test_update_rejects_unknown_fields(self):
        iid = self.add()
        with self.assertRaises(ValueError):
            self.st.update_item(iid, NOW, page="people/x")

    def test_ids_unique_for_same_text(self):
        a, b = self.add(), self.add()
        self.assertNotEqual(a, b)

    def test_open_items_and_page_counts(self):
        v = self.add()
        self.add(text="Unverified rumour about the budget", verified=0)
        self.assertEqual([i["id"] for i in self.st.open_items(verified_only=True)], [v])
        self.assertEqual(len(self.st.open_items()), 2)
        self.assertEqual(self.st.all_pages()[0]["open"], 2)
        self.assertEqual(len(self.st.items_for_page("projects/acme")), 2)

    def test_transaction_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.st.db:
                self.st.ensure_page("projects/x", "X", "", NOW)
                raise RuntimeError("boom")
        self.assertIsNone(self.st.get_page("projects/x"))

    def test_ensure_page_keeps_title_and_fills_empty_summary(self):
        with self.st.db:
            self.st.ensure_page("people/sam", "Sam", "", NOW)
            self.st.ensure_page("people/sam", "Samuel", "Client contact", NOW)
        p = self.st.get_page("people/sam")
        self.assertEqual((p["title"], p["summary"]), ("Sam", "Client contact"))


class SearchTest(StoreTestCase):
    def test_finds_page_by_item_text(self):
        self.add()
        self.add(page="people/priya", text="Priya approved the budget")
        self.assertEqual(self.st.search("anything about the signed SOW?")[0], "projects/acme")   # "the" also hits priya
        self.assertEqual(self.st.search("budget"), ["people/priya"])
        self.assertEqual(self.st.search(""), [])
        self.assertEqual(self.st.search('"; DROP TABLE items; --'), [])

    def test_non_ascii_terms(self):
        self.add(page="people/juergen", text="Jürgen schickt die Übersicht bis Freitag")
        self.add(page="projects/budget", text="बजट review pending")
        self.add(page="projects/other", text="Unrelated gadget notes")
        for q in ("Übersicht", "übersicht", "Jürgen", "JÜRGEN"):
            with self.subTest(q=q):
                self.assertEqual(self.st.search(q), ["people/juergen"])
        self.assertEqual(self.st.search("बजट"), ["projects/budget"])

    def test_like_wildcards_are_literal(self):
        self.add(page="projects/money", text="Budget is 100_000 euro")
        self.add(page="projects/other", text="Budget is 100x000 euro and 50 percent")
        self.assertEqual(self.st.search("100_000"), ["projects/money"])
        self.assertEqual(self.st.search("%"), [])
        self.assertEqual(self.st.search("___"), [])


class SearchFallbackTest(SearchTest):
    fts = False

    def test_flag(self):
        self.assertFalse(self.st.fts)


class CursorMetaTest(StoreTestCase):
    def test_roundtrip(self):
        self.assertEqual(self.st.get_cursor("notes"), {})
        with self.st.db:
            self.st.set_cursor("notes", {"a.md": "1:2"})
            self.st.set_meta("lint_flags", [{"item": "x"}])
        self.assertEqual(self.st.get_cursor("notes"), {"a.md": "1:2"})
        self.assertEqual(self.st.get_meta("lint_flags"), [{"item": "x"}])
        self.assertEqual(self.st.get_meta("missing", 5), 5)


class HardeningTest(StoreTestCase):
    def test_hostile_search_queries_never_raise(self):
        self.add()
        for q in ['"', '""', 'sow*', '-sow', 'sow AND', 'OR', 'NEAR(sow budget)', '(sow', 'sow)', 'a:b',
                  'NOT sow', '^sow', "'; DROP TABLE items; --", 'pages_fts:sow', '\u0000sow', '*' * 50]:
            with self.subTest(q=q):
                self.assertIsInstance(self.st.search(q), list)
        self.assertEqual(self.st.search("signed AND (SOW OR NEAR)")[0], "projects/acme")
        self.assertEqual(len(self.st.open_items()), 1)        # nothing was dropped

    def test_search_degrades_on_sqlite_error(self):
        self.add()
        real = self.st.db
        class Boom:
            def execute(self, *a, **k):
                raise __import__("sqlite3").OperationalError("fts5: syntax error")
        self.st.db = Boom()
        try:
            self.assertEqual(self.st.search("sow"), [])
        finally:
            self.st.db = real

    def test_values_are_parameters_not_sql(self):
        nasty = "x'); DROP TABLE items; --"
        iid = self.add(text=nasty)
        self.assertEqual(self.st.get_item(iid)["text"], nasty)
        with self.st.db:
            self.st.update_item(iid, NOW, owner=nasty)
        self.assertEqual(self.st.get_item(iid)["owner"], nasty)

    def test_update_item_column_allow_list(self):
        iid = self.add()
        for bad in ("id=?, text", "text=text, status", "page", "id", "updated", "1=1; --"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.st.update_item(iid, NOW, **{bad: "z"})
        before = self.st.get_item(iid)
        with self.st.db:
            self.st.update_item(iid, NOW + dt.timedelta(days=2))   # no fields: only touches updated
        after = self.st.get_item(iid)
        self.assertEqual(after["updated"], "2026-10-07T02:00:00")
        self.assertEqual({k: v for k, v in after.items() if k != "updated"}, {k: v for k, v in before.items() if k != "updated"})

    def test_ids_validated_with_fullmatch(self):
        with self.st.db:
            self.st.ensure_page("projects/acme", "Acme", "", NOW)
        for bad in ("it-8f2c1a2b\n", "it-8f2c1a2bX", "IT-8f2c1a2b", "it-8f2c1a2", ""):
            with self.subTest(item=bad), self.assertRaises(ValueError):
                self.st.add_item(bad, "projects/acme", "loop", "t", "", "", 1, NOW)
        for bad in ("projects/acme\n", "projects/../x", "projects", "Projects/acme", "a/b/c"):
            with self.subTest(page=bad), self.assertRaises(ValueError):
                self.st.ensure_page(bad, "T", "", NOW)
        for bad in ("a1b2c3d4e5f6\n", "a1b2c3d4e5f", "A1B2C3D4E5F6", "a1b2c3d4e5f6a"):
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                self.st.add_raw(bad, "notes", "a.md", "a.md", NOW, 1)
            with self.assertRaises(ValueError):
                self.st.add_evidence("it-8f2c1a2b", bad, "quote", NOW)
        with self.assertRaises(ValueError):
            self.st.add_evidence("it-8f2c1a2b\n", "a1b2c3d4e5f6", "quote", NOW)
        self.assertEqual(self.st.all_raws(), [])

    def test_missing_fts5_falls_back_cleanly(self):
        import sqlite3, unittest.mock as m
        real_connect = sqlite3.connect
        class NoFts:
            def __init__(self, c): self._c = c
            def execute(self, sql, *a):
                if "fts5" in sql.lower():
                    raise sqlite3.OperationalError("no such module: fts5")
                return self._c.execute(sql, *a)
            def __enter__(self): return self._c.__enter__()
            def __exit__(self, *e): return self._c.__exit__(*e)
            def __getattr__(self, n): return getattr(self._c, n)
        with m.patch("embers.store.sqlite3.connect", lambda *a, **k: NoFts(real_connect(*a, **k))):
            st = Store(os.path.join(self.dir, "nofts.db"))
        self.addCleanup(st.close)
        self.assertFalse(st.fts)
        with st.db:
            st.ensure_page("projects/acme", "Acme", "", NOW)
        st.index_page("projects/acme")                         # no-op, no crash
        self.assertEqual(st.search("acme"), ["projects/acme"])

    def test_close_is_idempotent_and_releases_file(self):
        path = os.path.join(self.dir, "other.db")
        st = Store(path)
        st.close()
        st.close()
        os.remove(path)                                        # fails on Windows if still open

    def test_failed_open_closes_connection(self):
        bad = os.path.join(self.dir, "bad.db")
        with open(bad, "wb") as f:
            f.write(b"this is not a sqlite database" * 50)
        with self.assertRaises(Exception):
            Store(bad)
        os.remove(bad)


class MigrationTest(StoreTestCase):
    def test_fts_backfilled_when_enabled_on_existing_db(self):
        p = os.path.join(self.dir, "re.db")
        a = Store(p, fts=False)
        with a.db:
            a.ensure_page("projects/acme", "Acme rollout", "", NOW)
        a.close()
        b = Store(p)
        self.addCleanup(b.close)
        self.assertTrue(b.fts)
        self.assertEqual(b.search("rollout"), ["projects/acme"])

    def test_user_version_set_on_fresh_db_only(self):
        self.assertEqual(self.st.db.execute("PRAGMA user_version").fetchone()[0], 1)
        p = os.path.join(self.dir, "v.db")
        a = Store(p)
        a.db.execute("PRAGMA user_version = 7")
        a.close()
        b = Store(p)
        self.addCleanup(b.close)
        self.assertEqual(b.db.execute("PRAGMA user_version").fetchone()[0], 7)


if __name__ == "__main__":
    unittest.main()
