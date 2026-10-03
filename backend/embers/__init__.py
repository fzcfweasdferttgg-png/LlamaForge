"""Embers: persistent local agents that keep their own LLM wiki.

Stdlib only. Modules: templates (blueprints), verify (evidence checks),
wikifs (the wiki folder), store (sqlite index), sources (read-only inputs),
llm (router client), prompts (wording + schemas), jobs (ingest/brief/lint).
"""
import os

import config


def embers_dir(cfg=None):
    """Where ember data lives: config "embers_dir", default <ROOT>/embers."""
    cfg = cfg if cfg is not None else config.load()
    return config._abs(cfg.get("embers_dir") or os.path.join(config.ROOT, "embers"))
