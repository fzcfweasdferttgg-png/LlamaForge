import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// `node --check web/js/x.js` parses the file as a script, so module-only early
// errors pass it: an import and a local function with the same name went
// unnoticed until the browser refused the whole module and the panel never
// booted. Check every view as what the browser loads it as, an ES module.
const dir = fileURLToPath(new URL("../web/js/", import.meta.url));

test("every web/js file parses as an ES module", () => {
  const tmp = mkdtempSync(join(tmpdir(), "lf-esm-"));
  try {
    const bad = [];
    for (const f of readdirSync(dir).filter(f => f.endsWith(".js"))) {
      const copy = join(tmp, f.replace(/\.js$/, ".mjs"));
      writeFileSync(copy, readFileSync(join(dir, f)));
      const r = spawnSync(process.execPath, ["--check", copy], {encoding: "utf8"});
      if (r.status !== 0) bad.push(`${f}: ${(r.stderr.match(/SyntaxError[^\r\n]*/) || [r.stderr])[0]}`);
    }
    assert.deepEqual(bad, []);
  } finally {
    rmSync(tmp, {recursive: true, force: true});
  }
});
