"""Inference engines behind one interface.

llama.cpp and its forks share the router but not the binary or the knob
schema, and engines that manage their own process need the same verbs again.
Every caller that wanted to act on "a model" had to know which engine it
belonged to first.

So: a Backend is the set of verbs the dashboard needs from an engine, and the
engines are implementations of it. `for_model(id)` answers "who owns this?", so
routes can dispatch on the model rather than on the URL. The per-engine modules
keep doing the real work - this is the seam that lets callers stop caring which
one is on the other side.

A Backend implements:

    name            str, matches the "backend" field in a model row
    available()     is this engine usable on this machine at all
    list_models()   [model row]  - the shape /api/state already returns
    schema()        knob schema for the editor
    load(id)        -> (ok, error)
    unload(id)      -> (ok, error)
    save(id, knobs) -> {"restarted": bool} - persist, apply to a live model
    delete(id)      -> (ok, error); raises Unsupported when it can't

Pure stdlib.
"""
import os


class Unsupported(Exception):
    """The engine does not offer this verb (e.g. llama.cpp can't delete a model
    from the dashboard - the file is the user's, not ours)."""


class Backend:
    """Interface documentation; implementations don't inherit from it, they just
    satisfy it. Kept as a class so the contract has one obvious home."""
    name = ""

    def available(self):     return True
    def list_models(self):   raise NotImplementedError
    def schema(self):        raise NotImplementedError
    def load(self, mid):     raise NotImplementedError
    def unload(self, mid):   raise NotImplementedError
    def save(self, mid, knobs):  raise NotImplementedError
    def delete(self, mid):   raise Unsupported(f"{self.name} cannot delete models")


class LlamaCppBackend:
    """GGUF models served by llama.cpp's own multi-model router.

    The router owns process lifecycle and hot-reload, so this backend is mostly
    a translator between models.ini and the router's HTTP API."""
    name = "llamacpp"

    def __init__(self, deps):
        self._d = deps          # routes module functions, injected to stay testable

    def available(self):
        return True

    def state(self):
        """Rows plus the inherited [*] globals, from one router round-trip.
        /api/state polls every few seconds, so this stays a single call."""
        base = self._d.model_state()
        endpoint = f"http://127.0.0.1:{self._d.cfg()['router_port']}"
        for m in base["models"]:
            m["backend"] = self.name
            if m.get("status") == "loaded":
                m.setdefault("endpoint", endpoint)   # a model in its own process has its own
        return base

    def list_models(self):
        return self.state()["models"]

    def schema(self):
        return self._d.schema()

    def load(self, mid):
        code, res = self._d.router("/models/load", "POST", {"model": mid})
        if code == 200:
            return True, ""
        return False, (res or {}).get("error", "load failed")

    def unload(self, mid):
        code, res = self._d.router("/models/unload", "POST", {"model": mid})
        if code == 200:
            return True, ""
        return False, (res or {}).get("error", "unload failed")

    def save(self, mid, knobs):
        was_running = self._d._apply_knobs_and_reload(mid, knobs)
        # llama.cpp reads args at load time, so a live model is unloaded rather
        # than restarted - the user reloads when ready.
        return {"restarted": False, "was_running": was_running}

    def delete(self, mid):
        raise Unsupported("llama.cpp models are files on disk; remove them with "
                          "Setup > prune instead")


class IkLlamaBackend(LlamaCppBackend):
    """GGUF models served by ik_llama (ikawrakow's llama.cpp fork).

    It speaks the same router API as llama.cpp, so it inherits everything except
    which binary is on the other end and which knob schema that binary reports."""
    name = "ikllama"

    def available(self):
        sbin = self._d.cfg().get("ik_llama_server_bin", "")
        return bool(sbin and os.path.exists(sbin))

    def schema(self):
        return self._d.ik_schema()


# Engines that drive llama.cpp's router. Only one can own the router at a time,
# so `state()` reports the active one and skips its sibling rather than listing
# the same models twice.
LLAMA_FAMILY = ("llamacpp", "ikllama")


class Registry:
    """The set of engines this install can use, and the lookup that lets a route
    act on a model without knowing which engine owns it."""

    def __init__(self, deps):
        self._d = deps
        self._backends = [LlamaCppBackend(deps), IkLlamaBackend(deps)]

    def active_engine(self):
        """Which llama-family engine owns the router right now.

        Read through the injected deps like everything else here, and validated:
        /api/state polls this every few seconds, so a stray value in config.json
        must degrade to the default rather than 500 the whole dashboard."""
        name = self._d.cfg().get("active_engine", "llamacpp")
        return name if name in LLAMA_FAMILY else "llamacpp"

    def all(self):
        return list(self._backends)

    def enabled(self):
        return [b for b in self._backends if b.available()]

    def get(self, name):
        for b in self._backends:
            if b.name == name:
                return b
        raise KeyError(name)

    def supports(self, name):
        try:
            return self.get(name).available()
        except KeyError:
            return False

    def state(self):
        """{"models": [...], "global": {...}} across every usable engine, ordered
        loaded-first then by id - the order the model list has always used.
        `global` stays the active engine's [*] section; it is a models.ini concept."""
        active = self.active_engine()
        base_backend = self.get(active)
        base = base_backend.state()
        rows = list(base["models"])
        for b in self.enabled():
            if b is base_backend:
                continue
            # The idle llama-family sibling would re-list the same router rows.
            if b.name in LLAMA_FAMILY:
                continue
            rows.extend(b.list_models())
        rows.sort(key=lambda m: (m["status"] != "loaded", m["id"]))
        return {"models": rows, "global": base.get("global", {})}

    def list_models(self):
        return self.state()["models"]

    def for_model(self, mid, hint=""):
        """Which engine owns this model id.

        `hint` is the row's own `backend` field, which the dashboard already
        has - taking it avoids listing every engine's models just to route one
        click. It is only trusted if it names a usable engine; otherwise we look
        the id up, and fall back to llama.cpp so ids that predate the registry
        resolve the way they always did.

        A llama-family hint is snapped to whichever of those engines is active:
        an open tab can hold a row tagged with the engine that was current when
        it rendered, and honouring that stale tag would edit knobs against the
        wrong binary's schema."""
        if hint in LLAMA_FAMILY:
            return self.get(self.active_engine())
        if hint and self.supports(hint):
            return self.get(hint)
        for b in self.enabled():
            if any(m["id"] == mid for m in b.list_models()):
                return b
        return self.get("llamacpp")
