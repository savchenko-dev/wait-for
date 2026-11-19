/**
 * wait-for for OpenCode (plugin API v1, @opencode-ai/plugin): a `wait_for` tool that polls an
 * external condition in a detached process and prompts the session when it is met, a guard
 * against hand-rolled `sleep` loops in bash, and an idle handler that keeps the session going
 * while a wait is unhandled. Shared logic lives in ../bin/wait-for.sh and ../hooks/*.py
 * (WAIT_FOR_HOME overrides the location).
 */
import { spawn, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { tool } from "@opencode-ai/plugin";

const HOME = process.env.WAIT_FOR_HOME || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BIN = path.join(HOME, "bin", "wait-for.sh");
const ENGINE = path.join(HOME, "hooks", "stop_while_waiting.py");
const GUARD = path.join(HOME, "hooks", "block_poll_loops.py");
const START_HINT = " (or call the wait_for tool with action=start)";

function py(script, args, stdin = "") {
  const r = spawnSync("python3", [script, ...args], { input: stdin, encoding: "utf8", env: { ...process.env, WAIT_FOR_BIN: BIN } });
  try { return JSON.parse(r.stdout || "{}"); } catch { return {}; }
}

export const WaitForPlugin = async ({ client }) => {
  const nudged = new Map();
  const prompt = (sessionID, text) =>
    client.session.prompt({ path: { id: sessionID }, body: { parts: [{ type: "text", text }] } }).catch(() => {});

  async function lastAssistantText(sessionID) {
    try {
      const res = await client.session.messages({ path: { id: sessionID } });
      const msgs = res?.data ?? res ?? [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m?.info?.role !== "assistant") continue;
        const t = (m.parts || []).filter((p) => p.type === "text").map((p) => p.text).join("\n");
        if (t) return t;
      }
    } catch {}
    return "";
  }

  return {
    "shell.env": async (input, output) => {
      if (input.sessionID) output.env.WAIT_FOR_SESSION = input.sessionID;
    },
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash" && input.tool !== "shell") return;
      const command = output.args?.command ?? "";
      const out = py(GUARD, [], JSON.stringify({ tool_name: "Bash", tool_input: { command } }));
      const d = out?.hookSpecificOutput;
      if (d?.permissionDecision === "deny") throw new Error(d.permissionDecisionReason);
    },
    event: async ({ event }) => {
      const idle = event.type === "session.idle" || (event.type === "session.status" && event.properties?.status?.type === "idle");
      if (!idle) return;
      const sessionID = event.properties?.sessionID;
      if (!sessionID) return;
      const text = await lastAssistantText(sessionID);
      const args = ["--check", "--session", sessionID, "--session", "nosession", "--text", text, "--start-hint", START_HINT];
      if (nudged.get(sessionID)) args.push("--nudged");
      const d = py(ENGINE, args);
      if (d?.decision !== "block") { nudged.delete(sessionID); return; }
      nudged.set(sessionID, true);
      await prompt(sessionID, d.reason);
    },
    tool: {
      wait_for: tool({
        description:
          "Wait for an external condition (review comments, an access grant, a deploy, a file or table appearing) without ending the task. " +
          "action=start runs `check` via bash every `every` seconds until it exits 0 or `for` expires, in the background; you are prompted with the result. " +
          "action=list shows open waits; action=ack forgets a handled wait. Never hand-roll sleep loops instead.",
        args: {
          action: tool.schema.enum(["start", "list", "ack"]),
          id: tool.schema.string().optional().describe("slug for the wait; required for start and ack"),
          what: tool.schema.string().optional().describe("what you are waiting for"),
          check: tool.schema.string().optional().describe("shell command that exits 0 when the condition holds; required for start"),
          every: tool.schema.number().optional().describe("seconds between checks, >= 15; remote APIs >= 300"),
          for: tool.schema.string().optional().describe("deadline, e.g. 20m, 2h, 1d (default 24h)"),
        },
        async execute(params, ctx) {
          const env = { ...process.env, WAIT_FOR_SESSION: ctx.sessionID, WAIT_FOR_BIN: BIN };
          if (params.action !== "start") {
            const args = params.action === "list" ? ["list"] : ["ack", params.id ?? ""];
            const r = spawnSync("bash", [BIN, ...args], { encoding: "utf8", env });
            return (r.stdout + r.stderr).trim();
          }
          if (!params.id || !params.check) throw new Error("start needs id and check");
          const args = [BIN, "start", "--id", params.id, "--what", params.what ?? params.id,
            "--every", String(params.every ?? 120), "--for", params.for ?? "24h", "--", params.check];
          const logPath = path.join(os.tmpdir(), `wait-for-${ctx.sessionID}-${params.id}.log`);
          const log = fs.openSync(logPath, "w");
          const child = spawn("bash", args, { env, detached: true, stdio: ["ignore", log, log] });
          fs.closeSync(log);
          const readLog = () => { try { return fs.readFileSync(logPath, "utf8").trim(); } catch { return ""; } };
          child.on("exit", (code) => {
            if (code === 130 || code === 143) return;
            prompt(ctx.sessionID, `wait_for '${params.id}' finished (exit ${code}).\n${readLog()}\nHandle the result, then call wait_for(action=ack, id="${params.id}").`);
          });
          child.unref();
          await new Promise((r) => setTimeout(r, 400));
          if (child.exitCode !== null && child.exitCode !== 0) throw new Error(readLog() || `wait-for exited ${child.exitCode}`);
          return `${readLog() || `wait_for '${params.id}' started`}\nEnd your turn; you will be prompted when it finishes.`;
        },
      }),
    },
  };
};
export default WaitForPlugin;
