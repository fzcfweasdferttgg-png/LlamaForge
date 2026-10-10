// Build tab: engine builds and the engine switch. Source builds are gone;
// the prebuilt card (engine.js) is how a binary gets here.
import { $, esc, setHTML, api, toast } from "./core.js";
import { t } from "./i18n.js";
import { mountEngineCard } from "./engine.js";

let _target = localStorage.getItem("build_target") || "llamacpp";

function setTarget(t) {
  _target = t;
  localStorage.setItem("build_target", t);
}

const ENGINE_LABELS = {
  llamacpp: "llama.cpp",
  ikllama: "ik_llama",
};

export async function loadBuild(force) {
  const v = $("#view-build");
  const st = await api("/api/state");
  const activeEngine = st.active_engine || "llamacpp";
  const label = ENGINE_LABELS[_target] || _target;
  const isActive = _target === activeEngine;

  setHTML(v, `
    <div class="card buildtarget">
      <span class="k">${t("Build Target")}</span>
      <span class="mode-toggle">
        <button class="${_target==='llamacpp'?'active':''}" id="btn-tgt-llamacpp">llama.cpp</button>
        <button class="${_target==='ikllama'?'active':''}" id="btn-tgt-ikllama">ik_llama</button>
      </span>
      <span class="buildtarget-active">
        ${t("Active engine:")} <strong class="${isActive?'ok':'dim'}">${esc(ENGINE_LABELS[activeEngine]||activeEngine)}</strong>
        ${!isActive?`<button class="ghost" id="btn-switch-engine">Switch to ${esc(label)}</button>`:''}
      </span>
    </div>
    ${_target === "llamacpp" ? `<div id="engine-prebuilt"></div>` : ""}`);

  if (_target === "llamacpp") mountEngineCard($("#engine-prebuilt"), {force});

  $("#btn-tgt-llamacpp").onclick = () => { setTarget("llamacpp"); loadBuild(); };
  $("#btn-tgt-ikllama").onclick = () => { setTarget("ikllama"); loadBuild(); };

  const switchBtn = $("#btn-switch-engine");
  if (switchBtn) switchBtn.onclick = async () => {
    switchBtn.disabled = true;
    switchBtn.textContent = "switching...";
    const r = await api("/api/engine/switch", {engine: _target});
    if (r.ok) {
      toast(`Switched to ${label}`, "ok");
      setTimeout(loadBuild, 1500);
    } else {
      toast(r.error || t("switch failed"), "err");
      switchBtn.disabled = false;
      switchBtn.textContent = `Switch to ${label}`;
    }
  };
}
