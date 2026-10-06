---
title: Theming & Accessibility
section: guides
order: 9
---

# Theming & Accessibility

Three skins (**Stowage**, the default, **Hearth** and **Classic**), each with light and dark appearance and a colorblind-safe mode. All three choices are persisted per browser and applied before first paint, so the dashboard never flashes the wrong look.

## What it does

### Skins

**Stowage** is the default. It is built like a ship's stowage plan: a hull-navy sidebar, a pale chart-grey page, Barlow Condensed headings, a coloured band under each page title in that section's hue, and a safety-yellow primary action. Its centrepiece is the **bay plan** on the Models page: each GPU is drawn as a cargo hold ruled in 1 GB cells, all on one shared scale, with every loaded model stowed in it as a container at its real size (orange for the main model, blue and green for workers, slate for system and other apps). When you open a model that is not loaded, its predicted footprint appears as a dashed **booked** box; if it would not fit, the overflow is drawn hatched as **Short X GB**. The ledger beside each hold adds up exactly: used + booked + free = total.

Per-model sizes come from the footprints of models this panel loaded in multi-model mode. Memory nothing accounts for is shown as one honest remainder block: "System + other apps", "System", or, when exactly one loaded model has no measured footprint, "*model* + system", since the two cannot be told apart there.

**Hearth** is warm paper and ember tones, Fraunces headings over Inter, a serif page title with a one-line lede. **Classic** is the original terminal look (monospace, phosphor colors, CRT scanlines in dark mode). All skins render the same markup; only the styling differs, except that Hearth and Classic draw GPUs as a tile with a segment meter instead of the bay plan.

The skin is a `data-skin` attribute on `<html>`: `"stowage"`, `"hearth"` or `"classic"`. Classic is the base stylesheet in `web/index.html`; Stowage lives in `web/css/stowage.css` and Hearth in `web/css/hearth.css`, with every rule scoped to `:root[data-skin="stowage"]` or `:root[data-skin="hearth"]`. Each file defines its own token blocks in order (dark, then `[data-theme="light"]`, then `[data-cvd="safe"]`), so every skin × theme × CVD combination works. In Hearth, ok/error toasts carry a small colored dot; in CVD-safe mode they keep the `✓`/`✗` glyphs instead.

Switch skins with the **Stowage / Hearth / Classic** toggle in the sidebar, or the skin button in the collapsed sidebar rail, which cycles through them.

### Light, dark and colorblind-safe

The rest of this page describes the token mechanics, using Classic's values as the example. Hearth works the same way with its own palette.

All colors in the dashboard are CSS custom properties defined on `:root` in `web/index.html`. The dark palette (the default) is the base `:root` block; `:root[data-theme="light"]` overrides it with a light set of the same variable names, so every component that already uses `var(--ink)`, `var(--panel)`, etc. repaints correctly with no per-component changes. Switching theme also toggles a couple of dark-mode-only effects off in light mode: the CRT scanline overlay (`body::after`) and the glow/box-shadow on the pulse indicator and lit meter segments.

A separate, independent attribute, `data-cvd="safe"`, layers a colorblind-safe palette on top of whichever theme is active. `:root[data-cvd="safe"]` overrides the status-color tokens with the Okabe–Ito palette:

| Token | Default | CVD-safe (Okabe–Ito) |
|---|---|---|
| `--green` (ok/success) | `#39d98a` | `#009E73` (bluish green) |
| `--red` (error/hot) | `#ff5c57` | `#D55E00` (vermillion) |
| `--amber` (accent/status) | `#ffb000` | `#E69F00` (orange) |
| `--cyan` (info) | `#3fd7e6` | `#56B4E9` (sky blue) |

CVD-safe mode doesn't stop at recoloring — it also adds cues that don't depend on hue at all, since a palette swap alone still relies on the viewer telling colors apart:

- `.msg.ok`, `.msg.err`, `.msg.work`, and `#toast.ok`/`#toast.err` get a `::before` content glyph: `✓` (U+2713) for ok, `✗` (U+2717) for error, `…` (U+2026) for in-progress.
- `.pulse` (the "live" status dot) gets a `::after` label reading `LIVE`.
- `.seg.hot` (an overloaded/hot meter segment) gets a `::after` `!` glyph.

Both settings are controlled from `web/js/ui.js`: `applyTheme(t)` sets `data-theme` to `"light"` or `"dark"` (anything else falls back to dark) and syncs the toggle buttons' active state; `applyCvd(on)` sets or removes `data-cvd="safe"` and syncs the checkbox. `setTheme()`/`setCvd()` wrap those with persistence: they call `applyTheme`/`applyCvd` immediately, write the choice to `localStorage` (`theme`, `cvd`), and push it to the backend via `POST /api/config` so it becomes that device's server-side default too.

Resolution order, most specific first:

1. **`localStorage`** — this browser's own explicit choice, if it has ever set one.
2. **Server config** (`cfg.theme` / `cfg.cvd`, read from `/api/state` on `refresh()`) — applied only when this browser's `localStorage` has no entry yet, so it acts as a fallback default rather than an override.
3. **OS preference** — a small inline script in `<head>`, which runs before the stylesheet paints and before the app modules load, checks `localStorage.getItem("theme")` first and otherwise falls back to `window.matchMedia("(prefers-color-scheme: light)")`. This same script also applies `data-cvd="safe"` immediately if `localStorage.getItem("cvd")==="1"`. Because it runs synchronously in `<head>` before any content renders, there is no flash of the wrong theme on load.

## How to use it

1. Pick a skin with the **Stowage** / **Hearth** / **Classic** toggle in the sidebar.
2. Open the theme toggle (**Light** / **Dark** buttons) anywhere it appears in the header — click one to switch immediately.
3. Toggle the colorblind-safe checkbox to switch the status palette to Okabe–Ito colors and enable the non-color status cues, independent of the light/dark choice.
4. Your choices are remembered on this device via `localStorage`, and also saved to LlamaForge's config so a fresh browser profile on the same machine picks it up as the default.

## Key options / reference

| Concept | Source | Behavior |
|---|---|---|
| Skin | `<html data-skin>` | `"stowage"` (default), `"hearth"` or `"classic"`. |
| Stowage stylesheet | `web/css/stowage.css` | Every rule scoped to `:root[data-skin="stowage"]`; own dark, light and CVD token blocks. |
| Hearth stylesheet | `web/css/hearth.css` | Every rule scoped to `:root[data-skin="hearth"]`; own dark, light and CVD token blocks. |
| Fonts | `web/css/fonts.css`, `web/fonts/` | Every face the three skins use, self-hosted as Latin-subset woff2 under the SIL OFL (`web/fonts/OFL.txt`). No font CDN is contacted. |
| Bay plan | `web/js/models.js` `bayPlans()` | Stowage's GPU view; sizes from `slots.footprints` in `/api/state`, the booked box from the open row's fit verdict. |
| Apply skin | `web/js/ui.js` `applySkin(k)` | Sets `data-skin` (anything unknown means Stowage), syncs `#skin-toggle`, swaps the tab icon to that skin's mark, fires an `lf-skin` event so the GPU view redraws. |
| Persist skin | `web/js/ui.js` `setSkin(k)` | `applySkin` + `localStorage.setItem("skin", k)` + `POST /api/config` (`skin` key: `""` = Stowage default, `"stowage"`, `"hearth"`, `"classic"`). |
| Dark palette (Classic) | `web/index.html` `:root{...}` | Base CSS custom properties for all colors, fonts, and surfaces. |
| Light palette | `web/index.html` `:root[data-theme="light"]{...}` | Overrides the same variable names; also disables scanlines and glow effects. |
| Colorblind-safe palette | `web/index.html` `:root[data-cvd="safe"]{...}` | Overrides `--green`/`--red`/`--amber`/`--cyan` (and related border/tint tokens) with Okabe–Ito hex values. |
| Non-color status cues | `web/index.html` `:root[data-cvd="safe"] .msg.*::before` etc. | Adds `✓`/`✗`/`…` glyphs, a `LIVE` label, and a `!` glyph so status doesn't rely on hue alone. |
| Apply theme | `web/js/ui.js` `applyTheme(t)` | Sets `data-theme`, syncs toggle button active state. |
| Apply CVD | `web/js/ui.js` `applyCvd(on)` | Sets/removes `data-cvd="safe"`, syncs the checkbox. |
| Persist theme | `web/js/ui.js` `setTheme(t)` | `applyTheme` + `localStorage.setItem("theme", t)` + `POST /api/config`. |
| Persist CVD | `web/js/ui.js` `setCvd(on)` | `applyCvd` + `localStorage.setItem("cvd", ...)` + `POST /api/config`. |
| No-flash resolver | `web/index.html` inline `<head>` script | Runs before paint: `localStorage` skin/theme/cvd, else OS `prefers-color-scheme`. |
| Config fallback | `web/js/main.js` boot | Applies server `cfg.skin`/`cfg.theme`/`cfg.cvd` only when this browser's `localStorage` has no entry yet. |

## Troubleshooting

If a new browser profile on the same machine doesn't pick up the theme you set elsewhere, that's expected the first time — `localStorage` is per-browser, and the server-side config value only becomes the applied default on the next `refresh()`, and only if that browser hasn't already chosen its own theme. If a previously-set theme seems "stuck" after changing the server default, clear the `skin`/`theme`/`cvd` keys from that browser's `localStorage`, since a local choice always wins over the config fallback.

See also [Context Wiki](context-wiki.md) and [Connect an Agent](agents.md) for the other panels this appearance system applies to.
