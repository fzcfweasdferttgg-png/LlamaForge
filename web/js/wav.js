// 16-bit mono WAV encoding for voice clips. Imports nothing (no DOM), so the
// node tests can load it directly. The Voice tab records or decodes any clip
// the browser understands, downmixes and resamples it with Web Audio, and
// sends the panel a plain WAV: the backend never has to know about webm/opus.

/** Float samples in [-1, 1] -> a WAV file as a Uint8Array. */
export function encodeWav(samples, rate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);   // PCM
  v.setUint16(22, 1, true); v.setUint32(24, rate, true);                   // mono
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i] || 0));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Uint8Array(buf);
}

/** Peak level per bucket of a 16-bit PCM WAV, 0..1, for drawing a waveform.
 *  Walks the chunk list to find "data", so extra header chunks are fine. */
export function wavPeaks(bytes, buckets) {
  const v = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let o = 12, start = -1, len = 0;
  while (o + 8 <= v.byteLength) {
    const id = String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
    const size = v.getUint32(o + 4, true);
    if (id === "data") { start = o + 8; len = Math.min(size, v.byteLength - start); break; }
    o += 8 + size + (size & 1);
  }
  const out = new Array(buckets).fill(0);
  if (start < 0) return out;
  const n = Math.floor(len / 2), per = Math.max(1, Math.floor(n / buckets));
  for (let b = 0; b < buckets; b++) {
    let peak = 0;
    for (let i = b * per; i < Math.min(n, (b + 1) * per); i++)
      peak = Math.max(peak, Math.abs(v.getInt16(start + i * 2, true)));
    out[b] = peak / 32768;
  }
  return out;
}

/** Drop leading/trailing near-silence so a clip starts where the voice does. */
export function trimSilence(samples, rate, threshold = 0.02) {
  const pad = Math.round(rate * 0.15);
  let a = 0, b = samples.length - 1;
  while (a < b && Math.abs(samples[a]) < threshold) a++;
  while (b > a && Math.abs(samples[b]) < threshold) b--;
  return samples.subarray(Math.max(0, a - pad), Math.min(samples.length, b + pad + 1));
}

/** Uint8Array -> base64 without blowing the call stack on a 2 MB clip. */
export function toBase64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
