"""Multi-model slots: where a model goes, and whether it fits at all.

With more than one model loaded, the router's own answer to "full" is to evict
the least recently used model, and llama.cpp's --fit (when it is not disabled
by a pinned n-gpu-layers) quietly shrinks the newcomer's context. Neither is
acceptable when the newcomer is a background job and the evictee is the model
the user is chatting with. So every multi-mode load asks plan() first.

plan() is pure: it sees one nvidia-smi snapshot (whose `used` already counts
the desktop, other apps and every loaded slot), the slots that are up, and the
candidate's memory need, and returns a verdict. A footprint measured on a
previous load of the same configuration beats the prediction; the prediction
only covers configurations never loaded before.

Measured on this project's dev box (2026-10-04): a process pinned to GPU1 still
opens a CUDA context on GPU0 (~231 MiB); one on GPU0 costs nothing on GPU1.

Pure stdlib, no I/O.
"""
import re

MIB_PER_GIB = 1024
DEFAULT_HEADROOM_MIB = 1536   # per GPU, kept free on top of everything planned
OVERHEAD_MIB = 700            # per GPU a process uses: CUDA context + compute buffers
OTHER_GPU0_MIB = 256          # a process pinned off GPU0 still opens a context there
RAM_HEADROOM_MIB = 2048

_CUDA = re.compile(r"cuda(\d+)", re.I)


def _floats(s):
    try:
        return [float(x) for x in str(s).split(",") if x.strip() != ""]
    except ValueError:
        return None


def pins(settings):
    """What a model's effective settings ([*] merged with its section) already
    fix about placement: {"devices": [gpu index], "share": {index: fraction}},
    devices None when they name something we can't map (never "free to place"),
    or None when nothing is pinned and the planner may choose."""
    s = {k: str(v).strip() for k, v in (settings or {}).items() if v is not None}
    devices = None
    if s.get("device"):
        names = [n.strip() for n in s["device"].split(",") if n.strip()]
        if [n.lower() for n in names] == ["none"]:
            return {"devices": [], "share": {}}
        idx = [_CUDA.fullmatch(n) for n in names]
        if not idx or not all(idx):
            return {"devices": None, "share": {}}
        devices = [int(m.group(1)) for m in idx]
    if s.get("split-mode", "").lower() == "none":
        try:
            mg = int(s.get("main-gpu") or 0)
        except ValueError:
            return {"devices": None, "share": {}}
        if devices is not None:
            if not 0 <= mg < len(devices):
                return {"devices": None, "share": {}}
            mg = devices[mg]
        return {"devices": [mg], "share": {mg: 1.0}}
    ts = _floats(s["tensor-split"]) if s.get("tensor-split") else None
    if ts is None and s.get("tensor-split"):
        return {"devices": None, "share": {}}
    if ts:
        order = devices if devices is not None and len(devices) == len(ts) else (
            None if devices is not None else list(range(len(ts))))
        if order is not None and sum(ts) > 0:
            pairs = [(d, w) for d, w in zip(order, ts) if w > 0]
            total = sum(w for _, w in pairs)
            return {"devices": [d for d, _ in pairs],
                    "share": {d: w / total for d, w in pairs}}
    if devices is not None:
        return {"devices": devices, "share": {d: 1.0 / len(devices) for d in devices}}
    return None


def _gib(mib):
    return f"{mib / MIB_PER_GIB:.1f} GiB"


def _predicted(need, devices, share):
    """Per-GPU MiB for a placement: the need (which already holds one process
    overhead) plus an overhead per extra device, shared like the weights, plus
    the context a process off GPU0 still opens on GPU0."""
    extra = OVERHEAD_MIB * max(len(devices) - 1, 0)
    fp = {d: int(round((need + extra) * share[d])) for d in devices}
    if devices and 0 not in devices:
        fp[0] = OTHER_GPU0_MIB
    return fp


def _footprint(cand, devices, share):
    m = (cand.get("measured") or {}).get(tuple(devices))
    if m:
        return dict(m), "measured"
    return _predicted(cand.get("need_mib") or 0, devices, share), "predicted"


def _fits(fp, free):
    return all(i in free and mib <= free[i] for i, mib in fp.items())


def _choose(cand, free, role, allow_span):
    """(devices, place, footprint, source) for an unpinned candidate, or None."""
    options = []
    for i in sorted(free):
        fp, src = _footprint(cand, [i], {i: 1.0})
        if _fits(fp, free):
            options.append((free[i], i, fp, src))
    if options:
        # main: the roomiest GPU; worker: the tightest that fits (keeps the big hole)
        pick = max(options) if role == "main" else min(options)
        _, i, fp, src = pick
        return [i], f"CUDA{i}", fp, src
    if allow_span and len(free) > 1:
        devs = sorted(free)
        room = {d: max(free[d], 0) for d in devs}
        if sum(room.values()) > 0:
            share = {d: room[d] / sum(room.values()) for d in devs}
            devs = [d for d in devs if share[d] > 0]
            fp, src = _footprint(cand, devs, share)
            if _fits(fp, free):
                return devs, "", fp, src
    return None


def _place(cand, free, role):
    """(devices, place, footprint, source) or None; place None = pinned, untouched."""
    p = cand.get("pins")
    if p is None:
        return _choose(cand, free, role, allow_span=(role == "main"))
    fp, src = _footprint(cand, p["devices"], p["share"])
    return (p["devices"], None, fp, src) if _fits(fp, free) else None


def _verdict(ok, reason="", devices=None, place=None, footprint=None, source=None,
             evict=None, already=False):
    return {"ok": ok, "reason": reason, "devices": devices or [], "place": place,
            "footprint": footprint or {}, "source": source, "evict": evict or [],
            "already": already}


def _refusal(cand, free, headroom_mib):
    p = cand.get("pins")
    if p is not None and p["devices"]:
        fp, src = _footprint(cand, p["devices"], p["share"])
        missing = [i for i in fp if i not in free]
        if missing:
            return f"it is pinned to GPU{missing[0]}, which this machine doesn't have"
        need = sum(fp.values())
    else:
        fp, src = _footprint(cand, [min(free)], {min(free): 1.0})
        need = fp[min(free)]
    where = ", ".join(f"GPU{i} has {_gib(max(f, 0))}" for i, f in sorted(free.items()))
    return (f"needs ~{_gib(need)} ({src}); {where} free after "
            f"{_gib(headroom_mib)} headroom")


def plan(candidate, gpus, loaded, headroom_mib=DEFAULT_HEADROOM_MIB, cap=3,
         role="worker", ram_free_mib=None):
    """Verdict for loading `candidate` next to `loaded`.

    candidate: {model, need_mib, pins (from pins()), measured: {(devices,): {gpu: MiB}},
                ram_mib (the part kept in system RAM)}
    gpus:      [{index, total_mib, used_mib}] - one nvidia-smi snapshot
    loaded:    [{model, role, footprint: {gpu: MiB}, last_used}]
    Returns {ok, reason, devices, place, footprint, source, evict, already}.
    place: "CUDAn" to write, "" to clear ours (span), None to leave the section alone.
    evict: workers (oldest first) whose unload would let a *main* load fit. Workers
    never get an evict list: a background job never unloads anything."""
    model = candidate.get("model")
    if any(s.get("model") == model for s in loaded):
        return _verdict(True, "already loaded", already=True)
    p = candidate.get("pins")
    if p is not None and p["devices"] is None:
        return _verdict(False, "its device setting names a device LlamaForge can't map to a "
                               "GPU; set device = CUDA0 / CUDA1, or clear it")
    ram = candidate.get("ram_mib") or 0
    if ram and ram_free_mib is not None and ram > ram_free_mib - RAM_HEADROOM_MIB:
        return _verdict(False, f"needs ~{_gib(ram)} of system RAM for the part kept on the "
                               f"CPU; {_gib(max(ram_free_mib, 0))} is free")
    if p is not None and not p["devices"]:
        if len(loaded) >= cap:
            return _verdict(False, f"{len(loaded)} models are loaded; the limit is {cap}")
        return _verdict(True, devices=[], place=None, footprint={}, source="predicted")
    if not gpus:
        return _verdict(False, "no GPU memory information (nvidia-smi didn't answer)")

    free = {g["index"]: g["total_mib"] - g["used_mib"] - headroom_mib for g in gpus}
    over_cap = len(loaded) >= cap
    if not over_cap:
        got = _place(candidate, free, role)
        if got:
            devs, place, fp, src = got
            return _verdict(True, devices=devs, place=place, footprint=fp, source=src)

    reason = (f"{len(loaded)} models are loaded; the limit is {cap}" if over_cap
              else _refusal(candidate, free, headroom_mib))
    if role != "main":
        return _verdict(False, reason)
    workers = sorted((s for s in loaded if s.get("role") != "main"),
                     key=lambda s: s.get("last_used") or 0)
    start = max(len(loaded) - cap + 1, 1)
    for k in range(start, len(workers) + 1):
        room = dict(free)
        for s in workers[:k]:
            for i, mib in (s.get("footprint") or {}).items():
                if i in room:
                    room[i] += mib
        got = _place(candidate, room, role)
        if got:
            names = [s["model"] for s in workers[:k]]
            devs, place, fp, src = got
            return _verdict(False, f"{reason}. Unloading {', '.join(names)} makes room",
                            devices=devs, place=place, footprint=fp, source=src, evict=names)
    return _verdict(False, reason)
