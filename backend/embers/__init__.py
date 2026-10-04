"""Embers: persistent local agents that keep their own LLM wiki.

Stdlib only. Modules: templates (blueprints), verify (evidence checks),
wikifs (the wiki folder), store (sqlite index), sources (read-only inputs),
llm (router client), prompts (wording + schemas), jobs (ingest/brief/lint).
"""
import os

import config


WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)] + [f"lpt{i}" for i in range(1, 10)])


def reserved_name(s):
    """True for Windows device names (unusable as file or folder names)."""
    return s.lower() in WINDOWS_RESERVED


def embers_dir(cfg=None):
    """Where ember data lives: config "embers_dir", default <ROOT>/embers."""
    cfg = cfg if cfg is not None else config.load()
    v = cfg.get("embers_dir") if isinstance(cfg, dict) else None
    if not (isinstance(v, str) and v.strip()):      # unset, or not a path at all: the default
        v = os.path.join(config.ROOT, "embers")
    return config._abs(v)
