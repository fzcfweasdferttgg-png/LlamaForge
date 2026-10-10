// Gateway tab: the LiteLLM gateway - one virtual model name over many real
// ones. A request naming a virtual name is handed to one of its models; the
// active preset's routing_strategy picks which per request, so traffic
// alternates across them. This view owns the settings, the free-form LiteLLM
// presets (written verbatim into the generated config), and the process.
import { $, $$, esc, setHTML, api, toast } from "./core.js";
import { models } from "./state.js";

export async function loadGateway() {
  const v = $("#view-gateway");
  const st = await api("/api/gateway");
  if (!st || st.error) {
    setHTML(v, `<div class="card"><h3>Gateway</h3><div class="note">${
      esc(st && st.error ? st.error : "the gateway did not answer")}</div></div>`);
    return;
  }
  const rows = models();
  const modelOpts = (sel) => rows.map(m =>
    `<option value="${esc(m.id)}"${sel.includes(m.id) ? " selected" : ""}>${
      esc(m.id)}</option>`).join("");
  const names = Object.keys(st.models || {});
  setHTML(v, `
    <div class="card"><h3>Model gateway <span style="color:var(--dim);font-weight:normal;font-size:11px">(LiteLLM)</span></h3>
      <div class="kv"><span class="k">LiteLLM</span><span class="v ${
        st.installed === "installed" ? "ok" : "bad"}">${st.installed === "installed"
          ? "installed" : st.installed === "installing" ? "installing..." : "not installed"}</span></div>
      <div class="kv"><span class="k">process</span><span class="v ${st.running ? "ok" : ""}">${
        st.running ? "running (pid " + esc(st.pid) + ")" : "stopped"}</span></div>
      <div class="kv"><span class="k">endpoint</span><span class="v">${esc(st.endpoint)}</span></div>
      <div class="kv"><span class="k">serve</span><span class="v">${st.enabled ? "on panel start" : "manual only"}</span></div>
      <div class="note">One virtual name over the real models: a request naming it goes to
        one of them (the preset's routing_strategy picks which per request), streaming passes
        through. ${st.bind === "0.0.0.0" ? "The gateway answers on the LAN." : "The gateway answers on this machine only."}</div>
      <div class="actions">
        <button id="gw-start" class="primary">${st.running ? "Restart" : "Start"}</button>
        <button id="gw-stop">Stop</button>
        ${st.installed === "missing" ? '<button id="gw-install">Install LiteLLM</button>' : ""}
        <span class="msg" id="gw-msg"></span>
      </div>
    </div>
    <div class="card"><h3>Virtual names</h3>
      <div id="gw-names">${(names.length ? names : [""]).map((n, i) => `
        <div class="formrow" data-gw-row>
          <label class="f grow"><span class="lbl">Virtual name</span>
            <input id="gw-name-${i}" value="${esc(n)}" placeholder="e.g. mix"></label>
          <label class="f grow"><span class="lbl">Real models</span>
            <select id="gw-models-${i}" multiple size="3">${modelOpts((st.models || {})[n] || [])}</select></label>
        </div>`).join("")}</div>
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

  $("#gw-add").onclick = () => {
    const host = $("#gw-names");
    const i = host.querySelectorAll("[data-gw-row]").length;
    host.insertAdjacentHTML("beforeend", `
      <div class="formrow" data-gw-row>
        <label class="f grow"><span class="lbl">Virtual name</span>
          <input id="gw-name-${i}" placeholder="e.g. mix"></label>
        <label class="f grow"><span class="lbl">Real models</span>
          <select id="gw-models-${i}" multiple size="3">${modelOpts([])}</select></label>
      </div>`);
  };
  $("#gw-save").onclick = async () => {
    const map = {};
    $$("[data-gw-row]").forEach((row, i) => {
      const n = ($(`#gw-name-${i}`) || {}).value;
      if (!n || !n.trim()) return;
      const sel = $(`#gw-models-${i}`);
      map[n.trim()] = [...sel.selectedOptions].map(o => o.value);
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
