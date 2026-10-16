// t("Bench") panel - runs llama-bench over a b/ub grid to tune batch-size and
// ubatch-size. llama-bench speaks its own CLI dialect (see backend/bench.py),
// so the tab never builds argv: it posts a spec and renders the polled state.
// loadBench() re-renders idempotently each time the tab is shown.
import { $, esc, setHTML, api, toast, askYes } from "./core.js";
import { t } from "./i18n.js";
import { models } from "./state.js";

let poll = 0;

export function loadBench() {
  setHTML($("#view-bench"), `
    <div class="card">
      <div class="formrow" style="gap:8px;margin:0;align-items:center">
        <select id="bn-model" style="flex:1 1 220px;min-width:0">
          ${models().map(m => `<option value="${esc(m.id)}">${esc(m.id)}</option>`).join("")}
        </select>
        <input id="bn-b" value="512,1024,2048,4096" title="${t("batch sizes")}" style="width:150px">
        <input id="bn-ub" value="128,256,512,1024" title="${t("micro-batch sizes")}" style="width:150px">
        <input id="bn-reps" value="2" title="${t("repeats")}" style="width:56px">
        <button id="bn-go" class="primary">${t("Run bench")}</button>
        <button id="bn-stop">${t("Stop")}</button>
      </div>
      <div id="bn-out" class="note" style="margin-top:10px">${t("no bench results yet")}</div>
    </div>`);
  $("#bn-go").onclick = runBench;
  $("#bn-stop").onclick = stopBench;
  refresh();
}

function nums(sel) {
  return ($(sel).value || "").split(",").map(s => parseInt(s, 10))
         .filter(n => n > 0);
}

async function runBench() {
  const r = await api("/api/bench/run", {
    model: $("#bn-model").value,
    b: nums("#bn-b"), ub: nums("#bn-ub"),
    reps: parseInt($("#bn-reps").value, 10) || 2,
  });
  if (!r || r.ok === false) toast((r && r.error) || t("request failed"), "err");
  refresh();
}

async function stopBench() {
  await api("/api/bench/stop", {});
  refresh();
}

async function refresh() {
  clearTimeout(poll);
  const st = await api("/api/bench/status");
  if (!st) return;
  const out = $("#bn-out");
  if (out) {
    setHTML(out, table(st));
    for (const btn of out.querySelectorAll("[data-bn-apply]")) {
      btn.onclick = () => apply(btn.dataset.b, btn.dataset.ub);
    }
  }
  if (st.running) poll = setTimeout(refresh, 2000);
}

function table(st) {
  const rec = st.recommended;
  const w = st.workload || {};
  const cell = (r, key) => {
    if (!r) return "&mdash;";
    const d = st.delta ? st.delta[key] : undefined;
    const dd = (d === undefined || d === null) ? "" :
      ` <span style="color:var(--dim)">${d > 0 ? "+" : ""}${d}%</span>`;
    return `${r.ts.toFixed(2)} <span style="color:var(--dim)">&plusmn;${r.sd.toFixed(2)}</span>${dd}`;
  };
  const rows = [];
  for (const e of st.grid || []) {
    for (const it of e.ubs) {
      const r = it.rows || {};
      const mine = rec && rec.b === e.b && rec.ub === it.ub;
      rows.push(`<tr${mine ? ' style="font-weight:600"' : ""}><td>${e.b}</td><td>${it.ub}` +
        (mine ? ` <span style="color:var(--dim)">${t("recommended")}</span>` : "") + `</td>` +
        `<td>${cell(r.pp, `${e.b}:${it.ub}:pp`)}</td>` +
        `<td>${cell(r.tg, `${e.b}:${it.ub}:tg`)}</td>` +
        `<td>${cell(r.pg, `${e.b}:${it.ub}:pg`)}</td><td>` +
        (st.phase === "done"
          ? `<button class="qbtn" data-bn-apply data-b="${e.b}" data-ub="${it.ub}">${t("Apply")}</button>`
          : "") + `</td></tr>`);
    }
  }
  const who = [st.model, st.engine].filter(Boolean).join(" · ");
  return `<div class="kv"><span class="k">${t("status")}</span><span class="v">` +
    `${esc(st.phase)} ${st.done_rows}/${st.total_rows} ` +
    esc(st.running ? (st.current || "") : (st.last_done || "")) +
    (st.error ? ` <span style="color:var(--red)">${esc(st.error)}</span>` : "") +
    `</span></div>` +
    (who ? `<div class="note">${esc(who)}${st.started_at ?
      ` · ${t("started")} ${esc(st.started_at)}` : ""}</div>` : "") +
    (rows.length ? `<table class="rows" style="width:100%;margin-top:8px">
      <tr><th>b</th><th>ub</th><th>pp ${w.p ?? 5000}</th><th>tg ${w.n ?? 2500}</th><th>pg ${(w.pg || [5000, 2500]).join("+")}</th><th></th></tr>
      ${rows.join("")}</table>` +
      (rec && st.phase === "done"
        ? `<button class="qbtn" style="margin-top:8px" data-bn-apply data-b="${rec.b}" data-ub="${rec.ub}">${t("Apply recommended")} (${rec.b}/${rec.ub})</button>`
        : "") : t("no bench results yet"));
}

async function apply(b, ub) {
  const mid = $("#bn-model").value;
  const ok = await askYes(
    t("Write batch-size = {b} and ubatch-size = {ub} to {model}?", {b, ub, model: mid}) +
    " " + t("Nothing else in the model's models.ini section changes."),
    {title: t("Apply")});
  if (!ok) return;
  const r = await api("/api/save",
    {model: mid, settings: {"batch-size": b, "ubatch-size": ub}});
  if (r && r.ok) toast(t("saved"), "ok");
  else toast((r && r.error) || t("request failed"), "err");
}
