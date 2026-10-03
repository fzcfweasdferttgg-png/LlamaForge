"""The ember's wiki folder: plain markdown that stays valid in Obsidian.

Layout: SCHEMA.md, index.md, log.md, raw/<sha12>.txt (immutable snapshots),
pages/<kind>/<slug>.md, briefs/YYYY-MM-DD.md. Ember-owned text lives between
marker comments; anything a person writes outside the markers is never touched.

Hardening: every path is checked to stay inside the wiki root (realpath +
commonpath), writes are atomic, and any model/source text written into markdown
is neutralised so it cannot forge the markers, block ids or footnotes that this
module parses back.
"""
import hashlib, os

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
    return (s.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
             .replace("^", "\\^"))


def _one_line(s, cap):
    return _neutralise(" ".join(str(s or "").split())[:cap])


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
                "index.md": f"# {_one_line(title, 120)}\n\n{_start('index')}\n{_end('index')}\n",
                "log.md": "# Log\n\n"}
    for name, text in defaults.items():
        path = _safe_path(root, name)
        if not os.path.exists(path):
            _write(path, text)


def page_path(root, page):
    kind, slug = _split_page(page)
    return _safe_path(root, "pages", kind, slug + ".md")


def replace_region(text, name, body):
    """Swap the body between this region's markers, or append the region if it
    is absent. Everything outside the markers is preserved byte for byte. Any
    comment delimiter inside body is defused so it cannot close the region."""
    start, end = _start(name), _end(name)
    body = body.replace("<!--", "&lt;!--")
    block = f"{start}\n{body.rstrip()}\n{end}" if body.strip() else f"{start}\n{end}"
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{sep}{block}\n"
    return text[:i] + block + text[j + len(end):]


def read_region(text, name):
    start, end = _start(name), _end(name)
    i = text.find(start)
    j = text.find(end, i + len(start)) if i >= 0 else -1
    return text[i + len(start):j].strip() if i >= 0 and j >= 0 else ""


def render_items(items, evidence):
    """items: dicts with id/text/owner/due/status/verified; evidence: {item_id:
    [{"raw", "quote"}]}. Returns a checklist with Obsidian block ids (^it-...)
    and footnotes that point at the raw snapshots."""
    lines, notes = [], {}
    for it in items:
        if not (isinstance(it.get("id"), str) and verify.ITEM_RE.fullmatch(it["id"])):
            raise WikiError(f"bad item id {it.get('id')!r}")
        box = "x" if it["status"] == "closed" else " "
        meta = ", ".join(v for v in (_one_line(it.get("owner"), 80), _one_line(it.get("due"), 40)) if v)
        line = f"- [{box}] {_one_line(it['text'], verify.MAX_TEXT)}"
        if meta:
            line += f" — {meta}"
        if not it.get("verified"):
            line += " *(unverified)*"
        refs = []
        for ev in evidence.get(it["id"], []):
            if not (isinstance(ev.get("raw"), str) and verify.RAW_RE.fullmatch(ev["raw"])):
                continue
            label = f"r-{ev['raw']}"
            if label not in refs:
                refs.append(label)
            notes.setdefault(label, ev)
        if refs:
            line += " · " + " ".join(f"[^{r}]" for r in refs)
        lines.append(f"{line} ^{it['id']}")
    if notes:
        lines.append("")
        for label, ev in notes.items():
            quote = _one_line(ev.get("quote"), 200).replace('"', "'")
            lines.append(f'[^{label}]: [raw/{ev["raw"]}.txt](../../raw/{ev["raw"]}.txt) "{quote}"')
    return "\n".join(lines)


def write_page(root, page, title, summary, items_md):
    """Create the page or refresh only its ember:items region."""
    path = page_path(root, page)
    if os.path.exists(path):
        text = _read(path)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        path = page_path(root, page)            # re-check after creating dirs
        head = f"# {_one_line(title, 120) or page}\n\n"
        summary = _one_line(summary, verify.MAX_TEXT)
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
            line = f"- [{_one_line(p.get('title'), 120) or p['page']}](pages/{p['page']}.md)"
            if p.get("summary"):
                line += f": {_one_line(p['summary'], 200)}"
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
