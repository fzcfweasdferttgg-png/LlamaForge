// UI localization. The key is the English text itself, so the untranslated
// fallback is free: t(key) returns the active language's string, or the key
// (= English) when a translation is missing. {param} placeholders are filled
// from the second argument.
// Language choice: the browser's own pick (localStorage lf_lang) over the
// panel default (config web_lang) over the browser's languages. The picker in
// the topbar drives setLang(); re-rendering is the caller's business through
// the callback passed to initLang().
// Imports nothing on purpose: core.js imports t() from here, so a cycle would
// make both modules' load order matter.

export const LANGS = { en: "English", ru: "Русский", ja: "日本語", ko: "한국어", zh: "中文" };

let dict = {};
let current = "en";
let onChange = null;

export const currentLang = () => current;

export function t(key, params) {
  let s = dict[key] != null ? dict[key] : key;
  if (params) for (const [k, v] of Object.entries(params))
    s = s.split("{" + k + "}").join(String(v));
  return s;
}

async function loadDict(lang) {
  if (lang === "en" || !LANGS[lang]) return {};
  try {
    return (await import(`./loc-${lang}.js`)).default;
  } catch (e) {
    return {};
  }
}

/** Load the dictionary and settle on a language. `apply` re-renders the UI. */
export async function initLang(apply) {
  onChange = apply;
  let lang = "";
  try { lang = localStorage.getItem("lf_lang") || ""; } catch (e) {}
  if (!LANGS[lang]) lang = document.documentElement.dataset.webLang || "";
  if (!LANGS[lang]) lang = String(navigator.language || "en").slice(0, 2);
  await setLang(lang, true);
}

export async function setLang(lang, silent) {
  if (!LANGS[lang]) lang = "en";
  current = lang;
  dict = await loadDict(lang);
  document.documentElement.lang = lang === "zh" ? "zh-CN" : lang;
  if (!silent) {
    try { localStorage.setItem("lf_lang", lang); } catch (e) {}
    if (onChange) onChange();
  }
}

/** The topbar language picker. Lives beside the engine badge. */
export function initLangPicker() {
  const host = document.querySelector("#lang-pick-host");
  if (!host) return;
  const sel = document.createElement("select");
  sel.id = "lang-pick";
  sel.setAttribute("aria-label", "Language");
  for (const [code, label] of Object.entries(LANGS)) {
    const o = document.createElement("option");
    o.value = code;
    o.textContent = label;
    sel.appendChild(o);
  }
  sel.value = current;
  sel.onchange = () => setLang(sel.value);
  host.appendChild(sel);
}

/** Translate the static parts of index.html: elements carrying data-i18n
 *  (textContent) and data-i18n-attr ("placeholder:key,title:key"). */
export function translateStatic(root = document) {
  for (const el of root.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.dataset.i18n);
  }
  // entries are "attr:key" joined with commas; keys may contain commas, so
  // split only where a comma is followed by a known attribute name
  for (const el of root.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of el.dataset.i18nAttr.split(/,(?=(?:placeholder|title|aria-label|data-lede):)/)) {
      const i = pair.indexOf(":");
      if (i > 0) el.setAttribute(pair.slice(0, i).trim(), t(pair.slice(i + 1).trim()));
    }
  }
  for (const el of root.querySelectorAll("[data-i18n-lede]")) {
    el.dataset.lede = t(el.dataset.i18nLede);
  }
}
