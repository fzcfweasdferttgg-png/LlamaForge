// Chrome around the views: lite/advanced mode, theme + colorblind-safe mode,
// the collapsible sidebar, the responsive drawer, and tab switching.
//
// Knows nothing about any individual view. Tab activation publishes through
// onTabShown(), and main.js registers each view's loader - so adding a tab
// never edits this file's imports.
import { $, $$, api } from "./core.js";

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
export async function setTheme(t) {
  applyTheme(t);
  try { localStorage.setItem("theme", t); } catch (e) {}
  try { await api("/api/config", {theme: t}); } catch (e) {}
}
export async function setCvd(on) {
  applyCvd(on);
  try { localStorage.setItem("cvd", on ? "1" : "0"); } catch (e) {}
  try { await api("/api/config", {cvd: !!on}); } catch (e) {}
}
/* ---------- skin: Hearth (default) / Classic ----------
   A third axis beside theme and cvd. Classic is the original terminal look,
   untouched; Hearth layers web/css/hearth.css on top, scoped to
   :root[data-skin="hearth"], so every light/dark/cvd combination still works. */
export function applySkin(k) {
  document.documentElement.dataset.skin = (k === "classic" ? "classic" : "hearth");
  $$("#skin-toggle button").forEach(b =>
    b.classList.toggle("active", b.dataset.skin === document.documentElement.dataset.skin));
}
export async function setSkin(k) {
  applySkin(k);
  try { localStorage.setItem("skin", k); } catch (e) {}
  try { await api("/api/config", {skin: k}); } catch (e) {}
}
export function initThemeControls() {
  $$("#theme-toggle button").forEach(b => b.onclick = () => setTheme(b.dataset.theme));
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
  if (rt) rt.onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  const rc = $("#rail-cvd");
  if (rc) rc.onclick = () => {
    const nx = document.documentElement.dataset.cvd !== "safe";
    setCvd(nx); rc.classList.toggle("on", nx);
  };
  if (rc) rc.classList.toggle("on", document.documentElement.dataset.cvd === "safe");
  const rk = $("#rail-skin");
  if (rk) rk.onclick = () => setSkin(document.documentElement.dataset.skin === "classic" ? "hearth" : "classic");
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
