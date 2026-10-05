import conftest_paths  # noqa: F401
import json, os, unittest

from embers import jobs, push
from embers_testkit import NOW, EmberCase, FakeLLM, raw_ids

NOTE = "Call with Sam.\nSam: I'll send the signed SOW by Friday.\nPriya: send the <deck> & notes."


def seed(messages):
    sha = raw_ids(messages)[0]
    return {"ops": [
        {"op": "add", "page": "projects/acme", "kind": "loop", "text": "Waiting on Sam for the signed SOW",
         "evidence": [{"raw": sha, "quote": "I'll send the signed SOW by Friday"}]},
        {"op": "add", "page": "projects/acme", "kind": "loop", "text": "Send Priya the <deck> & notes",
         "evidence": [{"raw": sha, "quote": "send the <deck> & notes"}]}]}


class FakeOpener:
    def __init__(self, fail=None):
        self.requests, self.fail = [], fail

    def __call__(self, req, timeout):
        if self.fail:
            raise self.fail
        self.requests.append((req, timeout))
        return 200


class ValidateTest(unittest.TestCase):
    def test_accepts_ntfy_and_webhook(self):
        self.assertEqual(push.validate({"kind": "ntfy", "url": " https://ntfy.sh/my-topic-x81 "}),
                         {"kind": "ntfy", "url": "https://ntfy.sh/my-topic-x81"})
        self.assertEqual(push.validate({"kind": "webhook", "url": "http://127.0.0.1:9000/hook"})["kind"], "webhook")

    def test_none_or_empty_turns_push_off(self):
        for off in (None, {}, {"kind": "", "url": ""}):
            self.assertIsNone(push.validate(off))

    def test_rejects_bad_input(self):
        for bad in ({"kind": "email", "url": "https://x.example"}, {"kind": "ntfy", "url": "ftp://x.example/t"},
                    {"kind": "ntfy", "url": "javascript:alert(1)"}, {"kind": "ntfy", "url": "https:///nohost"},
                    {"kind": "ntfy", "url": "https://x.example/a b"}, {"kind": "ntfy", "url": "https://x.example/a\nX-Evil: 1"},
                    {"kind": "ntfy", "url": "https://x.example/" + "a" * 2000}, {"kind": "ntfy"}, "ntfy", ["x"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                push.validate(bad)


class BriefCase(EmberCase):
    def setUp(self):
        self.ember = self.make_ember({"acme.md": NOTE})
        jobs.ingest(self.ember, FakeLLM(seed), NOW)
        ids = [i["id"] for i in self.ember.store.open_items(verified_only=True)]
        self.result = jobs.brief(self.ember, FakeLLM({"headline": "Two loose ends", "sections": [
            {"title": "Waiting", "bullets": [{"item": i, "text": t} for i, t in
                                            zip(ids, ["Sam owes the signed SOW.", "Send Priya the <deck> & notes."])]}]}),
            NOW)


class MessageTest(BriefCase, unittest.TestCase):
    def test_message_is_plain_text_headline_and_bullets(self):
        title, body, bullets = push.message(self.ember.conf, self.result)
        self.assertEqual(title, "Test Brief")
        self.assertEqual(bullets, ["Sam owes the signed SOW.", "Send Priya the <deck> & notes."])
        self.assertEqual(body, "Two loose ends\n- Sam owes the signed SOW.\n- Send Priya the <deck> & notes.")
        self.assertNotIn("](", body)
        self.assertNotIn("[^r-", body)
        self.assertNotIn("\\", body)

    def test_message_caps_bullets(self):
        md_path = self.result["path"]
        with open(md_path, "a", encoding="utf-8") as f:
            f.write("".join(f"- extra {n}\n" for n in range(6)))
        title, body, bullets = push.message(self.ember.conf, self.result)
        self.assertEqual(len(bullets), push.MAX_BULLETS)
        self.assertTrue(body.endswith(f"(+{2 + 6 - push.MAX_BULLETS} more)"))

    def test_ntfy_request(self):
        opener = FakeOpener()
        ok, err = push.send({"kind": "ntfy", "url": "https://ntfy.example/topic"}, "Test Brief", "body text",
                            {}, opener=opener)
        self.assertEqual((ok, err), (True, ""))
        req, timeout = opener.requests[0]
        self.assertEqual((req.get_method(), req.full_url, timeout), ("POST", "https://ntfy.example/topic", push.TIMEOUT))
        self.assertEqual(req.data, b"body text")
        self.assertEqual(req.get_header("Title"), "Test Brief")

    def test_ntfy_non_ascii_title_stays_out_of_headers(self):
        opener = FakeOpener()
        push.send({"kind": "ntfy", "url": "https://ntfy.example/t"}, "Brief ☕\r\nX-Evil: 1", "b", {}, opener=opener)
        req = opener.requests[0][0]
        self.assertEqual(req.get_header("Title"), "LlamaForge brief")
        self.assertTrue(req.data.decode("utf-8").startswith("Brief ☕ X-Evil: 1\n"))

    def test_webhook_json_works_for_slack_and_discord(self):
        opener = FakeOpener()
        push.send({"kind": "webhook", "url": "https://hooks.example/x"}, "T", "body", {"ember": "test", "bullets": ["a"]},
                  opener=opener)
        req = opener.requests[0][0]
        self.assertEqual(req.get_header("Content-type"), "application/json")
        payload = json.loads(req.data)
        self.assertEqual((payload["text"], payload["content"], payload["ember"]), ("T\nbody", "T\nbody", "test"))

    def test_send_failure_is_reported_not_raised(self):
        ok, err = push.send({"kind": "ntfy", "url": "https://ntfy.example/t"}, "T", "b", {},
                            opener=FakeOpener(fail=OSError("no route")))
        self.assertFalse(ok)
        self.assertIn("no route", err)


class AfterRunTest(BriefCase, unittest.TestCase):
    def conf(self, **push_conf):
        self.ember.conf["push"] = push_conf
        return self.ember

    def test_pushes_after_a_good_brief_and_records_it(self):
        opener = FakeOpener()
        t = push.after_run(self.conf(kind="ntfy", url="https://ntfy.example/t"), "brief", self.result, NOW,
                           opener=opener)
        t.join(5)
        self.assertEqual(len(opener.requests), 1)
        with open(os.path.join(self.ember.root, push.STATE_FILE), encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual((state["ok"], state["error"], state["kind"]), (True, "", "ntfy"))

    def test_failure_is_recorded(self):
        t = push.after_run(self.conf(kind="ntfy", url="https://ntfy.example/t"), "brief", self.result, NOW,
                           opener=FakeOpener(fail=OSError("refused")))
        t.join(5)
        state = push.last(self.ember.root)
        self.assertFalse(state["ok"])
        self.assertIn("refused", state["error"])

    def test_no_push_for_other_jobs_failed_briefs_or_no_config(self):
        opener = FakeOpener()
        self.assertIsNone(push.after_run(self.conf(kind="ntfy", url="https://ntfy.example/t"), "ingest",
                                         {"status": "ok"}, NOW, opener=opener))
        self.assertIsNone(push.after_run(self.ember, "brief", dict(self.result, status="error"), NOW, opener=opener))
        self.ember.conf.pop("push")
        self.assertIsNone(push.after_run(self.ember, "brief", self.result, NOW, opener=opener))
        self.assertIsNone(push.after_run(self.conf(kind="bogus", url="x"), "brief", self.result, NOW, opener=opener))
        self.assertEqual(opener.requests, [])
        self.assertIsNone(push.last(self.ember.root))


if __name__ == "__main__":
    unittest.main()
