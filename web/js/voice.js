// Voice tab: text-to-speech through llama.cpp's llama-tts (backend/tts.py).
// Set up (binary + Qwen3-TTS model), speak, keep reference voices (recorded
// here or uploaded) and copy the OpenAI-style /v1/audio/speech snippet.
//
// Clips are decoded and resampled to 24 kHz mono in the browser (Web Audio
// does every format the browser can play), then sent as a plain WAV: the
// stdlib backend never needs a codec. Every value interpolated goes through esc().
import { $, $$, api, esc, setHTML, toast, askYes, askText } from "./core.js";
import { encodeWav, wavPeaks, trimSilence, toBase64 } from "./wav.js";

const RATE = 24000;             // what Qwen3-TTS writes and what clips are stored at
const MAX_REC_S = 30;
const V = { st: null, poll: null, rec: null, url: "", lang: "en", voice: "" };

const gib = b => (b / 1073741824).toFixed(1) + " GiB";
const secs = s => s < 60 ? s.toFixed(1) + " s" : Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";

export async function loadVoice() {
  let st;
  try { st = await api("/api/tts/status"); } catch (e) { st = null; }
  const host = $("#view-voice");
  if (!host) return;
  if (!st || st.error) {
    setHTML(host, `<div class="card"><div class="msg err">Could not read the speech status.</div></div>`);
    return;
  }
  V.st = st;
  setHTML(host, setupCard(st) + speakCard(st) + voicesCard(st) + connectCard(st));
  wire(host, st);
  watchDownload(st);
}

/* ---------- cards ---------- */
function setupCard(st) {
  const d = st.download || {};
  const fetching = d.running || d.phase === "downloading";
  if (st.ready && !fetching) {
    return `<div class="card"><h3>Speech</h3>
      <div class="note">Text to speech runs on your GPU through llama.cpp&rsquo;s own
        <b>llama-tts</b> with Qwen3-TTS. Each request loads the model, speaks and exits,
        so nothing sits in VRAM between requests.</div>
      <div class="kv"><span class="k">engine</span><span class="v">${esc(st.binary)}</span>
        <span class="k">model</span><span class="v">${esc(st.model)}</span></div></div>`;
  }
  const noBin = st.missing.includes("binary");
  const noModel = st.missing.includes("model");
  const bar = fetching
    ? `<div class="msg work" id="tts-dl">${esc(dlText(d))}</div>`
    : d.phase === "failed" ? `<div class="msg err">Download failed: ${esc(d.error)}</div>` : "";
  return `<div class="card"><h3>Set up speech</h3>
    <div class="note">LlamaForge speaks with llama.cpp&rsquo;s <b>llama-tts</b> tool and the
      Qwen3-TTS model. Nothing here runs until both are present.</div>
    ${noBin ? `<div class="msg warn">llama-tts was not found next to llama-server. Official
      llama.cpp builds include it; for a source build, rebuild from the Build tab
      (or build the <b>llama-tts</b> target).</div>` : ""}
    ${noModel ? `<div class="note">Model: <b>${esc(st.repo)}</b>, Q8_0 backbone plus its audio
      projector, ${esc(gib(st.repo_bytes))}, saved to ${esc(st.models_dir)}.</div>
      <div class="actions"><button type="button" class="primary" id="tts-get"
        ${fetching ? "disabled" : ""}>Get Qwen3-TTS (${esc(gib(st.repo_bytes))})</button></div>` : ""}
    ${bar}</div>`;
}

function speakCard(st) {
  const opts = [`<option value="">${st.default_voice ? "Default (" + esc(st.default_voice) + ")" : "Default voice"}</option>`]
    .concat(st.voices.map(v => `<option value="${esc(v)}"${v === V.voice ? " selected" : ""}>${esc(v)}</option>`));
  const langs = st.langs.map(l => `<option${l === V.lang ? " selected" : ""}>${esc(l)}</option>`);
  return `<div class="card"><h3>Speak</h3>
    <textarea id="tts-text" maxlength="${esc(st.max_chars)}"
      placeholder="Type what you want said."></textarea>
    <div class="formrow">
      <div class="f"><span class="lbl">Voice</span><select id="tts-voice">${opts.join("")}</select></div>
      <div class="f"><span class="lbl">Language</span><select id="tts-lang">${langs.join("")}</select></div>
      <div class="f grow"><span class="lbl">&nbsp;</span><span class="note" id="tts-count">0 / ${esc(st.max_chars)}</span></div>
    </div>
    <div class="actions"><button type="button" class="primary" id="tts-speak"
      ${st.ready ? "" : "disabled"}>Speak</button></div>
    <div id="tts-out"></div></div>`;
}

function voicesCard(st) {
  const rows = st.voices.length
    ? st.voices.map(v => `<div class="kv"><span class="k">${esc(v)}</span><span class="v">
        <button type="button" class="ghost" data-del-voice="${esc(v)}">Remove</button></span></div>`).join("")
    : `<div class="note">No voices yet. Without one, the model picks its own voice.</div>`;
  return `<div class="card"><h3>Voices</h3>
    <div class="note">A voice is a short reference clip (5 to 20 seconds of clear speech
      works best). Qwen3-TTS speaks in the voice of the clip. Only clone voices you have
      permission to use: your own, or someone who said yes.</div>
    ${rows}
    <div class="actions">
      <button type="button" class="ghost" id="tts-rec">Record a clip</button>
      <button type="button" class="ghost" id="tts-up-btn">Upload a clip</button>
      <input type="file" id="tts-up" accept="audio/*" hidden>
    </div>
    <div id="tts-vmsg"></div>
    <div class="note">Stored in ${esc(st.voices_dir)}</div></div>`;
}

function connectCard(st) {
  const auth = st.needs_key ? `  -H "Authorization: Bearer $LLAMAFORGE_KEY" \\\n` : "";
  const curl = `curl ${location.origin}/v1/audio/speech \\
${auth}  -H "Content-Type: application/json" \\
  -d '{"model":"tts-1","input":"Hello from my own GPU.","voice":"alloy"}' \\
  -o speech.wav`;
  return `<div class="card"><h3>Use it from code</h3>
    <div class="note">The panel serves an OpenAI-style <b>POST /v1/audio/speech</b>.
      Audio comes back as WAV (or raw 24 kHz PCM with <code>"response_format":"pcm"</code>).
      Voice names you haven&rsquo;t saved, like OpenAI&rsquo;s "alloy", use the default voice.
      ${st.needs_key ? "Your router is reachable from the network, so this endpoint asks for the router&rsquo;s API key too." : ""}</div>
    <div class="slabel">curl</div><div class="snip"><button type="button" class="qbtn scopy"
      id="tts-copy" aria-label="Copy curl">Copy</button>${esc(curl)}</div></div>`;
}

/* ---------- behaviour ---------- */
function wire(host, st) {
  const text = $("#tts-text", host), count = $("#tts-count", host);
  text.oninput = () => { count.textContent = `${text.value.length} / ${st.max_chars}`; };
  $("#tts-voice", host).onchange = e => { V.voice = e.target.value; };
  $("#tts-lang", host).onchange = e => { V.lang = e.target.value; };
  $("#tts-speak", host).onclick = speak;
  const get = $("#tts-get", host);
  if (get) get.onclick = async () => {
    get.disabled = true;
    const r = await api("/api/tts/get", {});
    if (r.error) { toast(r.error, "err"); get.disabled = false; return; }
    loadVoice();
  };
  $("#tts-rec", host).onclick = toggleRecord;
  $("#tts-up-btn", host).onclick = () => $("#tts-up", host).click();
  $("#tts-up", host).onchange = e => { const f = e.target.files[0]; e.target.value = ""; if (f) addClip(f); };
  for (const b of $$("[data-del-voice]", host)) {
    b.onclick = async () => {
      const name = b.dataset.delVoice;
      if (!await askYes(`Remove the voice "${name}"? The clip file is deleted.`,
                        { title: "Remove voice", ok: "Remove", danger: true })) return;
      const r = await api("/api/tts/voice/delete", { name });
      if (r.error) toast(r.error, "err"); else { if (V.voice === name) V.voice = ""; loadVoice(); }
    };
  }
  const copy = $("#tts-copy", host);
  copy.onclick = () => navigator.clipboard.writeText(copy.nextSibling.textContent)
    .then(() => toast("Copied to clipboard", "ok"));
}

function dlText(d) {
  const pct = d.total ? Math.floor(100 * d.downloaded / d.total) : 0;
  return `Downloading ${d.file || "..."} (${(d.done_files || 0) + 1} of ${d.total_files || 2}) ${pct}%`;
}

function watchDownload(st) {
  clearTimeout(V.poll);
  const d = st.download || {};
  if (!d.running) return;
  V.poll = setTimeout(async () => {
    if (!$("#view-voice")?.offsetParent) return;      // tab hidden: loadVoice re-arms it
    let s;
    try { s = await api("/api/tts/status"); } catch (e) { return; }
    const el = $("#tts-dl");
    if (s.download && s.download.running && el) { el.textContent = dlText(s.download); watchDownload(s); }
    else loadVoice();
  }, 1000);
}

async function speak() {
  const btn = $("#tts-speak"), out = $("#tts-out");
  const input = $("#tts-text").value.trim();
  if (!input) { toast("Type something to say", "err"); return; }
  btn.disabled = true;
  const t0 = performance.now();
  setHTML(out, `<div class="msg work">Speaking... the model loads, talks, then exits.</div>`);
  try {
    const r = await fetch("/api/tts/speak", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input, voice: V.voice, language: V.lang }),
    });
    if (!r.ok) {
      let msg = r.status + "";
      try { msg = (await r.json()).error || msg; } catch (e) {}
      setHTML(out, `<div class="msg err" style="white-space:pre-wrap">${esc(msg)}</div>`);
      return;
    }
    const bytes = new Uint8Array(await r.arrayBuffer());
    const took = (performance.now() - t0) / 1000;
    const dur = Math.max(0, bytes.length - 44) / 2 / RATE;
    if (V.url) URL.revokeObjectURL(V.url);
    V.url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
    setHTML(out, `${wave(wavPeaks(bytes, 96))}
      <audio controls src="${esc(V.url)}" style="width:100%"></audio>
      <div class="note">${esc(secs(dur))} of speech in ${esc(secs(took))}.
        <a href="${esc(V.url)}" download="speech.wav">Download WAV</a></div>`);
    const a = $("audio", out);
    if (a) a.play().catch(() => {});
  } catch (e) {
    setHTML(out, `<div class="msg err">${esc(e.message || e)}</div>`);
  } finally {
    btn.disabled = false;
  }
}

/** Peaks -> a small mirrored bar waveform in the accent colour. */
function wave(peaks) {
  const w = 4, h = 48;
  // Scale to the clip's own peak: Qwen3-TTS speaks around -20 dBFS.
  const top = Math.max(...peaks, 1e-6);
  peaks = peaks.map(p => p / top);
  const bars = peaks.map((p, i) => {
    const bh = Math.max(1.5, p * h);
    return `<rect x="${i * w}" y="${((h - bh) / 2).toFixed(1)}" width="${w - 1.5}" height="${bh.toFixed(1)}" rx="1"/>`;
  }).join("");
  return `<svg class="tts-wave" viewBox="0 0 ${peaks.length * w} ${h}" preserveAspectRatio="none"
    style="width:100%;height:48px;fill:var(--amber);opacity:.85" aria-hidden="true">${bars}</svg>`;
}

/* ---------- voices: record or upload, then store as 24 kHz WAV ---------- */
function vmsg(cls, text) { setHTML($("#tts-vmsg"), text ? `<div class="msg ${cls}">${esc(text)}</div>` : ""); }

async function toggleRecord() {
  const btn = $("#tts-rec");
  if (V.rec) { V.rec.stop(); return; }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { vmsg("err", "No microphone access: " + (e.message || e)); return; }
  const chunks = [];
  const rec = new MediaRecorder(stream);
  V.rec = rec;
  rec.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
  const started = Date.now();
  const tick = setInterval(() => {
    const s = (Date.now() - started) / 1000;
    vmsg("work", `Recording ${s.toFixed(0)} s of ${MAX_REC_S}. Read a few natural sentences, then press Stop.`);
    if (s >= MAX_REC_S) rec.stop();
  }, 250);
  rec.onstop = () => {
    clearInterval(tick);
    stream.getTracks().forEach(t => t.stop());
    V.rec = null;
    btn.textContent = "Record a clip";
    addClip(new Blob(chunks, { type: rec.mimeType }));
  };
  rec.start();
  btn.textContent = "Stop";
}

async function addClip(blob) {
  vmsg("work", "Preparing the clip...");
  let samples;
  try {
    const ctx = new AudioContext();
    const src = await ctx.decodeAudioData(await blob.arrayBuffer());
    ctx.close();
    // A mono destination downmixes; the offline context resamples to 24 kHz.
    const off = new OfflineAudioContext(1, Math.ceil(src.duration * RATE), RATE);
    const node = off.createBufferSource();
    node.buffer = src; node.connect(off.destination); node.start();
    samples = trimSilence((await off.startRendering()).getChannelData(0), RATE);
  } catch (e) {
    vmsg("err", "Could not read that audio: " + (e.message || e)); return;
  }
  const len = samples.length / RATE;
  if (len < 1) { vmsg("err", "The clip is under a second after trimming silence."); return; }
  if (len > 60) samples = samples.subarray(0, 60 * RATE);
  vmsg("", "");
  const name = await askText("Voice name", { title: "Save voice",
    message: `${secs(Math.min(len, 60))} clip. Letters, digits, spaces, dots and dashes.`,
    placeholder: "e.g. my-voice" });
  if (!name) return;
  const r = await api("/api/tts/voice", { name, wav_b64: toBase64(encodeWav(samples, RATE)) });
  if (r.error) { vmsg("err", r.error); return; }
  V.voice = name;
  toast(`Voice "${name}" saved`, "ok");
  loadVoice();
}
