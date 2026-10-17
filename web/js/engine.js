// Prebuilt engine card: install / update / roll back official builds of the
// three engines (ggml-org llama.cpp, Thireus ik_llama, Anbeeld beellama), no
// compiler needed. Mounted on the Engines tab and inside the first-run wizard,
// so each card owns its own polling and imports neither view. Cards scope
// their lookups to the mount element, so several can coexist.
import { $, esc, setHTML, api, toast, meter, fmtAgo } from "./core.js";
import { t } from "./i18n.js";

const PHASES = {
  resolving: t("finding the right build"), downloading: "downloading",
  verifying: t("verifying sha256"), extracting: "extracting",
  testing: "test-running llama-server", activating: t("starting the router"),
};
const fmtMB = b => (b / 1e6).toFixed(0) + " MB";

export async function mountEngineCard(el, opts = {}) {
  if (!el) return;
  const engine = opts.engine || "llamacpp";
  // Literal t() keys per engine so the i18n extractor and tests see them;
  // read on every render so a language switch takes effect.
  const title = engine === "ikllama" ? t("ik_llama builds (Thireus)")
    : engine === "beellama" ? t("beellama builds (Anbeeld)")
    : t("official llama.cpp builds");
  const note = engine === "ikllama"
    ? t("Downloads a prebuilt ik_llama binary from github.com/Thireus/ik_llama.cpp (the CPU-ISA\n      build is picked for this machine), checks it and test-runs it before switching.")
    : engine === "beellama"
    ? t("Downloads a prebuilt beellama binary from github.com/Anbeeld/beellama.cpp (the GPU\n      backend is picked for this machine), checks it and test-runs it before switching.")
    : t("Downloads the official binary from github.com/ggml-org/llama.cpp, checks it against\n      GitHub's SHA-256, and test-runs it before switching. Old builds stay installed for rollback.");
  const force = !!opts.force;
  const prev = el.dataset.channel;
  const prevTag = el.dataset.tag;
  const q = `?engine=${encodeURIComponent(engine)}&${force ? "force=1&" : ""}`
    + `${prev ? "channel=" + encodeURIComponent(prev) + "&" : ""}`
    + `${prevTag ? "tag=" + encodeURIComponent(prevTag) : ""}`;
  const [info, st] = await Promise.all(
    [api("/api/engine/prebuilt" + q),
     api("/api/engine/prebuilt/status?engine=" + encodeURIComponent(engine))]);
  const L = info.latest || {};
  const installs = info.installs || [];
  const cur = installs.find(i => i.active);
  el.dataset.channel = info.channel;

  let headline, button;
  if (!L.ok) {
    headline = `<span class="v bad">can't reach GitHub releases: ${esc(L.error || "?")}</span>`;
    button = "";
  } else if (cur && !info.update_available) {
    headline = `<span class="v ok">up to date &middot; ${esc(cur.tag)} &middot; ${esc(cur.variant)}`
      + (cur.build ? ` &middot; build ${esc(String(cur.build))}` : "") + `</span>`;
    button = `<button class="ghost" id="eng-install">${t("Reinstall")}</button>`;
  } else if (cur) {
    headline = `<span class="v bad">${esc(L.tag)} available (you have ${esc(cur.tag)}`
      + (cur.build ? ` &middot; build ${esc(String(cur.build))}` : "") + `)</span>`;
    button = `<button class="primary" id="eng-install">Update to ${esc(L.tag)}</button>`;
  } else {
    headline = `<span class="v">${info.active_bin ? t("using your own binary") : t("no engine installed yet")}</span>`;
    button = `<button class="primary" id="eng-install">Install ${esc(L.tag)} &middot; ${esc(fmtMB(L.download_bytes || 0))}</button>`;
  }
  const variants = (L.alternatives || []).map(v =>
    `<option value="${esc(v)}">${esc(v)}${v === L.variant ? t(" (recommended)") : ""}</option>`).join("");
  const pickRow = engine === "llamacpp"
    ? `<div class="kv"><span class="k">channel</span><span class="v">
        <select id="eng-channel">
          <option value="nightly" ${info.channel === "nightly" ? "selected" : ""}>${t("nightly: every upstream merge, new models day 0")}</option>
          <option value="stable" ${info.channel === "stable" ? "selected" : ""}>${t("stable: curated llama.cpp releases")}</option>
        </select>
        ${L.label && L.label !== L.tag ? `<span class="note" style="margin:0 0 0 6px">${esc(L.label)} = ${esc(L.tag)}</span>` : ""}
      </span></div>`
    : `<div class="kv"><span class="k">${t("release")}</span><span class="v">
        <select id="eng-channel"><option value="">${t("latest (auto)")}</option>${
          (L.releases || []).map(r => `<option value="${esc(r.tag)}"${
            el.dataset.tag === r.tag ? " selected" : ""}>${esc(r.tag)}${
            r.prerelease ? " (preview)" : ""}</option>`).join("")}</select></span></div>`;

  setHTML(el, `<div class="card engine-card"><h3>${t("Engine")} &middot; ${title}</h3>
    <div class="kv"><span class="k">status</span>${headline}</div>
    ${L.ok ? `<div class="kv"><span class="k">${t("for this PC")}</span><span class="v">${esc(L.variant)} &middot; ${esc(L.reason)}</span></div>` : ""}
    ${pickRow}
    ${opts.compact ? "" : `<div class="kv"><span class="k">variant</span><span class="v">
      <select id="eng-variant"><option value="">auto (${esc(L.variant || "?")})</option>${variants}</select></span></div>`}
    <div class="actions" style="margin-top:6px">${button}
      ${opts.compact ? "" : `<button class="ghost" id="eng-refresh">${t("Check now")}</button>`}
      <span class="msg" id="eng-msg"></span></div>
    <div id="eng-progress" style="display:none;margin-top:8px">
      <div class="meter" id="eng-meter"></div>
      <div class="note" id="eng-phase" style="margin:4px 0 0"></div>
    </div>
    <div class="note">${note}</div>
    ${!opts.compact && installs.length ? `<div style="margin-top:8px">${installs.map(i => `
      <div class="kv"><span class="k">${esc(i.tag)} &middot; ${esc(i.variant)}`
        + (i.build ? ` &middot; build ${esc(String(i.build))}` : "") + `</span>
        <span class="v">installed ${esc(fmtAgo(i.installed_at))}
        ${i.active ? `<strong class="ok">active</strong>`
                   : `<button class="ghost" data-use="${esc(i.dir)}" style="padding:2px 8px">${t("Use")}</button>`}</span></div>`).join("")}</div>` : ""}
    ${opts.compact ? "" : `<div class="log" id="eng-log" style="display:none"></div>`}
  </div>`);

  $("#eng-channel", el).onchange = e => {
    if (engine === "llamacpp") el.dataset.channel = e.target.value;
    else if (e.target.value) el.dataset.tag = e.target.value;
    else delete el.dataset.tag;
    mountEngineCard(el, opts);
  };
  const ref = $("#eng-refresh", el);
  if (ref) ref.onclick = () => { ref.disabled = true; mountEngineCard(el, {...opts, force: true}); };
  const inst = $("#eng-install", el);
  if (inst) inst.onclick = async () => {
    inst.disabled = true;
    const variant = ($("#eng-variant", el) || {}).value || "";
    const r = await api("/api/engine/prebuilt/install",
      {engine, channel: info.channel, variant, tag: el.dataset.tag || ""});
    if (!r.started) toast("an install is already running", "err");
    poll(el, opts, engine, title);
  };
  el.querySelectorAll("[data-use]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    const r = await api("/api/engine/prebuilt/use", {engine, dir: b.dataset.use});
    toast(r.ok ? t("Switched engine build") : (r.error || t("switch failed")), r.ok ? "ok" : "err");
    mountEngineCard(el, opts);
  });
  if (st.running) poll(el, opts, engine, title);
}

function poll(el, opts, engine, title) {
  clearInterval(el._poll);
  const tick = async () => {
    const s = await api("/api/engine/prebuilt/status?engine=" + encodeURIComponent(engine));
    const box = $("#eng-progress", el), msg = $("#eng-msg", el), log = $("#eng-log", el);
    if (!box) { clearInterval(el._poll); return; }
    box.style.display = "";
    setHTML($("#eng-meter", el), meter(s.downloaded, Math.max(s.total, 1)));
    const what = PHASES[s.phase] || s.phase;
    $("#eng-phase", el).textContent = s.phase === "downloading"
      ? `${what} ${s.file} · ${fmtMB(s.downloaded)} / ${fmtMB(s.total)}` : what;
    if (log) { log.style.display = ""; log.textContent = s.log || ""; log.scrollTop = log.scrollHeight; }
    if (s.running) { msg.className = "msg work"; msg.textContent = "installing..."; return; }
    clearInterval(el._poll);
    if (s.phase === "done") {
      toast(`${title} ${s.tag} installed`, "ok");
      if (opts.onDone) opts.onDone(s);
      mountEngineCard(el, opts);
    } else if (s.phase === "failed") {
      msg.className = "msg err"; msg.textContent = s.error || t("install failed");
      const btn = $("#eng-install", el); if (btn) btn.disabled = false;
    } else {
      msg.className = "msg"; msg.textContent = s.phase;
    }
  };
  tick();
  el._poll = setInterval(tick, 1000);
}
