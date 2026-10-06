---
title: Voice (Text to Speech)
section: guides
order: 12
---

# Voice

The **Voice** tab turns text into speech on your own GPU, optionally in a voice you recorded. LlamaForge does not do the speaking: llama.cpp ships a text-to-speech tool, `llama-tts`, and LlamaForge runs it with the [Qwen3-TTS](https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF) model, then gives you a UI and an OpenAI-style endpoint for it.

## Set up

Two things are needed, and the tab tells you which one is missing:

1. **`llama-tts`**, next to your `llama-server`. Official llama.cpp builds include it. A source build from **Build / Update** builds it as well; an older source build may only have the server, so rebuild once.
2. **The model.** **Get Qwen3-TTS** downloads the Q8_0 backbone and its audio projector from `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF` (about 2.1 GiB) into `<root>/tts/models/` (or `tts_dir` in [config.json](config.md)). It is kept apart from your chat models and never appears in the Models list.

## Speak

Type up to 2000 characters, pick a voice and a language (en, zh, de, it, pt, es, ja, ko, fr, ru) and press **Speak**. You get a waveform, a player and a WAV download.

Each request starts `llama-tts`, loads the model, speaks and exits, so no VRAM is held between requests. One request runs at a time. On an RTX 5080, a sentence takes about 3 to 4 seconds including the model load, a little faster than the audio plays back.

## Voices

A voice is a short reference clip of someone speaking. Qwen3-TTS speaks in the voice of the clip. **Record a clip** uses your microphone (up to 30 seconds), **Upload a clip** takes any audio file your browser can play. Either way, the browser converts it to 24 kHz mono WAV, trims the silence at both ends and asks for a name. Clips are stored as `<root>/tts/voices/<name>.wav`; 5 to 20 seconds of clear speech in a quiet room works best.

> [!IMPORTANT]
> Only clone voices you have permission to use: your own, or someone who agreed to it.

Without a voice, the model picks its own. Set `tts_default_voice` in [config.json](config.md) to use one of your voices whenever a request doesn't name one.

## Use it from code

The panel serves `POST /v1/audio/speech`, shaped like OpenAI's speech API:

```bash
curl http://127.0.0.1:8090/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"tts-1","input":"Hello from my own GPU.","voice":"alloy"}' \
  -o speech.wav
```

- `input` (required): the text, up to 2000 characters.
- `voice`: the name of one of your voices. Names you haven't saved, like OpenAI's `alloy`, fall back to the default voice, so OpenAI clients work unchanged.
- `language`: one of the codes above, default `en`.
- `response_format`: `pcm` returns raw 16-bit 24 kHz mono samples; anything else returns WAV. LlamaForge's backend has no audio codecs, so `mp3` or `opus` requests get WAV.
- `model` and `speed` are accepted and ignored.

Errors use the OpenAI shape (`{"error": {"message", "type"}}`): 400 for a bad request, 503 when speech isn't set up, 500 with the tail of the `llama-tts` log when it fails. When the router is reachable from your network (`router_host` is not `127.0.0.1`), the endpoint asks for the router's API key, as `Authorization: Bearer <key>` or `x-api-key`, like the other endpoints on the panel.
