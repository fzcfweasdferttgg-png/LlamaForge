"""Ask an ember a question and get an answer from its wiki, citing items.

The flow is the brief's, pointed at a question: full-text search picks the
pages, their verified items (open and done) whose evidence still occurs in its
raw are offered with one source sentence each, and the reply keeps only ids it
was shown. The answer is free text, so it is not rewritten; when it names a
person, number or link that neither the question nor the shown items contain,
"grounded" is false and the panel says so. Read-only: no lock, no writes.
"""
from embers import jobs, prompts, verify

ASK_PAGES        = 3         # pages the search may pick
ASK_ITEMS        = 30        # verified items per page offered before the n_ctx cut
ASK_REPLY_TOKENS = 1024
MAX_ANSWER       = 2000      # characters kept of the model's answer
MAX_QUESTION     = 500


class AskError(Exception):
    """The question cannot be put to this model (its context is too small)."""


def _pages(st, question):
    pages = [p for p in st.search(question, ASK_PAGES) if verify.PAGE_RE.fullmatch(p)]
    if pages:
        return pages
    recent = sorted(st.all_pages(), key=lambda p: p["updated"] or "", reverse=True)   # "anything new?"
    return [p["page"] for p in recent if verify.PAGE_RE.fullmatch(p["page"] or "")][:ASK_PAGES]


def _prompt(tpl, now, question, offered, cap):
    """Drop the last offered item until the prompt fits `cap` estimated tokens.
    Returns (messages, size, shown_ids); shown_ids is empty when nothing fits."""
    shown = list(offered)
    while True:
        msgs = prompts.ask_messages(tpl["mission"], now, question, shown)
        size = sum(jobs._est(m["content"]) for m in msgs)
        if size <= cap or not shown:
            return msgs, size, [i["id"] for i in shown] if size <= cap else []
        shown.pop()


def _cited(reply, shown):
    ids = reply.get("items") if isinstance(reply, dict) else None
    out = []
    for iid in ids if isinstance(ids, list) else []:
        if not isinstance(iid, str):
            continue
        if iid not in shown:               # "[it-xxxxxxxx] page": the id inside counts
            iid = next((m for m in verify.ITEM_RE.findall(iid[:200]) if m in shown), iid)
        if iid in shown and iid not in out:
            out.append(iid)
    return out


def ask(ember, llm, question, now, n_ctx=8192):
    """Returns {"status": "ok"|"empty", "answer", "items": [{"id", "page", "text",
    "status", "quote", "raw"}], "pages", "grounded", "usage"}. Raises ValueError
    for a blank question, AskError when the context is too small, and whatever
    llm raises (LLMError, RouterUnavailable)."""
    question = prompts.one_line(question, MAX_QUESTION)
    if not question:
        raise ValueError("ask a question")
    if n_ctx < jobs.MIN_N_CTX:
        raise AskError(f"model context n_ctx={n_ctx} is below the minimum of {jobs.MIN_N_CTX} tokens; "
                       "load the model with a larger context")
    st = ember.store
    pages = _pages(st, question)
    candidates = [i for p in pages for i in st.items_for_page(p)[:ASK_ITEMS * 2]
                  if i["verified"] and isinstance(i["id"], str) and verify.ITEM_RE.fullmatch(i["id"])]
    evidence = jobs._checked_evidence(ember, candidates)
    items, per_page = [], {}
    for i in candidates:                   # open before done within a page (items_for_page order)
        if i["id"] in evidence and per_page.get(i["page"], 0) < ASK_ITEMS:
            per_page[i["page"]] = per_page.get(i["page"], 0) + 1
            items.append(dict(i, quote=evidence[i["id"]][0]["quote"], raw=evidence[i["id"]][0]["raw"]))
    result = {"status": "empty", "answer": "", "items": [], "pages": pages, "grounded": True, "usage": {}}
    if not items:
        return result
    msgs, size, shown = _prompt(ember.template, now, question, items, int(n_ctx * jobs.PROMPT_SHARE))
    if not shown:
        raise AskError("the question and instructions alone do not fit the model context")
    reply, usage = llm(msgs, prompts.ASK_SCHEMA, max(jobs.MIN_REPLY_TOKENS, min(ASK_REPLY_TOKENS, n_ctx - size)))
    answer = reply.get("answer") if isinstance(reply, dict) else None
    answer = " ".join(answer.split())[:MAX_ANSWER] if isinstance(answer, str) else ""
    by_id = {i["id"]: i for i in items}
    cited = _cited(reply, set(shown))
    ground = " ".join([verify.normalise(question)]
                      + [jobs._ground(by_id[iid], evidence[iid], now) for iid in shown])
    result.update(status="ok", answer=answer, usage=usage if isinstance(usage, dict) else {},
                  grounded=jobs._grounded_text(answer, ground),
                  items=[{k: by_id[iid][k] for k in ("id", "page", "text", "status", "quote", "raw")}
                         for iid in cited])
    return result
