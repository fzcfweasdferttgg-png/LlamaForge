---
title: Model gateway
section: guides
order: 6
---

# Model gateway (LiteLLM)

The gateway gives you **one virtual model name over several real models**. A
request that names a virtual name (say `mix`) is handed to one of its models;
the active preset's `routing_strategy` picks which one per request, so traffic
alternates across them. Clients see one model name and one OpenAI-compatible
endpoint; streaming passes straight through.

It runs [LiteLLM](https://github.com/BerriAI/litellm) (MIT) as a separate
process on its own port (default `8300`) in its own venv
(`tools/litellm/venv`) - the panel itself stays pure stdlib.

> Off by default: nothing runs until the gateway is installed, configured and
> started (or `gateway_enabled` is set).

## Settings

The **Gateway** tab owns the whole thing:

- **Virtual names** - each name maps to real model ids, with one of two ready
  behaviors:
  - **Alternate across models** - requests rotate across the name's models
    (LiteLLM `simple-shuffle`).
  - **Primary + reserve** - the first model is the primary: traffic stays on it
    while it has a free slot and spills to the reserves when it is full or not
    loaded (LiteLLM `order` priority plus a `max_parallel_requests` cap of the
    model's slot count - `parallel` in models.ini). **When all busy** says what
    happens then: *Wait in queue* keeps the last model queueing at llama.cpp,
    *Reply 429 busy* caps every model so a full group answers 429.

  Only models loaded right now are offered; a model unloaded later fails over
  to the next one automatically. One deployment per (name, model) is written
  into the generated config, all pointed at the local router.
- **port / bind** - the gateway's own port (default `8300`) and its bind:
  `127.0.0.1` by default, `0.0.0.0` to share it on the LAN like `panel_host`.
- **serve on panel start** - `gateway_enabled`: the panel starts the gateway on
  launch when set; `stop.sh` stops it along with everything else.

## Authentication

LiteLLM refuses to start with no master key, so one is filled in for it: the
router's own key becomes `general_settings.master_key`, or a preset can set
its own. A keyless setup (no router key and no preset `master_key`) starts
with LiteLLM's explicit permit for an unset master key - the gateway then
mirrors the router's own keyless-LAN stance. With a master key set, clients
send it as `Authorization: Bearer <master key>`.

## Presets

A preset is a free-form LiteLLM settings document. Its `router`,
`litellm_settings` and `general_settings` sections are written verbatim into
the generated config, so any documented LiteLLM setting can be turned here
without a code change. The shipped default is

```yaml
router:
  routing_strategy: "simple-shuffle"
```

`routing_strategy` decides which of a name's models takes each request:
`simple-shuffle` picks one at random per request (statistically even
alternation), `latency-based-routing` prefers the fastest responder. Presets
live in `gateway_presets`, the active one's name in `gateway_preset`. The
virtual names fill the generated `model_list`; the presets fill the rest.

## API and MCP

`GET /api/gateway`, `POST /api/gateway/save`,
`POST /api/gateway/preset/save`, `POST /api/gateway/install`,
`POST /api/gateway/start`, `POST /api/gateway/stop`
([HTTP API](api.md)). The MCP server mirrors them: `gateway_status`,
`gateway_save`, `gateway_preset_save`, `gateway_start`, `gateway_stop`.

## Install

**Install LiteLLM** (or `POST /api/gateway/install`) creates the venv and
installs `litellm[proxy]` in the background - on server images without
ensurepip it bootstraps pip from get-pip.py first. No root, no system
packages. The install log is `logs/gateway-install.log`; the gateway's own
log is `logs/gateway.log`, and the generated config is
`tools/litellm/gateway.yaml`.
