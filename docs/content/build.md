---
title: Engines
section: guides
order: 3
---

# Engines

Download and pick the build each engine runs - llama.cpp, ik_llama and beellama - and switch what the router drives. No compiler, no checkout: everything arrives as a prebuilt Ubuntu binary.

## The three engines

The **Engines** tab shows one card per engine and a switcher for the one the router runs:

| Engine | Builds come from | Picked for this machine |
|---|---|---|
| **llama.cpp** | `github.com/ggml-org/llama.cpp` releases | variant by GPU: CUDA by driver (newest the driver supports; Blackwell needs 12.8+), then Vulkan, Metal, CPU. A **channel**: `nightly` (every upstream merge) or `stable` (curated releases). |
| **ik_llama.cpp** | `github.com/Thireus/ik_llama.cpp` releases (tags `main-bNNNN-<sha>`) | GPU backend (Vulkan on AMD/Intel, CUDA by driver on NVIDIA) times the CPU-ISA build (`avx2` up to `avx512_vnni_vbmi_bf16`) read from `/proc/cpuinfo`. |
| **beellama.cpp** | `github.com/Anbeeld/beellama.cpp` releases (`vX.Y.Z`; previews `preview-vX.Y.Z`) | GPU backend: Vulkan on AMD/Intel, CUDA by driver on NVIDIA, else CPU. ROCm/SYCL builds exist and can be picked by hand. |

All three are llama.cpp-family: they speak the same router API, so only one runs as the router at a time (the switcher, `POST /api/engine/switch`), and a model can still pin its own build.

## How an install works

The card's button reads **Install <tag>**, **Update to <tag>** or **Reinstall**; **Check now** refreshes the lookup (the result is cached 15 minutes). One install runs at a time, on a background thread, with live progress and a log:

1. **resolve** - the newest release with a build for this machine (a release is skipped while its assets are still uploading). For the forks the **release** dropdown lets you pick any recent tag instead.
2. **download** - resumable; a `.part` file picks up where it stopped.
3. **verify** - GitHub's own `sha256:` digest when the asset publishes one; otherwise a size check (said so in the log).
4. **extract** - every archive member is checked to land inside the install dir before a byte is written, so a hostile archive leaves nothing behind.
5. **test** - `llama-server --version` must exit 0 on this machine before anything is switched.
6. **activate** - `server_bin` (or `ik_llama_server_bin` / `beellama_server_bin`) points at the new binary and the router restarts on it.

Installs live side by side in `engines/<engine>/<tag>-<variant>/` with an `lf-install.json` manifest. The newest 3 are kept (never the active one, never one a model or profile pins) and each has a **Use** button to roll back - switching builds is just a change of which binary the router runs.

## Reference

| Concept | Source | Behavior |
|---|---|---|
| Release lookup | `backend/prebuilt.py` `resolve()` / `resolve_release()` | llama.cpp channels; fork repos by tag or newest (stable before preview). Cached 15 min (60 s on failure), `force=1` refreshes. |
| Variant pick | `choose()` / `choose_fork()` | llama.cpp: CUDA by driver then Vulkan/Metal/CPU. Forks: GPU backend by hardware; ik_llama also picks the CPU-ISA build from `/proc/cpuinfo` (`pick_isa`). A manual `variant` always wins. |
| Install / rollback | `Installer`, `list_installs()`, `prune_installs()` | Download -> verify -> extract -> test-run -> activate; keep 3; `POST /api/engine/prebuilt/use` rolls back to any listed install. |
| Engine switch | `POST /api/engine/switch` | Sets `active_engine` (`llamacpp` / `ikllama` / `beellama`) and restarts the router; refused when the binary has no router mode. |
| Per-model pins | `model_builds` (builds.py) | A model can run on its own build in a side process while the router runs another. |
| Binaries in config | `server_bin`, `ik_llama_server_bin`, `beellama_server_bin` | The binary each engine runs; empty leaves the engine disabled. See [config.json Reference](config.md). |

## Troubleshooting

"can't reach GitHub releases" means the lookup failed - the panel retries sooner than the normal cache. An install that says "this build does not run here" failed its test-run; the log under the card shows the `llama-server --version` output. Windows/macOS assets are ignored: this fork targets Ubuntu Server, so the cards list Ubuntu x64 builds only.

See also [Models & Tuning](models.md) for the flags a `llama-server` binary exposes.
