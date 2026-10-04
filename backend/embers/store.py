"""The ember's sqlite index (embers.db): runs, raws, pages, items, evidence,
source cursors, meta and full-text search over pages.

Items live here; the markdown pages are rendered from this table, so the
database is the source of truth and the files are the readable view.
No method commits: wrap writes in `with store.db:` (commit or roll back).
"""
import hashlib, json, re, sqlite3

from .verify import ITEM_RE, PAGE_RE, RAW_RE

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, job TEXT NOT NULL, started TEXT NOT NULL,
  finished TEXT, status TEXT, error TEXT DEFAULT '', tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0, detail TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS raws(sha TEXT PRIMARY KEY, source TEXT, ref TEXT, title TEXT,
  fetched TEXT, size INTEGER, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS pages(page TEXT PRIMARY KEY, title TEXT, summary TEXT DEFAULT '',
  created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, page TEXT NOT NULL, kind TEXT, text TEXT,
  owner TEXT DEFAULT '', due TEXT DEFAULT '', status TEXT DEFAULT 'open',
  verified INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS items_page ON items(page);
CREATE TABLE IF NOT EXISTS evidence(item TEXT NOT NULL, raw TEXT NOT NULL, quote TEXT NOT NULL,
  added TEXT, PRIMARY KEY(item, raw, quote));
CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
# Column names never come from callers: update_item maps allow-listed keys to these fixed fragments.
ITEM_FIELDS = {"kind": "kind=?", "text": "text=?", "owner": "owner=?", "due": "due=?",
               "status": "status=?", "verified": "verified=?"}
_WORD = re.compile(r"\w{3,}")      # unicode words; "_" counts, so the LIKE fallback escapes it
SCHEMA_VERSION = 1


def _fold(s):
    return s.casefold() if isinstance(s, str) else ""


def _like(term):
    """Escape LIKE wildcards (use with ESCAPE '\')."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def ts(now):
    return now.strftime("%Y-%m-%dT%H:%M:%S")


def _check(rx, value, what):
    """Ids are used as keys: they must match the verify.py format in full."""
    if not (isinstance(value, str) and rx.fullmatch(value)):
        raise ValueError(f"bad {what} {value!r}")
    return value


def _rows(cur):
    return [dict(r) for r in cur]


class Store:
    def __init__(self, path, fts=True):
        self.db = sqlite3.connect(path, check_same_thread=False)
        try:
            self.db.row_factory = sqlite3.Row
            self.db.create_function("lf_fold", 1, _fold, deterministic=True)   # LIKE is ASCII-only case-insensitive
            self.db.executescript(SCHEMA)
            if self.db.execute("PRAGMA user_version").fetchone()[0] == 0:
                self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")     # constant, for later migrations
            self.fts = False
            if fts:
                existed = self.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='pages_fts'").fetchone() is not None
                try:
                    self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(page UNINDEXED, body)")
                    self.fts = True
                except sqlite3.OperationalError:
                    pass        # sqlite built without FTS5: search() falls back to LIKE
                if self.fts and not existed:                # db created without FTS: index what is already there
                    for (page,) in self.db.execute("SELECT page FROM pages").fetchall():
                        self.index_page(page)
            self.db.commit()
        except BaseException:
            self.db.close()     # don't leak a file handle (Windows can't delete it)
            raise

    def close(self):
        """Idempotent; releases the file so a temp dir can be removed on Windows."""
        db, self.db = self.db, None
        if db is not None:
            db.close()

    # runs
    def start_run(self, job, now):
        return self.db.execute("INSERT INTO runs(job, started, status) VALUES(?,?,'running')",
                               (job, ts(now))).lastrowid

    def finish_run(self, run_id, now, status, error="", tokens_in=0, tokens_out=0, detail=None):
        self.db.execute("UPDATE runs SET finished=?, status=?, error=?, tokens_in=?, tokens_out=?, detail=? "
                        "WHERE id=?", (ts(now), status, (error or "")[:2000], tokens_in, tokens_out,
                                       json.dumps(detail or {}), run_id))

    def abort_running(self, job, now):
        """Close rows of `job` left 'running' by a process that died mid-run. Returns how many."""
        return self.db.execute("UPDATE runs SET status='aborted', finished=?, "
                               "error='interrupted before it finished' WHERE job=? AND status='running'",
                               (ts(now), job)).rowcount

    def last_run(self, job, status="ok", before=None):
        """Latest run of `job` whose status is `status` (a string or a tuple of
        them), optionally only among runs started before the datetime `before`."""
        statuses = (status,) if isinstance(status, str) else tuple(status)
        sql = f"SELECT * FROM runs WHERE job=? AND status IN ({','.join('?' * len(statuses))})"
        args = [job, *statuses]
        if before is not None:
            sql += " AND started<?"
            args.append(ts(before))
        r = self.db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return dict(r) if r else None

    def recent_runs(self, limit=20):
        return _rows(self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))

    # raws
    def add_raw(self, sha, source, ref, title, now, size):
        _check(RAW_RE, sha, "raw id")
        cur = self.db.execute("INSERT OR IGNORE INTO raws(sha, source, ref, title, fetched, size) "
                              "VALUES(?,?,?,?,?,?)", (sha, source, ref, title, ts(now), size))
        if cur.rowcount == 1:
            return True
        # Fetched again after its file vanished or was pruned: the text is back, so it is work again.
        # done/failed/pending stay as they are (a failed raw must not loop forever).
        cur = self.db.execute("UPDATE raws SET status='pending', size=?, fetched=? "
                              "WHERE sha=? AND status IN ('missing', 'pruned')", (size, ts(now), sha))
        return cur.rowcount == 1

    def pending_raws(self):
        return _rows(self.db.execute("SELECT * FROM raws WHERE status='pending' ORDER BY fetched, sha"))

    def all_raws(self):
        return _rows(self.db.execute("SELECT * FROM raws ORDER BY fetched, sha"))

    def raw_status(self, sha):
        r = self.db.execute("SELECT status FROM raws WHERE sha=?", (sha,)).fetchone()
        return r[0] if r else None

    def mark_raws(self, shas, status):
        self.db.executemany("UPDATE raws SET status=? WHERE sha=?", [(status, s) for s in shas])

    def referenced_raws(self):
        return {r[0] for r in self.db.execute("SELECT DISTINCT raw FROM evidence")}

    # pages
    def ensure_page(self, page, title, summary, now):
        """Insert a page, or keep its title and only fill an empty summary."""
        _check(PAGE_RE, page, "page")
        self.db.execute(
            "INSERT INTO pages(page, title, summary, created, updated) VALUES(?,?,?,?,?) "
            "ON CONFLICT(page) DO UPDATE SET updated=excluded.updated, "
            "summary=CASE WHEN pages.summary='' THEN excluded.summary ELSE pages.summary END",
            (page, title, summary or "", ts(now), ts(now)))

    def get_page(self, page):
        r = self.db.execute("SELECT * FROM pages WHERE page=?", (page,)).fetchone()
        return dict(r) if r else None

    def all_pages(self):
        return _rows(self.db.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM items i WHERE i.page=p.page AND i.status='open') AS open "
            "FROM pages p ORDER BY p.page"))

    # items
    def new_item_id(self, page, text, now):
        seed = f"{page}\n{text}\n{ts(now)}"
        for n in range(100):
            iid = "it-" + hashlib.sha1(f"{seed}\n{n}".encode("utf-8")).hexdigest()[:8]
            if self.get_item(iid) is None:
                return iid
        raise RuntimeError("could not allocate an item id")

    def add_item(self, iid, page, kind, text, owner, due, verified, now):
        _check(ITEM_RE, iid, "item id")
        _check(PAGE_RE, page, "page")
        self.db.execute("INSERT INTO items(id, page, kind, text, owner, due, status, verified, created, updated) "
                        "VALUES(?,?,?,?,?,?,'open',?,?,?)",
                        (iid, page, kind, text, owner, due, int(bool(verified)), ts(now), ts(now)))

    def update_item(self, iid, now, **fields):
        bad = set(fields) - set(ITEM_FIELDS)
        if bad:
            raise ValueError(f"cannot update {', '.join(sorted(bad))}")
        cols = [ITEM_FIELDS[k] for k in fields] + ["updated=?"]      # fixed fragments only
        self.db.execute(f"UPDATE items SET {', '.join(cols)} WHERE id=?",
                        (*fields.values(), ts(now), iid))

    def get_item(self, iid):
        r = self.db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None

    def items_for_page(self, page):
        return _rows(self.db.execute("SELECT * FROM items WHERE page=? ORDER BY status='closed', created, id",
                                     (page,)))

    def open_items(self, verified_only=False):
        sql = "SELECT * FROM items WHERE status='open'" + (" AND verified=1" if verified_only else "")
        return _rows(self.db.execute(sql + " ORDER BY page, created, id"))

    def known_item_ids(self):
        return {r[0] for r in self.db.execute("SELECT id FROM items")}

    def add_evidence(self, iid, raw, quote, now):
        _check(ITEM_RE, iid, "item id")
        _check(RAW_RE, raw, "raw id")
        self.db.execute("INSERT OR IGNORE INTO evidence(item, raw, quote, added) VALUES(?,?,?,?)",
                        (iid, raw, quote, ts(now)))

    def evidence_for(self, iids):
        out = {}
        iids = list(iids)
        for i in range(0, len(iids), 500):
            chunk = iids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self.db.execute(f"SELECT item, raw, quote FROM evidence WHERE item IN ({marks}) "
                                     "ORDER BY added, raw", chunk):
                out.setdefault(r["item"], []).append({"raw": r["raw"], "quote": r["quote"]})
        return out

    def all_evidence(self):
        return _rows(self.db.execute("SELECT e.item, e.raw, e.quote, i.page FROM evidence e "
                                     "JOIN items i ON i.id=e.item ORDER BY e.item"))

    # search
    def index_page(self, page):
        if not self.fts:
            return
        p = self.get_page(page) or {"title": "", "summary": ""}
        texts = [r[0] or "" for r in self.db.execute("SELECT text FROM items WHERE page=?", (page,))]
        body = "\n".join([page.replace("/", " ").replace("-", " "), p["title"] or "", p["summary"] or ""] + texts)
        self.db.execute("DELETE FROM pages_fts WHERE page=?", (page,))
        self.db.execute("INSERT INTO pages_fts(page, body) VALUES(?,?)", (page, body))

    def search(self, text, limit=3):
        """Pages ranked by relevance to free text. Terms are reduced to plain
        unicode words and quoted, so user/model text can't inject FTS syntax.
        Known limitation: the default FTS5 tokenizer does not split CJK runs,
        so those match only as whole runs."""
        terms = list(dict.fromkeys(_WORD.findall(_fold(text))))[:24]
        if not terms:
            return []
        if self.fts:
            q = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)      # each term is a quoted FTS5 string literal
            try:
                return [r[0] for r in self.db.execute(
                    "SELECT page FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?", (q, limit))]
            except sqlite3.OperationalError:
                return []                                  # degrade, never raise on odd input
        scores = {}
        for t in terms:
            like = _like(t)
            for (page,) in self.db.execute(
                    "SELECT page FROM pages WHERE lf_fold(page) LIKE ? ESCAPE '\\' "
                    "OR lf_fold(title) LIKE ? ESCAPE '\\' OR lf_fold(summary) LIKE ? ESCAPE '\\' "
                    "UNION SELECT page FROM items WHERE lf_fold(text) LIKE ? ESCAPE '\\'", (like,) * 4):
                scores[page] = scores.get(page, 0) + 1
        return [p for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]

    # cursors + meta
    def get_cursor(self, source):
        r = self.db.execute("SELECT value FROM cursors WHERE source=?", (source,)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_cursor(self, source, value):
        self.db.execute("INSERT OR REPLACE INTO cursors(source, value) VALUES(?,?)", (source, json.dumps(value)))

    def get_meta(self, key, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, json.dumps(value)))
