import conftest_paths  # noqa: F401
import json, os, unittest

import config
from embers import templates


def base(**over):
    t = {"name": "morning-brief", "title": "Morning Brief", "mission": "Track loose ends.",
         "schema_md": "# Schema\nPages per project.", "page_kinds": ["projects", "people"],
         "slots": [{"id": "notes", "type": "folder", "label": "Notes folder", "required": True}]}
    t.update(over)
    return t


class ParseTemplateTest(unittest.TestCase):
    def test_minimal_template_gets_defaults(self):
        t = templates.parse_template(json.dumps(base()))
        self.assertEqual(t["jobs"], {"ingest": "02:00", "brief": "07:00", "lint": "sun 03:00"})
        self.assertEqual(t["stale_days"], 3)
        self.assertEqual(t["dropped"], [])
        self.assertEqual(t["slots"][0], {"id": "notes", "type": "folder",
                                         "label": "Notes folder", "required": True})

    def test_unknown_keys_dropped_and_reported(self):
        t = templates.parse_template(base(tools=[{"name": "shell"}], api_key="x"))
        self.assertEqual(sorted(t["dropped"]), ["api_key", "tools"])
        self.assertNotIn("tools", t)
        self.assertNotIn("api_key", t)

    def test_slots_cannot_carry_paths(self):
        t = templates.parse_template(base(slots=[
            {"id": "notes", "type": "folder", "default": "C:/Users/me", "path": "/x"}]))
        self.assertEqual(sorted(t["dropped"]), ["slots[0].default", "slots[0].path"])
        self.assertNotIn("default", t["slots"][0])

    def test_rss_default_must_be_http(self):
        ok = templates.parse_template(base(slots=[
            {"id": "feed", "type": "rss", "default": "https://example.com/feed.xml"}]))
        self.assertEqual(ok["slots"][0]["default"], "https://example.com/feed.xml")
        with self.assertRaises(ValueError):
            templates.parse_template(base(slots=[
                {"id": "feed", "type": "rss", "default": "file:///etc/passwd"}]))

    def test_rejects_bad_name_kinds_and_slots(self):
        bad_cases = (
            base(name="../evil"),
            base(page_kinds=[]),
            base(page_kinds=["A B"]),
            base(page_kinds=[f"k{i}" for i in range(9)]),
            base(slots=[{"id": "x", "type": "shell"}]),
            base(slots=[{"id": "x", "type": "folder"}, {"id": "x", "type": "git"}]),
            base(stale_days=0),
            base(title=""),
        )
        for bad in bad_cases:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                templates.parse_template(bad)

    def test_caps(self):
        with self.assertRaises(ValueError):
            templates.parse_template(base(schema_md="x" * (8 * 1024 + 1)))
        with self.assertRaises(ValueError):
            templates.parse_template("{" + " " * (64 * 1024) + "}")

    def test_jobs_validated(self):
        t = templates.parse_template(base(jobs={"brief": "06:30", "lint": "off", "archivist": "01:00"}))
        self.assertEqual(t["jobs"], {"brief": "06:30", "lint": "off", "ingest": "02:00"})
        self.assertEqual(t["dropped"], ["jobs.archivist"])
        with self.assertRaises(ValueError):
            templates.parse_template(base(jobs={"brief": "7am"}))

    def test_not_json(self):
        with self.assertRaises(ValueError):
            templates.parse_template("not json")

    def test_trailing_newline_rejected(self):
        for bad in (base(name="morning-brief\n"),
                    base(page_kinds=["projects\n"]),
                    base(slots=[{"id": "a\n", "type": "folder"}]),
                    base(jobs={"brief": "07:00\n"})):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                templates.parse_template(bad)

    def test_deep_nesting_is_value_error(self):
        with self.assertRaises(ValueError):
            templates.parse_template("[" * 20000 + "]" * 20000)

    def test_windows_reserved_names_rejected(self):
        for bad in (base(name="con"), base(name="NUL"), base(name="com1"),
                    base(page_kinds=["projects", "aux"]), base(page_kinds=["lpt9"])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                templates.parse_template(bad)
        import embers
        self.assertTrue(embers.reserved_name("CoN"))
        self.assertFalse(embers.reserved_name("console"))

    def test_blank_required_text_rejected(self):
        with self.assertRaises(ValueError):
            templates.parse_template(base(title="   "))

    def test_rss_default_needs_host(self):
        for url in ("http://", "https:///feed.xml"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                templates.parse_template(base(slots=[
                    {"id": "feed", "type": "rss", "default": url}]))

    def test_size_cap_counts_utf8_bytes(self):
        # 30k chars of a 3-byte char = 90 KB of bytes but only 30k characters
        with self.assertRaises(ValueError):
            templates.parse_template(json.dumps(base(junk="€" * 30000), ensure_ascii=False))


class BundledTemplatesTest(unittest.TestCase):
    def test_morning_brief_is_valid_and_clean(self):
        path = os.path.join(config.ROOT, "templates", "morning-brief.json")
        with open(path, encoding="utf-8") as f:
            t = templates.parse_template(f.read())
        self.assertEqual(t["name"], "morning-brief")
        self.assertEqual(t["dropped"], [])
        self.assertEqual({s["type"] for s in t["slots"]}, {"folder", "ics", "git", "rss"})

    def test_model_scout_is_valid_and_needs_no_binding(self):
        path = os.path.join(config.ROOT, "templates", "model-scout.json")
        with open(path, encoding="utf-8") as f:
            t = templates.parse_template(f.read())
        self.assertEqual((t["name"], t["dropped"]), ("model-scout", []))
        self.assertEqual([s["type"] for s in t["slots"]], ["llamacpp", "machine"])
        self.assertTrue(all(s["type"] in templates.AUTO_SLOTS for s in t["slots"]))
        self.assertEqual(t["page_kinds"], ["families", "machine"])
        self.assertIn("VRAM", t["schema_md"])


if __name__ == "__main__":
    unittest.main()
