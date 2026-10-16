"""llama-bench runs for -b / -ub tuning.

llama-bench is a separate binary next to llama-server and speaks its own
dialect: -p is prompt tokens (server: port), -n is generated tokens, -pg runs
one combined pp+tg test, -r repeats. So this module owns its own UI-variable ->
flag table instead of reusing argspec's server names.

The grid walks one b at a time and tests its ubs against it (ub <= b), keeping
the measured values in an array per b - what "pick a b, try ubs on it" means in
the tab and over MCP. One bench at a time, and only with VRAM free of regular
llama.cpp models: a second model next to the benched one skews the numbers.

Finished runs are appended to <logdir>/bench/history.jsonl together with the
raw llama-bench stream, so a panel restart still shows the last run and an
anomalous row can be traced back to the line that produced it.

Pure stdlib; the process runner takes its Popen so tests never spawn anything.
"""

import copy
import json
import os
import subprocess
import threading
from datetime import datetime, timezone

# UI variable -> llama-bench flag. Kept as a table on purpose: short names
# collide with the server's (-p is a port there), so nothing is passed through.
FLAGS = {
    "model": "-m", "b": "-b", "ub": "-ub", "p": "-p", "n": "-n", "pg": "-pg",
    "reps": "-r", "gpu-layers": "-ngl", "split-mode": "-sm",
    "main-gpu": "-mg", "device": "-dev", "threads": "-t",
}

DEFAULT_B = (512, 1024, 2048, 4096)
DEFAULT_UB = (128, 256, 512, 1024)
DEF_P, DEF_N = 5000, 2500        # prompt / generation tokens
DEF_PG = (5000, 2500)            # combined pp+tg workload
MAX_REPS = 2                     # repeats capped: the run is long enough at 2
NEAR = 0.03                      # within 3% of the best pg t/s, smaller wins

# Keys copied from the model's own models.ini section so the bench offloads the
# way the model really runs. "gpu-layers" is explicit per the standing no-99
# rule - a model that pins 66 layers is benched with 66, never 99/999.
OFFLOAD_KEYS = ("gpu-layers", "split-mode", "main-gpu", "device", "threads")

_BANNED_NGL = (99, 999)


def ngl(value):
    """The model's real layer count; 99/999 is refused, not silently used."""
    n = int(value)
    if n in _BANNED_NGL:
        raise ValueError(f"ngl {n} is not a layer count - set the model's real "
                         f"gpu-layers in models.ini")
    return n


def grid(b_list, ub_list):
    """[{b, ubs}] - one entry per b (ascending), its ubs in one array.

    ubs above b are dropped rather than run: an ubatch larger than the batch
    it slices measures nothing useful.
    """
    bs = sorted({int(b) for b in b_list})
    ubs_all = sorted({int(u) for u in ub_list})
    return [{"b": b, "ubs": [u for u in ubs_all if u <= b]} for b in bs
            if any(u <= b for u in ubs_all)]


def build_argv(bench_bin, model_file, b, ubs, offload=None, p=DEF_P, n=DEF_N,
               pg=DEF_PG, reps=MAX_REPS):
    """One invocation: one b, all its ubs, and the three workloads at once
    (llama-bench emits a row per workload)."""
    reps = max(1, min(int(reps or MAX_REPS), MAX_REPS))
    p, n = (DEF_P if p is None else p), (DEF_N if n is None else n)
    pg = DEF_PG if pg is None else pg
    argv = [bench_bin, FLAGS["model"], str(model_file),
            FLAGS["b"], str(int(b)), FLAGS["ub"],
            ",".join(str(int(u)) for u in ubs),
            FLAGS["p"], str(int(p)), FLAGS["n"], str(int(n))]
    if pg:
        argv += [FLAGS["pg"], f"{int(pg[0])},{int(pg[1])}"]
    argv += [FLAGS["reps"], str(reps), "-o", "jsonl"]
    for key in OFFLOAD_KEYS:
        v = (offload or {}).get(key)
        if v in (None, ""):
            continue
        argv += [FLAGS[key], str(ngl(v) if key == "gpu-layers" else v)]
    return argv


def parse_line(line):
    """One JSONL row, or None for noise (backend chatter, progress dots)."""
    s = (line or "").strip()
    if not s.startswith("{"):
        return None
    try:
        row = json.loads(s)
    except ValueError:
        return None
    return row if isinstance(row, dict) and "avg_ts" in row else None


def kind(row):
    """pp / tg / pg from the row's token counters."""
    p, g = int(row.get("n_prompt") or 0), int(row.get("n_gen") or 0)
    return "pg" if p and g else ("pp" if p else "tg")


def result(row):
    return {"ts": row.get("avg_ts"), "sd": row.get("stddev_ts"),
            "p": int(row.get("n_prompt") or 0), "n": int(row.get("n_gen") or 0)}


def next_expected(grid_rows, kinds):
    """Label of the first cell still unmeasured - what the bench is on now."""
    for e in grid_rows:
        for it in e["ubs"]:
            for k in kinds:
                if k not in it["rows"]:
                    return f"b={e['b']} ub={it['ub']} {k}"
    return ""


def best_of(grid_rows, kinds):
    """{kind: {b, ub, ts}} - the fastest cell of each workload."""
    out = {}
    for k in kinds:
        cells = [(e["b"], it["ub"], it["rows"][k]["ts"])
                 for e in grid_rows for it in e["ubs"]
                 if it["rows"].get(k, {}).get("ts") is not None]
        if cells:
            b, ub, ts = max(cells, key=lambda c: c[2])
            out[k] = {"b": b, "ub": ub, "ts": ts}
    return out


def recommend(grid_rows):
    """The cell to apply: highest combined (pg) t/s; when another cell is
    within NEAR of it, the smaller ub wins, then the smaller b. None when the
    run measured no pg row."""
    cells = [(e["b"], it["ub"], it["rows"]["pg"]["ts"])
             for e in grid_rows for it in e["ubs"]
             if it["rows"].get("pg", {}).get("ts") is not None]
    if not cells:
        return None
    top = max(ts for _, _, ts in cells)
    near = [c for c in cells if c[2] >= top * (1 - NEAR)]
    b, ub, ts = min(near, key=lambda c: (c[1], c[0]))
    return {"b": b, "ub": ub, "ts": ts}


def deltas(grid_rows, prev_rows):
    """{"b:ub:kind": percent} - t/s change against an earlier run's grid."""
    if not prev_rows:
        return {}
    was = {(e["b"], it["ub"], k): r.get("ts")
           for e in prev_rows for it in e["ubs"]
           for k, r in it.get("rows", {}).items()}
    out = {}
    for e in grid_rows:
        for it in e["ubs"]:
            for k, r in it["rows"].items():
                old = was.get((e["b"], it["ub"], k))
                if old and r.get("ts"):
                    out[f"{e['b']}:{it['ub']}:{k}"] = round(
                        (r["ts"] / old - 1.0) * 100.0, 1)
    return out


def history_path(logdir):
    return os.path.join(logdir, "bench", "history.jsonl")


def append_history(logdir, record):
    """One JSON line per finished run. A disk hiccup must not lose what was
    measured, so failures pass silently."""
    try:
        path = history_path(logdir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_history(logdir, model=None, limit=20):
    """[record, ...] newest last, optionally one model's runs only."""
    recs = []
    try:
        with open(history_path(logdir), encoding="utf-8") as f:
            for line in f:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    continue          # one bad line, keep the rest
    except OSError:
        return []
    if model:
        recs = [r for r in recs if r.get("model") == model]
    return recs[-max(1, int(limit or 20)):]


class BenchManager:
    """One bench at a time, results grouped per b into arrays of ubs.

    Same shape as hub.DownloadManager: a state dict the UI and MCP poll, and a
    cooperative cancel flag. start() refuses while a run is live - overlapping
    benches would fight over VRAM and measure each other.
    """

    def __init__(self, popen=subprocess.Popen, logdir=None):
        self.lock = threading.Lock()
        self._popen = popen
        self._proc = None
        self._logdir = logdir
        self._argvs = []
        self._raw = []
        self.state = self._idle()
        self.restore()

    @staticmethod
    def _idle():
        return {"running": False, "phase": "idle", "error": "", "current": "",
                "last_done": "", "done_rows": 0, "total_rows": 0, "grid": [],
                "cancel": False, "extra_rows": 0, "partial": False,
                "model": "", "model_file": "", "engine": "", "argv": [],
                "started_at": "", "workload": {}, "kinds": [],
                "best": {}, "recommended": None, "delta": {}}

    def restore(self):
        """Fill the state from the newest history record so the tab still shows
        the last run after a panel restart."""
        if not self._logdir:
            return
        recs = read_history(self._logdir, limit=1)
        if not recs:
            return
        r = recs[-1]
        grid_rows = r.get("grid") or []
        kinds = r.get("kinds") or []
        with self.lock:
            self.state.update({
                "phase": r.get("phase") or "done", "error": r.get("error", ""),
                "grid": grid_rows, "kinds": kinds,
                "model": r.get("model", ""), "model_file": r.get("model_file", ""),
                "engine": r.get("engine", ""), "argv": r.get("argv") or [],
                "started_at": r.get("started_at", ""),
                "workload": r.get("workload") or {},
                "best": r.get("best") or {}, "recommended": r.get("recommended"),
                "delta": r.get("delta") or {}, "partial": bool(r.get("partial")),
                "extra_rows": int(r.get("extra_rows") or 0),
                "done_rows": sum(len(it["rows"]) for e in grid_rows for it in e["ubs"]),
                "total_rows": sum(len(e["ubs"]) for e in grid_rows) * max(1, len(kinds)),
            })

    def progress(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def start(self, spec):
        """spec: {bench_bin, model_file, grid, offload, p, n, pg, reps,
        model, engine}. Raises RuntimeError while another bench runs."""
        reps = max(1, min(int(spec.get("reps") or MAX_REPS), MAX_REPS))
        p = DEF_P if spec.get("p") is None else int(spec["p"])
        n = DEF_N if spec.get("n") is None else int(spec["n"])
        pg = DEF_PG if spec.get("pg") is None else spec["pg"]
        kinds = ["pp", "tg"] + (["pg"] if pg else [])
        argvs = [build_argv(spec["bench_bin"], spec["model_file"],
                            e["b"], e["ubs"], spec["offload"],
                            p, n, pg, reps) for e in spec["grid"]]
        with self.lock:
            if self.state["running"]:
                raise RuntimeError("a bench is already running")
            self.state = self._idle()
            self.state.update(
                running=True, phase="running",
                model=str(spec.get("model") or ""),
                model_file=str(spec.get("model_file") or ""),
                engine=str(spec.get("engine") or ""),
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                kinds=kinds,
                workload={"p": p, "n": n, "reps": reps,
                          "pg": list(pg) if pg else None},
                total_rows=sum(len(e["ubs"]) for e in spec["grid"]) * len(kinds),
                grid=[{"b": e["b"], "ubs": [{"ub": u, "rows": {}} for u in e["ubs"]]}
                      for e in spec["grid"]])
            self.state["argv"] = [list(a) for a in argvs]
            self.state["current"] = next_expected(self.state["grid"], kinds)
            spec = dict(spec, reps=reps, p=p, n=n, pg=pg)
        self._argvs, self._raw = argvs, []
        self._thread = threading.Thread(target=self._run, args=(spec,),
                                       daemon=True, name="bench")
        self._thread.start()

    def cancel(self):
        """Stop the running bench. Returns whether one ran."""
        with self.lock:
            if not self.state["running"]:
                return False
            self.state["cancel"] = True
            proc = self._proc
        if proc:
            proc.terminate()
        return True

    def _put(self, row, b, ub):
        """Store one row in its cell. A re-emitted cell keeps its first
        measurement and only bumps extra_rows - llama-bench occasionally
        repeats a test, and the repeat must not move the numbers or the
        counter (done_rows counts cells, not lines)."""
        k = kind(row)
        with self.lock:
            for entry in self.state["grid"]:
                if entry["b"] != b:
                    continue
                for item in entry["ubs"]:
                    if item["ub"] != ub:
                        continue
                    if k in item["rows"]:
                        self.state["extra_rows"] += 1
                    else:
                        item["rows"][k] = result(row)
                        self.state["done_rows"] += 1
                    self.state["last_done"] = f"b={b} ub={ub} {k}"
                    self.state["current"] = next_expected(self.state["grid"],
                                                          self.state["kinds"])
                    return

    def _run(self, spec):
        tail, err = [], ""
        try:
            for entry, argv in zip(spec["grid"], self._argvs):
                with self.lock:
                    self.state["current"] = next_expected(self.state["grid"],
                                                          self.state["kinds"])
                    self._proc = self._popen(
                        argv, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
                for line in self._proc.stdout:
                    self._raw.append(line.rstrip("\n"))
                    row = parse_line(line)
                    if row:
                        self._put(row, entry["b"],
                                  int(row.get("n_ubatch") or 0))
                    else:
                        tail = (tail + [line.strip()])[-4:]
                code = self._proc.wait()
                if code:
                    raise RuntimeError(
                        f"llama-bench exited {code}: "
                        f"{' | '.join(t for t in tail if t) or 'no output'}")
        except Exception as e:     # report, don't leave the tab spinning
            err = str(e) or type(e).__name__
        with self.lock:
            cancelled = self.state["cancel"]
            self._proc = None
            self.state["running"] = False
            self.state["current"] = ""
            self.state["phase"] = ("cancelled" if cancelled
                                   else "error" if err else "done")
            self.state["error"] = "" if cancelled else err
            self.state["partial"] = (self.state["done_rows"] > 0
                                     and self.state["phase"] != "done")
            snap = copy.deepcopy(self.state)
        best = best_of(snap["grid"], snap["kinds"])
        rec = recommend(snap["grid"])
        delta = deltas(snap["grid"], self._previous(snap["model"]))
        with self.lock:
            self.state["best"], self.state["recommended"] = best, rec
            self.state["delta"] = delta
            final = copy.deepcopy(self.state)
        self._record(final)

    def _previous(self, model):
        """The newest earlier run's grid of this model, or None."""
        if not (self._logdir and model):
            return None
        recs = read_history(self._logdir, model=model, limit=200)
        return recs[-1].get("grid") if recs else None

    def _record(self, final):
        """Append the run to history.jsonl and drop the raw stream beside it."""
        if not self._logdir:
            return
        base = os.path.join(self._logdir, "bench")
        stamp = (final.get("started_at") or "").replace(":", "").replace("-", "")
        name = "".join(c if c.isalnum() or c in "-_." else "-"
                       for c in (final.get("model") or "run"))
        raw_file = f"{stamp}-{name}.jsonl"
        rec = {k: final.get(k) for k in
               ("model", "model_file", "engine", "argv", "workload", "kinds",
                "phase", "error", "partial", "extra_rows", "grid", "best",
                "recommended", "delta", "started_at")}
        rec["date"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rec["raw_file"] = raw_file
        append_history(self._logdir, rec)
        try:
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, raw_file), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(self._raw) + ("\n" if self._raw else ""))
        except OSError:
            pass
