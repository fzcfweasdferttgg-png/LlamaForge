// Engines tab: download and select builds for the three engines (llama.cpp,
// ik_llama, beellama) and switch which one the router runs. Source builds are
// gone; the prebuilt cards (engine.js) are how a binary gets here.
import { $, $$, esc, setHTML, api, toast } from "./core.js";
import { t } from "./i18n.js";
import { mountEngineCard } from "./engine.js";

const ENGINES = [["llamacpp", "llama.cpp"], ["ikllama", "ik_llama.cpp"],
                 ["beellama", "beellama.cpp"]];

export async function loadBuild(force) {
  const v = $("#view-build");
  const st = await api("/api/state");
  const activeEngine = st.active_engine || "llamacpp";

  setHTML(v, `
    <div class="card buildtarget">
      <span class="k">${t("Active engine")}</span>
      <span class="mode-toggle">${ENGINES.map(([id, label]) =>
        `<button class="${activeEngine === id ? "active" : ""}" data-eng="${id}">${label}</button>`).join("")}</span>
      <span class="buildtarget-active"><span class="note" style="margin:0">${t("The engine the router runs; each model can still pin its own build.")}</span></span>
    </div>
    ${ENGINES.map(([id]) => `<div id="eng-card-${id}"></div>`).join("")}`);

  for (const [id] of ENGINES) {
    mountEngineCard($(`#eng-card-${id}`), {engine: id, force});
  }
  $$("[data-eng]", v).forEach(b => b.onclick = async () => {
    if (b.dataset.eng === activeEngine) return;
    b.disabled = true;
    const r = await api("/api/engine/switch", {engine: b.dataset.eng});
    if (r.ok) {
      toast(t("Switched engine build"), "ok");
      setTimeout(loadBuild, 1500);
    } else {
      toast(r.error || t("switch failed"), "err");
      b.disabled = false;
    }
  });
}
