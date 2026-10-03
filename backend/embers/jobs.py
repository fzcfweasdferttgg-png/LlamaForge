"""Ember pipelines: create/open, ingest, brief, lint.

Pure orchestration. The model (llm), the clock (now) and source reading
(fetch) are passed in, so tests drive everything with fakes. sqlite holds the
truth; markdown pages are re-rendered from it after each committed batch.

Consistency: model calls never happen inside a db transaction. A batch's
verified ops, its raws' "done" mark and the list of pages to re-render
(meta "dirty_pages") commit together; the markdown is written afterwards and a
page leaves the dirty list only once its file is written. A crash between the
commit and the render is repaired at the start of the next run.
"""
import json, os, re

import atomicio
from embers import prompts, reserved_name, sources, templates, verify, wikifs
from embers.llm import RouterUnavailable
from embers.store import Store, ts

ID_RE            = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
BUDGET_SHARE     = 0.45      # of the model's n_ctx, for raw text in one batch
PROMPT_SHARE     = 0.75      # of n_ctx, for the whole prompt; the rest is left for the reply
CHARS_PER_TOKEN  = 4
MIN_REPLY_TOKENS = 256
MAX_REPLY_TOKENS = 4096
CONTEXT_PAGES    = 3
CONTEXT_ITEMS    = 40        # open items shown per matched page
MAX_RAWS_PER_RUN = 200       # pending raws folded per run; the rest wait for the next run
RAW_CAP_BYTES    = 200 * 1024 * 1024
DIRTY_KEY        = "dirty_pages"
ATTEMPTS_KEY     = "raw_attempts"
SOLO_AFTER       = 2         # failed batch attempts before a raw is retried on its own
MAX_ATTEMPTS     = 3         # failed attempts before a raw is marked "failed" for good
MIN_N_CTX        = 2048      # below this the fixed instructions leave no room for sources
MAX_REJECTIONS   = 20        # rejection reasons kept in a run's detail
_ORPHAN_RE       = re.compile(r"[0-9a-f]{12}\.txt")
_WORD = re.compile(r"\w{3,}")
_STOP = frozenset("""the and for that this with from have will your you are was were been into about
there their what when which would could should them they then than just also only over more some
such file not but can our out has had its who how all any""".split())


class Ember:
    """An ember folder opened for work. Use as a context manager or close()."""

    def __init__(self, root):
        self.root = root
        with open(os.path.join(root, "ember.json"), encoding="utf-8") as f:
            self.conf = json.load(f)
        self.template = templates.parse_template(self.conf["template"])
        self.store = Store(os.path.join(root, "embers.db"))

    @property
    def id(self):
        return self.conf["id"]

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def create_ember(embers_dir, template, ember_id, bindings, now):
    """template: a parse_template() result. bindings: {slot_id: path_or_url}.
    Returns the new ember's folder."""
    if not isinstance(ember_id, str) or not ID_RE.fullmatch(ember_id):
        raise ValueError("ember id must be lowercase letters, digits and dashes")
    if reserved_name(ember_id):
        raise ValueError(f"ember id {ember_id!r} is a reserved device name")
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be an object")
    root = os.path.join(embers_dir, ember_id)
    if os.path.exists(os.path.join(root, "ember.json")):
        raise ValueError(f"ember {ember_id!r} already exists")
    slots = {s["id"]: s for s in template["slots"]}
    unknown = sorted(set(bindings) - set(slots))
    if unknown:
        raise ValueError(f"unknown slot(s): {', '.join(map(str, unknown))}")
    clean = {k: str(v).strip() for k, v in bindings.items() if v is not None and str(v).strip()}
    for s in template["slots"]:
        if s["type"] == "rss" and s.get("default") and s["id"] not in clean:
            clean[s["id"]] = s["default"]
    missing = [s["id"] for s in template["slots"]
               if s["required"] and s["type"] != "llamacpp" and s["id"] not in clean]
    if missing:
        raise ValueError(f"required slot(s) not bound: {', '.join(missing)}")
    os.makedirs(root, exist_ok=True)
    wikifs.init_wiki(root, template["title"], template["schema_md"])
    atomicio.write_json(os.path.join(root, "ember.json"), {
        "id": ember_id, "name": template["title"],
        "template": {k: v for k, v in template.items() if k != "dropped"},
        "bindings": clean, "model": "", "enabled": True, "created": ts(now)})
    return root


def _bound(ember):
    for s in ember.template["slots"]:
        b = ember.conf["bindings"].get(s["id"], "")
        if b or s["type"] == "llamacpp":
            yield s, b


def _terms(text, limit=16):
    """The most frequent unicode words (3+ chars, casefolded) of text, for search."""
    counts = {}
    for w in _WORD.findall(text.casefold()):
        if w not in _STOP:
            counts[w] = counts.get(w, 0) + 1
    return " ".join(sorted(counts, key=lambda w: (-counts[w], w))[:limit])


def _est(text):
    """Conservative token count: ~4 ASCII characters per token, but every
    non-ASCII character (CJK, emoji, accents) counted as a whole token."""
    wide = sum(1 for c in text if ord(c) > 127)
    return wide + -(-(len(text) - wide) // CHARS_PER_TOKEN)


def _cut(text, tokens):
    """The longest prefix of text whose _est() is at most `tokens`."""
    if _est(text) <= tokens:
        return text
    lo, hi = 0, len(text)              # _est of a prefix only grows with its length
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _est(text[:mid]) <= tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _batches(raws, budget):
    """Group raws into batches of at most `budget` estimated tokens; a raw over
    the budget is cut to fit and goes alone."""
    batch, size = [], 0
    for r in raws:
        text = _cut(r["text"], budget)
        n = _est(text)
        if batch and size + n > budget:
            yield batch
            batch, size = [], 0
        batch.append(dict(r, text=text))
        size += n
    if batch:
        yield batch


def _schema_text(ember):
    try:
        with open(os.path.join(ember.root, "SCHEMA.md"), encoding="utf-8") as f:
            return f.read()[:templates.MAX_SCHEMA]
    except (OSError, ValueError):
        return ember.template["schema_md"]


def _prompt(tpl, schema, index_head, context, batch, cap):
    """Build the ingest prompt and shrink it until it fits `cap` estimated
    tokens: first the matched pages, then the index excerpt, the schema, and
    last the raw text. Returns (messages, size_in_tokens, batch_as_shown)."""
    context = [dict(p, items=list(p["items"])) for p in context]
    batch = [dict(r) for r in batch]
    while True:
        msgs = prompts.ingest_messages(tpl["mission"], schema, tpl["page_kinds"], index_head, context, batch)
        size = sum(_est(m["content"]) for m in msgs)
        over = size - cap
        if over <= 0:
            return msgs, size, batch
        if context:
            if context[-1]["items"]:
                context[-1]["items"].pop()
            else:
                context.pop()
        elif index_head:
            index_head = _cut(index_head, max(0, _est(index_head) - over))
        elif schema:
            schema = _cut(schema, max(0, _est(schema) - over))
        else:
            longest = max(batch, key=lambda r: _est(r["text"]), default=None)
            if not longest or not longest["text"]:
                return msgs, size, batch   # the fixed instructions alone exceed the cap
            longest["text"] = _cut(longest["text"], max(0, _est(longest["text"]) - over))


def _render_page(ember, page):
    st = ember.store
    items = st.items_for_page(page)
    p = st.get_page(page) or {"title": page, "summary": ""}
    wikifs.write_page(ember.root, page, p["title"], p["summary"],
                      wikifs.render_items(items, st.evidence_for([i["id"] for i in items])))


def _write_index(ember, lint_md=None):
    wikifs.write_index(ember.root, ember.store.all_pages(), lint_md)


def _dirty(st):
    try:
        v = st.get_meta(DIRTY_KEY, [])
    except (ValueError, RecursionError):   # the list itself is lost: treat every page as dirty
        return [p["page"] for p in st.all_pages()]
    return [p for p in v if isinstance(p, str)] if isinstance(v, list) else []


def _attempts(st):
    """meta "raw_attempts": {sha: {"attempts": n, "error": str}} for raws whose batch failed."""
    try:
        v = st.get_meta(ATTEMPTS_KEY, {})
    except (ValueError, RecursionError):
        return {}
    return {k: a for k, a in v.items() if isinstance(a, dict) and isinstance(a.get("attempts"), int)} \
        if isinstance(v, dict) else {}


def raw_failures(ember):
    """Raws whose batches failed: {sha: {"attempts": n, "error": last_error}}."""
    return _attempts(ember.store)


def _mark_dirty(st, pages):
    """Call inside the transaction that changed these pages."""
    st.set_meta(DIRTY_KEY, sorted(set(_dirty(st)) | set(pages)))


def _flush_dirty(ember):
    """Render every dirty page and the index, then clear the pages that made it
    to disk. Returns error strings; pages that failed stay dirty for next time."""
    st = ember.store
    dirty = _dirty(st)
    if not dirty:
        return []
    errors, done = [], set()
    for page in dirty:
        try:
            _render_page(ember, page)
            done.add(page)
        except wikifs.WikiError as e:      # a bad name can never render: drop it
            errors.append(f"render {page}: {e}")
            done.add(page)
        except (OSError, ValueError) as e:
            errors.append(f"render {page}: {e}")
    try:
        _write_index(ember)
    except (OSError, ValueError) as e:
        errors.append(f"index: {e}")
        return errors                      # keep everything dirty so the index is redone too
    with st.db:
        st.set_meta(DIRTY_KEY, sorted(set(_dirty(st)) - done))
    return errors


def _apply(ember, ops, new_pages, now):
    """Write verified ops to sqlite (caller holds the transaction).
    Returns the set of pages whose markdown must be re-rendered."""
    st, touched = ember.store, set()
    meta = {p["page"]: p for p in new_pages}
    for op in ops:
        if op["op"] == "add":
            page = op["page"]
            m = meta.get(page, {})
            st.ensure_page(page, m.get("title") or page.split("/")[1].replace("-", " ").title(),
                           m.get("summary", ""), now)
            iid = st.new_item_id(page, op["text"], now)
            st.add_item(iid, page, op["kind"], op["text"], op["owner"], op["due"], op["verified"], now)
        else:
            iid = op["item"]
            old = st.get_item(iid)
            if old is None:
                continue
            page = old["page"]
            fields = {}
            # Unverified text never overwrites an item that is already verified.
            if op["op"] == "update" and op["text"] and (op["verified"] or not old["verified"]):
                fields["text"] = op["text"]
            if op["owner"]:
                fields["owner"] = op["owner"]
            if op["due"]:
                fields["due"] = op["due"]
            if op["verified"]:
                fields["verified"] = 1
            if op["op"] == "close":
                fields["status"] = "closed"
            if fields or op["evidence"]:
                st.update_item(iid, now, **fields)
        for ev in op["evidence"]:
            st.add_evidence(iid, ev["raw"], ev["quote"], now)
        touched.add(page)
    for page in touched:
        st.index_page(page)
    return touched


def _err(e):
    return f"{type(e).__name__}: {e}"


def ingest(ember, llm, now, fetch=sources.fetch, n_ctx=8192):
    """Fetch new source items into raws, then fold pending raws into the wiki
    batch by batch. Returns a stats dict with "status" and "summary".
    The run row is always closed: on an unexpected error it is finished as
    "failed" with the error, then the error propagates."""
    st = ember.store
    with st.db:
        st.abort_running("ingest", now)    # rows left "running" by a process that died
        run = st.start_run("ingest", now)
    errors = []
    try:
        return _ingest(ember, llm, now, fetch, n_ctx, run, errors)
    except BaseException as e:
        try:
            st.db.rollback()
            with st.db:
                st.finish_run(run, now, "failed", "; ".join(errors + [_err(e)]))
        except Exception:
            pass                           # the original error matters more
        raise


def _settle_failure(st, shas, error, stats):
    """Count a failed attempt against each raw of a batch (in one transaction);
    raws out of attempts are marked "failed" so they stop blocking cursors."""
    with st.db:
        att = _attempts(st)
        for sha in shas:
            a = att.get(sha) or {"attempts": 0}
            att[sha] = {"attempts": a["attempts"] + 1, "error": error[:300]}
            if att[sha]["attempts"] >= MAX_ATTEMPTS:
                st.mark_raws([sha], "failed")
                stats["failed_raws"] += 1
        st.set_meta(ATTEMPTS_KEY, att)


def _ingest(ember, llm, now, fetch, n_ctx, run, errors):
    st, root, tpl = ember.store, ember.root, ember.template
    errors += _flush_dirty(ember)          # repair a run that died between commit and render

    stale, new_by_source, cursors = {}, {}, {}
    for slot, binding in _bound(ember):
        sid = slot["id"]
        try:
            cursor_in = st.get_cursor(sid)
        except (ValueError, RecursionError):
            cursor_in = None
        if not isinstance(cursor_in, dict):
            cursor_in = {}
            errors.append(f"{sid}: corrupt cursor reset")
        try:                               # any failure isolates this source only
            items, cursor = fetch(slot["type"], binding, cursor_in, now)
            shas = []
            with st.db:
                for it in items:
                    text = sources.clip(it["text"])
                    sha = wikifs.write_raw(root, text)
                    st.add_raw(sha, sid, it["ref"], it["title"], now, len(text.encode("utf-8")))
                    shas.append(sha)
        except Exception as e:
            stale[sid] = _err(e) if not isinstance(e, sources.SourceError) else str(e)
            errors.append(f"{sid}: {_err(e)}")
            continue
        new_by_source[sid], cursors[sid] = shas, cursor

    queued = st.pending_raws()
    pending, read_errors = [], 0
    for r in queued[:MAX_RAWS_PER_RUN]:
        try:
            text = wikifs.read_raw(root, r["sha"])
        except FileNotFoundError:
            text = None
        except OSError as e:               # locked, permissions, network share: try again next run
            errors.append(f"raw {r['sha']}: {_err(e)}")
            read_errors += 1
            continue
        except ValueError as e:            # bad id or undecodable bytes: never readable
            errors.append(f"raw {r['sha']}: {_err(e)}")
            text = None
        if text is None:
            with st.db:
                st.mark_raws([r["sha"]], "missing")
        else:
            pending.append(dict(r, text=text))
    full_text = {r["sha"]: r["text"] for r in pending}

    stats = {"raws": len(pending), "deferred": max(0, len(queued) - MAX_RAWS_PER_RUN), "batches": 0,
             "failed": 0, "failed_raws": 0, "truncated": 0, "ops": 0, "unverified": 0, "rejected": 0,
             "tokens_in": 0, "tokens_out": 0}
    rejections = []
    too_small = n_ctx < MIN_N_CTX
    if too_small:
        errors.append(f"model context n_ctx={n_ctx} is below the minimum of {MIN_N_CTX} tokens; "
                      "load the model with a larger context")
        pending = []
    budget = int(n_ctx * BUDGET_SHARE)
    cap = int(n_ctx * PROMPT_SHARE)
    schema = _schema_text(ember)
    att = _attempts(st)
    grouped = [r for r in pending if att.get(r["sha"], {}).get("attempts", 0) < SOLO_AFTER]
    alone = [r for r in pending if att.get(r["sha"], {}).get("attempts", 0) >= SOLO_AFTER]
    batches = list(_batches(grouped, budget)) + [b for r in alone for b in _batches([r], budget)]
    index_error = False
    router_down = False
    for batch in batches:
        stats["batches"] += 1
        context = []
        for page in st.search(_terms(" ".join(r["text"] for r in batch)), CONTEXT_PAGES):
            p = st.get_page(page) or {"title": page}
            context.append({"page": page, "title": p["title"],
                            "items": [i for i in st.items_for_page(page) if i["status"] == "open"][:CONTEXT_ITEMS]})
        try:
            index_head = wikifs.index_head(root)
        except (OSError, ValueError) as e:
            index_head = ""
            if not index_error:
                errors.append(f"index head: {_err(e)}")
                index_error = True
        msgs, size, shown = _prompt(tpl, schema, index_head, context, batch, cap)
        stats["truncated"] += sum(1 for r in shown if len(r["text"]) < len(full_text[r["sha"]]))
        shown_shas = [r["sha"] for r in shown if r["text"]]   # a raw cut to nothing was not seen
        reply_tokens = max(MIN_REPLY_TOKENS, min(MAX_REPLY_TOKENS, n_ctx - size))
        try:                               # the model call runs outside any transaction
            update, usage = llm(msgs, prompts.UPDATE_SCHEMA, reply_tokens)
        except RouterUnavailable as e:     # not the input's fault: stop, count nothing
            stats["failed"] += 1
            errors.append(_err(e))
            router_down = True
            break
        except Exception as e:
            stats["failed"] += 1
            errors.append(_err(e))
            _settle_failure(st, shown_shas, _err(e), stats)
            continue
        usage = usage if isinstance(usage, dict) else {}
        for k in ("prompt_tokens", "completion_tokens"):
            try:
                stats["tokens_in" if k == "prompt_tokens" else "tokens_out"] += int(usage.get(k) or 0)
            except (TypeError, ValueError):
                pass
        # Quotes are checked against the full raw, but only raws shown in this batch count.
        raws = {sha: full_text[sha] for sha in shown_shas}
        try:
            ops, new_pages, rejected = verify.verify_batch(update, raws, tpl["page_kinds"], st.known_item_ids())
            kept = []
            for op in ops:                 # an update/close must name the page its item lives on
                old = st.get_item(op["item"]) if op["op"] != "add" else None
                if old is not None and old["page"] != op["page"]:
                    rejected.append((-1, f"{op['op']} {op['item']}: page {op['page']} "
                                         f"is not the item's page {old['page']}"))
                else:
                    kept.append(op)
            ops = kept
            with st.db:                    # ops, raw status and the render list commit together
                touched = _apply(ember, ops, new_pages, now)
                st.mark_raws(shown_shas, "done")
                att = _attempts(st)
                if any(s in att for s in shown_shas):
                    st.set_meta(ATTEMPTS_KEY, {k: v for k, v in att.items() if k not in shown_shas})
                _mark_dirty(st, touched)
        except Exception as e:             # rolled back: the raws stay pending for the next run
            stats["failed"] += 1
            errors.append(f"apply: {_err(e)}")
            _settle_failure(st, shown_shas, f"apply: {_err(e)}", stats)
            continue
        stats["ops"] += len(ops)
        stats["unverified"] += sum(1 for o in ops if not o["verified"])
        stats["rejected"] += len(rejected)
        rejections += [str(why) for _, why in rejected][:MAX_REJECTIONS - len(rejections)]
        errors += _flush_dirty(ember)

    with st.db:
        for sid, shas in new_by_source.items():
            # missing/pruned/failed raws can never become done; they must not pin the cursor
            if all(st.raw_status(s) != "pending" for s in shas):
                st.set_cursor(sid, cursors[sid])
    try:
        prune_raws(ember)
    except (OSError, ValueError) as e:
        errors.append(f"prune: {_err(e)}")
    render_failed = bool(_dirty(st))
    if too_small or (stats["batches"] and stats["failed"] == stats["batches"]):
        status = "failed"
    else:
        status = ("partial" if stats["failed"] or stale or render_failed or read_errors or router_down
                  else "ok")
    summary = (f"{stats['raws']} new, {stats['ops']} ops ({stats['unverified']} unverified, "
               f"{stats['rejected']} rejected), {stats['failed']}/{stats['batches']} batches failed")
    for key, label in (("deferred", "deferred"), ("truncated", "truncated"), ("failed_raws", "raws given up")):
        if stats[key]:
            summary += f", {stats[key]} {label}"
    if stale:
        summary += f", stale: {', '.join(sorted(stale))}"
    try:
        wikifs.append_log(root, now, "ingest", summary)
    except (OSError, ValueError) as e:
        errors.append(f"log: {_err(e)}")
        if status == "ok":
            status = "partial"
    with st.db:
        st.finish_run(run, now, status, "; ".join(errors), stats["tokens_in"], stats["tokens_out"],
                      dict(stats, stale=stale, rejections=rejections))
    return dict(stats, run=run, status=status, stale=stale, summary=summary)


def prune_raws(ember, cap=RAW_CAP_BYTES):
    """Keep raw/ under `cap` bytes by deleting the oldest raws that are already
    settled (done or failed) and cited by no item. Referenced or pending raws are
    never pruned. Also removes orphan raw files (named like a raw, no db row),
    e.g. left by a source whose transaction rolled back. The file goes first,
    then the row: a crash in between leaves a row whose file is gone, which the
    next prune simply marks. Returns how many files were removed."""
    st = ember.store
    rows = st.all_raws()
    removed = 0
    known = {r["sha"] for r in rows}
    raw_dir = wikifs._safe_path(ember.root, "raw")
    try:
        entries = list(os.scandir(raw_dir))
    except FileNotFoundError:
        entries = []
    for entry in entries:
        if not _ORPHAN_RE.fullmatch(entry.name) or entry.name[:-4] in known:
            continue
        try:
            if entry.is_file(follow_symlinks=False):
                os.remove(entry.path)
                removed += 1
        except FileNotFoundError:
            pass
    total = sum(r["size"] or 0 for r in rows if r["status"] in ("pending", "done", "failed"))
    if total <= cap:
        return removed
    keep = st.referenced_raws()
    for r in rows:
        if total <= cap:
            break
        if r["status"] not in ("done", "failed") or r["sha"] in keep \
                or not verify.RAW_RE.fullmatch(r["sha"] or ""):
            continue
        try:
            os.remove(os.path.join(raw_dir, r["sha"] + ".txt"))
        except FileNotFoundError:
            pass
        with st.db:
            st.mark_raws([r["sha"]], "pruned")
        total -= r["size"] or 0
        removed += 1
    return removed
