// i18n completeness: every UI string the code uses must exist in every locale,
// every locale entry must be used, and translations keep their {param}s.
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const jsDir = new URL("../web/js/", import.meta.url);
const html = await readFile(new URL("../web/index.html", import.meta.url), "utf8");

const used = new Set();
for (const name of await readdir(jsDir)) {
  if (!name.endsWith(".js") || name.startsWith("loc-") || name === "i18n.js") continue;
  const text = await readFile(new URL(name, jsDir), "utf8");
  for (const m of text.matchAll(/(?<![\w$])t\("((?:[^"\\]|\\.)*)"/g)) {
    used.add(JSON.parse(`"${m[1]}"`));
  }
}
for (const m of html.matchAll(/data-i18n="([^"]+)"/g)) used.add(m[1]);
for (const m of html.matchAll(/data-i18n-attr="([^"]+)"/g)) {
  for (const pair of m[1].split(/,(?=(?:placeholder|title|aria-label|data-lede):)/)) {
    const i = pair.indexOf(":");
    if (i > 0) used.add(pair.slice(i + 1).trim());
  }
}

const LANGS = ["ru", "ja", "ko", "zh"];
const dicts = {};
for (const lang of LANGS) {
  dicts[lang] = (await import(`../web/js/loc-${lang}.js`)).default;
}

test("every UI string is translated in every language", () => {
  const missing = {};
  for (const lang of LANGS) {
    const miss = [...used].filter(k => !(k in dicts[lang]));
    if (miss.length) missing[lang] = miss.slice(0, 10);
  }
  assert.deepEqual(missing, {});
});

test("locale dictionaries carry no stale keys", () => {
  const stale = {};
  for (const lang of LANGS) {
    const extra = Object.keys(dicts[lang]).filter(k => !used.has(k));
    if (extra.length) stale[lang] = extra.slice(0, 10);
  }
  assert.deepEqual(stale, {});
});

test("translations keep the {param} placeholders of their key", () => {
  const ph = s => [...s.matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort().join(",");
  const bad = [];
  for (const lang of LANGS) {
    for (const [k, v] of Object.entries(dicts[lang])) {
      if (ph(k) !== ph(v)) bad.push(`${lang}: ${k}`);
    }
  }
  assert.deepEqual(bad, []);
});
