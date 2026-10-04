"""The one place Embers talks to a model: the router's OpenAI-compatible
/v1/chat/completions with a JSON-schema response format.

ask_json() owns the retry policy, so jobs stay pure: they receive a callable
llm(messages, schema, max_tokens) -> (obj, usage) and never see HTTP.

Everything here treats the router's reply and the model's text as untrusted:
every failure surfaces as LLMError with a short message (never the API key,
never a KeyError/TypeError), reads are size-capped and time-limited.
"""
import http.client, json, math, re, time, urllib.error, urllib.parse, urllib.request

TIMEOUT = 600            # a long batch on a 16 GB card can take minutes
DEFAULT_N_CTX = 8192
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_JSON_CHARS = 2 * 1024 * 1024
MAX_DEPTH = 64
_TYPES = {"object": dict, "array": list, "string": str, "integer": int,
          "number": (int, float), "boolean": bool}


MAX_N_CTX = 1 << 20     # a larger "context" from /props is not believable; clamp it


def clamp_n_ctx(value):
    """The router's context size as an int in [1, MAX_N_CTX], else DEFAULT_N_CTX.
    Values below jobs.MIN_N_CTX pass through: the jobs record why they cannot run."""
    if isinstance(value, bool):
        return DEFAULT_N_CTX
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value <= 0:
        return DEFAULT_N_CTX
    return min(value, MAX_N_CTX)


class LLMError(Exception):
    pass


class RouterUnavailable(LLMError):
    """The router refused the connection or answered 503 (down, restarting,
    loading a model): nothing about the request was at fault, so callers
    should stop and try later rather than count it against the input."""


class JSONParseError(LLMError, ValueError):
    """Model text was not usable JSON (is a ValueError for callers that expect one)."""


def check_shape(obj, schema, path="$", depth=0):
    """A tiny JSON Schema subset (type, properties, required, items, enum).
    Returns an error string or None. llama.cpp enforces the schema with a
    grammar; this catches truncated replies and servers that ignore it.
    Depth-capped so deeply nested model output cannot blow the stack."""
    if depth > MAX_DEPTH:
        return f"{path} is too deeply nested"
    t = schema.get("type")
    py = _TYPES.get(t)
    if py is not None:
        if not isinstance(obj, py) or (t in ("integer", "number") and isinstance(obj, bool)):
            return f"{path} should be {t}"
    if "enum" in schema and obj not in schema["enum"]:
        return f"{path} must be one of {schema['enum']}"
    if t == "object":
        for k in schema.get("required", []):
            if k not in obj:
                return f"{path}.{k} is missing"
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                err = check_shape(obj[k], sub, f"{path}.{k}", depth + 1)
                if err:
                    return err
    if t == "array":
        for i, v in enumerate(obj):
            err = check_shape(v, schema.get("items", {}), f"{path}[{i}]", depth + 1)
            if err:
                return err
    return None


_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)


def parse_json(text):
    """Model text -> JSON value. Handles a leading <think>...</think> block
    (reasoning models), ```json fences, and prose around one object.
    Linear-time string ops only; raises JSONParseError on anything else."""
    if not isinstance(text, str):
        raise JSONParseError("empty reply")
    if len(text) > MAX_JSON_CHARS:
        raise JSONParseError("reply too large to parse")
    t = text.lstrip("﻿").strip().lstrip("﻿")
    # Reasoning models: the answer follows the LAST </think> (case-insensitive).
    # Some templates put <think> in the prompt, so only the closing tag appears.
    closes = list(_THINK_CLOSE.finditer(t))
    if closes:
        t = t[closes[-1].end():].strip()
    elif _THINK_OPEN.match(t):
        raise JSONParseError("reply ended inside a <think> block")
    if t.startswith("```"):
        t = t[3:]
        if t[:4].lower() == "json":
            t = t[4:]
        t = t.strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    try:
        return json.loads(t)
    except (ValueError, RecursionError):
        pass
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if starts:
        try:
            return json.JSONDecoder().raw_decode(t, min(starts))[0]
        except (ValueError, RecursionError):
            pass
    raise JSONParseError("reply is not valid JSON")


def ask_json(complete, messages, schema, max_tokens=2048):
    """complete(messages, schema, max_tokens) -> (text, usage). Parses and
    shape-checks the reply; on failure retries once with the error as a hint."""
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    msgs, err = list(messages), ""
    for attempt in (1, 2):
        text, u = complete(msgs, schema, max_tokens)
        for k in usage:
            try:
                usage[k] += int((u or {}).get(k) or 0)
            except (TypeError, ValueError, AttributeError):
                pass
        try:
            obj = parse_json(text)
            err = check_shape(obj, schema)
        except ValueError as e:
            err = f"not valid JSON ({e})"
        if not err:
            return obj, usage
        if attempt == 1:
            msgs = msgs + [{"role": "assistant", "content": (text or "")[:2000]},
                           {"role": "user", "content": f"That reply was rejected: {err}. "
                                                       "Reply again with only JSON matching the schema."}]
    raise LLMError(f"model reply rejected twice: {err}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The router never redirects; following one would forward the key elsewhere."""
    def redirect_request(self, *a, **kw):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout):
    return _opener.open(req, timeout=timeout)


def _fetch(url, body, key, timeout):
    """The single HTTP path: raw reply bytes, size-capped. Every failure is an
    LLMError (RouterUnavailable for refused/503) with the key scrubbed."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")

    def clean(s):
        s = str(s)
        return s.replace(key, "***") if key else s
    try:
        with _open(req, timeout or TIMEOUT) as r:
            raw = r.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("router reply too large")
        return raw
    except LLMError:
        raise
    except urllib.error.HTTPError as e:
        try:
            detail = e.read(300).decode("utf-8", "replace")
        except Exception:
            detail = ""
        cls = RouterUnavailable if e.code == 503 else LLMError
        raise cls(clean(f"router answered {e.code}: {detail}")[:400]) from None
    except (OSError, ValueError, RecursionError, http.client.HTTPException) as e:
        refused = isinstance(e, ConnectionRefusedError) or (
            isinstance(e, urllib.error.URLError) and isinstance(e.reason, ConnectionRefusedError))
        cls = RouterUnavailable if refused else LLMError     # a timeout may be the input's fault
        raise cls(clean(f"router unreachable or unreadable: {type(e).__name__}: {e}")[:400]) from None


def _request(url, body=None, key="", timeout=TIMEOUT):
    raw = _fetch(url, body, key, timeout)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as e:
        msg = f"router reply unreadable: {type(e).__name__}: {e}"
        raise LLMError((msg.replace(key, "***") if key else msg)[:400]) from None


def _request_text(url, body=None, key="", timeout=TIMEOUT):
    return _fetch(url, body, key, timeout).decode("utf-8", errors="replace")


# One Prometheus sample of a request counter: name, optional {labels}, value, optional timestamp.
# fullmatch, so llamacpp:requests_processing_total and friends never count.
_NUM = r"[+-]?(?:\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|[Nn]a[Nn]|[Ii]nf(?:inity)?)"
_COUNTER = re.compile(r"llamacpp:(requests_processing|requests_deferred)(\{[^}]*\})?[ \t]+(" + _NUM
                      + r")(?:[ \t]+-?\d+)?")
# Anything that claims to be one of those samples; if it then fails _COUNTER it is garbage, not skipped.
_COUNTER_CLAIM = re.compile(r"llamacpp:requests_(?:processing|deferred)(?:[{ \t]|$)")
_FAILED = ("fail", "error")
_SATISFIES = {"loaded": ("loaded", "sleeping")}   # a sleeping child is loaded; a request wakes it
POLL_SECONDS = 2


def _valid_port(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        p = v
    elif isinstance(v, str) and v.isascii() and v.isdigit():
        p = int(v)
    else:
        return None
    return p if 1 <= p <= 65535 else None


class Router:
    """The llama.cpp router LlamaForge already runs. Always 127.0.0.1; only the
    port is read from config. The key (router_api_key, else router_local_key,
    as the panel's own proxy does) is never part of repr or error text."""

    def __init__(self, cfg, request=_request, request_text=_request_text):
        port = _valid_port(cfg.get("router_port"))
        if port is None:
            raise LLMError("router_port must be an integer between 1 and 65535")
        self.base = f"http://127.0.0.1:{port}"
        key = cfg.get("router_api_key") or cfg.get("router_local_key") or ""
        self._key = key if isinstance(key, str) else ""
        self.request = request
        self.request_text = request_text

    def __repr__(self):
        return f"Router({self.base})"

    def loaded_model(self):
        return next((m["id"] for m in self.models() if m["status"] == "loaded"), None)

    def models(self):
        """[{"id", "status", "failed"}] for every well-formed registry entry. status
        is the router's value as a str ("" when absent); failed mirrors the
        status dict's flag (a failed load reports value "unloaded" + failed).
        Junk entries are skipped."""
        r = self.request(self.base + "/v1/models", key=self._key, timeout=10)
        data = r.get("data") if isinstance(r, dict) else None
        out = []
        for m in data if isinstance(data, list) else []:
            if not isinstance(m, dict) or not isinstance(m.get("id"), str):
                continue
            st, failed = m.get("status"), False
            if isinstance(st, dict):
                st, failed = st.get("value"), bool(st.get("failed"))
            out.append({"id": m["id"], "status": "" if st is None else str(st)[:100], "failed": failed})
        return out

    def activity(self, model):
        """In-flight plus queued requests for model, from the router's /metrics.
        Raises LLMError rather than guess 0 when the counters are absent (the
        router runs without --metrics), unparseable or nonsensical.
        autoload=false: without it the router would load model to answer,
        evicting the user's model under --models-max 1 (unloaded -> 400)."""
        text = self.request_text(f"{self.base}/metrics?model={urllib.parse.quote(str(model), safe='')}"
                                 "&autoload=false", key=self._key, timeout=10)
        if not isinstance(text, str):
            raise LLMError("router metrics were not text")
        if len(text) > MAX_RESPONSE_BYTES:
            raise LLMError("router metrics too large")
        total, processing = 0.0, False
        for line in text.splitlines():
            line = line.strip()
            m = _COUNTER.fullmatch(line)
            if not m:
                if _COUNTER_CLAIM.match(line):
                    raise LLMError("router metrics has an unparseable request counter line")
                continue
            v = float(m.group(3))
            if not math.isfinite(v) or v < 0:
                raise LLMError(f"router metrics has a bad {m.group(1)} value")
            total += v
            processing = processing or m.group(1) == "requests_processing"
        if not processing:
            raise LLMError("router metrics are missing request counters")
        if not math.isfinite(total):
            raise LLMError("router metrics request counters overflow")
        return math.ceil(total)       # 0.6 in flight is busy, never a guessed idle

    def _model_op(self, op, model):
        if not isinstance(model, str) or not model:
            raise LLMError("model must be a non-empty string")
        self.request(f"{self.base}/models/{op}", {"model": model}, key=self._key, timeout=30)

    def load(self, model):
        """Ask the router to load model. Any 2xx is success; failures raise."""
        self._model_op("load", model)

    def unload(self, model):
        self._model_op("unload", model)

    def wait_status(self, model, want, timeout=600, sleep=time.sleep, clock=time.monotonic):
        """Poll models() every POLL_SECONDS until model's status is want -> True
        ("sleeping" satisfies "loaded"). False on timeout or on a failed load
        (the failed flag, or a status naming fail/error). RouterUnavailable
        (busy loading, restarting) is tolerated; any other LLMError propagates."""
        ok = _SATISFIES.get(want, (want,))
        deadline = clock() + timeout
        while True:
            try:
                entry = next((m for m in self.models() if m["id"] == model), None)
            except RouterUnavailable:
                entry = None
            status = entry["status"] if entry else None
            if entry and entry["failed"] and want != "unloaded":
                return False
            if status in ok:
                return True
            if status and any(w in status.lower() for w in _FAILED):
                return False
            if clock() >= deadline:
                return False
            sleep(POLL_SECONDS)

    def n_ctx(self, model):
        try:
            # autoload=false: never load (and so evict for) a model just to read its props.
            props = self.request(f"{self.base}/props?model={urllib.parse.quote(str(model), safe='')}"
                                 "&autoload=false", key=self._key, timeout=10)
            return int(props.get("default_generation_settings", {}).get("n_ctx") or DEFAULT_N_CTX)
        except (LLMError, TypeError, ValueError, AttributeError):
            return DEFAULT_N_CTX

    def complete(self, model):
        def call(messages, schema, max_tokens):
            # Thinking off: a reasoning model otherwise spends max_tokens reasoning and never
            # writes the JSON. Chat templates without the variable ignore it.
            body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "reply", "schema": schema}}}
            r = self.request(self.base + "/v1/chat/completions", body, key=self._key, timeout=TIMEOUT)
            try:
                text = r["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise LLMError("router reply had no message") from None
            if not isinstance(text, str):
                raise LLMError("router reply had no text content")
            usage = r.get("usage")
            return text, usage if isinstance(usage, dict) else {}
        return call

    def llm(self, model, max_tokens_cap=4096):
        complete = self.complete(model)

        def call(messages, schema, max_tokens=2048):
            return ask_json(complete, messages, schema, min(max_tokens, max_tokens_cap))
        return call
