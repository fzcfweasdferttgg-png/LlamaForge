// Gateway tab: the LiteLLM gateway - virtual model names over the real ones.
// A name is either "alternation" (requests rotate across its models) or
// "reserve" (the first model is primary: traffic stays on it while it has a
// free slot and spills to the rest when it is full or not loaded). Only models
// LlamaForge has loaded are offered. This view owns the settings, the
// free-form LiteLLM presets (written verbatim into the generated config), and
// the process.
import { $, $$, esc, setHTML, api, toast } from "./core.js";
import { models } from "./state.js";

const MODES = [["alternation", "Alternate across models"],
               ["reserve", "Primary + reserve"]];
const BUSY = [["queue", "Wait in queue"], ["reject", "Reply 429 busy"]];

export async function loadGateway() {
  const v = $("#view-gateway");
  const st = await api("/api/gateway");
  if (!st || st.error) {
    setHTML(v, `<div class="card"><h3>Gateway</h3><div class="note">${
      esc(st && st.error ? st.error : "the gateway did not answer")}</div></div>`);
    return;
  }
  const state = st.model_states || {};
  const configured = new Set(Object.values(st.models || {})
    .flatMap(s => (s && s.models) || []));
  const rows = models().map(m => m.id)
    .filter(id => (state[id] || {}).loaded || configured.has(id));
  const chip = (id) => {
    const s = state[id] || {};
    return s.loaded ? (s.busy ? "busy" : "free") : "not loaded";
  };
  const specs = Object.entries(st.models || {}).map(([name, s]) => ({
    name,
    mode: s.mode === "reserve" ? "reserve" : "alternation",
    when_busy: s.when_busy === "reject" ? "reject" : "queue",
    models: [...(s.models || [])],
  }));
  if (!specs.length) specs.push({ name: "", mode: "alternation", when_busy: "queue", models: [] });

  const pickHTML = (i) => {
    const s = specs[i];
    if (s.mode === "reserve") {
      const primary = s.models[0] || rows[0] || "";
      return `<span class="lbl">Primary</span>
        <select data-gw-primary="${i}">${rows.map(id =>
          `<option value="${esc(id)}"${id === primary ? " selected" : ""}>${
            esc(id)} (${chip(id)})</option>`).join("")}</select>
        <span class="lbl">Reserve models</span>
        <div>${rows.filter(id => id !== primary).map(id => `
          <label style="display:block;font-size:12px"><input type="checkbox" data-gw-res="${i}" value="${
            esc(id)}"${s.models.slice(1).includes(id) ? " checked" : ""}> ${
            esc(id)} (${chip(id)})</label>`).join("")}</div>`;
    }
    return `<span class="lbl">Models</span><div>${rows.map(id => `
      <label style="display:block;font-size:12px"><input type="checkbox" data-gw-alt="${i}" value="${
        esc(id)}"${s.models.includes(id) ? " checked" : ""}> ${
        esc(id)} (${chip(id)})</label>`).join("")}</div>`;
  };

  const rowHTML = (i) => {
    const s = specs[i];
    return `
      <div class="formrow" data-gw-row>
        <label class="f"><span class="lbl">Virtual name</span>
          <input data-gw-name="${i}" value="${esc(s.name)}" placeholder="e.g. mix"></label>
        <label class="f"><span class="lbl">Behavior</span>
          <select data-gw-mode="${i}">${MODES.map(([val, lab]) =>
            `<option value="${val}"${s.mode === val ? " selected" : ""}>${lab}</option>`).join("")}</select></label>
        <div class="f grow" data-gw-pick="${i}">${pickHTML(i)}</div>
        <label class="f"${s.mode === "reserve" ? "" : " hidden"} data-gw-busy-wrap="${i}"><span class="lbl">When all busy</span>
          <select data-gw-busy="${i}">${BUSY.map(([val, lab]) =>
            `<option value="${val}"${s.when_busy === val ? " selected" : ""}>${lab}</option>`).join("")}</select></label>
      </div>`;
  };

  setHTML(v, `
    <div class="card"><h3>Model gateway <span style="color:var(--dim);font-weight:normal;font-size:11px">(LiteLLM)</span></h3>
      <div class="kv"><span class="k">LiteLLM</span><span class="v ${
        st.installed === "installed" ? "ok" : "bad"}">${st.installed === "installed"
          ? "installed" : st.installed === "installing" ? "installing..." : "not installed"}</span></div>
      <div class="kv"><span class="k">process</span><span class="v ${st.running ? "ok" : ""}">${
        st.running ? "running (pid " + esc(st.pid) + ")" : "stopped"}</span></div>
      <div class="kv"><span class="k">endpoint</span><span class="v">${esc(st.endpoint)}</span></div>
      <div class="kv"><span class="k">serve</span><span class="v">${st.enabled ? "on panel start" : "manual only"}</span></div>
      ${specs.filter(s => s.name).map(s => `<div class="kv"><span class="k">${esc(s.name)}</span>
        <span class="v">${s.mode === "reserve" ? "primary + reserve" : "alternation"}: ${
        s.models.map(id => `${esc(id)} (${chip(id)})`).join(", ")}</span></div>`).join("")}
      <div class="note">A virtual name hides the real models behind one API name.
        <b>Alternate</b> rotates requests across its models; <b>Primary + reserve</b> keeps
        traffic on the primary while it has a free slot and spills to the reserves when it is
        full or not loaded. Streaming passes through. ${st.bind === "0.0.0.0" ? "The gateway answers on the LAN." : "The gateway answers on this machine only."}</div>
      <div class="actions">
        <button id="gw-start" class="primary">${st.running ? "Restart" : "Start"}</button>
        <button id="gw-stop">Stop</button>
        ${st.installed === "missing" ? '<button id="gw-install">Install LiteLLM</button>' : ""}
        <span class="msg" id="gw-msg"></span>
      </div>
    </div>
    <div class="card"><h3>Virtual names</h3>
      <div id="gw-names">${specs.map((_, i) => rowHTML(i)).join("")}</div>
      <div class="actions">
        <button id="gw-add">Add name</button>
        <label class="f"><span class="lbl">port</span>
          <input id="gw-port" type="number" min="1" max="65535" value="${esc(st.port)}" style="width:9ch"></label>
        <label class="f"><span class="lbl">bind</span>
          <select id="gw-bind">
            <option value=""${st.bind === "127.0.0.1" ? " selected" : ""}>127.0.0.1 (this machine)</option>
            <option value="0.0.0.0"${st.bind === "0.0.0.0" ? " selected" : ""}>0.0.0.0 (LAN)</option>
          </select></label>
        <label class="f"><span class="lbl">serve on panel start</span>
          <input id="gw-enabled" type="checkbox"${st.enabled ? " checked" : ""}></label>
        <button id="gw-save" class="primary">Save settings</button>
        <span class="msg" id="gw-save-msg"></span>
      </div>
    </div>
    <div class="card"><h3>LiteLLM preset</h3>
      <div class="formrow">
        <label class="f grow"><span class="lbl">Preset</span>
          <select id="gw-preset">${Object.keys(st.presets || {}).map(n =>
            `<option${n === st.preset ? " selected" : ""}>${esc(n)}</option>`).join("")}
          </select></label>
        <label class="f grow"><span class="lbl">New / rename to</span>
          <input id="gw-preset-name" value="${esc(st.preset)}"></label>
      </div>
      <textarea id="gw-preset-json" rows="8" spellcheck="false" style="width:100%;font-family:var(--mono);font-size:12px">${
        esc(JSON.stringify((st.presets || {})[st.preset] || {}, null, 2))}</textarea>
      <div class="note">Free-form LiteLLM settings: the <b>router</b> / <b>litellm_settings</b> /
        <b>general_settings</b> sections are written verbatim into the generated config, so any
        documented LiteLLM setting can be turned here. The virtual names above fill model_list.</div>
      <div class="actions"><button id="gw-preset-save" class="primary">Save preset</button>
        <span class="msg" id="gw-preset-msg"></span></div>
    </div>`);

  $("#gw-names").onchange = (e) => {
    const t = e.target;
    const idx = (key) => Number(t.dataset[key]);
    if (t.dataset.gwMode !== undefined) {
      const i = idx("gwMode");
      specs[i].mode = t.value;
      specs[i].models = [];
      $(`[data-gw-pick="${i}"]`).innerHTML = pickHTML(i);
      $(`[data-gw-busy-wrap="${i}"]`).hidden = specs[i].mode !== "reserve";
    } else if (t.dataset.gwPrimary !== undefined) {
      const i = idx("gwPrimary");
      specs[i].models = [t.value, ...specs[i].models.slice(1)];
      $(`[data-gw-pick="${i}"]`).innerHTML = pickHTML(i);
    }
  };
  $("#gw-add").onclick = () => {
    specs.push({ name: "", mode: "alternation", when_busy: "queue", models: [] });
    $("#gw-names").insertAdjacentHTML("beforeend", rowHTML(specs.length - 1));
  };
  $("#gw-save").onclick = async () => {
    const map = {};
    specs.forEach((s, i) => {
      const name = (($(`[data-gw-name="${i}"]`) || {}).value || "").trim();
      if (!name) return;
      const when = ($(`[data-gw-busy="${i}"]`) || {}).value || "queue";
      if (s.mode === "reserve") {
        const primary = ($(`[data-gw-primary="${i}"]`) || {}).value || "";
        const reserves = [...($$(`[data-gw-res="${i}"]:checked`) || [])].map(o => o.value);
        map[name] = { mode: "reserve", models: [primary, ...reserves].filter(Boolean), when_busy: when };
      } else {
        const picked = [...($$(`[data-gw-alt="${i}"]:checked`) || [])].map(o => o.value);
        map[name] = { mode: "alternation", models: picked, when_busy: when };
      }
    });
    const body = {
      enabled: $("#gw-enabled").checked,
      port: Number($("#gw-port").value),
      bind: $("#gw-bind").value,
      models: map,
      preset: $("#gw-preset").value,
    };
    const r = await api("/api/gateway/save", body);
    const msg = $("#gw-save-msg");
    msg.className = "msg " + (r && r.ok ? "ok" : "err");
    msg.textContent = r && r.ok ? (r.restarted ? "saved - gateway restarted" : "saved")
      : (r && r.error) || "not saved";
    if (r && r.ok && r.restarted && r.status && r.status.running) toast("Gateway restarted", "ok");
  };
  $("#gw-preset").onchange = () => {
    const n = $("#gw-preset").value;
    $("#gw-preset-json").value = JSON.stringify((st.presets || {})[n] || {}, null, 2);
    $("#gw-preset-name").value = n;
  };
  $("#gw-preset-save").onclick = async () => {
    let settings;
    try {
      settings = JSON.parse($("#gw-preset-json").value || "{}");
    } catch {
      const msg = $("#gw-preset-msg");
      msg.className = "msg err";
      msg.textContent = "the preset must be valid JSON";
      return;
    }
    const name = ($("#gw-preset-name").value || $("#gw-preset").value || "").trim();
    const r = await api("/api/gateway/preset/save", { name, settings });
    const msg = $("#gw-preset-msg");
    msg.className = "msg " + (r && r.ok ? "ok" : "err");
    msg.textContent = r && r.ok ? "saved" : (r && r.error) || "not saved";
    if (r && r.ok) loadGateway();
  };
  const act = async (path, label) => {
    const msg = $("#gw-msg");
    msg.className = "msg work";
    msg.textContent = label + "...";
    const r = await api("/api/gateway/" + path, {});
    if (r && r.error && r.ok === false) {
      msg.className = "msg err";
      msg.textContent = r.error;
      return;
    }
    msg.className = "msg ok";
    msg.textContent = label + " done";
    loadGateway();
  };
  $("#gw-start").onclick = () => act("start", "starting");
  $("#gw-stop").onclick = () => act("stop", "stopping");
  const inst = $("#gw-install");
  if (inst) inst.onclick = async () => {
    const msg = $("#gw-msg");
    msg.className = "msg work";
    msg.textContent = "installing LiteLLM (several minutes)...";
    await api("/api/gateway/install", {});
    loadGateway();
  };
}
