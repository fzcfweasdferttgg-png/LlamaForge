---
title: Keyboard Shortcuts
section: reference
order: 4
---

# Keyboard Shortcuts

LlamaForge's dashboard listens for a fixed set of keyboard shortcuts (`web/js/models.js`, the `keydown` handler). Shortcuts are ignored while focus is inside an input, select, or textarea (except `Escape`, which still clears the model search box), and none of them fire while a modifier key (`Ctrl`, `Alt`, `Cmd`) is held.

## Global

| Key | Action |
|---|---|
| `1`–`9` | Switch tab, in sidebar order: `1` Models, `2` Chat, `3` Embers, `4` Stats, `5` Discover, `6` Will it run?, `7` Build / Update, `8` Setup, `9` Context. Help, the tenth tab, has no number key. The numbers follow the sidebar, so they stay right if tabs are added. |
| `Escape` | Close an open modal. If a modal isn't open and the model search box is focused, clear the search box instead. |

## Models tab only

The remaining shortcuts only act while the **Models** tab is active and dashboard state has loaded.

| Key | Action |
|---|---|
| `/` | Focus the model search box. |
| `↓` or `j` | Move the selection down one row. |
| `↑` or `k` | Move the selection up one row. |
| `Enter` | Expand/collapse the selected model's detail row. |
| `l` / `L` | Load the selected model, if it isn't already loaded. |
| `u` / `U` | Unload the selected model, if it's loaded or loading. |
| `s` / `S` | Save the selected model's settings, if its detail row is open (clicks the row's save button). |

The load/unload/save actions call the same `/api/load`, `/api/unload`, and `/api/save` endpoints as their corresponding buttons — see [HTTP API](api.md).
