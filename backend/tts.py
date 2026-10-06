"""Text-to-speech through llama.cpp's own `llama-tts` tool.

llama-server has no speech endpoint, but upstream llama.cpp ships `llama-tts`
(tools/tts), a one-shot binary that runs a TTS backbone GGUF plus an audio
mmproj (Qwen3-TTS today) and writes a WAV. This module wraps it as an
OpenAI-style POST /v1/audio/speech on the panel, next to the chat shims.

    <tts_dir>/models/   one backbone *.gguf + its mmproj-*.gguf
    <tts_dir>/voices/   reference clips (wav/mp3/flac); the file name is the
                        voice name. Qwen3-TTS clones the clip's voice.

Each request runs llama-tts as a short-lived process (load, speak, exit): no
VRAM is held between requests, at the cost of a couple of seconds of model
load. One job at a time, so two requests never stack two copies on the GPU.

Output is always WAV (or raw PCM): encoding mp3/opus would need a codec, and
this backend is stdlib-only. Every common player and browser plays WAV.

Pure Python stdlib.
"""
import io, os, re, shutil, subprocess, sys, tempfile, threading, wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO = "ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF"
REPO_FILES = ["Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf",
              "mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf"]
REPO_BYTES = 1847874400 + 446422912

BIN_NAMES = ("llama-tts.exe", "llama-tts")
AUDIO_EXT = (".wav", ".mp3", ".flac")
LANGS = ("en", "zh", "de", "it", "pt", "es", "ja", "ko", "fr", "ru")
MAX_CHARS = 2000
# Qwen3-TTS speaks at 12 frames per second of audio; llama-tts stops at end of
# speech, -n is only the ceiling. ~15 characters per spoken second, doubled for
# slow voices and pauses, never under llama-tts' own default of 512.
FRAMES_PER_CHAR = 1.6
MIN_FRAMES, MAX_FRAMES = 512, 4096
TIMEOUT = 600
_VOICE_RE = re.compile(r"^[\w][\w .-]{0,63}$")
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class TtsError(Exception):
    pass


# ------------------------------------------------------------------- where
def tts_dir(cfg):
    return (cfg or {}).get("tts_dir") or os.path.join(ROOT, "tts")


def models_dir(cfg):
    return os.path.join(tts_dir(cfg), "models")


def voices_dir(cfg):
    return os.path.join(tts_dir(cfg), "voices")


def find_bin(server_bin):
    """llama-tts beside the active llama-server: a source build and an
    official release both put every tool in the same bin directory."""
    if not server_bin:
        return None
    d = os.path.dirname(server_bin)
    for name in BIN_NAMES:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def find_model(folder):
    """(backbone, mmproj) in folder, either None when absent."""
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".gguf"))
    except OSError:
        return None, None
    mm = [n for n in names if n.lower().startswith("mmproj")]
    back = [n for n in names if not n.lower().startswith("mmproj")]
    pick = lambda xs: os.path.join(folder, xs[0]) if xs else None
    return pick(back), pick(mm)


def list_voices(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(os.path.splitext(n)[0] for n in names
                  if os.path.splitext(n)[1].lower() in AUDIO_EXT
                  and os.path.isfile(os.path.join(folder, n)))


def resolve_voice(folder, name, default=None):
    """Path of the named voice clip, else of `default`, else None.

    An unknown name is not an error: OpenAI clients send "alloy" and friends
    by default, and should still get speech. Names are matched against the
    folder listing, never joined onto it, so "../x" cannot escape."""
    for want in (name, default):
        if not isinstance(want, str) or not _VOICE_RE.match(want):
            continue
        for n in os.listdir(folder) if os.path.isdir(folder) else []:
            stem, ext = os.path.splitext(n)
            if stem == want and ext.lower() in AUDIO_EXT:
                return os.path.join(folder, n)
    return None


MIN_CLIP_S, MAX_CLIP_S = 1.0, 60.0


def save_voice(folder, name, data):
    """Store a reference clip as <folder>/<name>.wav. The panel records and
    re-encodes in the browser, so only a real WAV is accepted here; any other
    format already saved under that name is replaced."""
    if not isinstance(name, str) or not _VOICE_RE.match(name) or name != name.strip():
        raise ValueError("voice name: letters, digits, space, dot, dash; up to 64")
    try:
        with wave.open(io.BytesIO(data)) as w:
            secs = w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        raise ValueError("not a WAV file")
    if not MIN_CLIP_S <= secs <= MAX_CLIP_S:
        raise ValueError(f"clip is {secs:.1f}s; use {MIN_CLIP_S:.0f} to {MAX_CLIP_S:.0f} seconds")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, name + ".wav")
    tmp = dest + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)
    for ext in AUDIO_EXT:
        if ext != ".wav":
            try: os.remove(os.path.join(folder, name + ext))
            except OSError: pass
    return dest


def delete_voice(folder, name):
    path = resolve_voice(folder, name)
    if not path:
        return False
    os.remove(path)
    return True


# ----------------------------------------------------------------- request
def parse_request(body):
    """Validate an OpenAI /v1/audio/speech body -> {text, voice, lang, format}.
    `model` is accepted and ignored (there is one TTS model); `speed` too."""
    text = (body or {}).get("input")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("input must be non-empty text")
    text = text.strip()
    if len(text) > MAX_CHARS:
        raise ValueError(f"input is {len(text)} characters; the limit is {MAX_CHARS}")
    lang = str(body.get("language") or "en").strip().lower()
    if lang not in LANGS:
        raise ValueError(f"language must be one of {', '.join(LANGS)}")
    fmt = "pcm" if body.get("response_format") == "pcm" else "wav"
    voice = body.get("voice") if isinstance(body.get("voice"), str) else ""
    return {"text": text, "voice": voice, "lang": lang, "format": fmt}


def build_cmd(binary, model, mmproj, text, out, lang="en", speaker=None):
    frames = int(len(text) * FRAMES_PER_CHAR) + 120
    frames = max(MIN_FRAMES, min(MAX_FRAMES, frames))
    cmd = [binary, "-m", model, "--mmproj", mmproj, "-p", text, "-o", out,
           "--tts-lang", lang, "-n", str(frames)]
    if speaker:
        cmd += ["--tts-speaker-file", speaker]
    return cmd


# --------------------------------------------------------------------- wav
def wav_to_pcm(data):
    with wave.open(io.BytesIO(data)) as w:
        return w.readframes(w.getnframes())


def wav_seconds(data):
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except (wave.Error, EOFError):
        return 0.0


# ------------------------------------------------------------------ status
def status(cfg):
    binary = find_bin(cfg.get("server_bin", ""))
    model, mmproj = find_model(models_dir(cfg))
    missing = []
    if not binary:
        missing.append("binary")
    if not (model and mmproj):
        missing.append("model")
    return {"ready": not missing, "missing": missing, "binary": binary or "",
            "model": model or "", "mmproj": mmproj or "",
            "voices": list_voices(voices_dir(cfg)),
            "default_voice": cfg.get("tts_default_voice", ""),
            "voices_dir": voices_dir(cfg), "models_dir": models_dir(cfg),
            "repo": REPO, "repo_files": REPO_FILES, "repo_bytes": REPO_BYTES,
            "langs": list(LANGS), "max_chars": MAX_CHARS}


def _tail(text, lines=12):
    keep = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(keep[-lines:])


class Speaker:
    """Runs llama-tts, one job at a time. `run` is injectable for tests."""

    def __init__(self, run=subprocess.run):
        self._run = run
        self.lock = threading.Lock()
        self.busy = False
        self.last_tmp = None

    def speak(self, cfg, req):
        st = status(cfg)
        if not st["ready"]:
            raise TtsError("text-to-speech is not set up: missing " + " and ".join(st["missing"]))
        speaker = resolve_voice(voices_dir(cfg), req["voice"], cfg.get("tts_default_voice"))
        with self.lock:
            self.busy = True
            tmp = tempfile.mkdtemp(prefix="lf-tts-")
            self.last_tmp = tmp
            try:
                out = os.path.join(tmp, "speech.wav")
                cmd = build_cmd(st["binary"], st["model"], st["mmproj"], req["text"],
                                out, req["lang"], speaker)
                try:
                    r = self._run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT, creationflags=_NO_WINDOW)
                except subprocess.TimeoutExpired:
                    raise TtsError(f"llama-tts took longer than {TIMEOUT}s")
                except OSError as e:
                    raise TtsError(f"could not start llama-tts: {e}")
                if r.returncode != 0 or not os.path.isfile(out):
                    log = _tail((r.stderr or "") + "\n" + (r.stdout or ""))
                    raise TtsError(f"llama-tts exited with {r.returncode}\n{log}".rstrip())
                with open(out, "rb") as f:
                    data = f.read()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
                self.busy = False
        return wav_to_pcm(data) if req["format"] == "pcm" else data
