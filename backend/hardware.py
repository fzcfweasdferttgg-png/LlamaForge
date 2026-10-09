"""Detect CPU/GPU and recommend CMake build flags + runtime defaults.

Windows + Linux: NVIDIA CUDA is the primary accelerator, CPU-only fallback.
macOS: Apple Silicon unified memory with a Metal build (no CUDA).
Platform branching lives in osplat; this module just asks it.
"""
import re, subprocess

import os
import osplat

def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

# Compute-capability -> CUDA arch number used by CMAKE_CUDA_ARCHITECTURES
def detect_gpus():
    if osplat.IS_MAC:
        return []                       # no NVIDIA on Apple Silicon; Metal instead
    out = _run(["nvidia-smi",
                "--query-gpu=index,name,memory.total,compute_cap",
                "--format=csv,noheader,nounits"])
    gpus = []
    for ln in out.strip().splitlines():
        f = [x.strip() for x in ln.split(",")]
        if len(f) >= 3:
            cc = f[3] if len(f) > 3 and f[3] and f[3] != "[N/A]" else ""
            gpus.append({"index": int(f[0]), "name": f[1],
                         "vram_mib": int(f[2]) if f[2].isdigit() else None,
                         "compute_cap": cc})
    return gpus

def detect_ram_gb():
    """Total system RAM in GB (SI, /1e9 to match GPU vendor GB units), 0 if unknown."""
    return round(osplat.total_ram_bytes() / 1e9, 1)


def _detect_cpu_windows():
    # wmic is removed on recent Windows 11; use PowerShell CIM.
    out = _run(["powershell", "-NoProfile", "-Command",
                "$c=Get-CimInstance Win32_Processor|Select-Object -First 1;"
                "'{0}|{1}|{2}' -f $c.Name,$c.NumberOfCores,$c.NumberOfLogicalProcessors"])
    info = {"name": "", "cores": None, "threads": None}
    parts = out.strip().split("|")
    if len(parts) == 3:
        info["name"] = parts[0].strip()
        info["cores"] = int(parts[1]) if parts[1].strip().isdigit() else None
        info["threads"] = int(parts[2]) if parts[2].strip().isdigit() else None
    # crude AVX-512 hint: recent AMD Zen4/5 and Intel server/HEDT
    n = info["name"].lower()
    info["avx512_hint"] = any(x in n for x in ["ryzen 7 9", "ryzen 9 9", "ryzen 7 7", "ryzen 9 7", "xeon", "threadripper"])
    return info

def detect_cpu():
    if osplat.IS_LINUX:
        c = osplat.linux_cpu()
        return {"name": c["name"], "cores": c["cores"], "threads": c["threads"],
                "avx512_hint": c["avx512"]}      # real flag, not a name heuristic
    if osplat.IS_MAC:
        c = osplat.mac_cpu()
        return {"name": c["name"], "cores": c["cores"], "threads": c["threads"],
                "avx512_hint": False}
    return _detect_cpu_windows()

def recommend(gpus=None, cpu=None):
    """Return {cmake_flags:{...}, notes:[...], runtime:{...}} for this machine."""
    gpus = detect_gpus() if gpus is None else gpus
    cpu  = detect_cpu()  if cpu  is None else cpu
    flags, notes = {}, []

    if osplat.IS_MAC:
        flags["GGML_METAL"] = "ON"
        notes.append("Apple Silicon detected - Metal build (uses unified memory as VRAM).")
        runtime = {"fit": "on", "flash-attn": "auto"}
        flags["GGML_NATIVE"] = "ON"
        return {"cmake_flags": flags, "notes": notes, "runtime": runtime,
                "gpus": gpus, "cpu": cpu}

    if gpus:
        archs = sorted({g["compute_cap"].replace(".", "") for g in gpus if g["compute_cap"]})
        flags["GGML_CUDA"] = "ON"
        if archs:
            flags["CMAKE_CUDA_ARCHITECTURES"] = ";".join(archs)
            notes.append(f"CUDA build for arch(s) {', '.join(archs)} ({len(gpus)} GPU(s)).")
        flags["GGML_CUDA_FA_ALL_QUANTS"] = "ON"   # quantized-KV flash attention
        notes.append("Enabled flash-attention for all quant KV combos.")
    else:
        notes.append("No NVIDIA GPU detected - configuring a CPU-only build.")

    flags["GGML_NATIVE"] = "ON"
    if cpu.get("avx512_hint"):
        for f in ("GGML_AVX512", "GGML_AVX512_VNNI", "GGML_AVX512_VBMI", "GGML_AVX512_BF16"):
            flags[f] = "ON"
        notes.append("Enabled AVX-512 (+VNNI/VBMI/BF16) for this CPU.")

    # memory sizing (ctx, GPU layers, split) is llama.cpp's --fit; never pinned here
    runtime = {"fit": "on", "flash-attn": "auto"}
    return {"cmake_flags": flags, "notes": notes, "runtime": runtime,
            "gpus": gpus, "cpu": cpu}

if __name__ == "__main__":
    import json
    print(json.dumps(recommend(), indent=2))


# ------------------------------------------- Linux DRM sysfs (vendor-tool-free)
# The kernel's own view of the GPUs: works for any compute API (Vulkan
# included) and needs no vendor tool. amdgpu exposes mem_info_vram_* and
# gpu_busy_percent; Intel i915/xe expose mem_info_total/used. Names are
# placeholders until enrich_names() refines them from llama-server.
_SYSFS_DRM = "/sys/class/drm"
_COMPUTE_VENDORS = {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}
_LIST_DEVICE = re.compile(r"^\s*([A-Za-z]+?)(\d+):\s*(.+?)\s*\(", re.M)
_LIST_MEM_TOTAL = re.compile(r"\((\d+)\s*MiB,")   # the engine's "(total MiB, ... free)"


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def sysfs_gpus(sysfs_root=_SYSFS_DRM):
    """[{index, name, total_mib, used_mib, free_mib, util, temp}] from DRM
    sysfs; [] when the kernel exposes nothing usable (or non-Linux). Cards
    come out in PCI bus order - the engine lists devices the same way."""
    def bdf(card):
        try:
            return os.path.basename(os.path.realpath(
                os.path.join(sysfs_root, card, "device")))
        except OSError:
            return card
    try:
        cards = sorted((n for n in os.listdir(sysfs_root)
                        if re.fullmatch(r"card\d+", n)),
                       key=lambda n: (bdf(n), int(n[4:])))
    except OSError:
        return []
    gpus = []
    for card in cards:
        dev = os.path.join(sysfs_root, card, "device")
        vendor = (_read(os.path.join(dev, "vendor")) or "").lower()
        if vendor and vendor not in _COMPUTE_VENDORS:
            continue
        total = (_read(os.path.join(dev, "mem_info_vram_total"))
                 or _read(os.path.join(dev, "mem_info_total")))
        used = (_read(os.path.join(dev, "mem_info_vram_used"))
                or _read(os.path.join(dev, "mem_info_used")))
        busy = _read(os.path.join(dev, "gpu_busy_percent"))
        temp = None
        try:
            hw = os.path.join(dev, "hwmon")
            for h in sorted(os.listdir(hw)):
                t = _read(os.path.join(hw, h, "temp1_input"))
                if t and t.isdigit():
                    temp = int(t) // 1000
                    break
        except OSError:
            pass
        if total is None and busy is None:
            continue          # a display-only device (e.g. an ASPEED BMC)
        total_mib = int(total) // (1024 * 1024) if (total or "").isdigit() else None
        used_mib = int(used) // (1024 * 1024) if (used or "").isdigit() else None
        gpus.append({"index": len(gpus),
                     "name": f"{_COMPUTE_VENDORS.get(vendor, 'GPU')} ({card})",
                     "total_mib": total_mib, "used_mib": used_mib,
                     "free_mib": (total_mib - used_mib)
                     if total_mib is not None and used_mib is not None else None,
                     "util": int(busy) if (busy or "").isdigit() else None,
                     "temp": temp})
    return gpus


def enrich_names(gpus, list_devices_text):
    """Replace sysfs placeholder names with the engine's own device names from
    `llama-server --list-devices`, matched by enumeration order (both list PCI
    order). The engine also prints "(total MiB, ... free)" per device: a slot
    whose totals disagree keeps its placeholder name rather than being
    mislabeled. Names then also match what slots.cuda_map compares against."""
    text = list_devices_text or ""
    names = [n.strip() for _p, _i, n in _LIST_DEVICE.findall(text)]
    totals = [int(t) for t in _LIST_MEM_TOTAL.findall(text)]
    for i, (g, n) in enumerate(zip(gpus, names)):
        if not n:
            continue
        t = g.get("total_mib")
        if i < len(totals) and t and abs(totals[i] - t) > max(64, t // 50):
            continue        # the engine's order disagrees with this card
        g["name"] = n
    return gpus
