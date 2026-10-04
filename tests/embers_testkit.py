"""Shared fakes for Embers pipeline tests (not a test module itself)."""
import conftest_paths  # noqa: F401
import datetime as dt, os, re, shutil, tempfile

from embers import jobs, templates

NOW = dt.datetime(2026, 10, 5, 2, 0)
TEMPLATE = templates.parse_template({
    "name": "test-brief", "title": "Test Brief", "mission": "Track loose ends.",
    "schema_md": "# Schema\nprojects/<slug> per project.", "page_kinds": ["projects", "people", "events"],
    "slots": [{"id": "notes", "type": "folder", "label": "Notes", "required": True}]})


def raw_ids(messages):
    """Raw ids of the real delimiter lines (they start a line; forged ones inside raw text don't)."""
    return re.findall(r"(?m)^=== raw ([0-9a-f]{12})", messages[-1]["content"])


def item_ids(messages):
    return re.findall(r"- \[(it-[0-9a-f]{8})\]", messages[-1]["content"])


class FakeLLM:
    """Scripted model. Each reply is a dict, an Exception (raised), or a
    callable(messages) -> dict."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.max_tokens = []

    def __call__(self, messages, schema, max_tokens=2048):
        self.calls.append(messages)
        self.max_tokens.append(max_tokens)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(messages)
        return r, {"prompt_tokens": 100, "completion_tokens": 20}


class EmberCase:
    """Mixin for unittest.TestCase: a temp notes folder + an ember bound to it."""

    def make_ember(self, files, template=TEMPLATE, bindings=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        self.notes = os.path.join(tmp, "notes")
        os.makedirs(self.notes)
        for name, text in files.items():
            self.write_note(name, text)
        root = jobs.create_ember(os.path.join(tmp, "embers"), template, "test",
                                 bindings if bindings is not None else {"notes": self.notes}, NOW)
        ember = jobs.Ember(root)
        self.addCleanup(ember.close)
        return ember

    def write_note(self, name, text):
        with open(os.path.join(self.notes, name), "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, ember, *parts):
        with open(os.path.join(ember.root, *parts), encoding="utf-8") as f:
            return f.read()
