---
title: Fork differences
section: reference
order: 3
---

# Fork differences

English · [한국어](../readme/fork-diff.ko.md) · [日本語](../readme/fork-diff.ja.md) · [简体中文](../readme/fork-diff.zh-CN.md) · [Русский](../readme/fork-diff.ru.md)

The original LlamaForge plus the changes below, minus the features listed under
*Removed in this build*. Tested on Ubuntu Server only.

## LAN exposure (opt-in)

`panel_host` and `router_allow_keyless_lan` in `config.json` move
the dashboard and the router onto the LAN. Defaults are
unchanged: loopback binds, and the router still fails closed without a key.
The Host/Origin guard stays on and additionally accepts the machine's own
names. Details: [Config](config.md),
[Security](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md).

## MCP over HTTP (opt-in)

`mcp_host` / `mcp_port` (default `8092`) serve the MCP server over stateless
Streamable HTTP next to stdio. Off by default. Details: [MCP Server](mcp.md).

## GPU telemetry without vendor tools

When `nvidia-smi` is absent, GPU state (VRAM used/total, utilization,
temperature) is read from the kernel's DRM sysfs. Device names and tokens come
from `llama-server --list-devices`, so the multi-model planner speaks `Vulkan0`
and not just `CUDA0`.

## Model gateway (LiteLLM)

The **Gateway** tab runs [LiteLLM](https://github.com/BerriAI/litellm) (MIT)
as a managed sidecar: one virtual model name alternates requests across
several real models (`gateway_models`), tuned by free-form LiteLLM presets
(`gateway_presets`). Each virtual name runs in one of two behaviors: alternate
(requests rotate strictly in turn across the models - a round-robin LlamaForge
installs into LiteLLM) or primary + reserve (traffic stays on the first model
while it has a free slot and spills to the reserves when it is full). With
`router_allow_keyless_lan` the gateway serves without a key too. Off by
default; own port (`gateway_port`, default 8300), own venv
(`tools/litellm/venv`), managed through the panel's API and MCP
tools. Details: [Model gateway](gateway.md).

## Engines (prebuilt builds)

The Build / Update tab is **Engines**: download and select builds for three
llama.cpp-family engines - llama.cpp (ggml-org), ik_llama.cpp (Thireus
prebuilts) and beellama.cpp (Anbeeld) - and switch what the router runs.
Launch profiles are separated per engine family: an unpinned profile refuses
to launch on another engine, and preset keys its engine does not know are
dropped and listed in the launch report. Building from source and the vLLM
backend are gone. Details: [Engines](build.md).

## Model benchmarking (Bench)

The **Bench** tab runs [llama-bench](https://github.com/ggml-org/llama.cpp) over a
batch-size / ubatch-size grid to pick the sizes a model actually wants. The
model's offload keys become the matching llama-bench flags, workloads cover
prompt, generation and combined tests, and one bench runs at a time - the bench
loads the model itself and refuses while any model is loaded in the regular
llama.cpp. Finished runs are recorded in `logs/bench/history.jsonl` with the raw
llama-bench output beside them: each cell shows its change against the previous
run of the same model, the best pair is recommended (highest combined
throughput; within 3% the smaller micro-batch wins, then the smaller batch) and
one click writes it into the model's `models.ini` section. MCP exposes the same
as `bench_run`, `bench_status`, `bench_stop`, `bench_apply` and `bench_history`.

## Web UI localization

The web UI speaks English, Russian, Japanese, Korean and Simplified Chinese.
A picker in the topbar, beside the engine badge, chooses the language and stores
it per browser (localStorage `lf_lang`); `web_lang` in `config.json` sets a panel
default. Strings missing a translation fall back to English.

## Removed in this build

Chat, Voice and Embers (the mini-agent), the Context wiki, the Help viewer,
the topbar clock and the credit/star banner are cut. The vLLM backend is cut
in full: the `backend/vllm_*` modules, the WSL2 helper that drove it, its
docs page and its tests are gone.

## Install and updates

The one-line installers in the README install the original project. This build
deploys from this repository. The in-app updater refuses copies without the
install manifest (`.lf-files.json`); such copies are updated by hand.
