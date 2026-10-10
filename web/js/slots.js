// Multi-model slots in the Models tab: which loaded model is the main and
// which are workers, whether an unloaded one fits beside them, why a load was
// refused, and the t("unload these workers, then load") the planner offers a main
// load that doesn't fit.
//
// Cost contract: role()/chip() read S.STATE.slots, the summary /api/state
// carries on every poll (no planner, no nvidia-smi). The planner itself
// (/api/slots/plan) runs only for the open row, and again only when the set
// of loaded models changes.
import { $, esc, setHTML, api, toast, askYes } from "./core.js";
import { t } from "./i18n.js";
import { S, models as modelRows } from "./state.js";

const UP = new Set(["loaded", "sleeping"]);
const plans = {};       // model id -> {sig, verdict}; verdict null while the request is out
const refusals = {};    // model id -> {sig, reason, evict} from the last refused load

export const slots = () => (S.STATE && S.STATE.slots) || {};
export const slotsOn = () => !!slots().enabled;
const isLlama = m => (m.backend || "llamacpp") === "llamacpp";
const gpus = devs => (devs || []).map(d => t("GPU") + d).join("+");
const gib = mib => (mib / 1024).toFixed(1) + t(" GiB");

/** "main", "worker", or "" when the model isn't up on a pool. */
export function role(m) {
  if (!slotsOn() || !isLlama(m) || !UP.has(m.status)) return "";
  return m.id === slots().main ? "main" : "worker";
}

export function chip(m) {
  const r = role(m);
  if (!r) return "";
  const where = gpus((slots().devices || {})[m.id]);
  const tip = r === "main"
    ? t("Main model: the one you work with. It gets the fastest GPU that fits.")
    : t("Worker: loaded beside the main model, on the GPUs the main doesn't use.");
  return `<span class="tag slot-${esc(r)}" title="${esc(tip)}">${esc(r)}${where ? " " + esc(where) : ""}</span>`;
}

/** Everything chip() reads, for the row's change signature. */
export function chipSig(m) {
  const r = role(m);
  return r ? [r, (slots().devices || {})[m.id] || []] : "";
}

// The loaded set as the planner sees it: a verdict is stale once this changes.
function loadedSig() {
  const up = modelRows().filter(m => isLlama(m) && (UP.has(m.status) || m.status === t("loading")));
  return JSON.stringify([slots().main || "", up.map(m => m.id).sort()]);
}

function planFor(id, rerender) {
  const sig = loadedSig(), p = plans[id];
  if (p && p.sig === sig) return p.verdict;
  plans[id] = {sig, verdict: null};
  const settle = v => { if (plans[id] && plans[id].sig === sig) plans[id].verdict = v; };
  api(`/api/slots/plan?model=${encodeURIComponent(id)}&role=main`)
    .then(v => settle(v && typeof v === "object" ? v : {ok: false, reason: t("no answer")}),
          () => settle({ok: false, reason: t("could not ask the planner")}))
    .finally(rerender);
  return null;
}

/** The open row's settled planner verdict while it is still current, for
 *  the bay plan's "booked" box; null when there is none or it is loaded. */
export function booked(id) {
  const p = id && plans[id];
  return p && p.sig === loadedSig() && p.verdict && !p.verdict.already ? p.verdict : null;
}

/** Drop what we know about a model's fit (its knobs changed, or a new load starts). */
export function forget(id) { delete plans[id]; delete refusals[id]; delete dropped[id]; }

// Settings a model's own build doesn't take (slotproc.translate), from its last load.
const dropped = {};
export function droppedNote(id) {
  const d = dropped[id];
  return d && d.length
    ? `<div class="slotnote dim">Started without ${esc(d.join(", "))}: this build doesn't have ${d.length > 1 ? t("those options") : t("that option")}.</div>`
    : "";
}

function warn(id, lead, reason, evict) {
  const btn = evict && evict.length
    ? ` <button class="qbtn" data-slot-evict="${esc(id)}" title="unload ${esc(evict.join(", "))}, then load this one as the main model">Unload ${esc(evict.join(", "))} and load</button>`
    : "";
  return `<div class="slotnote warn"><b>${esc(lead)}:</b> ${esc(reason)}${btn}</div>`;
}

/** The slot region of an open row's editor ("" when there's nothing to say). */
export function block(m, rerender) {
  if (!slotsOn() || !isLlama(m)) return "";
  const r = role(m), where = gpus((slots().devices || {})[m.id]);
  if (r === "main")
    return `<div class="slotnote">Main model${where ? " on " + esc(where) : ""}. Other models load beside it as workers when the VRAM math leaves room.</div>`;
  if (r === "worker")
    return `<div class="slotnote">Worker${where ? " on " + esc(where) : ""}, beside the main model. <button class="qbtn" data-slot-main="${esc(m.id)}" title="make this the model you work with; nothing moves or reloads">${t("Make main")}</button></div>`;
  if (m.status === t("loading") || m.failed) return "";
  const ref = refusals[m.id];
  if (ref && ref.sig === loadedSig()) return warn(m.id, t("Didn't load"), ref.reason, ref.evict);
  const v = planFor(m.id, rerender);
  if (v === null) return `<div class="slotnote dim">${t("Checking whether it fits beside the loaded models...")}</div>`;
  if (!v.ok) return warn(m.id, t("Won't fit"), v.reason || v.error || t("the planner refused"), v.evict);
  const need = Object.values(v.footprint || {}).reduce((a, b) => a + (Number(b) || 0), 0);
  const how = v.source === "measured" ? "measured" : v.confident ? "predicted" : t("rough estimate");
  const on = gpus(v.devices);
  return `<div class="slotnote ok">Fits${on ? " on " + esc(on) : ""}${need ? `, needs ~${esc(gib(need))} (${esc(how)})` : ""}.</div>`;
}

/** Can this row offer t("Load as worker")? Only beside a model that is already up. */
export function canLoadWorker(m) {
  return slotsOn() && isLlama(m) && !UP.has(m.status) && m.status !== t("loading")
    && modelRows().some(x => x.id !== m.id && isLlama(x) && UP.has(x.status));
}

export function errText(r) {
  if (!r) return t("load failed");
  if (typeof r.error === "string" && r.error) return r.error;
  return (r.error && r.error.message) || r.reason || t("load failed");
}

/** POST /api/load; a planner refusal is remembered for the row's editor. */
export async function load(id, asRole = "main", evict = false) {
  forget(id);
  const r = await api("/api/load", slotsOn() ? {model: id, role: asRole, evict} : {model: id});
  if (r && !r.success && slotsOn() && typeof r.reason === "string")
    refusals[id] = {sig: loadedSig(), reason: errText(r), evict: r.evict || []};
  if (r && r.success && Array.isArray(r.dropped) && r.dropped.length) dropped[id] = r.dropped;
  return r || {};
}

export async function makeMain(id) {
  const r = await api("/api/slots/main", {model: id});
  toast(r && r.ok ? `${id} is the main model` : t("could not change the main model"), r && r.ok ? "ok" : "err");
}

/* ---------- the banner above the list ---------- */
let bannerShown = null;
export function renderBanner() {
  const el = $("#slots-banner");
  if (!el) return;
  const sl = slots();
  let h = "";
  if (sl.restart_needed)
    h = `<div class="slotnote warn"><b>${t("Multi-model is on,")}</b> ${t("but the router running now holds one model at a time (it was started before the setting, or by the run script).")} <button class="qbtn" data-slot-apply title="restart the router with the multi-model pool; loaded models are unloaded">${t("Restart router")}</button></div>`;
  else if (sl.enabled) {
    const cap = (sl.settings || {}).slot_cap;
    h = `<div class="slotnote dim">MULTI-MODEL &middot; up to ${esc(cap)} at once &middot; main: ${esc(sl.main || "none")}</div>`;
  }
  if (h !== bannerShown) { setHTML(el, h); bannerShown = h; }
}

export async function applyPool() {
  if (!(await askYes(t("Every loaded model is unloaded."),
      {title: t("Restart the router with multi-model on"), ok: t("Restart"), danger: true}))) return false;
  toast(t("Restarting the router..."), "ok");
  const r = await api("/api/slots/apply", {});
  toast(r && r.ok ? (r.restarted ? t("Router restarted with multi-model") : t("Already running with multi-model"))
                  : `Restart failed: ${(r && r.error) || t("unknown error")}`, r && r.ok ? "ok" : "err");
  return true;
}
