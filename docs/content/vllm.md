---
title: vLLM Backend
section: guides
order: 6
---

# vLLM Backend

Install and run [vLLM](https://github.com/vllm-project/vllm) inside WSL2 as a second inference engine alongside llama.cpp, for full-precision and quantized safetensors models.

## What it does

LlamaForge is a llama.cpp control panel first. vLLM is an optional second backend for models that ship as HuggingFace `safetensors` checkpoints rather than GGUF — full precision (FP16/BF16) or pre-quantized formats vLLM itself supports natively. `backend/vllm_hub.py` reads a model's `quantization_config` to tag its quant as **NVFP4**, **FP8**, **AWQ**, or **GPTQ** (falling back to dtype, e.g. FP16/BF16, when unquantized), matching the formats named in the project README's two-engine description.

**Windows/WSL2-only, and the exact gate.** `backend/routes.py` sets `VLLM_SUPPORTED = osplat.IS_WIN` at import time — a plain check of `sys.platform == "win32"`, nothing more (it does not itself probe whether WSL2 is installed; that's checked separately by `vllm_setup.status()`). `server.py`'s `_vllm_gate()` short-circuits every `/api/vllm/*` route when `VLLM_SUPPORTED` is false: `/api/vllm/setup` still answers `200` with `supported: false` (so the Setup tab can render "not available" instead of erroring), and every other vLLM route answers `400` with `"vLLM backend requires Windows + WSL2"`. On Linux and macOS the **vLLM Backend (WSL2)** card on Setup and Discover's safetensors mode are hidden in the UI entirely; llama.cpp (CUDA/CPU on Linux, Metal on Apple Silicon) is the only engine there.

**Installation.** `backend/vllm_setup.py` installs vLLM sudo-free inside your default (or chosen) WSL2 distro: it downloads the static `uv` binary to `~/.llamaforge/bin`, has `uv` create an isolated venv at `~/.llamaforge/vllm-venv` with Python 3.12, then `uv pip install vllm` into that venv. Nothing touches the system Python or `apt`. The install streams as a background job (`POST /api/vllm/setup/install`, tracked by `vllm_job.WslJob`) whose output the Setup tab polls live, the same pattern used for CMake builds.

**Running a model.** `backend/vllm_ctl.py`'s `Manager` spawns `<venv>/bin/vllm serve <model_ref> --host 0.0.0.0 --port <vllm_port>` inside WSL via `wsl.popen`, with stdout/stderr redirected to Windows-side log files. It polls vLLM's `/health` endpoint over WSL's localhost port-forwarding and flips the instance's state from `starting` → `loading` → `ready` (or `failed` after `READY_TIMEOUT = 600` seconds). On startup, `reconcile()` checks for a live `vllm serve` process in WSL (`pgrep -f 'vllm serve'`) and drops any stale instance record if none is found, so a LlamaForge restart never thinks a model is running when it isn't. **v1 caps at one running instance at a time** (`MAX_INSTANCES = 1`) — starting a second model while one is loaded returns an error asking you to stop the first. vLLM also has no hot reload: changing a loaded model's settings restarts the `vllm serve` process, and cold starts can take 1–5 minutes.

Usage for vLLM-served models is tracked the same way as llama.cpp models — see [Usage Stats](stats.md) for how `backend/stats.py` scrapes vLLM's own `/metrics` endpoint.

## How to use it

1. Confirm you're on Windows with WSL2 installed. If not, run `wsl --install -d Ubuntu` from an admin PowerShell and reboot; the **vLLM Backend (WSL2)** card on the **Setup** tab will only render if `VLLM_SUPPORTED` is true (Windows) in the first place.
2. Open **Setup** and check the vLLM card: WSL2 presence, chosen distro, GPU passthrough (`nvidia-smi -L` run inside WSL), and whether vLLM is already installed.
3. Click **Install vLLM (uv, no sudo)**. This downloads several GB (uv + a standalone Python + vLLM's dependencies); the install log under the card shows progress.
4. Once installed, safetensors models discovered or downloaded via **Discover** are tagged for the vLLM engine and appear in the shared Models list alongside your GGUF models.
5. Load a vLLM model from the Models tab like any other. Because only one instance runs at a time, loading a second vLLM model requires unloading the first.
6. Check the **Build / Update** tab periodically — its vLLM card compares your installed version against the latest PyPI release and offers an **Update vLLM to <version>** button when a newer one exists.

## Reference

| Concept | Source | Behavior |
|---|---|---|
| Platform gate | `routes.py: VLLM_SUPPORTED = osplat.IS_WIN`, enforced by `server.py: _vllm_gate()` | `True` only when `sys.platform == "win32"`; every `/api/vllm/*` route 400s (except `/api/vllm/setup`, which 200s with `supported: false`) when it's `False`. |
| Supported formats | `vllm_hub.py` (quant tags), README | Full precision FP16/BF16, plus pre-quantized **AWQ**, **GPTQ**, **FP8**, **NVFP4** safetensors checkpoints. |
| Install target | `vllm_setup.install_script()` | `uv venv ~/.llamaforge/vllm-venv --python 3.12` then `uv pip install vllm`; no `sudo`, no system Python touched. |
| Serve command | `vllm_ctl.build_serve_script()` | `<venv>/bin/vllm serve <model_ref> --host 0.0.0.0 --port <port> <flags>`, run inside WSL via `wsl.popen`; the script is built by `vllm_ctl.build_serve_script()`. |
| Instance cap | `vllm_ctl.py: MAX_INSTANCES = 1` | Only one vLLM model can run at a time in v1; starting a second fails until the first is stopped. |
| Readiness | `vllm_ctl.Manager._await_ready()` | Polls `/health` every `HEALTH_INTERVAL = 3`s; `READY_TIMEOUT = 600`s before marking `failed`. |
| Reconciliation | `vllm_ctl.Manager.reconcile()` | On startup, `pgrep -f 'vllm serve'` in WSL; drops the tracked instance if no matching process exists. |
| Version check | `vllm_setup.latest_pypi_version()` | Queries PyPI's JSON API, cached 1 hour (`_PYPI_TTL`), bypassed by the Build tab's **Check GitHub now** button. |

## Troubleshooting

If the vLLM card doesn't appear on the Setup tab at all, you're not on Windows — `VLLM_SUPPORTED` is a platform check, not something you can toggle. If it appears but shows "WSL2 is required," install WSL2 (`wsl --install -d Ubuntu`, admin PowerShell, reboot) and reload the tab. If GPU passthrough shows "NOT DETECTED," verify your NVIDIA driver supports WSL2 GPU passthrough and that `nvidia-smi -L` succeeds when run from inside the WSL distro directly. If a model gets stuck in `loading` past a few minutes, open the **vLLM Log (WSL stdout/stderr)** section at the bottom of the Models tab — cold starts legitimately take 1–5 minutes, but a `failed` state after `READY_TIMEOUT` usually means the model didn't fit in VRAM or `vllm serve` errored; the Windows-side log files `logs/vllm.out.log` / `logs/vllm.err.log` (under the LlamaForge folder) have the underlying vLLM output. If you can't start a second model, stop the currently loaded one first — v1 only supports one running instance.

See also [Discover](discover.md) for finding safetensors models rated against your VRAM, and [Usage Stats](stats.md) for how vLLM's token metrics are tracked alongside llama.cpp's.
