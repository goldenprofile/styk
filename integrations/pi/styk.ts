/** Thin Pi UI integration. The Python runtime owns workflow state. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn, type ChildProcess } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

export default function (pi: ExtensionAPI) {
  const source = fileURLToPath(new URL("../../src", import.meta.url));
  let active: ChildProcess | undefined;

  pi.registerCommand("styk-run", {
    description: "Run a Styk JSON plan: /styk-run /absolute/path/plan.json",
    handler: async (args, ctx) => {
      if (active) {
        ctx.ui.notify("A Styk run is already active", "warning");
        return;
      }
      if (!args.trim()) {
        ctx.ui.notify("Usage: /styk-run path/to/plan.json", "warning");
        return;
      }
      const child = spawn(process.env.STYK_PYTHON ?? "python3", [
        "-m", "styk", "--workspace", ctx.cwd, "run", resolve(ctx.cwd, args.trim()),
      ], {
        cwd: ctx.cwd,
        env: { ...process.env, PYTHONPATH: source },
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
      active = child;
      let tail = "";
      const update = (data: Buffer) => {
        tail = (tail + data.toString()).slice(-4000);
        ctx.ui.setStatus("styk", tail.trim().split("\n").at(-1)?.slice(0, 180));
      };
      child.stdout?.on("data", update);
      child.stderr?.on("data", update);
      child.on("error", (error) => {
        active = undefined;
        ctx.ui.setStatus("styk", undefined);
        ctx.ui.notify(error.message, "error");
      });
      child.on("close", (code) => {
        active = undefined;
        ctx.ui.setStatus("styk", undefined);
        ctx.ui.notify(`Styk exit ${code}\n${tail}`, code === 0 ? "info" : "warning");
      });
      ctx.ui.notify("Styk started. /styk-cancel stops the run.", "info");
    },
  });

  pi.registerCommand("styk-cancel", {
    description: "Cancel the active Styk run and save its state",
    handler: async (_args, ctx) => {
      if (active?.pid) {
        // Signal the Python host first; it cleans up its separately grouped workers.
        active.kill("SIGINT");
        ctx.ui.notify("Cancellation requested", "info");
      } else {
        ctx.ui.notify("No active Styk run", "info");
      }
    },
  });

  pi.on("session_shutdown", async () => {
    if (active?.pid) active.kill("SIGINT");
  });
}
