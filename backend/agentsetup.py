"""Generate (and optionally apply) coding-agent config for LlamaForge endpoints.

Claude Code talks to the Anthropic shim (panel port); Codex and pi.dev talk to
the OpenAI-compatible router (router port /v1). Generators are pure (inputs ->
config content); apply() performs file I/O. Pure stdlib: JSON configs are
deep-merged; Codex TOML is append-if-absent (no stdlib TOML writer exists).
"""
import copy
import json
import os
import re
import shutil

AGENTS = ("claude-code", "codex", "pi")
PROVIDER = "llamaforge"

_PATHS = {
    "claude-code": ".claude/settings.json",
    "codex": ".codex/config.toml",
    "pi": ".pi/agent/models.json",
}


def _display_path(agent):
    return "~/" + _PATHS[agent]


# ---------------- generators (pure) ----------------

def _gen_claude(endpoint, api_key, model, small_model):
    settings = {"env": {
        "ANTHROPIC_BASE_URL": endpoint,
        "ANTHROPIC_AUTH_TOKEN": api_key or "llamaforge",
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": small_model,
    }}
    return {
        "agent": "claude-code", "format": "json",
        "target_path": _display_path("claude-code"),
        "content": json.dumps(settings, indent=2),
        "endpoint": endpoint,
        "instructions": ("Merge this into ~/.claude/settings.json, or export the "
                         "same keys as environment variables before running Claude Code. "
                         "Claude Code must run on this machine (localhost only)."),
    }


def _codex_provider_block(endpoint, api_key):
    lines = [f"[model_providers.{PROVIDER}]", 'name = "LlamaForge"',
             f'base_url = "{endpoint}"', 'wire_api = "chat"']
    if api_key:
        lines.append('env_key = "LLAMAFORGE_API_KEY"')
    return "\n".join(lines) + "\n"


def _codex_top_level(model):
    return [f'model = "{model}"', f'model_provider = "{PROVIDER}"']


def _gen_codex(endpoint, api_key, model):
    content = _codex_provider_block(endpoint, api_key) + "\n" + "\n".join(_codex_top_level(model)) + "\n"
    instr = "Add this to ~/.codex/config.toml."
    if api_key:
        instr += " Set LLAMAFORGE_API_KEY in your environment to your router API key."
    return {"agent": "codex", "format": "toml", "target_path": _display_path("codex"),
            "content": content, "endpoint": endpoint, "instructions": instr}


def _gen_pi(endpoint, api_key, model):
    cfg = {"providers": {PROVIDER: {
        "baseUrl": endpoint, "api": "openai-completions",
        "apiKey": api_key or "llamaforge", "models": [{"id": model}]}}}
    return {"agent": "pi", "format": "json", "target_path": _display_path("pi"),
            "content": json.dumps(cfg, indent=2), "endpoint": endpoint,
            "instructions": "Merge this into ~/.pi/agent/models.json."}


def generate(agent, endpoint, api_key, model, small_model=None):
    if agent == "claude-code":
        return _gen_claude(endpoint, api_key, model, small_model or model)
    if agent == "codex":
        return _gen_codex(endpoint, api_key, model)
    if agent == "pi":
        return _gen_pi(endpoint, api_key, model)
    raise ValueError(f"unknown agent: {agent}")


# ---------------- apply (file I/O) ----------------

def _target_path(agent, home):
    if agent not in _PATHS:
        raise ValueError(f"unknown agent: {agent}")
    return os.path.join(home, *_PATHS[agent].split("/"))


def _backup(path):
    if os.path.exists(path):
        bak = path + ".llamaforge.bak"
        if not os.path.exists(bak):        # preserve the user's true original across re-applies
            shutil.copy2(path, bak)
        return bak
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _merge_json(path, patch):
    existed = os.path.exists(path)
    data = _read_json(path) if existed else {}
    if not isinstance(data, dict):
        data = {}
    _deep_merge(data, patch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, indent=2)
    return "merged" if existed else "created"


def _append_toml_block(path, block, top_level):
    existed = os.path.exists(path)
    text = ""
    if existed:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    if f"[model_providers.{PROVIDER}]" in text:
        return "present"
    out = []
    if text and not text.endswith("\n"):
        out.append("\n")
    out.append("\n" + block + "\n")
    for line in top_level:
        key = line.split("=", 1)[0].strip()
        if re.search(rf"(?m)^\s*{re.escape(key)}\s*=", text):
            out.append("# " + line + "   # set this to use LlamaForge\n")
        else:
            out.append(line + "\n")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "a" if existed else "w"
    with open(path, mode, encoding="utf-8", newline="") as f:
        f.write("".join(out))
    return "appended" if existed else "created"


def apply(agent, home, endpoint, api_key, model, small_model=None):
    path = _target_path(agent, home)          # raises ValueError on unknown agent
    backup = _backup(path)
    if agent == "claude-code":
        action = _merge_json(path, {"env": {
            "ANTHROPIC_BASE_URL": endpoint,
            "ANTHROPIC_AUTH_TOKEN": api_key or "llamaforge",
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": small_model or model}})
    elif agent == "pi":
        action = _merge_json(path, {"providers": {PROVIDER: {
            "baseUrl": endpoint, "api": "openai-completions",
            "apiKey": api_key or "llamaforge", "models": [{"id": model}]}}})
    elif agent == "codex":
        action = _append_toml_block(path, _codex_provider_block(endpoint, api_key),
                                    _codex_top_level(model))
    return {"ok": True, "path": path, "backup": backup, "action": action}
