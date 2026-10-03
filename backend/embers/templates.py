"""Ember templates: shareable blueprints, treated as untrusted input.

A template says what an ember is for and how its wiki is shaped. It never
carries paths, credentials or tool definitions: bindings (the user's folders,
calendar URLs, repos) live in the ember's own ember.json. Validation mirrors
recipes.parse: unknown keys are dropped and reported, not fatal.
"""
import json, re
from urllib.parse import urlparse

from . import reserved_name

MAX_BYTES    = 64 * 1024
MAX_SCHEMA   = 8 * 1024
MAX_MISSION  = 1000
MAX_KINDS    = 8
MAX_SLOTS    = 12
SLOT_TYPES   = ("folder", "ics", "rss", "git", "llamacpp")
JOBS         = ("ingest", "brief", "lint")
DEFAULT_JOBS = {"ingest": "02:00", "brief": "07:00", "lint": "sun 03:00"}
NAME_RE    = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
KIND_RE    = re.compile(r"^[a-z0-9-]{1,40}$")
SLOT_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
WHEN_RE    = re.compile(r"^(?:(mon|tue|wed|thu|fri|sat|sun) )?([01]\d|2[0-3]):[0-5]\d$")
TOP_KEYS  = ("name", "title", "mission", "schema_md", "page_kinds", "slots",
             "jobs", "model_hint", "about", "stale_days")
SLOT_KEYS = ("id", "type", "label", "required", "default")


def _text(v, what, cap, required=True):
    if v is not None and not isinstance(v, str):
        raise ValueError(f"{what} must be text")
    v = (v or "").strip()
    if not v:
        if required:
            raise ValueError(f"{what} is required")
        return ""
    if len(v) > cap:
        raise ValueError(f"{what} is longer than {cap} characters")
    return v


def _slot(raw, i, dropped):
    if not isinstance(raw, dict):
        raise ValueError(f"slot {i} must be an object")
    dropped += [f"slots[{i}].{k}" for k in raw if k not in SLOT_KEYS]
    sid = raw.get("id")
    if not isinstance(sid, str) or not SLOT_ID_RE.fullmatch(sid):
        raise ValueError(f"slot {i}: id must be lowercase letters, digits or _")
    stype = raw.get("type")
    if stype not in SLOT_TYPES:
        raise ValueError(f"slot {sid}: type must be one of {', '.join(SLOT_TYPES)}")
    out = {"id": sid, "type": stype,
           "label": _text(raw.get("label"), f"slot {sid} label", 120, required=False) or sid,
           "required": bool(raw.get("required", False))}
    default = raw.get("default")
    if default not in (None, ""):
        # Only an rss slot may suggest a public feed URL (shown before binding).
        # Paths and calendar URLs must always come from the user.
        if stype != "rss":
            dropped.append(f"slots[{i}].default")
        else:
            url = _text(default, f"slot {sid} default", 500)
            parts = urlparse(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError(f"slot {sid}: default must be an http(s) URL")
            out["default"] = url
    return out


def _jobs(raw, dropped):
    if raw is None:
        return dict(DEFAULT_JOBS)
    if not isinstance(raw, dict):
        raise ValueError("jobs must be an object")
    out = {}
    for k, v in raw.items():
        if k not in JOBS:
            dropped.append(f"jobs.{k}")
        elif v in (None, "", "off"):
            out[k] = "off"
        elif isinstance(v, str) and WHEN_RE.fullmatch(v):
            out[k] = v
        else:
            raise ValueError(f"jobs.{k}: use HH:MM, 'sun 03:00' or 'off'")
    for k in JOBS:
        out.setdefault(k, DEFAULT_JOBS[k])
    return out


def parse_template(src):
    """Validate a template (JSON text/bytes or a dict). Returns a clean dict
    with a "dropped" list of ignored keys. Raises ValueError with a reason."""
    if isinstance(src, (bytes, str)):
        if isinstance(src, str):
            src = src.encode("utf-8", "surrogatepass")
        if len(src) > MAX_BYTES:
            raise ValueError("template is too large")
        try:
            src = json.loads(src)
        except (ValueError, RecursionError) as e:
            raise ValueError(f"template is not valid JSON ({type(e).__name__})") from None
    if not isinstance(src, dict):
        raise ValueError("template must be a JSON object")
    dropped = [k for k in src if k not in TOP_KEYS]
    name = src.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("name must be lowercase letters, digits and dashes")
    if reserved_name(name):
        raise ValueError(f"name '{name}' is a reserved device name")
    kinds = src.get("page_kinds")
    if (not isinstance(kinds, list) or not kinds or len(kinds) > MAX_KINDS
            or not all(isinstance(k, str) and KIND_RE.fullmatch(k) for k in kinds)):
        raise ValueError(f"page_kinds must be 1-{MAX_KINDS} lowercase slugs")
    if any(reserved_name(k) for k in kinds):
        raise ValueError("page_kinds contains a reserved device name")
    slots_raw = src.get("slots") or []
    if not isinstance(slots_raw, list) or len(slots_raw) > MAX_SLOTS:
        raise ValueError(f"slots must be a list of at most {MAX_SLOTS}")
    slots = [_slot(s, i, dropped) for i, s in enumerate(slots_raw)]
    if len({s["id"] for s in slots}) != len(slots):
        raise ValueError("slot ids must be unique")
    stale = src.get("stale_days", 3)
    if not isinstance(stale, int) or isinstance(stale, bool) or not 1 <= stale <= 60:
        raise ValueError("stale_days must be a whole number from 1 to 60")
    return {
        "name": name,
        "title": _text(src.get("title"), "title", 120),
        "mission": _text(src.get("mission"), "mission", MAX_MISSION),
        "schema_md": _text(src.get("schema_md"), "schema_md", MAX_SCHEMA),
        "page_kinds": list(dict.fromkeys(kinds)),
        "slots": slots,
        "jobs": _jobs(src.get("jobs"), dropped),
        "model_hint": _text(src.get("model_hint"), "model_hint", 200, required=False),
        "about": _text(src.get("about"), "about", 2000, required=False),
        "stale_days": stale,
        "dropped": dropped,
    }
