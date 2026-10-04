"""The ember's wiki folder: plain markdown that stays valid in Obsidian.

Layout: SCHEMA.md, index.md, log.md, raw/<sha12>.txt (immutable snapshots),
pages/<kind>/<slug>.md, briefs/YYYY-MM-DD.md. Ember-owned text lives between
marker comments; anything a person writes outside the markers is never touched.

Hardening: every path is checked to stay inside the wiki root (realpath +
commonpath), writes are atomic, and any model/source text written into markdown
is neutralised so it cannot forge the markers, block ids or footnotes that this
module parses back.
"""
import datetime as dt, hashlib, os, re

import atomicio
from . import reserved_name
from . import verify


class WikiError(ValueError):
    pass


def _start(name):
    return f"<!-- ember:{name} -->"


def _end(name):
    return f"<!-- /ember:{name} -->"


def _neutralise(s):
    """Defuse comment delimiters (region markers) and ^ (block ids, footnotes)."""
    return (s.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
             .replace("<!--", "&lt;!--").replace("-->", "--&gt;")
             .replace("^", "\\^"))


# Terminal/display hazards for files the user may `cat` (shared with embers_cli):
# CSI (ESC [ ... final); OSC, DCS, SOS, PM, APC strings (ESC ] P X ^ _ ... BEL or ST); other
# ESC sequences (ESC, intermediates, final). Then any remaining C0/C1 control character, DEL,
# the bidi controls that reorder displayed text, zero-width/invisible format characters, line
# and paragraph separators, and the tag block (invisible "ASCII smuggling" characters).
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[\]PX^_][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[ -/]*[0-~]")
CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200b-\u200f\u2028\u2029\u202a-\u202e"
                  r"\u2060\u2066-\u2069\ufeff\U000e0000-\U000e007f]")


def clean(s):
    """Escape sequences removed; whitespace controls (tab, line breaks, ...)
    become spaces; every other CTRL character is dropped."""
    return CTRL.sub(lambda m: " " if m.group().isspace() else "", ANSI.sub("", str(s)))


def _one_line(s, cap):
    return _neutralise(" ".join(clean(s or "").split())[:cap])


_BLOCK_START = re.compile(r"[#>+*=|~:_`-]")      # would open a heading, quote, list, rule, table, fence
_ORDERED = re.compile(r"(\d{1,9})([.)])")
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _html(s):
    """Defuse raw HTML in already one-lined text (tags render in Obsidian)."""
    return s.replace("<", "&lt;").replace(">", "&gt;")


_WWW = re.compile(r"(?i)\b(www)\.")
ZWSP = "\u200b"                                   # zero-width space


def inline(s, cap):
    """Model text as inert inline markdown: one line, neutralised, raw HTML
    defused, Obsidian syntax (#tag, %%comment%%, $math$) escaped, autolinks
    (scheme://, www., user@host) broken with a zero-width space so they read
    the same but do not link, and a leading character that could open a block
    (heading, quote, list, rule, ordered list) escaped. Safe at the start of a
    line, after "- " or after "## "."""
    s = (_html(_one_line(s, cap)).replace("#", "\\#").replace("%%", "\\%\\%").replace("$", "\\$")
         .replace("://", ":" + ZWSP + "//").replace("@", "@" + ZWSP))
    s = _WWW.sub(lambda m: m.group(1) + ZWSP + ".", s)
    if _BLOCK_START.match(s):
        return "\\" + s
    m = _ORDERED.match(s)
    return f"{m.group(1)}\\{s[m.end(1):]}" if m else s


def footnote_quote(quote):
    """A quote for a footnote's link title: one line, neutralised, no double
    quotes (they would end the title), raw HTML defused."""
    return _html(_one_line(quote, 200).replace('"', "'"))


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write(path, text):
    atomicio.write_text(path, text, encoding="utf-8", newline="\n")


def _safe_path(root, *parts):
    """Join parts under root; refuse anything whose real path leaves root."""
    path = os.path.join(root, *parts)
    base = os.path.realpath(root)
    real = os.path.realpath(path)
    try:
        inside = os.path.commonpath([base, real]) == base
    except ValueError:                  # different drives
        inside = False
    if not inside:
        raise WikiError(f"path escapes the wiki: {path!r}")
    return path


def _split_page(page):
    if not (isinstance(page, str) and verify.PAGE_RE.fullmatch(page)):
        raise WikiError(f"bad page name {page!r}")
    kind, slug = page.split("/")
    if reserved_name(kind) or reserved_name(slug):
        raise WikiError(f"reserved page name {page!r}")
    return kind, slug


def init_wiki(root, title, schema_md):
    """Create the folder layout. Existing files (the user may have edited
    SCHEMA.md) are left alone."""
    for sub in ("raw", "pages", "briefs"):
        os.makedirs(_safe_path(root, sub), exist_ok=True)
    defaults = {"SCHEMA.md": schema_md.rstrip() + "\n",
                "index.md": f"# {inline(title, 120)}\n\n{_start('index')}\n{_end('index')}\n",
                "log.md": "# Log\n\n"}
    for name, text in defaults.items():
        path = _safe_path(root, name)
        if not os.path.exists(path):
            _write(path, text)


def page_path(root, page):
    kind, slug = _split_page(page)
    return _safe_path(root, "pages", kind, slug + ".md")


def _find_region(text, name):
    """Locate a region as (start_marker_pos, body_start, body_end, end_marker_end).
    Markers count only as whole lines. Take the first end marker that has a
    start marker after the previous end, paired with the LAST such start, so a
    stray or half-deleted marker never swallows user text. None when there is
    no complete pair."""
    start, end = re.escape(_start(name)), re.escape(_end(name))
    # an optional BOM may precede a marker line; group 1 excludes it so the
    # BOM is preserved when the region is replaced
    starts = list(re.finditer(rf"(?m)^﻿?({start})\r?$", text))
    prev = 0
    for e in re.finditer(rf"(?m)^﻿?({end})\r?$", text):
        between = [m for m in starts if m.start(1) >= prev and m.end(1) <= e.start(1)]
        if between:                     # an unpaired end marker is skipped
            s = between[-1]
            return s.start(1), s.end(1), e.start(1), e.end(1)
        prev = e.end(1)
    return None


def replace_region(text, name, body):
    """Swap the body between this region's markers, or append the region if it
    is absent. Everything outside the markers is preserved byte for byte. Any
    comment delimiter inside body is defused so it cannot close the region."""
    start, end = _start(name), _end(name)
    body = body.replace("<!--", "&lt;!--")
    block = f"{start}\n{body.rstrip()}\n{end}" if body.strip() else f"{start}\n{end}"
    found = _find_region(text, name)
    if not found:
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{sep}{block}\n"
    return text[:found[0]] + block + text[found[3]:]


def read_region(text, name):
    found = _find_region(text, name)
    return text[found[1]:found[2]].strip() if found else ""


def render_items(items, evidence):
    """items: dicts with id/text/owner/due/status/verified; evidence: {item_id:
    [{"raw", "quote"}]}. Returns a checklist with Obsidian block ids (^it-...)
    and footnotes that point at the raw snapshots."""
    lines, labels = [], {}
    for it in items:
        if not (isinstance(it.get("id"), str) and verify.ITEM_RE.fullmatch(it["id"])):
            raise WikiError(f"bad item id {it.get('id')!r}")
        box = "x" if it["status"] == "closed" else " "
        meta = ", ".join(v for v in (inline(it.get("owner"), 80), inline(it.get("due"), 40)) if v)
        line = f"- [{box}] {inline(it['text'], verify.MAX_TEXT)}"
        if meta:
            line += f" — {meta}"
        if not it.get("verified"):
            line += " *(unverified)*"
        refs = []
        for ev in evidence.get(it["id"], []):
            label = footnote_label(labels, ev)
            if label and label not in refs:
                refs.append(label)
        if refs:
            line += " · " + " ".join(f"[^{r}]" for r in refs)
        lines.append(f"{line} ^{it['id']}")
    if labels:
        lines.append("")
        lines += footnotes(labels, "../../raw")
    return "\n".join(lines)


def footnote_label(labels, ev):
    """Label for one (raw, quote) piece of evidence, registered in `labels`
    ({(raw, quote): label}, insertion ordered). The first quote cited from a
    raw is r-<sha>, later different quotes from the same raw r-<sha>-2, -3...
    so two items citing one raw each keep their own quote. None for a bad raw id."""
    raw = ev.get("raw")
    if not (isinstance(raw, str) and verify.RAW_RE.fullmatch(raw)):
        return None
    key = (raw, footnote_quote(ev.get("quote")))
    if key not in labels:
        n = sum(1 for r, _ in labels if r == raw)
        labels[key] = f"r-{raw}" + (f"-{n + 1}" if n else "")
    return labels[key]


def footnotes(labels, raw_dir):
    """Footnote definition lines for footnote_label's `labels`."""
    return [f'[^{label}]: [raw/{raw}.txt]({raw_dir}/{raw}.txt) "{quote}"'
            for (raw, quote), label in labels.items()]


def write_page(root, page, title, summary, items_md):
    """Create the page or refresh only its ember:items region."""
    path = page_path(root, page)
    if os.path.exists(path):
        text = _read(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        path = page_path(root, page)            # re-check after creating dirs
        head = f"# {inline(title, 120) or page}\n\n"
        summary = inline(summary, verify.MAX_TEXT)
        text = head + (f"{summary}\n\n" if summary else "")
    _write(path, replace_region(text, "items", items_md))
    return path


def read_page(root, page):
    path = page_path(root, page)
    return _read(path) if os.path.exists(path) else ""


def list_page_files(root):
    base, out = _safe_path(root, "pages"), []
    if not os.path.isdir(base):
        return out
    for kind in sorted(os.listdir(base)):
        folder = os.path.join(base, kind)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            page = f"{kind}/{name[:-3]}"
            if not name.endswith(".md"):
                continue
            try:
                _split_page(page)
            except WikiError:
                continue
            out.append(page)
    return out


def write_index(root, pages, lint_md=None):
    """pages: [{"page", "title", "summary", "open"}]. Rewrites index.md's
    ember:index region; lint_md (when given) replaces the ember:lint region."""
    path = _safe_path(root, "index.md")
    text = _read(path) if os.path.exists(path) else "# Index\n\n"
    by_kind = {}
    for p in pages:
        kind, _ = _split_page(p["page"])
        by_kind.setdefault(kind, []).append(p)
    out = []
    for kind in sorted(by_kind):
        out.append(f"## {kind}")
        for p in sorted(by_kind[kind], key=lambda p: p["page"]):
            line = f"- [{inline(p.get('title'), 120) or p['page']}](pages/{p['page']}.md)"
            if p.get("summary"):
                line += f": {inline(p['summary'], 200)}"
            if p.get("open"):
                line += f" ({int(p['open'])} open)"
            out.append(line)
        out.append("")
    text = replace_region(text, "index", "\n".join(out))
    if lint_md is not None:
        text = replace_region(text, "lint", lint_md)
    _write(path, text)


def index_head(root, cap=3000):
    path = _safe_path(root, "index.md")
    return _read(path)[:cap] if os.path.exists(path) else ""


def write_brief(root, day, text):
    """Atomically write briefs/<day>.md (day is YYYY-MM-DD) inside the wiki.
    A failed write leaves the previous file for that day untouched."""
    if not (isinstance(day, str) and _DAY_RE.fullmatch(day)):
        raise WikiError(f"bad brief day {day!r}")
    try:
        dt.date.fromisoformat(day)                      # 9999-99-99, 2026-02-30
    except ValueError:
        raise WikiError(f"bad brief day {day!r}") from None
    os.makedirs(_safe_path(root, "briefs"), exist_ok=True)
    path = _safe_path(root, "briefs", day + ".md")      # re-check after creating the folder
    _write(path, text)
    return path


def append_log(root, now, job, summary):
    """Karpathy-style log line, greppable with: grep "^## \\[" log.md"""
    path = _safe_path(root, "log.md")
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"## [{now:%Y-%m-%d %H:%M}] {_one_line(job, 40)} | {_one_line(summary, 500)}\n")


def write_raw(root, text):
    """Store an immutable snapshot named by its content hash. Returns sha12."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    path = _safe_path(root, "raw", sha + ".txt")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, text)
    return sha


def read_raw(root, sha):
    if not isinstance(sha, str) or not verify.RAW_RE.fullmatch(sha):
        raise WikiError(f"bad raw id {sha!r}")
    path = _safe_path(root, "raw", sha + ".txt")
    return _read(path) if os.path.exists(path) else None
