"""The one place Embers talks to a model: the router's OpenAI-compatible
/v1/chat/completions with a JSON-schema response format.

ask_json() owns the retry policy, so jobs stay pure: they receive a callable
llm(messages, schema, max_tokens) -> (obj, usage) and never see HTTP.

Everything here treats the router's reply and the model's text as untrusted:
every failure surfaces as LLMError with a short message (never the API key,
never a KeyError/TypeError), reads are size-capped and time-limited.
"""
import http.client, json, re, urllib.error, urllib.parse, urllib.request

TIMEOUT = 600            # a long batch on a 16 GB card can take minutes
DEFAULT_N_CTX = 8192
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_JSON_CHARS = 2 * 1024 * 1024
MAX_DEPTH = 64
_TYPES = {"object": dict, "array": list, "string": str, "integer": int,
          "number": (int, float), "boolean": bool}


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


def _request(url, body=None, key="", timeout=TIMEOUT):
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
        return json.loads(raw.decode("utf-8", errors="replace"))
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

    def __init__(self, cfg, request=_request):
        port = _valid_port(cfg.get("router_port"))
        if port is None:
            raise LLMError("router_port must be an integer between 1 and 65535")
        self.base = f"http://127.0.0.1:{port}"
        key = cfg.get("router_api_key") or cfg.get("router_local_key") or ""
        self._key = key if isinstance(key, str) else ""
        self.request = request

    def __repr__(self):
        return f"Router({self.base})"

    def loaded_model(self):
        r = self.request(self.base + "/v1/models", key=self._key, timeout=10)
        data = r.get("data") if isinstance(r, dict) else None
        for m in data if isinstance(data, list) else []:
            if not isinstance(m, dict):
                continue
            st = m.get("status")
            if (st.get("value") if isinstance(st, dict) else st) == "loaded":
                return m.get("id")
        return None

    def n_ctx(self, model):
        try:
            props = self.request(f"{self.base}/props?model={urllib.parse.quote(str(model), safe='')}",
                                 key=self._key, timeout=10)
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
