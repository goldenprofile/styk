import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, copyFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

// Use Pi 1.5.2's actual loader, including its aliases for standalone extensions.
const { loadExtensions } = await import(new URL("./core/extensions/loader.js",
  import.meta.resolve("@earendil-works/pi-coding-agent")));
const source = fileURLToPath(new URL("../../../src/styk/pi/styk.ts", import.meta.url));

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), "styk supervisor "));
  t.after(() => rm(directory, { recursive: true, force: true }));
  // Copy outside the repository to check the installed extension's module resolution.
  const extension = join(directory, "styk.ts");
  await copyFile(source, extension);
  const loaded = await loadExtensions([extension], directory);
  assert.deepEqual(loaded.errors, []);
  const context = { cwd: directory, ui: { setStatus() {}, notify() {} } };
  return { directory, extension: loaded.extensions[0], context };
}

function demoPlan() {
  return { backend: "demo", tasks: [{
    id: "build", role: "builder", objective: "Write the greeting",
    acceptance_criteria: ["Exact greeting"],
    checks: [[process.env.STYK_TEST_PYTHON ?? "python", "-c",
      "from pathlib import Path; assert Path('hello.txt').read_text() == 'Hello from Styk!\\n'"]],
  }] };
}

test("Pi loads the standalone extension and executes, reads and resumes a verified plan", async (t) => {
  const { directory, extension, context } = await fixture(t);
  assert.deepEqual([...extension.tools.keys()], ["styk_run", "styk_status", "styk_resume"]);
  const run = extension.tools.get("styk_run").definition;
  assert.equal(run.executionMode, "sequential");
  const updates = [];
  const result = await run.execute("1", { plan: demoPlan() }, undefined,
    (update) => updates.push(update), context);
  assert.equal(result.isError, false, JSON.stringify(result));
  assert.equal(result.details.status, "SUCCEEDED");
  assert.equal((await readFile(join(directory, "hello.txt"), "utf8")).replace(/\r\n/g, "\n"), "Hello from Styk!\n");
  assert.ok(updates.some((value) => value.content[0].text.includes("CHECK_STARTED")));
  const run_id = result.details.run_id;
  const status = await extension.tools.get("styk_status").definition.execute("2", { run_id }, undefined, undefined, context);
  assert.equal(status.details.tasks[0].verification[0].exit_code, 0);
  const resumed = await extension.tools.get("styk_resume").definition.execute("3", { run_id }, undefined, undefined, context);
  assert.equal(resumed.details.status, "SUCCEEDED");
  const prompt = await extension.handlers.get("before_agent_start")[0]({ systemPrompt: "Base prompt" }, context);
  assert.ok(prompt.systemPrompt.includes("never for hand-written task JSON"));
});

test("tool abort cancels the workflow and returns its saved checkpoint", async (t) => {
  const { directory, extension, context } = await fixture(t);
  const plan = demoPlan();
  plan.timeout_seconds = 60;
  plan.tasks[0].checks = [[process.env.STYK_TEST_PYTHON ?? "python", "-c", "import time; time.sleep(30)"]];
  const abort = new AbortController();
  const started = Date.now();
  const result = await extension.tools.get("styk_run").definition.execute("1", { plan }, abort.signal,
    (update) => { if (update.content[0].text.includes("CHECK_STARTED")) abort.abort(); }, context);
  assert.equal(result.details.status, "CANCELLED", JSON.stringify(result));
  assert.ok(Date.now() - started < 10000, "Cancellation must terminate the sleeping child promptly");
  assert.equal(result.isError, true);
  const state = JSON.parse(await readFile(join(directory, ".agent-runs", result.details.run_id, "state.json"), "utf8"));
  assert.ok(state.checkpoint);
});

test("invalid unchecked builder plans are rejected before workers run", async (t) => {
  const { extension, context } = await fixture(t);
  const plan = demoPlan();
  plan.tasks[0].checks = [];
  await assert.rejects(extension.tools.get("styk_run").definition.execute("1", { plan }, undefined, undefined, context), /Builders require/);
});
