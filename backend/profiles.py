"""Launch profiles: model + preset + pinned engine build, in one click.

plan() turns a stored profile into the concrete steps routes.py executes
(switch engine? which knobs? which model?), checked against what is actually
installed right now. Pure, so the decisions are unit-tested without a router.

Profiles are separated by engine family (llamacpp/ikllama/beellama): the
families take different launch flags, so a profile targets one family and
its preset knobs are filtered against that engine's own --help.
"""
import os

FAMILIES = ("llamacpp", "ikllama", "beellama")


def _install_named(installs, name):
    return next((i for i in installs if os.path.basename(i["dir"]) == name), None)


def family(prof, installs):
    """The engine family this profile targets: the pinned build's family wins,
    else the stored one, else the backend it was saved from."""
    prof = prof or {}
    if prof.get("engine"):
        inst = _install_named(installs or [], prof["engine"])
        if inst and inst.get("engine") in FAMILIES:
            return inst["engine"]
    fam = str(prof.get("family") or "").strip()
    return fam if fam in FAMILIES else (prof.get("backend") or "llamacpp")


def target_bin(prof, installs):
    """server_bin the profile will run on: its pinned build's, else None
    (the active build of its family)."""
    if not (prof or {}).get("engine"):
        return None
    inst = _install_named(installs or [], prof["engine"])
    return (inst or {}).get("server_bin")


def _filter_settings(settings, known):
    """(kept, dropped): keys the target engine's --help never heard of are
    dropped, and the launch reports which ones."""
    kept, dropped = {}, []
    for k, v in settings.items():
        if isinstance(k, str) and k in known:
            kept[k] = v
        else:
            dropped.append(str(k)[:60])
    return kept, dropped


def plan(prof, installs, presets, known=None):
    """{model, backend, family, target_bin, switch_bin, settings, dropped};
    ValueError when the profile can't launch as saved (its build was pruned,
    its preset deleted...). `known` is the target engine's knob index
    (recipes.knob_index); with one, preset keys it doesn't know are dropped."""
    backend = prof.get("backend") or "llamacpp"
    fam = family(prof, installs)
    switch_bin = None
    bin_ = target_bin(prof, installs)
    if prof.get("engine"):
        if not bin_:
            raise ValueError(f"engine build {prof['engine']} is no longer installed - "
                             f"edit the profile or reinstall it from Engines")
        inst = _install_named(installs, prof["engine"])
        if not inst.get("active"):
            switch_bin = bin_
    settings = None
    dropped = []
    if prof.get("preset"):
        preset = presets.get(prof["preset"])
        if preset is None:
            raise ValueError(f"preset {prof['preset']} no longer exists")
        # same normalization as /api/save: blank means "unset this key"
        settings = {k: (None if str(v).strip() == "" else str(v).strip())
                    for k, v in preset.items()}
        if known is not None:
            settings, dropped = _filter_settings(settings, known)
    return {"model": prof["model"], "backend": backend, "family": fam,
            "target_bin": bin_, "switch_bin": switch_bin,
            "settings": settings, "dropped": dropped}


def pinned_dirs(profiles, installs):
    """Install dirs some profile pins, so pruning old builds never breaks one."""
    names = {p.get("engine") for p in profiles.values() if isinstance(p, dict)}
    return [i["dir"] for i in installs if os.path.basename(i["dir"]) in names]
