"""Evidence checks: the reliability core of Embers.

The model proposes wiki updates; code decides what is believed. A claim counts
as verified only if it carries a verbatim quote that really occurs in an
immutable raw snapshot. Owners and due dates must appear inside such a quote,
so the model cannot invent who owes what or by when.
"""
import re, unicodedata

from . import reserved_name

MIN_QUOTE    = 12
MAX_TEXT     = 600
MAX_OPS      = 40
MAX_EVIDENCE = 5
OPS          = ("add", "update", "close")
ITEM_KINDS   = ("loop", "fact", "person", "event")
PAGE_RE = re.compile(r"[a-z0-9-]{1,40}/[a-z0-9-]{1,60}")   # <kind>/<slug>
ITEM_RE = re.compile(r"it-[0-9a-f]{8}")                     # also a valid Obsidian block id
RAW_RE  = re.compile(r"[0-9a-f]{12}")

_QUOTES = dict.fromkeys(map(ord, "'\"`\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00ab\u00bb"), None)
_DASHES = dict.fromkeys(map(ord, "-\u2010\u2012\u2013\u2014\u2015"), " ")
HYPHEN  = "\u2011"                       # a hyphen inside a word stays part of it
MINUS   = "\u2212"                       # kept only in front of a digit
_MINUS  = re.compile(r"[-" + MINUS + r"](?=\d)")
_HYPHEN = re.compile(r"(?<=\w)-(?=\w)")
_WORD   = r"\w" + MINUS + HYPHEN                      # characters that glue a match to its neighbours


def _contains(hay, needle):
    """Whole-word containment on normalised text (a minus sign counts as part of a word)."""
    return re.search(r"(?<![" + _WORD + r"])" + re.escape(needle) + r"(?![" + _WORD + r"])", hay) is not None


def normalise(s):
    """NFKC, lowercase, quotes removed, dashes to spaces, whitespace collapsed."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.translate(_QUOTES)
    s = _MINUS.sub(MINUS, s)             # a minus in front of a digit is significant
    s = _HYPHEN.sub(HYPHEN, s)           # so is the hyphen of non-refundable
    s = s.translate(_DASHES)
    return " ".join(s.split())


def check_quote(quote, raw_text):
    q = normalise(quote)
    return len(q) >= MIN_QUOTE and _contains(normalise(raw_text), q)


def page_ok(page, kinds):
    if not (isinstance(page, str) and PAGE_RE.fullmatch(page)):
        return False
    kind, slug = page.split("/")
    return kind in kinds and not reserved_name(kind) and not reserved_name(slug)


def _one_line(s, cap):
    return " ".join(s.split())[:cap] if isinstance(s, str) else ""


def _grounded(value, quotes):
    v = normalise(value)
    return len(v) >= 2 and any(_contains(normalise(q), v) for q in quotes)


def verify_op(op, raws, kinds, known_items=()):
    """Check one proposed op against raws ({sha12: text}).
    Returns (clean_op, None) or (None, reason)."""
    if not isinstance(op, dict):
        return None, "not an object"
    action = op.get("op")
    if action not in OPS:
        return None, f"unknown op {action!r}"
    page = op.get("page")
    if not page_ok(page, kinds):
        return None, f"bad page {page!r}"
    item = op.get("item")
    if action == "add":
        item = None                     # code assigns ids; the model never does
    elif not (isinstance(item, str) and ITEM_RE.fullmatch(item) and item in known_items):
        return None, f"unknown item {item!r}"
    kind = op.get("kind") or "loop"
    if kind not in ITEM_KINDS:
        return None, f"bad kind {kind!r}"
    text = _one_line(op.get("text"), MAX_TEXT)
    if action == "add" and not text:
        return None, "add without text"
    evs = op.get("evidence") if isinstance(op.get("evidence"), list) else []
    evidence = []
    for ev in evs[:MAX_EVIDENCE]:
        if not isinstance(ev, dict):
            continue
        sha, quote = ev.get("raw"), ev.get("quote")
        if isinstance(sha, str) and sha in raws and isinstance(quote, str) and check_quote(quote, raws[sha]):
            evidence.append({"raw": sha, "quote": _one_line(quote, MAX_TEXT)})
    if action == "close" and not evidence:
        return None, "close without passing evidence"
    quotes = [e["quote"] for e in evidence]
    owner, due = op.get("owner"), op.get("due")
    return {"op": action, "page": page, "kind": kind, "item": item, "text": text,
            "owner": _one_line(owner, 80) if isinstance(owner, str) and _grounded(owner, quotes) else "",
            "due": _one_line(due, 40) if isinstance(due, str) and _grounded(due, quotes) else "",
            "evidence": evidence, "verified": bool(evidence)}, None


def verify_batch(update, raws, kinds, known_items=()):
    """Check a whole model reply. Returns (ops, new_pages, rejected), where
    rejected is [(op_index, reason)]."""
    if not isinstance(update, dict):
        return [], [], [(-1, "update is not an object")]
    ops, rejected = [], []
    raw_ops = update.get("ops") if isinstance(update.get("ops"), list) else []
    for i, op in enumerate(raw_ops):
        if i >= MAX_OPS:
            rejected.append((i, "over the op cap"))
            continue
        clean, why = verify_op(op, raws, kinds, known_items)
        if clean:
            ops.append(clean)
        else:
            rejected.append((i, why))
    pages = []
    raw_pages = update.get("new_pages") if isinstance(update.get("new_pages"), list) else []
    for p in raw_pages[:MAX_OPS]:
        if isinstance(p, dict) and page_ok(p.get("page"), kinds):
            pages.append({"page": p["page"],
                          "title": _one_line(p.get("title"), 120) or p["page"],
                          "summary": _one_line(p.get("summary"), MAX_TEXT)})
    return ops, pages, rejected
