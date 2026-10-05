// Forge: a loaded model interviews the user and builds an ember by itself.
//
// The conversation lives here, in localStorage ("lf_forge"); each turn sends
// it whole to /api/embers/forge, which re-checks every source against what the
// user typed and builds the ember when the model says the user agreed. All
// model text goes through esc().
import { $, api, esc, setHTML, toast } from "./core.js";

const KEY = "lf_forge";
const MAX_MESSAGES = 100;           // panel.FORGE_MESSAGES
const GREETING = "I'm Forge. I build embers: background workers that read your notes, calendar, repos or feeds "
  + "on a schedule, keep a wiki of what matters, and brief you each morning. What do you want kept track of?";
const KIND = { folder: "folder", ics: "calendar", rss: "feed", git: "repo", llamacpp: "LlamaForge models", machine: "this machine" };

let S = fresh();                    // {messages: [{role, text, notes?, built?, local?}], draft, built: [ids], model}
let busy = false;
let onBuilt = () => {};
try { const saved = JSON.parse(localStorage.getItem(KEY) || "null"); if (saved && Array.isArray(saved.messages)) S = saved; } catch (e) {}

function fresh() {
  return { messages: [{ role: "forge", text: GREETING, local: true }], draft: null, built: [], model: "" };
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} }

/** cb(id): an ember was just built (refresh the list, select it if wanted). */
export function initForge(cb) { onBuilt = cb; }

export function renderForge(el) {
  setHTML(el, `<div id="ef-forge" class="fg">
      <div class="ef-head">
        <div class="ef-headtext">
          <h2 class="ef-title">Forge</h2>
          <div class="ef-mission">Say what you want kept track of. Forge asks a few questions, then builds the ember itself.</div>
        </div>
        <div class="qbtns"><button type="button" class="qbtn" data-fg="reset" title="Clear this conversation">Start over</button></div>
      </div>
      <div class="fg-grid">
        <div class="fg-chat">
          <div id="fg-log" class="fg-log" aria-live="polite"></div>
          <form id="fg-form" class="fg-form">
            <textarea id="fg-in" maxlength="2000" rows="2" autocomplete="off" spellcheck="true"
              placeholder="Type here. Paste folder paths and URLs exactly; Forge only uses ones you type."></textarea>
            <button class="primary" type="submit">Send</button>
          </form>
          <div class="note" id="fg-foot"></div>
        </div>
        <aside id="fg-bp" class="fg-bp" aria-label="Blueprint"></aside>
      </div>
    </div>`);
  $("#fg-in").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#fg-form").requestSubmit(); }
  });
  draw();
  if (!busy) $("#fg-in").focus();
}

function msgHTML(m) {
  const who = m.role === "user" ? "you" : m.role === "error" ? "error" : "forge";
  const label = who === "you" ? "YOU" : who === "error" ? "LLAMAFORGE" : "FORGE";
  return `<div class="fg-msg fg-${who}"><span class="fg-who">${label} ▸</span><div class="fg-body">
      <div class="fg-text">${esc(m.text)}</div>
      ${(m.notes || []).map(n => `<div class="fg-note">${esc(n)}</div>`).join("")}
      ${m.built ? `<button type="button" class="ef-flink fg-open" data-ember="${esc(m.built.id)}">Open ${esc(m.built.name)} →</button>` : ""}
    </div></div>`;
}

function draw() {
  const log = $("#fg-log");
  if (!log) return;
  setHTML(log, S.messages.map(msgHTML).join("")
    + (busy ? `<div class="fg-msg fg-forge fg-wait"><span class="fg-who">FORGE ▸</span><div class="fg-body"><div class="fg-text">thinking…</div></div></div>` : ""));
  log.scrollTop = log.scrollHeight;
  const full = S.messages.length >= MAX_MESSAGES - 1;
  $("#fg-in").disabled = busy || full;
  $("#fg-form button").disabled = busy || full;
  setHTML($("#fg-foot"), full ? `This conversation is full. Start over to keep going.`
    : esc(S.model ? `Talking with ${S.model}, a model that's already loaded. ` : "Uses a model that's already loaded and never loads one itself. ")
      + "A 12B+ instruct model works best.");
  drawBlueprint();
}

function drawBlueprint() {
  const d = S.draft || {};
  const blank = `<span class="fg-blank">not yet</span>`;
  const row = (k, v) => `<div class="fg-k">${k}</div><div class="fg-v">${v}</div>`;
  const pages = (d.pages || []).map(p => `<div><code>${esc(p.kind)}/</code> ${esc(p.about || "")}</div>`).join("");
  const sources = (d.sources || []).map(s => `<div class="fg-src ${s.ok ? "ok" : "bad"}" title="${esc(s.why || "")}">
      <span class="fg-mark">${s.ok ? "✓" : "✗"}</span>
      <span><span class="fg-kind">${esc(KIND[s.type] || s.type)}</span> ${s.value ? `<code>${esc(s.value)}</code>` : ""}
      ${s.ok ? "" : `<span class="fg-why">${esc(s.why)}</span>`}</span></div>`).join("");
  setHTML($("#fg-bp"), `<div class="fg-bphead">Blueprint</div><div class="fg-rows">
      ${row("Name", d.title ? esc(d.title) : blank)}
      ${row("Mission", d.mission ? esc(d.mission) : blank)}
      ${row("Pages", pages || blank)}
      ${row("Sources", sources || blank)}
      ${row("Brief", d.title ? `${esc(d.brief_at || "07:00")} daily <span class="fg-why">reads sources an hour before</span>` : blank)}
      ${d.rules ? row("Rules", esc(d.rules)) : ""}
    </div>
    <div class="note">Forge builds it when you say yes to its summary. ✗ sources are left out; LlamaForge
      only uses paths and URLs you typed that exist on this machine.</div>
    ${S.built.length ? `<div class="note">Built here: ${S.built.map(id => `<button type="button" class="ef-flink" data-ember="${esc(id)}">${esc(id)}</button>`).join(", ")}</div>` : ""}`);
}

export async function forgeSend() {
  const input = $("#fg-in");
  const text = input.value.trim();
  if (!text || busy) return;
  S.messages.push({ role: "user", text });
  input.value = "";
  busy = true; save(); draw();
  const sent = S.messages.filter(m => !m.local && m.role !== "error")
    .map(m => ({ role: m.role, text: m.text, notes: m.notes || [] }));
  const d = await api("/api/embers/forge", { messages: sent, draft: S.draft, built: S.built, model: S.model })
    .catch(() => null);
  busy = false;
  if (!d || d.error) {
    S.messages.pop();                                  // give the words back to retry
    S.messages.push({ role: "error", text: d ? d.error : "The panel didn't answer.", local: true });
    save(); draw();
    const box = $("#fg-in");
    if (box) { box.value = text; box.focus(); }
    return;
  }
  S.messages = S.messages.filter(m => m.role !== "error");
  S.draft = d.draft; S.model = d.model || "";
  const m = { role: "forge", text: d.say, notes: d.notes || [] };
  if (d.built) {
    m.built = { id: d.built.id, name: d.built.name };
    S.built.push(d.built.id);
  }
  S.messages.push(m);
  save(); draw();
  if ($("#fg-in")) $("#fg-in").focus();
  if (d.built) {
    toast(`Forge built ${d.built.name}. ` + (d.built.queued.length ? "First run queued." : ""), "ok");
    onBuilt(d.built.id);
  }
}

export function forgeReset() {
  if (busy) return;
  if (S.messages.length > 1 && !confirm("Clear this Forge conversation? Embers it built stay.")) return;
  S = fresh(); save(); draw();
  $("#fg-in").focus();
}
