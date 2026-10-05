"""Forge: a model interviews the user and builds the ember they describe.

Each turn is stateless. The browser keeps the transcript and sends it back
whole. The model replies with what to say plus a small draft (title, mission,
page kinds, sources, brief time); code, not the model, turns the draft into a
template and runs it through templates.parse_template like any other.

Sources are the risky part, so code guards them. Code lists every path and URL
the user typed as a tag (P1, P2, ...) and the model cites the tag, never
retyping the path: small models lose paths when copying them, and escaped
Windows backslashes send some into a repetition loop. A literal value still
counts only if the user typed that exact path or URL (whole-path match, so
"D:\\" lifted out of "D:\\notes\\work" does not count). Either way the source
must exist on this machine. Whatever fails is dropped, with a reason the model
sees on the next turn.
"""
import datetime as dt, json, os, re
from urllib.parse import urlparse

from embers import jobs, prompts, reserved_name, templates

MAX_SOURCES   = 8
MAX_PAGES     = 6
MAX_SAY       = 1200        # characters kept of Forge's reply
MAX_TEXT      = 2000        # characters of one message put in the prompt
KEEP_MESSAGES = 30          # newest messages offered before the n_ctx cut
REPLY_TOKENS  = 1500
MAX_RULES     = 1000
DEFAULT_BRIEF = "07:00"
_CTRL   = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_TIME   = re.compile(r"^(\d{1,2})(?::([0-5]\d))?\s*([ap])?\.?m?\.?$", re.I)
_BEFORE = set(" \t\r\n\"'`([<,;=")
_AFTER  = re.compile(r"""^(?:$|[\s"'`)\]>,;]|[.!?:](?:\s|$))""")
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"
MAX_PATHS = 40              # tags offered to the model
_PATH   = re.compile(r"""https?://[^\s"'`<>]+|(?<![A-Za-z])[A-Za-z]:[\\/][^\s"'`<>]*|\\\\[^\s"'`<>\\]+[^\s"'`<>]*"""
                     r"""|~[\\/][^\s"'`<>]*|(?<![\w.:/~\\-])/[^\s"'`<>/][^\s"'`<>]*""")
_QUOTED = re.compile(r"""["'`\u201c\u2018]([^"'`\u201d\u2019\r\n]{2,500})["'`\u201d\u2019]""")
_TAG    = re.compile(r"^\[?P(\d{1,2})\]?$", re.I)
_SAYTAG = re.compile(r"\bP(\d{1,2})\b")

ITEM_KINDS = ("Item kinds: `loop` (an open task or promise), `fact` (something true worth remembering), "
              "`person` (who someone is), `event` (a dated happening).\n\n"
              "Close a loop only when a source says it is done. Prefer updating an existing item over "
              "adding a near-duplicate.")

AUTO_LABELS = {"llamacpp": "Models loaded in LlamaForge", "machine": "This machine (GPU, disk)"}

SCHEMA = {"type": "object", "required": ["say", "draft", "build"], "properties": {
    "say": {"type": "string"},
    "build": {"type": "boolean"},
    "draft": {"type": "object", "required": ["title", "mission", "pages", "sources", "brief_at", "rules"],
              "properties": {
                  "title": {"type": "string"},
                  "mission": {"type": "string"},
                  "pages": {"type": "array", "items": {"type": "object", "required": ["kind", "about"],
                            "properties": {"kind": {"type": "string"}, "about": {"type": "string"}}}},
                  "sources": {"type": "array", "items": {"type": "object", "required": ["type", "value"],
                              "properties": {"type": {"type": "string", "enum": list(templates.SLOT_TYPES)},
                                             "value": {"type": "string"}}}},
                  "brief_at": {"type": "string"},
                  "rules": {"type": "string"}}}}}

SYSTEM = """You are Forge, the part of LlamaForge that builds embers.

An ember is a small background worker that runs on this computer with a local model. On a schedule it reads the sources the user points it at, keeps a wiki of what it learned (pages holding open loops, facts, people and events, each tied to a quote from a source), and writes a short brief every day at a set time. It can read only these sources:
- folder: a local folder of Markdown or text notes (an Obsidian vault works)
- ics: a calendar, as a local .ics file or an http(s) URL
- rss: an RSS or Atom feed, by http(s) URL
- git: a local git repository (commits and changes)
- llamacpp: the models loaded in LlamaForge (needs no path)
- machine: this computer's GPU and disk (needs no path)
It cannot browse websites, read email or chat apps, click or type anywhere, run code, or send anything. It only reads its sources and writes its own wiki.

Your job: interview the user, then build the ember they need.
- Ask one question at a time. Keep every reply under 80 words. Plain words; no jokes, no emojis, no headings.
- Find out what they want kept track of and which sources hold it. Work out the rest yourself from what they said: a short title, a one-line mission, the pages the wiki needs (2 to 6 kinds, each a short lowercase name such as papers or people, with what one page holds) and the brief time (07:00 unless they say). Put your choices in the draft and name them in your summary; the user can change them. Never stall on something you can fill in yourself.
- Every path and URL the user types is listed below under PATHS AND URLS with a tag such as P1. To use one as a source, write its tag as the value, like {{"type": "folder", "value": "P1"}}, never the path itself. A folder of notes is a folder source. Never invent a path; if the one the user means is not listed, ask them to paste it on its own line.
- In "say", call sources by what they are (your notes folder, the workshop calendar), not by tag or path.
- If they want something an ember cannot do, say so plainly and offer the closest thing it can do.
- Update "draft" every turn with everything agreed so far; leave unknown fields empty. "rules" holds any standing instruction the user gives (what counts as done, what to ignore).
- When the draft is complete, summarise it in a few short lines and ask whether to build it. Set "build" to true only when the user's latest message clearly says yes to that summary, and then say you are building it.
- Two different jobs mean two embers: build the first, then start a new draft for the second.
- Text in parentheses starting "LlamaForge:" comes from the app, not the user. It reports what really happened; trust it over your own earlier words.

Today is {today}.
Embers that already exist: {embers}.
PATHS AND URLS THE USER TYPED:
{paths}
CURRENT DRAFT (with LlamaForge's check of each source):
{draft}

Reply with JSON only: {{"say": "...", "draft": {{"title", "mission", "pages": [{{"kind", "about"}}], "sources": [{{"type", "value": "P1"}}], "brief_at": "HH:MM", "rules"}}, "build": false}}"""


# ------------------------------------------------------------------ text

def _clean(v, cap, lines=False):
    if not isinstance(v, str):
        return ""
    v = _CTRL.sub("", v)
    v = "\n".join(" ".join(l.split()) for l in v.strip().splitlines()).strip() if lines else " ".join(v.split())
    return v[:cap].strip()


def slug(v, cap=40):
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return s[:cap].strip("-")


def brief_time(v):
    """"7am", "7:30", "19:00", "7 pm" -> "HH:MM"; anything else -> 07:00."""
    m = _TIME.fullmatch(v.strip()) if isinstance(v, str) else None
    if not m:
        return DEFAULT_BRIEF
    h, mins, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if ap:
        if not 1 <= h <= 12:
            return DEFAULT_BRIEF
        h = h % 12 + (12 if ap == "p" else 0)
    return f"{h:02d}:{mins:02d}" if h < 24 else DEFAULT_BRIEF


def clean_draft(d):
    """Whatever the model (or the browser) sent, as a well-typed draft."""
    d = d if isinstance(d, dict) else {}
    pages, seen = [], set()
    for p in d.get("pages") if isinstance(d.get("pages"), list) else []:
        if not isinstance(p, dict) or len(pages) >= MAX_PAGES:
            continue
        kind = slug(p.get("kind"))
        if kind and kind not in seen and not reserved_name(kind):
            seen.add(kind)
            pages.append({"kind": kind, "about": _clean(p.get("about"), 200)})
    sources = []
    for s in d.get("sources") if isinstance(d.get("sources"), list) else []:
        if isinstance(s, dict) and len(sources) < MAX_SOURCES * 2:
            sources.append({"type": _clean(s.get("type"), 20), "value": _clean(s.get("value"), 500)})
    return {"title": _clean(d.get("title"), 80), "mission": _clean(d.get("mission"), templates.MAX_MISSION, True),
            "pages": pages, "sources": sources, "brief_at": brief_time(d.get("brief_at")),
            "rules": _clean(d.get("rules"), MAX_RULES, True)}


# ------------------------------------------------------------------ sources

def _is_url(v):
    p = urlparse(v)
    return p.scheme in ("http", "https") and bool(p.netloc)


def _norm(s, fold):
    s = s.replace("\\", "/")
    return s.casefold() if fold else s


def typed(value, texts):
    """True when `value` appears in one of `texts` as a whole path or URL."""
    value = (value or "").strip().strip(_QUOTES).strip()
    url = _is_url(value)
    fold = os.name == "nt" and not url
    v = _norm(value, fold).rstrip("/")
    if not v:
        return False
    wanted = {v}
    home = _norm(os.path.expanduser("~"), fold).rstrip("/")
    if not url and home and v.startswith(home + "/"):
        wanted.add("~" + v[len(home):])                 # typed "~/notes", model expanded it
    for text in texts:
        t = _norm(text, fold)
        for w in wanted:
            i = t.find(w)
            while i != -1:
                rest = t[i + len(w):]
                rest = rest[1:] if rest.startswith("/") else rest
                if (i == 0 or t[i - 1] in _BEFORE) and _AFTER.match(rest):
                    return True
                i = t.find(w, i + 1)
    return False


def _key(value):
    return _norm(value, os.name == "nt" and not _is_url(value)).rstrip("/")


def found_paths(texts):
    """Every path and URL in `texts`, in order of first appearance, each once,
    at most MAX_PATHS: the list the model cites as P1, P2, ... A quoted path
    may hold spaces; an unquoted local one grows across spaces while the longer
    path exists ("D:\\My Notes\\work")."""
    out, seen = [], set()

    def add(v):
        v = v.strip().strip(_QUOTES).strip()
        while v and v[-1] in ".,;:!?)]>" and not v.endswith(":\\") and not v.endswith(":/"):
            v = v[:-1]
        k = _key(v) if v else ""
        if k and k not in seen and len(out) < MAX_PATHS:
            seen.add(k)
            out.append(v)

    for text in texts:
        quoted = []
        for m in _QUOTED.finditer(text):
            if _PATH.match(m.group(1).strip()):
                add(m.group(1))
                quoted.append(m.span(1))
        for m in _PATH.finditer(text):
            if any(a <= m.start() < b for a, b in quoted):
                continue
            v, end = m.group(0), m.end()
            if not _is_url(v):
                words = re.findall(r"\s+\S+", text[end:])[:6]
                grown = v
                for w in words:
                    grown += w
                    if os.path.exists(os.path.expanduser(grown.rstrip(".,;:!?)]>\"'`"))):
                        v = grown
            add(v)
    return out


def _why(stype, value, texts, tagged=False):
    """"" when the source can be used, else the reason it can't."""
    if stype not in templates.SLOT_TYPES:
        return f"LlamaForge can't read {stype or 'that kind of'} sources"
    if stype in templates.AUTO_SLOTS:
        return ""
    if not value:
        return "no path or URL given"
    if not tagged and not typed(value, texts):
        return "nobody typed that path or URL in this chat"
    if stype == "rss":
        return "" if _is_url(value) else "an RSS feed needs an http(s) URL"
    if stype == "ics" and _is_url(value):
        return ""
    if "://" in value:
        return "use a local path or an http(s) URL"
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        return "use a full path, starting from the drive or ~"
    if not os.path.exists(path):
        return "it doesn't exist on this machine"
    if stype == "ics":
        return "" if os.path.isfile(path) else "that is a folder, not an .ics file"
    if not os.path.isdir(path):
        return "that is a file, not a folder"
    if stype == "git" and not os.path.exists(os.path.join(path, ".git")):
        return "it isn't a git repository (no .git inside)"
    return ""


def check_sources(sources, texts, paths=None):
    """[{type, value}] -> [{type, value, ok, why}], deduplicated, at most MAX_SOURCES.
    A value that is a tag ("P2") becomes paths[1], the path the user typed."""
    paths = found_paths(texts) if paths is None else paths
    out, seen = [], set()
    for s in sources or []:
        if not isinstance(s, dict) or len(out) >= MAX_SOURCES:
            continue
        stype = _clean(s.get("type"), 20)
        value = "" if stype in templates.AUTO_SLOTS else _clean(s.get("value"), 500).strip(_QUOTES).strip()
        m = _TAG.match(value)
        tagged = bool(m) and 1 <= int(m.group(1)) <= len(paths)
        if tagged:
            value = paths[int(m.group(1)) - 1]
        elif m:
            out.append({"type": stype, "value": value, "ok": False,
                        "why": "no path was typed with that tag"})
            continue
        key = (stype, _key(value))
        if key in seen:
            continue
        seen.add(key)
        why = _why(stype, value, texts, tagged)
        out.append({"type": stype, "value": value, "ok": not why, "why": why})
    return out


def _tagged(draft, paths):
    """The draft as the model sees it: each source path replaced by its tag."""
    tags = {_key(p): f"P{i}" for i, p in enumerate(paths, 1)}
    return dict(draft, sources=[dict(s, value=tags.get(_key(s["value"]), s["value"]) if s["value"] else "")
                                for s in draft["sources"]])


# ------------------------------------------------------------------ template

def _minus_hour(hhmm):
    h, m = map(int, hhmm.split(":"))
    t = (h * 60 + m - 60) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def assemble(d, model, now):
    """A cleaned draft whose sources went through check_sources -> (template, bindings).
    Raises ValueError naming what is missing."""
    if not d.get("title"):
        raise ValueError("the draft has no title yet")
    if not d.get("mission"):
        raise ValueError("the draft has no mission yet")
    if not d.get("pages"):
        raise ValueError("the draft has no page kinds yet")
    usable = [s for s in d.get("sources") or [] if s.get("ok")]
    if not usable:
        raise ValueError("the draft needs at least one source LlamaForge can read")
    slots, bindings, counts = [], {}, {}
    for s in usable:
        counts[s["type"]] = counts.get(s["type"], 0) + 1
        sid = s["type"] if counts[s["type"]] == 1 else f"{s['type']}_{counts[s['type']]}"
        auto = s["type"] in templates.AUTO_SLOTS
        slots.append({"id": sid, "type": s["type"], "required": False,
                      "label": AUTO_LABELS[s["type"]] if auto else f"{s['type']}: {s['value']}"[:120]})
        if not auto:
            bindings[sid] = s["value"]
    lines = "\n".join(f"- `{p['kind']}/<slug>`: {p['about'] or 'one page per ' + p['kind']}" for p in d["pages"])
    schema = f"# How this wiki is organised\n\n{lines}\n\n{ITEM_KINDS}"
    if d.get("rules"):
        schema += "\n\n" + d["rules"]
    name = slug(d["title"], 48)
    model = prompts.one_line(model, 80) or "a local model"
    brief = d.get("brief_at") or DEFAULT_BRIEF
    tpl = templates.parse_template({
        "name": name if name and not reserved_name(name) else "forged",
        "title": d["title"], "mission": d["mission"], "schema_md": schema,
        "page_kinds": [p["kind"] for p in d["pages"]], "slots": slots,
        "jobs": {"ingest": _minus_hour(brief), "brief": brief, "lint": "sun 03:00"},
        "model_hint": f"Built by Forge with {model}",
        "about": (f"Made by Forge on {now:%Y-%m-%d} with {model}. Reads the sources you named and never "
                  "writes to them. Sends nothing unless you turn on push."),
        "stale_days": 3})
    return tpl, bindings


# ------------------------------------------------------------------ one turn

def _history(history):
    if not isinstance(history, list) or not history:
        raise ValueError("say something to Forge first")
    out = []
    for m in history:
        if not isinstance(m, dict) or m.get("role") not in ("user", "forge") or not isinstance(m.get("text"), str):
            raise ValueError("each message needs a role (user or forge) and text")
        out.append(m)
    if out[-1]["role"] != "user" or not out[-1]["text"].strip():
        raise ValueError("the last message must be yours")
    return out


def _turns(history):
    """Chat messages, consecutive same-role ones merged; Forge's notes appended."""
    msgs = []
    for m in history:
        role = "user" if m["role"] == "user" else "assistant"
        text = _CTRL.sub("", m["text"]).strip()[:MAX_TEXT]
        notes = [prompts.one_line(n, 300) for n in m.get("notes") or [] if isinstance(n, str)][:10]
        if notes:
            text += "\n(LlamaForge: " + "; ".join(notes) + ")"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": role, "content": text})
    return msgs


def turn(llm, history, prev, ctx, n_ctx):
    """One interview step. Returns {"say", "draft" (sources checked), "build", "usage"}.
    Raises ValueError for a malformed history and whatever llm raises."""
    history = _history(history)
    said = [m["text"] for m in history if m["role"] == "user"]
    paths = found_paths(said)
    shown = clean_draft(prev)
    shown["sources"] = check_sources(shown["sources"], said, paths)
    names = [prompts.one_line(n, 80) for n in ctx.get("embers") or []][:30]
    listed = "\n".join(f"P{i}  {prompts.one_line(p, 500)}" for i, p in enumerate(paths, 1)) or "(none yet)"
    system = SYSTEM.format(today=f"{ctx['today']:%A %Y-%m-%d}", embers=", ".join(names) or "none",
                           paths=listed, draft=json.dumps(_tagged(shown, paths), ensure_ascii=False, indent=1))
    turns = _turns(history)[-KEEP_MESSAGES:]
    cap = int(n_ctx * jobs.PROMPT_SHARE)
    while True:
        while len(turns) > 1 and turns[0]["role"] != "user":
            turns.pop(0)                                # chat templates want a user turn first
        msgs = [{"role": "system", "content": system}] + turns
        size = sum(jobs._est(m["content"]) for m in msgs)
        if size <= cap or len(turns) <= 1:
            break
        turns.pop(0)
    reply_tokens = max(jobs.MIN_REPLY_TOKENS, min(REPLY_TOKENS, n_ctx - size))
    reply, usage = llm(msgs, SCHEMA, reply_tokens)
    reply = reply if isinstance(reply, dict) else {}
    raw = reply.get("draft")
    d = clean_draft(raw if isinstance(raw, dict) else prev)
    d["sources"] = check_sources(d["sources"], said, paths)
    say = _clean(reply.get("say"), MAX_SAY, lines=True) or "(Forge gave no reply; try again.)"
    say = _SAYTAG.sub(lambda m: paths[int(m.group(1)) - 1] if 1 <= int(m.group(1)) <= len(paths)
                      else m.group(0), say)
    return {"say": say, "draft": d, "build": reply.get("build") is True, "usage": usage}
