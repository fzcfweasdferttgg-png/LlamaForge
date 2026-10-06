import assert from "node:assert/strict";
import test from "node:test";
import { encodeWav, wavPeaks, trimSilence, toBase64 } from "../web/js/wav.js";

test("encodeWav writes a 16-bit mono PCM header and clamps samples", () => {
  const w = encodeWav(new Float32Array([0, 1, -1, 2]), 24000);
  const v = new DataView(w.buffer);
  assert.equal(String.fromCharCode(...w.subarray(0, 4)), "RIFF");
  assert.equal(String.fromCharCode(...w.subarray(8, 12)), "WAVE");
  assert.equal(v.getUint16(22, true), 1);          // channels
  assert.equal(v.getUint32(24, true), 24000);      // rate
  assert.equal(v.getUint16(34, true), 16);         // bits
  assert.equal(v.getUint32(40, true), 8);          // data bytes
  assert.equal(w.length, 44 + 8);
  assert.deepEqual([0, 1, 2, 3].map(i => v.getInt16(44 + i * 2, true)), [0, 32767, -32768, 32767]);
});

test("wavPeaks finds the data chunk and buckets the loudness", () => {
  const w = encodeWav(new Float32Array([0, 0, 0.5, -1]), 8000);
  assert.deepEqual(wavPeaks(w, 2).map(x => Math.round(x * 100) / 100), [0, 1]);
  assert.deepEqual(wavPeaks(new Uint8Array(20), 3), [0, 0, 0]);
});

test("trimSilence keeps the voice plus a little padding", () => {
  const rate = 100, s = new Float32Array(300);
  s.fill(0.5, 100, 200);
  const t = trimSilence(s, rate);
  assert.equal(t.length, 100 + 2 * 15);
  assert.equal(trimSilence(new Float32Array(10), rate).length <= 10, true);
});

test("toBase64 handles clips larger than one chunk", () => {
  const b = new Uint8Array(70000).map((_, i) => i % 251);
  assert.equal(toBase64(b), Buffer.from(b).toString("base64"));
});
