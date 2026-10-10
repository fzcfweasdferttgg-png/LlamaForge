---
title: FAQ
section: faq
order: 1
---

# FAQ

## How is LlamaForge different from LM Studio, Ollama, or Jan?

LlamaForge runs the official llama.cpp server itself (or your own build), so new model support arrives the day llama.cpp ships it, and it gives you per-model control of every `llama-server` flag. Install is also one line with no compiler. Those tools are more mature and more polished: if you'd rather have polish than control, [LM Studio](https://lmstudio.ai), [Ollama](https://ollama.com) or [Jan](https://jan.ai) are good choices.

## What platforms does it run on?

Windows with an NVIDIA GPU is the most-tested path. Linux and macOS (Apple Silicon) are an early preview: the same dashboard and a one-line installer, CI-tested on all three but with little real-hardware use so far. See [Installation](install.md) for the per-OS commands.

## What GPU do I need?

None strictly. The official llama.cpp builds cover NVIDIA (CUDA), AMD and Intel (Vulkan), Apple Silicon (Metal) and CPU-only, and **Install llama.cpp** picks the right one. More VRAM means bigger models: Discover rates every quant against your VRAM before you download it.

## Does LlamaForge come with any models?

No. LlamaForge contains no llama.cpp source code and bundles no models. The backend (`backend/server.py`, pure Python stdlib) drives llama.cpp's own router. Models are found two ways: the **Setup** tab scans your drives (or `$HOME` and mounts) for GGUFs you already have, and the **Discover** tab searches huggingface.co for GGUF (llama.cpp) models to download, rated **FITS / TIGHT / CPU OFFLOAD** against your VRAM before you pull one down.


## Is my API key or model traffic sent anywhere?

The dashboard and its management API always bind to `127.0.0.1`. The separate
llama.cpp router is local by default; the Setup tab can make that router LAN
accessible at `0.0.0.0`, but a usable API key is backend-enforced for every new
LAN configuration and LlamaForge-owned start. There is no unauthenticated-LAN
toggle. See [SECURITY.md](https://github.com/dadwritestech/LlamaForge/blob/master/SECURITY.md) in the repository.

## Where do I go if something breaks?

See [Troubleshooting](troubleshooting.md) for the llama.cpp errors the load-failure hint recognizes, plus common install and build issues.
