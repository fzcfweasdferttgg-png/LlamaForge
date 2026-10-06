import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

// confirm() and prompt() belong to the host browser, and some hosts answer them
// "cancel" without showing anything (the Claude app's browser pane, a tab Chrome
// was told to stop showing dialogs for). Every Remove/Delete then did nothing.
// The panel asks with core.js ask()/askText() instead.
const dir = new URL("../web/js/", import.meta.url);
const files = (await readdir(dir)).filter(f => f.endsWith(".js"));

test("no view calls the native confirm(), prompt() or alert()", async () => {
  const hits = [];
  for (const f of files) {
    const lines = (await readFile(new URL(f, dir), "utf8")).split(/\r?\n/);
    lines.forEach((line, i) => {
      if (/^\s*(\/\*|\*)/.test(line)) return;          // block-comment prose may name them
      if (/(^|[^.\w])(window\.)?(confirm|prompt|alert)\s*\(/.test(line.replace(/\/\/.*$/, "")))
        hits.push(`${f}:${i + 1}: ${line.trim()}`);
    });
  }
  assert.deepEqual(hits, []);
});
