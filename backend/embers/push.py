"""Opt-in push of a finished brief to the user's phone or chat.

ember.json may hold "push": {"kind": "ntfy"|"webhook", "url": ...}. After a
brief that reached the reader (ok or partial), the brief's title, headline
and first MAX_BULLETS bullets go out as plain text, nothing else: no wiki
pages, no quotes, no raw text. ntfy gets the text as the POST body; a
webhook gets JSON with both "text" (Slack) and "content" (Discord) set.

Sending happens on a daemon thread so a slow server never holds up the
scheduler. The outcome is written to <ember>/push.json, not log.md (a job
may be appending to that at the same moment).
"""
import datetime as dt, json, os, re, threading, urllib.request

import atomicio
from embers import view

KINDS       = ("ntfy", "webhook")
MAX_URL     = 1000
MAX_BULLETS = 3
TIMEOUT     = 10
STATE_FILE  = "push.json"
_URL_RE     = re.compile(r"https?://[^/\s?#@]*[^/\s?#:@][^\s]*", re.I)
_CTRL       = re.compile(r"[\x00-\x20\x7f-\x9f\u00a0\u2028\u2029]")
_TAIL       = re.compile(r"\s*\(\[[a-z0-9/-]+\]\((?:\.\./)*pages/[a-z0-9/-]+\.md(?:#\^it-[0-9a-f]{8})?\)\)"
                         r"(?:\s*\[\^r-[0-9a-f]{12}(?:-\d{1,4})?\])*\s*$")


def validate(conf):
    """{"kind", "url"} cleaned, None when push is off, ValueError when malformed."""
    if conf is None or conf == {}:
        return None
    if not isinstance(conf, dict):
        raise ValueError("push must be an object")
    kind, url = conf.get("kind"), conf.get("url")
    if not kind and not url:
        return None
    if kind not in KINDS:
        raise ValueError("push kind must be ntfy or webhook")
    url = url.strip() if isinstance(url, str) else ""
    if not url or len(url) > MAX_URL or _CTRL.search(url) or not _URL_RE.fullmatch(url):
        raise ValueError("push url must be an http(s) address")
    return {"kind": kind, "url": url}


def _brief_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except (OSError, TypeError, ValueError):
        return []


def parse_brief(path):
    """(headline, bullets) of a brief file as plain text; None when it can't be read."""
    lines = _brief_lines(path)
    if not lines:
        return None
    headline, bullets = "", []
    for line in lines[1:]:
        if line.startswith("- "):
            bullets.append(view.plain(_TAIL.sub("", line[2:])).strip())
        elif not headline and line.strip() and not line.startswith(("#", "[^")):
            headline = view.plain(line).strip()
    return headline, bullets


def message(conf, result):
    """(title, body, bullets) of a brief as plain text."""
    title = " ".join(str(conf.get("name") or conf.get("id") or "Brief").split())[:120]
    parsed = parse_brief(result.get("path"))
    headline, bullets = parsed or (" ".join(str(result.get("headline") or "").split())[:200], [])
    shown = [b for b in bullets if b][:MAX_BULLETS]
    rest = len([b for b in bullets if b]) - len(shown)
    body = "\n".join([headline] + [f"- {b}" for b in shown] + ([f"(+{rest} more)"] if rest > 0 else []))
    return title, body.strip(), shown


def _open(req, timeout):
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as r:
        r.read(4096)
        return r.status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                        # the user named a host; never follow to another


def send(conf, title, body, extra, opener=_open):
    """POST one message. Returns (ok, error); never raises."""
    try:
        if conf["kind"] == "ntfy":
            safe = title.isascii() and title.isprintable() and title
            data = (body if safe else f"{' '.join(title.split())}\n{body}").encode("utf-8")
            headers = {"Title": safe or "LlamaForge brief", "Tags": "fire",
                       "Content-Type": "text/plain; charset=utf-8"}
        else:
            text = f"{title}\n{body}"
            data = json.dumps(dict(extra, title=title, text=text, content=text[:2000])).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(conf["url"], data=data, headers=headers, method="POST")
        status = opener(req, TIMEOUT)
        if not 200 <= int(status) < 300:
            return False, f"HTTP {status}"
        return True, ""
    except Exception as e:                 # urllib raises a zoo; the caller only records it
        return False, f"{type(e).__name__}: {e}"[:300]


def last(root):
    """The last recorded push outcome, or None."""
    try:
        with open(os.path.join(root, STATE_FILE), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _record(root, kind, ok, error, now):
    try:
        atomicio.write_json(os.path.join(root, STATE_FILE),
                            {"at": f"{now:%Y-%m-%dT%H:%M:%S}", "kind": kind, "ok": ok, "error": error})
    except OSError:
        pass


def deliver(root, conf, push_conf, result, now, opener=_open):
    """Build, send and record one brief push. Returns (ok, error)."""
    title, body, bullets = message(conf, result)
    ok, error = send(push_conf, title, body,
                     {"ember": conf.get("id"), "date": f"{now:%Y-%m-%d}", "bullets": bullets}, opener)
    _record(root, push_conf["kind"], ok, error, now)
    return ok, error


def after_run(ember, job, result, now=None, opener=_open):
    """Scheduler hook. Returns the started thread, or None when nothing is sent."""
    if job != "brief" or not isinstance(result, dict) or result.get("status") not in ("ok", "partial"):
        return None
    try:
        push_conf = validate(ember.conf.get("push"))
    except ValueError:
        return None
    if push_conf is None:
        return None
    conf, root, now = dict(ember.conf), ember.root, now or dt.datetime.now()
    t = threading.Thread(target=deliver, args=(root, conf, push_conf, dict(result), now, opener),
                         name=f"ember-push-{conf.get('id')}", daemon=True)
    t.start()
    return t
