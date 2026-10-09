---
title: Fork differences
section: reference
order: 3
---

# Fork differences

The original LlamaForge plus the changes below. Everything else is upstream
as-is. Tested on Ubuntu Server only.

## LAN exposure (opt-in)

`panel_host`, `chat_host` and `router_allow_keyless_lan` in `config.json` move
the dashboard, the chat listener and the router onto the LAN. Defaults are
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

## Install and updates

The one-line installers in the README install the original project. This build
deploys from this repository. The in-app updater refuses copies without the
install manifest (`.lf-files.json`); such copies are updated by hand.
