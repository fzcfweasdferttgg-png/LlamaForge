"""Single-source markdown renderer + content model (pure stdlib, strict subset)."""
import html, os, re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def content_dir():
    import config
    return config.load().get("docs_dir") or os.path.join(_ROOT, "docs", "content")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _slug(text):
    s = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


# ---------- inline spans ----------
_CODE = re.compile(r"`([^`]+)`")
_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITAL = re.compile(r"\*([^*]+)\*")


def _safe_url(u):
    first = u.split("/", 1)[0]
    if u.lower().startswith(("http://", "https://", "mailto:")) or ":" not in first:
        return u                     # http(s)/mailto, or a relative URL (slug.md, docs/img/x, #anchor)
    return "#"                       # neutralize javascript:, data:, etc.


def _inline(text, images=True):
    out = html.escape(text, quote=True)           # escape first (strict/safe)
    codes = []

    def _stash(m):
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    out = _CODE.sub(_stash, out)                   # protect code spans
    if images:
        out = _IMG.sub(lambda m: '<img alt="%s" src="%s">' % (m.group(1), _safe_url(m.group(2))), out)
    else:                                          # a link instead: viewing fetches nothing remote
        out = _IMG.sub(lambda m: '<a href="%s">%s</a>' % (_safe_url(m.group(2)), m.group(1) or "image"), out)
    out = _LINK.sub(lambda m: '<a href="%s">%s</a>' % (_safe_url(m.group(2)), m.group(1)), out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITAL.sub(r"<em>\1</em>", out)
    out = re.sub(r"\x00(\d+)\x00",
                 lambda m: "<code>%s</code>" % codes[int(m.group(1))], out)
    return out


def _render_list(lines, i, n, images=True):
    def kind(l):
        if re.match(r"\s*\d+\.\s+", l):
            return "ol"
        if re.match(r"\s*[-*]\s+", l):
            return "ul"
        return None

    def indent(l):
        return len(l) - len(l.lstrip(" "))

    top, base = kind(lines[i]), (len(lines[i]) - len(lines[i].lstrip(" ")))
    items = []
    while i < n and kind(lines[i]) and indent(lines[i]) == base:
        text = re.sub(r"\s*(?:[-*]|\d+\.)\s+", "", lines[i], count=1)
        i += 1
        sub = ""
        if i < n and kind(lines[i]) and indent(lines[i]) > base:
            sub, i = _render_list(lines, i, n, images)
        items.append("<li>%s%s</li>" % (_inline(text.strip(), images), sub))
    return "<%s>%s</%s>" % (top, "".join(items), top), i


def render(md, images=True):
    """Markdown subset -> HTML, escaped before formatting. images=False turns
    image syntax into plain links (pages whose text came from a model or an
    outside source must not make the viewer fetch remote URLs)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, para, i, n = [], [], 0, len(lines)

    def flush():
        if para:
            out.append("<p>%s</p>" % _inline(" ".join(para).strip(), images))
            para.clear()

    def cells(row):
        return [c.strip() for c in row.strip().strip("|").split("|")]

    while i < n:
        line = lines[i]
        if line.startswith("```"):
            flush()
            lang = line[3:].strip()
            i += 1
            code = []
            while i < n and not lines[i].startswith("```"):
                code.append(lines[i]); i += 1
            i += 1
            cls = ' class="lang-%s"' % _slug(lang) if lang else ""
            out.append("<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(code))))
            continue
        m = re.match(r"(#{1,4})\s+(.*)$", line)
        if m:
            flush()
            lvl, txt = len(m.group(1)), m.group(2).strip()
            out.append('<h%d id="%s">%s</h%d>' % (lvl, _slug(txt), _inline(txt, images), lvl))
            i += 1
            continue
        if re.match(r"(-{3,}|\*{3,})\s*$", line):
            flush(); out.append("<hr>"); i += 1; continue
        if line.startswith(">"):
            flush()
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i][1:].lstrip()); i += 1
            adm = re.match(r"\[!(NOTE|TIP|WARNING)\]\s*(.*)$", buf[0]) if buf else None
            if adm:
                body = [adm.group(2)] + buf[1:]
                out.append('<div class="admon admon-%s"><p>%s</p></div>'
                           % (adm.group(1).lower(), _inline(" ".join(body).strip(), images)))
            else:
                out.append("<blockquote><p>%s</p></blockquote>"
                           % _inline(" ".join(buf).strip(), images))
            continue
        if "|" in line and i + 1 < n and re.match(
                r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", lines[i + 1]):
            flush()
            header = cells(line); i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(cells(lines[i])); i += 1
            th = "".join("<th>%s</th>" % _inline(c, images) for c in header)
            trs = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c, images) for c in r)
                          for r in rows)
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                       % (th, trs))
            continue
        if re.match(r"\s*[-*]\s+", line) or re.match(r"\s*\d+\.\s+", line):
            flush()
            html_list, i = _render_list(lines, i, n, images)
            out.append(html_list)
            continue
        if not line.strip():
            flush(); i += 1; continue
        para.append(line.strip()); i += 1
    flush()
    return "\n".join(out)


# ---------- content model ----------
_FM = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_SECTION_ORDER = {"whats-new": 0, "getting-started": 1, "guides": 2,
                  "reference": 3, "faq": 4, "troubleshooting": 5}
_HEADING = re.compile(r"(?m)^(#{1,4})\s+(.*)$")


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def parse_frontmatter(md):
    m = _FM.match(md.replace("\r\n", "\n"))
    if not m:
        return {}, md
    meta = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def _pages():
    d = content_dir()
    res = []
    if not os.path.isdir(d):
        return res
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        meta, body = parse_frontmatter(_read(os.path.join(d, fn)))
        res.append({"slug": fn[:-3], "title": meta.get("title", fn[:-3]),
                    "section": meta.get("section", "guides"),
                    "order": _int(meta.get("order", 0)), "_body": body})
    res.sort(key=lambda p: (_SECTION_ORDER.get(p["section"], 9), p["order"], p["title"]))
    return res


def list_pages():
    return [{k: p[k] for k in ("slug", "title", "section", "order")} for p in _pages()]


def page(slug):
    for p in _pages():
        if p["slug"] == slug:
            toc = [{"level": len(m.group(1)), "text": m.group(2).strip(),
                    "id": _slug(m.group(2).strip())}
                   for m in _HEADING.finditer(p["_body"])]
            return {"title": p["title"], "html": render(p["_body"]), "toc": toc}
    return None


def search_index():
    return [{"slug": p["slug"], "title": p["title"],
             "headings": [m.group(2).strip() for m in _HEADING.finditer(p["_body"])]}
            for p in _pages()]


def manifest():
    secs, order = {}, []
    for p in list_pages():
        sid = p["section"]
        if sid not in secs:
            secs[sid] = {"id": sid, "title": sid.replace("-", " ").title(), "pages": []}
            order.append(sid)
        secs[sid]["pages"].append({"slug": p["slug"], "title": p["title"]})
    order.sort(key=lambda s: _SECTION_ORDER.get(s, 9))
    return {"sections": [secs[s] for s in order], "search": search_index()}


def _safe_img(name):
    if (not name or "/" in name or "\\" in name or ".." in name
            or ":" in name or os.path.isabs(name)):
        raise ValueError("bad image name: %r" % name)
    base = os.path.realpath(os.path.join(content_dir(), "img"))
    full = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, full]) != base:
        raise ValueError("bad image name: %r" % name)
    return full
