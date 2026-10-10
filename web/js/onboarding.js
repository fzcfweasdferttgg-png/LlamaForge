// The t("Getting Started") checklist above the model list. Driven by the "state"
// event, so it never needs to import the model list.
import { $, $$, esc, setHTML } from "./core.js";
import { t } from "./i18n.js";
import { on } from "./bus.js";
import { switchTab } from "./ui.js";

function renderOnboarding(s) {
  const el = $("#onboard"); if (!el) return;
  const ob = s.onboarding || {};
  const anyLoaded = (s.models||[]).some(m => m.status === "loaded");
  const steps = [
    {done: ob.server_bin_ok, label: t("Install llama.cpp"), tab: "build", btn: t("Open Engine"),
     hint: t("One click downloads the official build for your GPU (verified, no compiler). Or build from source on the same tab.")},
    {done: ob.model_count > 0, label: t("Get models"), tab: "discover", btn: t("Open Discover"),
     hint: t("Discover downloads from huggingface.co with VRAM-fit ratings, or scan your drives from Setup.")},
    {done: anyLoaded, label: t("Load a model"), tab: "models", btn: "",
     hint: t("Expand a model below, tune knobs if you like, and hit Load. Then point any OpenAI client at the router port.")},
  ];
  // once every step has been completed once, stay hidden for good (a later
  // unload shouldn't resurrect the checklist)
  if (steps.every(x => x.done)) localStorage.setItem("lf_onboard_done", "1");
  if (localStorage.getItem("lf_onboard_done")) { el.style.display = "none"; return; }
  el.style.display = "";
  setHTML(el, `<div class="card" style="border-color:var(--amber)"><h3>${t("Getting Started")}</h3>
    ${steps.map((st,i) => `<div class="kv"><span class="k">
      <span style="color:${st.done?"var(--green)":"var(--dim)"}">${st.done?"&#10003;":"&#9675;"}</span>
      ${i+1}. ${esc(st.label)}</span>
      <span class="v" style="text-align:right">${st.done?'<span style="color:var(--green)">done</span>':
        (st.btn?`<button data-goto="${st.tab}" style="padding:4px 10px">${esc(st.btn)}</button>`:'<span style="color:var(--dim)">below &darr;</span>')}</span></div>
      ${st.done?"":`<div class="note" style="margin:2px 0 8px">${esc(st.hint)}</div>`}`).join("")}
  </div>`);
  $$("[data-goto]", el).forEach(b => b.onclick = () => switchTab(b.dataset.goto));
}

export function initOnboarding() {
  on("state", renderOnboarding);
}
