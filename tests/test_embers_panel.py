import conftest_paths  # noqa: F401
import json, os, shutil, tempfile, unittest

from embers import jobs, panel, push
from embers.llm import RouterUnavailable
from embers_testkit import NOW, TEMPLATE, FakeLLM, LockHolder, raw_ids

NOTE = "Call with Sam.\nSam: I'll send the signed SOW by Friday.\nMaybe the budget doubles."


def seed(messages):
    sha = raw_ids(messages)[0]
    return {"ops": [
        {"op": "add", "page": "projects/acme", "kind": "loop", "text": "Waiting on Sam for the signed SOW",
         "evidence": [{"raw": sha, "quote": "I'll send the signed SOW by Friday"}]},
        {"op": "add", "page": "projects/acme", "kind": "fact", "text": "Budget will triple", "evidence": []}]}


class Req:
    def __init__(self, body=None, qs=None):
        self.body, self.qs = body or {}, qs or {}

    def q(self, name, default=""):
        return self.qs.get(name, default)


class FakeScheduler:
    def __init__(self):
        self.requests, self.cancelled, self.st = [], [], {}

    def status(self):
        return dict(self.st, last_error=None)

    def request(self, eid, wanted):
        self.requests.append((eid, list(wanted)))
        return list(wanted)

    def cancel(self, eid):
        self.cancelled.append(eid)
        return []


class FakeRouter:
    loaded = []
    reply = {"answer": "Sam owes you the signed SOW.", "items": []}
    fail = None
    seen = []

    def __init__(self, cfg):
        pass

    def loaded_ids(self, main=""):
        return list(self.loaded)

    def n_ctx(self, model):
        return 8192

    def llm(self, model, autoload=True):
        FakeRouter.seen.append((model, autoload))
        return FakeLLM(self.fail or self.reply)


class PanelCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.base = os.path.join(self.dir, "embers")
        self.cfg = {"embers_dir": self.base}
        self.sched = FakeScheduler()
        for name, value in (("SCHEDULER", self.sched), ("ROUTER_CLS", FakeRouter),
                            ("MAIN_FN", lambda: ""), ("NOW_FN", lambda: NOW)):
            old = getattr(panel, name)
            setattr(panel, name, value)
            self.addCleanup(setattr, panel, name, old)
        FakeRouter.loaded, FakeRouter.fail, FakeRouter.seen = [], None, []

    def call(self, fn, body=None, **qs):
        return fn(Req(body, qs), self.cfg)

    def err(self, status, fn, body=None, **qs):
        with self.assertRaises(panel.Error) as cm:
            self.call(fn, body, **qs)
        self.assertEqual(cm.exception.status, status, cm.exception.message)
        return cm.exception.message

    def make(self, eid="test", ingest=True, brief=True):
        """An ember with the test template under base, optionally with a wiki and a brief."""
        notes = os.path.join(self.dir, "notes-" + eid)
        os.makedirs(notes)
        with open(os.path.join(notes, "acme.md"), "w", encoding="utf-8") as f:
            f.write(NOTE)
        root = jobs.create_ember(self.base, TEMPLATE, eid, {"notes": notes}, NOW)
        with jobs.Ember(root) as e:
            if ingest:
                jobs.ingest(e, FakeLLM(seed), NOW)
            if brief:
                iid = e.store.open_items(verified_only=True)[0]["id"]
                jobs.brief(e, FakeLLM({"headline": "One loose end", "sections": [
                    {"title": "Waiting", "bullets": [{"item": iid, "text": "Sam owes the signed SOW."}]}]}), NOW)
        return root


class ListTest(PanelCase):
    def test_empty(self):
        s, out = self.call(panel.get_embers)
        self.assertEqual(s, 200)
        self.assertEqual(out["embers"], [])
        self.assertEqual(out["embers_dir"], self.base)
        self.assertTrue(out["scheduler"])

    def test_card(self):
        self.make()
        self.sched.st = {"test": {"next": {"brief": "2026-10-06T07:00"}, "running": None,
                                  "waiting": {}, "queued": ["lint"]}}
        card = self.call(panel.get_embers)[1]["embers"][0]
        self.assertEqual((card["id"], card["name"], card["enabled"]), ("test", "Test Brief", True))
        self.assertEqual(card["mission"], "Track loose ends.")
        self.assertEqual(card["counts"], {"pages": 1, "open": 2, "verified": 1})
        self.assertEqual(card["brief"]["date"], "2026-10-05")
        self.assertEqual(card["brief"]["headline"], "One loose end")
        self.assertEqual(card["last"]["brief"]["status"], "ok")
        self.assertEqual(card["queued"], ["lint"])
        self.assertEqual(card["next"], {"brief": "2026-10-06T07:00"})
        self.assertEqual(card["slots"][0]["id"], "notes")
        self.assertIn("notes", card["bindings"])
        self.assertEqual(card["schedule"]["brief"], TEMPLATE["jobs"]["brief"])
        self.assertIsNone(card["push"])

    def test_broken_ember_does_not_hide_others(self):
        self.make("good", ingest=False, brief=False)
        os.makedirs(os.path.join(self.base, "bad"))
        with open(os.path.join(self.base, "bad", "ember.json"), "w") as f:
            f.write("{not json")
        cards = self.call(panel.get_embers)[1]["embers"]
        self.assertEqual([c["id"] for c in cards], ["bad", "good"])
        self.assertIn("error", cards[0])

    def test_without_scheduler(self):
        panel.SCHEDULER = None
        self.make(ingest=False, brief=False)
        out = self.call(panel.get_embers)[1]
        self.assertFalse(out["scheduler"])
        self.assertEqual(out["embers"][0]["queued"], [])


class TemplatesTest(PanelCase):
    def test_bundled_templates(self):
        out = self.call(panel.get_templates)[1]["templates"]
        names = [t["name"] for t in out]
        self.assertIn("model-scout", names)
        self.assertIn("morning-brief", names)
        scout = out[names.index("model-scout")]
        self.assertEqual(scout["title"], "Model Scout")
        self.assertTrue(scout["zero_setup"])
        self.assertFalse(out[names.index("morning-brief")]["zero_setup"])
        self.assertIn("about", scout)
        self.assertNotIn("schema_md", scout)


class CreateTest(PanelCase):
    def test_zero_setup_template_and_first_run(self):
        s, out = self.call(panel.post_create, {"template": "model-scout"})
        self.assertEqual((s, out["id"]), (200, "model-scout"))
        self.assertTrue(os.path.isfile(os.path.join(self.base, "model-scout", "ember.json")))
        self.assertEqual(self.sched.requests, [("model-scout", ["ingest", "brief"])])
        self.assertEqual(self.call(panel.post_create, {"template": "model-scout"})[1]["id"], "model-scout-2")
        names = {c["id"]: c["name"] for c in self.call(panel.get_embers)[1]["embers"]}
        self.assertEqual(names, {"model-scout": "Model Scout", "model-scout-2": "Model Scout 2"})

    def test_no_first_run_when_asked(self):
        self.call(panel.post_create, {"template": "model-scout", "start": False})
        self.assertEqual(self.sched.requests, [])

    def test_bindings_and_explicit_id(self):
        notes = os.path.join(self.dir, "vault")
        os.makedirs(notes)
        out = self.call(panel.post_create, {"template": "morning-brief", "id": "work",
                                            "bindings": {"notes": notes}})[1]
        self.assertEqual(out["id"], "work")
        with open(os.path.join(self.base, "work", "ember.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["bindings"], {"notes": notes})

    def test_sources_needed_when_template_has_no_auto_slots(self):
        self.assertIn("source", self.err(400, panel.post_create, {"template": "morning-brief"}))

    def test_bad_input(self):
        self.err(404, panel.post_create, {"template": "../templates/model-scout"})
        self.err(404, panel.post_create, {"template": "nope"})
        self.err(400, panel.post_create, {"template": "model-scout", "id": "Bad Id"})
        self.err(400, panel.post_create, {"template": "model-scout", "id": "con"})
        self.err(400, panel.post_create, {"template": "morning-brief", "bindings": {"bogus": "x"}})

    def test_existing_or_removed_folder_is_refused(self):
        self.call(panel.post_create, {"template": "model-scout", "id": "scout"})
        self.err(409, panel.post_create, {"template": "model-scout", "id": "scout"})
        self.call(panel.post_delete, {"id": "scout"})
        msg = self.err(409, panel.post_create, {"template": "model-scout", "id": "scout"})
        self.assertIn("removed", msg)


class UpdateTest(PanelCase):
    def conf(self):
        with open(os.path.join(self.base, "test", "ember.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_update_fields(self):
        self.make(ingest=False, brief=False)
        out = self.call(panel.post_update, {"id": "test", "name": " Work  loops ", "enabled": False,
                                            "model": "qwen3-8b", "jobs": {"brief": "06:45", "lint": "off"},
                                            "push": {"kind": "ntfy", "url": "https://ntfy.sh/lf-x81"}})[1]
        self.assertTrue(out["ok"])
        c = self.conf()
        self.assertEqual((c["name"], c["enabled"], c["model"]), ("Work loops", False, "qwen3-8b"))
        self.assertEqual(c["jobs"], {"ingest": TEMPLATE["jobs"]["ingest"], "brief": "06:45", "lint": "off"})
        self.assertEqual(c["push"], {"kind": "ntfy", "url": "https://ntfy.sh/lf-x81"})
        self.call(panel.post_update, {"id": "test", "push": None})
        self.assertNotIn("push", self.conf())

    def test_bindings_are_checked_like_create(self):
        self.make(ingest=False, brief=False)
        self.err(400, panel.post_update, {"id": "test", "bindings": {"bogus": "x"}})
        self.err(400, panel.post_update, {"id": "test", "bindings": {}})      # notes is required
        new = os.path.join(self.dir, "other")
        self.call(panel.post_update, {"id": "test", "bindings": {"notes": new}})
        self.assertEqual(self.conf()["bindings"], {"notes": new})

    def test_bad_values(self):
        self.make(ingest=False, brief=False)
        for body in ({"name": ""}, {"name": "x" * 200}, {"enabled": "yes"}, {"model": 5},
                     {"jobs": {"brief": "7am"}}, {"jobs": {"nap": "01:00"}}, {"jobs": "07:00"},
                     {"push": {"kind": "ntfy", "url": "file:///etc/passwd"}}, {"template": {}}):
            self.err(400, panel.post_update, dict(body, id="test"))
        self.assertEqual(self.conf()["name"], "Test Brief")

    def test_unknown_ember(self):
        self.err(404, panel.post_update, {"id": "ghost", "name": "x"})
        self.err(400, panel.post_update, {"id": "../x", "name": "x"})


class DeleteTest(PanelCase):
    def test_delete_keeps_the_data(self):
        root = self.make(ingest=False, brief=False)
        out = self.call(panel.post_delete, {"id": "test"})[1]
        self.assertEqual(out["kept"], root)
        self.assertTrue(os.path.isfile(os.path.join(root, panel.REMOVED)))
        self.assertFalse(os.path.exists(os.path.join(root, "ember.json")))
        self.assertTrue(os.path.isfile(os.path.join(root, "index.md")))
        self.assertEqual(self.call(panel.get_embers)[1]["embers"], [])
        self.assertEqual(self.sched.cancelled, ["test"])

    def test_running_ember_is_not_deleted(self):
        root = self.make(ingest=False, brief=False)
        holder = LockHolder(root)
        try:
            self.err(409, panel.post_delete, {"id": "test"})
        finally:
            holder.release()
        self.assertTrue(os.path.isfile(os.path.join(root, "ember.json")))


class RunTest(PanelCase):
    def test_run_queues_jobs(self):
        self.make(ingest=False, brief=False)
        self.assertEqual(self.call(panel.post_run, {"id": "test", "job": "run"})[1]["queued"], ["ingest", "brief"])
        self.call(panel.post_run, {"id": "test", "job": "lint"})
        self.assertEqual(self.sched.requests[-1], ("test", ["lint"]))
        self.call(panel.post_cancel, {"id": "test"})
        self.assertEqual(self.sched.cancelled, ["test"])

    def test_run_errors(self):
        self.make(ingest=False, brief=False)
        self.err(400, panel.post_run, {"id": "test", "job": "everything"})
        self.err(404, panel.post_run, {"id": "ghost", "job": "run"})
        panel.SCHEDULER = None
        self.assertIn("scheduler", self.err(503, panel.post_run, {"id": "test", "job": "run"}))


class ReadTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.root = self.make()

    def test_brief(self):
        out = self.call(panel.get_brief, id="test")[1]
        self.assertEqual((out["date"], out["dates"]), ("2026-10-05", ["2026-10-05"]))
        self.assertIn('class="ef-link" data-page="projects/acme"', out["html"])
        self.assertIn("Sam owes the signed SOW.", out["html"])
        self.assertEqual(self.call(panel.get_brief, id="test", date="2026-10-05")[1]["date"], "2026-10-05")
        self.err(404, panel.get_brief, id="test", date="2026-10-04")
        self.err(400, panel.get_brief, id="test", date="../../ember")

    def test_no_brief_yet(self):
        self.make("fresh", ingest=False, brief=False)
        out = self.call(panel.get_brief, id="fresh")[1]
        self.assertEqual((out["date"], out["dates"], out["html"]), (None, [], ""))

    def test_pages_and_page(self):
        out = self.call(panel.get_pages, id="test")[1]
        self.assertEqual([p["page"] for p in out["pages"]], ["projects/acme"])
        self.assertIn('data-page="projects/acme"', out["index_html"])
        page = self.call(panel.get_page, id="test", page="projects/acme")[1]
        self.assertIn('class="ef-item ef-unverified"', page["html"])
        self.assertIn("ef-quote", page["html"])
        self.err(404, panel.get_page, id="test", page="projects/nope")
        for bad in ("../ember", "projects/../../x", "con/x", ""):
            self.err(400, panel.get_page, id="test", page=bad)

    def test_raw(self):
        sha = self.call(panel.get_pages, id="test")[1]["raws"][0]["sha"]
        out = self.call(panel.get_raw, id="test", sha=sha)[1]
        self.assertTrue(out["text"].endswith(NOTE))
        self.assertFalse(out["truncated"])
        self.assertEqual(out["source"], "notes")
        self.err(400, panel.get_raw, id="test", sha="../x")
        self.err(404, panel.get_raw, id="test", sha="0" * 12)

    def test_log(self):
        out = self.call(panel.get_log, id="test")[1]
        self.assertEqual([r["job"] for r in out["runs"]], ["brief", "ingest"])
        self.assertTrue(any("brief |" in line for line in out["log"]))

    def test_ids_are_checked(self):
        for bad in ("../x", "Test", "con", ""):
            self.err(400, panel.get_brief, id=bad)
        self.err(404, panel.get_brief, id="ghost")


class AskTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.make()

    def test_nothing_loaded(self):
        self.assertIn("Load a model", self.err(409, panel.post_ask, {"id": "test", "question": "Sam?"}))

    def test_answers_with_the_first_loaded_model_and_never_loads(self):
        FakeRouter.loaded = ["main-model", "other"]
        out = self.call(panel.post_ask, {"id": "test", "question": "What does Sam owe me?"})[1]
        self.assertEqual((out["status"], out["model"]), ("ok", "main-model"))
        self.assertEqual(out["answer"], "Sam owes you the signed SOW.")
        self.assertEqual(FakeRouter.seen, [("main-model", False)])

    def test_prefers_the_pinned_model_when_it_is_up(self):
        self.call(panel.post_update, {"id": "test", "model": "other"})
        FakeRouter.loaded = ["main-model", "other"]
        self.assertEqual(self.call(panel.post_ask, {"id": "test", "question": "Sam?"})[1]["model"], "other")

    def test_errors(self):
        FakeRouter.loaded = ["m"]
        self.err(400, panel.post_ask, {"id": "test", "question": "  "})
        FakeRouter.fail = RouterUnavailable("router down")
        self.err(503, panel.post_ask, {"id": "test", "question": "Sam?"})


class PushTestTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.make()
        self.sent = []

        def opener(req, timeout):
            self.sent.append(req)
            return 200
        old = panel.PUSH_OPENER
        panel.PUSH_OPENER = opener
        self.addCleanup(setattr, panel, "PUSH_OPENER", old)

    def test_needs_a_push_config(self):
        self.err(400, panel.post_push_test, {"id": "test"})

    def test_sends_the_latest_brief_and_records_it(self):
        self.call(panel.post_update, {"id": "test", "push": {"kind": "ntfy", "url": "https://ntfy.example/t"}})
        out = self.call(panel.post_push_test, {"id": "test"})[1]
        self.assertEqual((out["ok"], out["error"]), (True, ""))
        self.assertIn(b"Sam owes the signed SOW.", self.sent[0].data)
        card = self.call(panel.get_embers)[1]["embers"][0]
        self.assertEqual(card["push"], {"kind": "ntfy", "url": "https://ntfy.example/t"})
        self.assertTrue(card["push_last"]["ok"])


class RoutesTest(PanelCase):
    def test_routes_are_wired_and_errors_become_api_errors(self):
        import routes
        for path in ("/api/embers", "/api/embers/brief", "/api/embers/page", "/api/embers/raw"):
            self.assertIn(path, routes.GET_ROUTES)
        for path in ("/api/embers/create", "/api/embers/ask", "/api/embers/push/test"):
            self.assertIn(path, routes.POST_ROUTES)
        old = routes.cfg
        routes.cfg = lambda: self.cfg
        self.addCleanup(setattr, routes, "cfg", old)
        self.assertEqual(routes.GET_ROUTES["/api/embers"](routes.Req())[1]["embers"], [])
        with self.assertRaises(routes.ApiError) as cm:
            routes.GET_ROUTES["/api/embers/brief"](routes.Req(qs={"id": "ghost"}))
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
