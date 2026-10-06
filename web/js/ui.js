// Chrome around the views: lite/advanced mode, theme + colorblind-safe mode,
// the collapsible sidebar, the responsive drawer, and tab switching.
//
// Knows nothing about any individual view. Tab activation publishes through
// onTabShown(), and main.js registers each view's loader - so adding a tab
// never edits this file's imports.
import { $, $$, api, motionOK } from "./core.js";

/* ---------- lite/advanced mode ---------- */
export function applyMode(mode) {
  document.body.classList.toggle("mode-lite", mode !== "advanced");
  $$("#mode-toggle button").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === (mode === "advanced" ? "advanced" : "lite")));
}
export async function setMode(mode) {
  applyMode(mode);
  try { await api("/api/config", {ui_mode: mode}); } catch (e) {}
}
export function initModeToggle() {
  $$("#mode-toggle button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
}

/* ---------- theme / colorblind-safe ---------- */
export function applyTheme(t) {
  document.documentElement.dataset.theme = (t === "light" ? "light" : "dark");
  $$("#theme-toggle button").forEach(b =>
    b.classList.toggle("active", b.dataset.theme === document.documentElement.dataset.theme));
}
export function applyCvd(on) {
  if (on) document.documentElement.dataset.cvd = "safe";
  else document.documentElement.removeAttribute("data-cvd");
  const c = $("#cvd-check");
  if (c) c.checked = !!on;
}
// Hearth: the new theme spreads out from the control that asked for it, like a
// lamp coming on. Without View Transitions (or with reduced motion) it just swaps.
function revealFrom(e, apply) {
  const el = e && e.currentTarget;
  if (!el || !el.getBoundingClientRect || !document.startViewTransition || !motionOK()) { apply(); return; }
  const r = el.getBoundingClientRect(), root = document.documentElement.style;
  root.setProperty("--rx", Math.round(r.left + r.width / 2) + "px");
  root.setProperty("--ry", Math.round(r.top + r.height / 2) + "px");
  // hover/colour transitions would otherwise fade the cards inside the reveal
  document.documentElement.classList.add("theme-swap");
  document.startViewTransition(apply).finished
    .finally(() => document.documentElement.classList.remove("theme-swap"));
}
export async function setTheme(t, e) {
  if (t === document.documentElement.dataset.theme) applyTheme(t);
  else revealFrom(e, () => applyTheme(t));
  try { localStorage.setItem("theme", t); } catch (e) {}
  try { await api("/api/config", {theme: t}); } catch (e) {}
}
export async function setCvd(on) {
  applyCvd(on);
  try { localStorage.setItem("cvd", on ? "1" : "0"); } catch (e) {}
  try { await api("/api/config", {cvd: !!on}); } catch (e) {}
}
/* ---------- skin: Stowage (default) / Hearth / Classic ----------
   A third axis beside theme and cvd. Classic is the original terminal look,
   untouched; Stowage and Hearth layer web/css/stowage.css and hearth.css on
   top, each scoped to its own :root[data-skin=...], so every light/dark/cvd
   combination still works. "lf-skin" tells views that draw differently per
   skin (the Stowage bay plan) to redraw. */
const SKINS = ["stowage", "hearth", "classic"];
const skinOf = k => SKINS.includes(k) ? k : "stowage";
// each skin's mark doubles as the tab icon
const FAVICON = {
  stowage: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#13243f"/><rect x="2.5" y="2.5" width="27" height="27" fill="none" stroke="#ffc21a" stroke-width="2"/><rect x="6" y="6" width="12" height="9" fill="#e8661c"/><rect x="19.5" y="6" width="6.5" height="9" fill="#2c6fb7"/><rect x="6" y="17" width="8" height="9" fill="#26754f"/><rect x="15.75" y="17.75" width="9.5" height="7.5" fill="none" stroke="#a6bad0" stroke-width="1.5" stroke-dasharray="2 1.5"/></svg>',
  hearth: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd27a"/><stop offset=".55" stop-color="#ff8a4c"/><stop offset="1" stop-color="#d94a1f"/></linearGradient></defs><path fill="url(#g)" d="M12 1.8c.9 4 6.2 6 6.2 12.2a6.2 6.2 0 0 1-12.4 0c0-3.6 2.4-5.2 2.6-8.4 1.2 1.4 2.3 2.5 3.6 2.7.3-2.1.3-4.3 0-6.5z"/><path fill="#fff1c9" d="M12 12.6c.5 1.9 2.9 2.8 2.9 5.6a2.9 2.9 0 0 1-5.8 0c0-1.6 1-2.4 1.2-3.7.6.6 1 1 1.7 1.1.1-1 .1-1.9 0-3z"/></svg>',
  classic: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#080a0b"/><rect x="3.5" y="3.5" width="25" height="25" fill="none" stroke="#ffb000" stroke-width="2"/><rect x="9" y="10" width="14" height="2.6" fill="#ffb000"/><rect x="9" y="14.7" width="14" height="2.6" fill="#ffb000"/><rect x="9" y="19.4" width="14" height="2.6" fill="#ffb000"/></svg>',
};
export function applySkin(k) {
  const root = document.documentElement, was = root.dataset.skin;
  root.dataset.skin = skinOf(k);
  $$("#skin-toggle button").forEach(b =>
    b.classList.toggle("active", b.dataset.skin === root.dataset.skin));
  const icon = document.querySelector('link[rel="icon"]');
  if (icon) icon.href = "data:image/svg+xml," + encodeURIComponent(FAVICON[root.dataset.skin]);
  if (was !== root.dataset.skin) document.dispatchEvent(new Event("lf-skin"));
}
export async function setSkin(k) {
  k = skinOf(k);
  applySkin(k);
  try { localStorage.setItem("skin", k); } catch (e) {}
  try { await api("/api/config", {skin: k}); } catch (e) {}
}
export function initThemeControls() {
  $$("#theme-toggle button").forEach(b => b.onclick = e => setTheme(b.dataset.theme, e));
  $$("#skin-toggle button").forEach(b => b.onclick = () => setSkin(b.dataset.skin));
  const c = $("#cvd-check");
  if (c) c.onchange = () => setCvd(c.checked);
  // reflect the attributes already set by the <head> script
  applyTheme(document.documentElement.dataset.theme);
  applyCvd(document.documentElement.dataset.cvd === "safe");
  applySkin(document.documentElement.dataset.skin);
}

/* ---------- sidebar rail / expanded ---------- */
export function setNav(state) {
  const s = state === "expanded" ? "expanded" : "rail";
  document.documentElement.dataset.nav = s;
  try { localStorage.setItem("nav", s); } catch (e) {}
  const b = $("#nav-toggle");
  if (b) b.textContent = s === "rail" ? "›" : "‹";
}
export function toggleNav() {
  setNav(document.documentElement.dataset.nav === "rail" ? "expanded" : "rail");
}
function showNavHint() {
  try { if (localStorage.getItem("navHint") === "seen") return; } catch (e) {}
  if (document.documentElement.dataset.nav !== "rail") return;
  if (window.innerWidth <= 900) return;
  const h = $("#nav-hint");
  if (h) { h.hidden = false; setTimeout(dismissNavHint, 8000); }
}
export function dismissNavHint() {
  const h = $("#nav-hint");
  if (h) h.hidden = true;
  try { localStorage.setItem("navHint", "seen"); } catch (e) {}
}
export function initSidebar() {
  const b = $("#nav-toggle");
  if (b) b.onclick = () => { toggleNav(); dismissNavHint(); };
  // rail settings cycle (reuse existing setters)
  const rm = $("#rail-mode");
  if (rm) rm.onclick = () => setMode(document.body.classList.contains("mode-lite") ? "advanced" : "lite");
  const rt = $("#rail-theme");
  if (rt) rt.onclick = e => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", e);
  const rc = $("#rail-cvd");
  if (rc) rc.onclick = () => {
    const nx = document.documentElement.dataset.cvd !== "safe";
    setCvd(nx); rc.classList.toggle("on", nx);
  };
  if (rc) rc.classList.toggle("on", document.documentElement.dataset.cvd === "safe");
  const rk = $("#rail-skin");
  if (rk) rk.onclick = () => setSkin(SKINS[(SKINS.indexOf(document.documentElement.dataset.skin) + 1) % SKINS.length]);
  setNav(document.documentElement.dataset.nav || "rail");   // sync chevron glyph
  showNavHint();
}

/* ---------- responsive drawer (<=600px) ---------- */
export function openDrawer() {
  document.body.classList.add("drawer-open");
  const s = $("#scrim"); if (s) s.hidden = false;
}
export function closeDrawer() {
  document.body.classList.remove("drawer-open");
  const s = $("#scrim"); if (s) s.hidden = true;
}
export function initDrawer() {
  const m = $("#nav-menu"); if (m) m.onclick = openDrawer;
  const s = $("#scrim"); if (s) s.onclick = closeDrawer;
  $$(".navitem").forEach(n => n.addEventListener("click", () => {
    if (document.body.classList.contains("drawer-open")) closeDrawer();
  }));
}

/* ---------- tabs ---------- */
const tabHandlers = {};
const tabHiddenHandlers = {};
/** Register the loader for a tab. main.js wires every view through this, which
 *  is what keeps ui.js free of imports from the views themselves. */
export function onTabShown(name, fn) { tabHandlers[name] = fn; }
/** Register cleanup that must run synchronously before a view is hidden. */
export function onTabHidden(name, fn) { tabHiddenHandlers[name] = fn; }

export function switchTab(name) {
  const t = $(`.tab[data-tab="${name}"]`);
  if (t) t.click();
}
// Restart a one-shot CSS entrance (.swap) on an element that persists across tabs.
function replay(el) { el.classList.remove("swap"); void el.offsetWidth; el.classList.add("swap"); }
export function updatePageTitle() {
  const a = $(".navitem.active .label");
  const t = $("#page-title");
  const l = $("#page-lede"), n = $(".navitem.active");
  const changed = !!(a && t && t.textContent !== a.textContent);
  if (a && t) t.textContent = a.textContent;
  if (l) l.textContent = (n && n.dataset.lede) || "";
  if (changed) { replay(t); if (l) replay(l); }
}
export function initTabs() {
  $$(".tab").forEach(t => t.onclick = () => {
    const previous = $(".tab.active");
    const previousName = previous ? previous.dataset.tab : "";
    if (previousName && previousName !== t.dataset.tab) {
      const hide = tabHiddenHandlers[previousName];
      if (hide) hide();
    }
    $$(".tab").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    $$(".view").forEach(v => v.classList.remove("active"));
    const view = $("#view-" + t.dataset.tab);
    if (view) view.classList.add("active");
    const fn = tabHandlers[t.dataset.tab];
    if (fn) fn();
    updatePageTitle();
    dismissNavHint();
  });
  // nav items are divs: give keyboard users the same activation as a button
  $$(".navitem").forEach(t => t.onkeydown = e => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); t.click(); }
  });
}
/** The tab currently showing, e.g. "models" - polls use this to stay idle. */
export function activeTab() {
  const t = $(".tab.active");
  return t ? t.dataset.tab : "";
}
