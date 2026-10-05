"""Ember markdown -> HTML for the panel.

Pages, briefs and the index are Obsidian-friendly markdown written by wikifs:
model and source text is neutralised (backslash escapes, &lt; entities), items
end in ^it-... block ids, and [^r-...] footnotes point at raw snapshots with
the quote as the link title. docs.render(images=False) does the escaping and
the formatting; this module only translates the ember syntax around it.

It does so through private-use placeholders that are stripped from the input
first, so no page text can forge a placeholder. Everything substituted back is
built here from values that matched a strict pattern (ids, hashes) or went
through html.escape (quotes).
"""
import html, re

import docs

_PUA = re.compile("[\ue000-\ue0ff]")
_MARKER = re.compile(r"^\s*<!-- /?ember:[a-z-]+ -->\s*$")
_FN_DEF = re.compile(r'^\[\^(r-[0-9a-f]{12}(?:-\d{1,4})?)\]: \[raw/([0-9a-f]{12})\.txt\]\([^)\s]*\) "(.*)"\s*$')
_FN_REF = re.compile(r"(?<!\\)\[\^(r-[0-9a-f]{12}(?:-\d{1,4})?)\]")
_ITEM = re.compile(r"^- \[([ x])\] (.*?)\s+\^(it-[0-9a-f]{8})\s*$")
_UNVERIFIED = " *(unverified)*"
_ESC = re.compile(r"\\([!-/:-@\[-`{-~])")          # a backslash before ASCII punctuation
_ENT = {"&lt;": "<", "&gt;": ">"}
_ENT_RE = re.compile("&lt;|&gt;")
_PH = re.compile("\ue000([EF])([0-9a-f]{1,6})\ue001")
_LI_ITEM = re.compile("<li>\ue000I([0-9a-f]{1,6})\ue001")
_PAGE_LINK = re.compile(r'<a href="(?:\.\./)*pages/([a-z0-9-]{1,40}/[a-z0-9-]{1,60})\.md(?:#\^(it-[0-9a-f]{8}))?">')
_RAW_LINK = re.compile(r'<a href="(?:\.\./)*raw/([0-9a-f]{12})\.txt">')


def plain(s):
    """Neutralised one-line markdown back to the text a person reads."""
    s = _ENT_RE.sub(lambda m: _ENT[m.group()], str(s or ""))
    return _ESC.sub(lambda m: m.group(1), s)


def _ph(kind, n):
    return f"\ue000{kind}{n:x}\ue001"


def render(md):
    """HTML for one ember markdown file (page, brief or index)."""
    lines = _PUA.sub("", str(md or "")).replace("\r\n", "\n").split("\n")
    defs, body = {}, []
    for line in lines:
        if _MARKER.match(line):
            continue
        m = _FN_DEF.match(line)
        if m:
            defs[m.group(1)] = (m.group(2), plain(m.group(3)))
            continue
        body.append(line)

    order, items = [], []                  # footnote labels by first use; item line facts

    def ref(m):
        label = m.group(1)
        if label not in defs:
            return m.group(0)
        if label not in order:
            order.append(label)
        return _ph("F", order.index(label))

    out = []
    for line in body:
        m = _ITEM.match(line)
        if m:
            text = m.group(2)
            unverified = _UNVERIFIED in text
            text = text.replace(_UNVERIFIED, "")
            items.append((m.group(3), m.group(1) == "x", unverified))
            line = "- " + _ph("I", len(items) - 1) + text
        line = _FN_REF.sub(ref, line)
        line = _ENT_RE.sub(lambda e: _ENT[e.group()], line)
        line = _ESC.sub(lambda e: _ph("E", ord(e.group(1))), line)
        out.append(line)

    rendered = docs.render("\n".join(out), images=False)

    def item(m):
        iid, closed, unverified = items[int(m.group(1), 16)]
        cls = "ef-item" + (" ef-unverified" if unverified else "") + (" ef-closed" if closed else "")
        box = ('<span class="ef-box" title="done">&#9745;</span>' if closed
               else '<span class="ef-box" title="open">&#9744;</span>')
        tag = ' <span class="ef-tag">unverified</span>' if unverified else ""
        return f'<li class="{cls}" data-item="{iid}">{box}{tag} '

    def back(m):
        kind, n = m.group(1), int(m.group(2), 16)
        if kind == "E":
            return html.escape(chr(n), quote=True)
        raw, quote = defs[order[n]]
        q = html.escape(quote, quote=True)
        return (f'<sup class="ef-fn"><button type="button" class="ef-quote" data-raw="{raw}" '
                f'title="{q}" aria-label="source {n + 1}: {q}">{n + 1}</button></sup>')

    rendered = _LI_ITEM.sub(item, rendered)
    rendered = _PH.sub(back, rendered)
    rendered = _PAGE_LINK.sub(
        lambda m: f'<a href="#" class="ef-link" data-page="{m.group(1)}"'
                  + (f' data-item="{m.group(2)}"' if m.group(2) else "") + ">", rendered)
    rendered = _RAW_LINK.sub(lambda m: f'<a href="#" class="ef-rawlink" data-raw="{m.group(1)}">', rendered)
    if defs:
        listed = order + [k for k in defs if k not in order]
        lis = "".join(
            f'<li><button type="button" class="ef-quote" data-raw="{defs[k][0]}">raw/{defs[k][0]}.txt</button> '
            f'<q>{html.escape(defs[k][1])}</q></li>' for k in listed)
        rendered += (f'\n<details class="ef-sources"><summary>Sources ({len(listed)})</summary>'
                     f"<ol>{lis}</ol></details>")
    return _PUA.sub("", rendered)
