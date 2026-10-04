"""Read-only source adapters. Each returns (items, new_cursor).

An item is {"ref", "title", "text"}: ref is a stable human-readable pointer
(relative path, event UID, feed link, commit sha). Adapters never write
anywhere and cap what they read, because sources can be large or hostile.
"""
import datetime as dt, hashlib, html, http.client, os, re, stat, subprocess, time, urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

MAX_ITEM_BYTES = 32 * 1024
MAX_FILES      = 2000
MAX_EVENTS     = 500
MAX_FEED_ITEMS = 100
MAX_COMMITS    = 200
MAX_DOWNLOAD   = 2 * 1024 * 1024
MAX_WALK       = 50000      # directories + files visited, whatever their type
FETCH_TIMEOUT  = 20         # per socket operation
DOWNLOAD_DEADLINE = 60      # wall-clock limit for a whole download
CHUNK          = 64 * 1024
FIRST_RUN_RELEASES = 30
MAX_MODELS     = 200        # installed models listed in a machine snapshot
TEXT_EXT = (".md", ".txt")
UA = "LlamaForge-Embers/1 (+https://github.com/dadwritestech/LlamaForge)"
ATOM = "{http://www.w3.org/2005/Atom}"


class SourceError(Exception):
    """A source could not be read. The run continues; the source is marked stale."""


def _now():
    return time.monotonic()


def _dict(v):
    """Cursors come from disk; a wrong-typed one means 'start fresh'."""
    return v if isinstance(v, dict) else {}


def clip(text, cap=MAX_ITEM_BYTES):
    """Normalise newlines, collapse runs of spaces/blank lines, cap to `cap`
    UTF-8 bytes without splitting a character."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    data = text.encode("utf-8")
    return text if len(data) <= cap else data[:cap].decode("utf-8", "ignore")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects may only land on http(s). urllib would also follow ftp://."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme.lower() not in ("http", "https"):
            raise SourceError("refusing a redirect to a non-http(s) URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _download(url, opener=None, timeout=FETCH_TIMEOUT):
    if urlparse(url).scheme.lower() not in ("http", "https"):
        raise SourceError("only http(s) URLs are supported")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    deadline = _now() + DOWNLOAD_DEADLINE
    chunks, total = [], 0
    try:
        with (opener or _OPENER.open)(req, timeout=timeout) as r:
            while total <= MAX_DOWNLOAD:
                # read1 returns what has arrived; read(n) would block for the full chunk
                read = r.read1 if isinstance(r, http.client.HTTPResponse) else r.read
                chunk = read(min(CHUNK, MAX_DOWNLOAD + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if _now() > deadline:
                    raise SourceError(f"{url} took too long to download")
    except SourceError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise SourceError(f"could not fetch {url}: {e}") from None
    if total > MAX_DOWNLOAD:
        raise SourceError(f"{url} is larger than {MAX_DOWNLOAD // 1024} KB")
    return b"".join(chunks)


def _read_bytes(binding, opener=None):
    if not isinstance(binding, str) or not binding:
        raise SourceError("no file or URL bound")
    if "://" in binding:
        return _download(binding, opener)
    try:
        with open(binding, "rb") as f:
            data = f.read(MAX_DOWNLOAD + 1)
    except OSError as e:
        raise SourceError(f"could not read {binding}: {e}") from None
    if len(data) > MAX_DOWNLOAD:
        raise SourceError(f"{binding} is larger than {MAX_DOWNLOAD // 1024} KB")
    return data


def _inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:              # different drives on Windows
        return False


def fetch_folder(root, cursor):
    """.md/.txt files under root (dot-dirs skipped, symlinks out of root
    ignored, each real directory visited once). Cursor: {relpath: "mtime_ns:size"};
    old "mtime_seconds:size" values are still honoured."""
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        raise SourceError(f"folder not found: {root}")
    real_root = os.path.realpath(root)
    cursor = _dict(cursor)
    seen, items, visited = {}, [], 0
    real_dirs = {real_root}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # symlinks and junctions that resolve outside the root are never entered
        keep = []
        for d in sorted(dirnames):
            visited += 1
            if visited > MAX_WALK:
                return items, seen
            if d.startswith("."):
                continue
            full = os.path.join(dirpath, d)
            real_d = os.path.realpath(full)
            if not _inside(real_root, real_d):
                continue
            if not os.path.islink(full):        # os.walk never descends symlinks; junctions it does
                if real_d in real_dirs:         # a junction loop or alias already visited
                    continue
                real_dirs.add(real_d)
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            visited += 1
            if visited > MAX_WALK:
                return items, seen
            if name.startswith(".") or not name.lower().endswith(TEXT_EXT):
                continue
            if len(seen) >= MAX_FILES:
                return items, seen
            path = os.path.join(dirpath, name)
            real = os.path.realpath(path)
            if not _inside(real_root, real):
                continue
            try:
                st = os.stat(real)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:      # lone surrogates in a file name
                continue
            sig = f"{st.st_mtime_ns}:{st.st_size}"
            seen[rel] = sig
            if cursor.get(rel) in (sig, f"{int(st.st_mtime)}:{st.st_size}"):
                continue
            try:
                with open(real, "rb") as f:
                    data = f.read(MAX_ITEM_BYTES)
            except OSError:
                continue
            items.append({"ref": rel, "title": rel,
                          "text": clip(f"File: {rel}\n\n" + data.decode("utf-8", "replace"))})
    return items, seen


def _ics_lines(text):
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _ics_unescape(v):
    return re.sub(r"\\([\\,;nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v or "")


def _ics_date(v):
    m = re.match(r"(\d{4})(\d{2})(\d{2})", v or "")
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError:
        return None


def parse_ics(text, today):
    """VEVENTs whose DTSTART falls within -7..+30 days of today (recurring
    events are matched on their first DTSTART only in v1)."""
    events, cur, depth = [], None, 0
    for line in _ics_lines(text):
        up = line.upper()
        if up.startswith("BEGIN:"):
            if cur is not None:
                depth += 1                      # VALARM etc. inside the event
            elif up[6:].strip() == "VEVENT":
                cur, depth = {}, 0
        elif up.startswith("END:"):
            if cur is not None:
                if depth:
                    depth -= 1
                else:
                    events.append(cur)
                    cur = None
        elif cur is not None and depth == 0 and ":" in line:
            head, value = line.split(":", 1)
            cur.setdefault(head.split(";", 1)[0].upper(), value)
    lo, hi = today - dt.timedelta(days=7), today + dt.timedelta(days=30)
    out = []
    for ev in events:
        day = _ics_date(ev.get("DTSTART"))
        if day and lo <= day <= hi:
            out.append(ev)
            if len(out) >= MAX_EVENTS:
                break
    return out


def fetch_ics(binding, cursor, now, opener=None):
    """Cursor: {uid: LAST-MODIFIED or DTSTAMP}."""
    text = _read_bytes(binding, opener).decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text.upper():
        raise SourceError("not an iCalendar file")
    cursor = _dict(cursor)
    seen, items = {}, []
    for ev in parse_ics(text, now.date()):
        title = _ics_unescape(ev.get("SUMMARY")) or "(no title)"
        uid = _ics_unescape(ev.get("UID")) or f"{ev.get('DTSTART', '')}-{title}"
        version = ev.get("LAST-MODIFIED") or ev.get("DTSTAMP") or ""
        seen[uid] = version
        if cursor.get(uid) == version:
            continue
        lines = [f"Event: {title}", f"Start: {ev.get('DTSTART', '')}"]
        if ev.get("DTEND"):
            lines.append(f"End: {ev['DTEND']}")
        if ev.get("LOCATION"):
            lines.append(f"Where: {_ics_unescape(ev['LOCATION'])}")
        if ev.get("DESCRIPTION"):
            lines += ["", _ics_unescape(ev["DESCRIPTION"])]
        items.append({"ref": uid, "title": title, "text": clip("\n".join(lines))})
    return items, seen


def _strip_html(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def parse_feed(data):
    """RSS 2.0 or Atom entries. Any DTD is refused outright: without one,
    expat cannot expand custom entities (no billion-laughs, no XXE). The check
    is a plain byte search, so a feed that merely quotes "<!DOCTYPE" inside
    CDATA is rejected too: refusing a few odd-but-harmless feeds is the price of
    never parsing a DTD."""
    flat = data.replace(b"\x00", b"").lower()      # also catches UTF-16/32 spellings
    if b"<!doctype" in flat or b"<!entity" in flat:
        raise SourceError("feed contains a DTD; refusing to parse it")
    decl = re.match(rb"(?:\xef\xbb\xbf|\xff\xfe|\xfe\xff)?\s*<\?xml[^>]*?encoding\s*=\s*[\"']([^\"']+)", flat)
    if decl and decl[1].decode("ascii", "replace") not in (
            "utf-8", "utf8", "us-ascii", "ascii", "iso-8859-1", "latin1", "utf-16", "utf-32"):
        raise SourceError("feed uses an unsupported text encoding")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, RecursionError, LookupError) as e:
        raise SourceError(f"feed is not valid XML ({e})") from None
    entries = []
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = e.find(ATOM + "link")
            entries.append({"id": (e.findtext(ATOM + "id") or "").strip(),
                            "link": ((link.get("href") if link is not None else "") or "").strip(),
                            "title": e.findtext(ATOM + "title") or "",
                            "date": e.findtext(ATOM + "updated") or e.findtext(ATOM + "published") or "",
                            "body": e.findtext(ATOM + "summary") or e.findtext(ATOM + "content") or ""})
    else:
        for it in root.iter("item"):
            entries.append({"id": (it.findtext("guid") or "").strip(),
                            "link": (it.findtext("link") or "").strip(),
                            "title": it.findtext("title") or "",
                            "date": it.findtext("pubDate") or "",
                            "body": it.findtext("description") or ""})
    return entries[:MAX_FEED_ITEMS]


def fetch_rss(binding, cursor, now, opener=None):
    """Cursor: {"seen": [keys, newest first, at most 500]}."""
    before = _dict(cursor).get("seen")
    before = [k for k in before if isinstance(k, str)] if isinstance(before, list) else []
    seen_before = set(before)
    items, keys = [], []
    for e in parse_feed(_read_bytes(binding, opener)):
        title = _strip_html(e["title"])
        key = e["id"] or e["link"] or title
        if not key:
            continue
        keys.append(key)
        if key in seen_before:
            continue
        text = "\n".join([f"Title: {title}", f"Link: {e['link']}", f"Date: {e['date'].strip()}",
                          "", _strip_html(e["body"])])
        items.append({"ref": e["link"] or key, "title": title or key, "text": clip(text)})
    current = set(keys)
    return items, {"seen": (keys + [k for k in before if k not in current])[:500]}


def fetch_git(repo, cursor, now, run=subprocess.run):
    """Commits from the last 30 days, newest first, stopping at the cursor's
    last seen sha. argv only, never a shell. Cursor: {"last": sha}."""
    if not isinstance(repo, str) or not repo or repo.startswith("-") or not os.path.isdir(repo):
        raise SourceError(f"repository not found: {repo}")
    since = (now - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    args = ["git", "--no-pager", "-C", repo,
            # neutralise the repo-config hooks we know of (fsmonitor, hooks, gpg signature
            # verification); log with no -p/--ext-diff runs no other configured program
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=", "-c", "log.showSignature=false",
            "log", "--no-show-signature", f"--max-count={MAX_COMMITS}", f"--since={since}",
            "-z", "--format=%H%n%an%n%aI%n%B"]     # NUL-terminated records; a message cannot contain NUL
    try:
        r = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                env=dict(os.environ, GIT_PAGER="cat", PAGER="cat", GIT_TERMINAL_PROMPT="0"),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        raise SourceError(f"git failed: {e}") from None
    if r.returncode != 0:
        raise SourceError(f"git log failed: {r.stderr.strip()[:200]}")
    last = _dict(cursor).get("last")
    last = last if isinstance(last, str) else None
    head, items = None, []
    for rec in r.stdout.split("\0"):
        parts = rec.lstrip("\n").split("\n", 3)
        if len(parts) < 4 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]):
            continue
        sha, author, when = parts[0], parts[1].strip(), parts[2].strip()
        subject, _, body = parts[3].strip().partition("\n")
        subject, body = subject.strip(), body.strip()
        head = head or sha
        if sha == last:
            break
        items.append({"ref": sha[:12], "title": subject,
                      "text": clip(f"Commit {sha[:12]} by {author} on {when}\n\n{subject}\n\n{body}")})
    return items, {"last": head or last}


def fetch_llamacpp(binding, cursor, now, releases=None):
    """New llama.cpp builds from feed.py. The first run takes only the newest
    FIRST_RUN_RELEASES so a fresh ember isn't flooded. Cursor: {"build": N}."""
    import feed
    try:
        rels = releases if releases is not None else feed.llama_releases()
    except Exception as e:      # network, rate limit, bad JSON: the source goes stale
        raise SourceError(f"could not read the llama.cpp release feed: {e}") from None
    if not isinstance(rels, list):
        raise SourceError("the llama.cpp release feed was not a list")
    try:
        last = max(0, int(_dict(cursor).get("build", 0)))
    except (TypeError, ValueError, OverflowError):
        last = 0
    def parse(rel):
        try:
            return feed.parse_release(rel) if isinstance(rel, dict) else None
        except Exception:               # a release with odd field types is skipped
            return None
    parsed = sorted((r for r in map(parse, rels) if r and r["build"] > last),
                    key=lambda r: -r["build"])
    parsed = parsed[:FIRST_RUN_RELEASES if not last else MAX_FEED_ITEMS]
    items = [{"ref": r["tag"], "title": r["title"],
              "text": clip(f"llama.cpp release {r['tag']} ({r['date']})\n\n{r['title']}\n\n"
                           f"{r['url']}\n{r['pr'] or ''}")} for r in parsed]
    return items, {"build": max([last] + [r["build"] for r in parsed])}


_SPLIT_GGUF = re.compile(r"^(.*)-00001-of-(\d{5})\.gguf$", re.IGNORECASE)


def _model_bytes(path):
    """Size of a model file; a split GGUF (-00001-of-0000N) counts all its parts.
    None when any part is missing or unreadable."""
    m = _SPLIT_GGUF.match(os.path.basename(path))
    parts = [path] if not m else [os.path.join(os.path.dirname(path), f"{m.group(1)}-{i:05d}-of-{m.group(2)}.gguf")
                                  for i in range(1, int(m.group(2)) + 1)]
    try:
        return sum(os.path.getsize(p) for p in parts)
    except (OSError, ValueError):
        return None


def installed_models(sections, unit=1e9):
    """models.ini sections -> [{"id", "file", "size_gb"}], sorted by id. Only the
    file's basename leaves this function: a snapshot never carries a path."""
    out = []
    for sid, keys in sorted((sections or {}).items()):
        path = keys.get("model") if isinstance(keys, dict) else None
        if sid == "*" or not isinstance(path, str) or not path.strip():
            continue
        size = _model_bytes(path.strip())
        out.append({"id": sid, "file": os.path.basename(path.strip().replace("\\", "/")),
                    "size_gb": round(size / unit, 1) if size is not None else None})
        if len(out) >= MAX_MODELS:
            break
    return out


def _probe_machine():
    import config, hardware
    return {"gpus": hardware.detect_gpus(), "ram_gb": hardware.detect_ram_gb(),
            "models": installed_models(config.read_sections())}


def machine_text(snap):
    """The snapshot as stable text: the same machine always reads the same."""
    lines = ["This machine (snapshot)"]
    gpus = sorted(snap["gpus"], key=lambda g: g.get("index", 0))
    for g in gpus:
        mib = g.get("vram_mib")
        vram = f"{round(mib / 1024)} GB VRAM ({mib} MiB)" if isinstance(mib, int) and mib > 0 else "VRAM unknown"
        lines.append(f"GPU {g.get('index', 0)}: {g.get('name') or 'unknown GPU'}, {vram}")
    if not gpus:
        lines.append("GPU: none detected (CPU or Apple Silicon)")
    ram = snap.get("ram_gb")
    lines.append(f"System RAM: {round(ram)} GB" if isinstance(ram, (int, float)) and ram > 0 else "System RAM: unknown")
    models = sorted(snap["models"], key=lambda m: m["id"])[:MAX_MODELS]
    lines.append(f"Installed models ({len(models)}):")
    for m in models:
        size = f"{m['size_gb']} GB" if isinstance(m.get("size_gb"), (int, float)) else "size unknown"
        lines.append(f"- {m['id']}: {m['file']}, {size}")
    return "\n".join(lines) + "\n"


def fetch_machine(binding, cursor, now, probe=None):
    """One snapshot of this machine's GPUs, RAM and installed models, only when it
    changed since the last run. Cursor: {"sha": first 16 hex of its sha256}."""
    try:
        snap = (probe or _probe_machine)()
        text = clip(machine_text(snap)) + "\n"     # clip() strips the final newline
    except Exception as e:      # nvidia-smi hung, odd models.ini, a probe returning garbage
        raise SourceError(f"could not read this machine's hardware: {type(e).__name__}: {e}"[:300]) from None
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if _dict(cursor).get("sha") == sha[:16]:
        return [], cursor
    return [{"ref": f"machine-{sha[:12]}", "title": "Machine snapshot", "text": text}], {"sha": sha[:16]}


def fetch(stype, binding, cursor, now, **kw):
    """Dispatch to an adapter. kw passes test seams (opener, run, releases, probe)."""
    if stype == "folder":
        return fetch_folder(binding, cursor)
    if stype == "ics":
        return fetch_ics(binding, cursor, now, kw.get("opener"))
    if stype == "rss":
        return fetch_rss(binding, cursor, now, kw.get("opener"))
    if stype == "git":
        return fetch_git(binding, cursor, now, kw.get("run", subprocess.run))
    if stype == "llamacpp":
        return fetch_llamacpp(binding, cursor, now, kw.get("releases"))
    if stype == "machine":
        return fetch_machine(binding, cursor, now, kw.get("probe"))
    raise SourceError(f"unknown source type {stype!r}")
