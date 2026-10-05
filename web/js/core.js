// Primitives every view uses. Imports nothing, so it can never be part of a
// cycle. Everything that builds markup lives downstream of esc().
export const $ = (s, e = document) => e.querySelector(s);
export const $$ = (s, e = document) => [...e.querySelectorAll(s)];

export const esc = v => String(v == null ? "" : v)
  .replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Every value interpolated into the templates in this app goes through esc().
// This used to be written el["inner"+"HTML"] to keep the name away from a
// scanner; the escaping is the actual control, so the plain name stays.
export const setHTML = (el, h) => { if (el) el.innerHTML = h; };

export async function api(path, body) {
  const o = body ? {method:"POST", headers:{"Content-Type":"application/json"},
                    body:JSON.stringify(body)} : {};
  const r = await fetch(path, o);
  return r.json();
}

export function toast(m, c = "") {
  const t = $("#toast");
  if (!t) return;
  t.textContent = m;
  t.className = "show " + c;
  clearTimeout(t._t);
  t._t = setTimeout(() => t.className = "", 2600);
}

/** JS-driven flourishes (count-ups, row ignition, the theme reveal) are part of
 *  the Hearth skin and respect the OS reduced-motion setting, like its CSS. */
export function motionOK() {
  const root = document.documentElement;
  return !!(root && root.dataset && root.dataset.skin === "hearth")
    && !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
}

/* ---------- formatting ---------- */
export function fmtNum(n) {
  n = Number(n) || 0;
  return n >= 1e9 ? (n/1e9).toFixed(2)+"B" : n >= 1e6 ? (n/1e6).toFixed(2)+"M"
       : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : String(Math.round(n));
}
export function fmtDur(s) {
  s = Math.round(Number(s) || 0);
  const h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}
export function fmtAgo(ts) {
  if (!ts) return "never";
  const d = Date.now()/1000 - ts;
  return d < 60 ? "just now" : d < 3600 ? Math.floor(d/60)+"m ago"
       : d < 86400 ? Math.floor(d/3600)+"h ago" : Math.floor(d/86400)+"d ago";
}
export function agoText(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  return secs < 60 ? "just now" : secs < 3600 ? Math.floor(secs/60)+"m ago"
       : Math.floor(secs/3600)+"h ago";
}

/* Segmented bar used by the GPU cards and the download progress card. */
export function meter(u, tot) {
  const N = 28, on = Math.round(N * u / Math.max(tot, 1));
  let s = "";
  for (let i = 0; i < N; i++) s += `<div class="seg ${i<on?(i/N>.85?'hot':'on'):''}"></div>`;
  return s;
}
