/**
 * wait-for for pi: a `wait_for` tool that polls an external condition in a detached process and
 * wakes the session when it is met, a guard against hand-rolled `sleep` loops in bash, and an
 * `agent_before_settle` handler that refuses to settle while a wait is unhandled.
 * Shared logic lives in ../bin/wait-for.sh and ../hooks/*.py (WAIT_FOR_HOME overrides the location).
 */
import { spawn, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "@sinclair/typebox";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const HOME = process.env.WAIT_FOR_HOME || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BIN = path.join(HOME, "bin", "wait-for.sh");
const ENGINE = path.join(HOME, "hooks", "stop_while_waiting.py");
const GUARD = path.join(HOME, "hooks", "block_poll_loops.py");
const START_HINT = " (or call the wait_for tool with action=start)";
// Without a UI (print mode) nothing can wake an idle session, so the settle hook holds the run open instead.
const HOLD_SECONDS = Number(process.env.WAIT_FOR_HOLD || 3300);

function py(script: string, args: string[], stdin = ""): any {
	const r = spawnSync("python3", [script, ...args], { input: stdin, encoding: "utf8", env: { ...process.env, WAIT_FOR_BIN: BIN } });
	try { return JSON.parse(r.stdout || "{}"); } catch { return {}; }
}

function lastAssistantText(messages: any[]): string {
	for (let i = messages.length - 1; i >= 0; i--) {
		const m = messages[i];
		if (m?.role !== "assistant") continue;
		const c = m.content;
		if (typeof c === "string") return c;
		if (Array.isArray(c)) return c.filter((b: any) => b?.type === "text").map((b: any) => b.text).join("\n");
	}
	return "";
}

export default function (pi: ExtensionAPI) {
	let sessionId = "nosession";
	let nudged = false;
	const sessionOf = (ctx: ExtensionContext) => (ctx.sessionManager.getSessionId?.() as string | undefined) || "nosession";

	pi.on("session_start", async (_e, ctx) => { sessionId = sessionOf(ctx); nudged = false; });

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName !== "bash") return undefined;
		const input = event.input as { command?: string };
		const command = input.command ?? "";
		if (command.includes("wait-for.sh") && !/WAIT_FOR_SESSION=/.test(command)) {
			input.command = `WAIT_FOR_SESSION=${sessionOf(ctx)} ${command}`;
		}
		const out = py(GUARD, [], JSON.stringify({ tool_name: "Bash", tool_input: { command } }));
		const d = out?.hookSpecificOutput;
		if (d?.permissionDecision === "deny") return { block: true, reason: d.permissionDecisionReason };
		return undefined;
	});

	pi.on("agent_before_settle", async (event, ctx) => {
		const sid = sessionOf(ctx);
		const text = lastAssistantText(event.context?.contextMessages ?? []);
		const args = ["--check", "--session", sid, "--session", "nosession", "--text", text, "--start-hint", START_HINT];
		if (nudged) args.push("--nudged");
		if (!ctx.hasUI) args.push("--hold", String(HOLD_SECONDS));
		const d = py(ENGINE, args);
		if (d?.decision !== "block") { nudged = false; return undefined; }
		nudged = true;
		return { entries: [{ type: "custom_message", customType: "wait-for", content: d.reason, display: true }], continue: true };
	});

	pi.registerTool({
		name: "wait_for",
		label: "wait for",
		description:
			"Wait for an external condition (review comments, an access grant, a deploy, a file or table appearing) without ending the task. " +
			"action=start runs `check` via bash every `every` seconds until it exits 0 or `for` expires, in the background; you are woken with the result. " +
			"action=list shows open waits; action=ack forgets a handled wait.",
		promptSnippet: "wait_for: poll an external condition in the background instead of giving up or sleeping",
		promptGuidelines: [
			"When a task is blocked on something external, call wait_for(action=start) with a real check command and end the turn; never hand-roll sleep loops.",
			"After a wait_for result arrives, handle it and call wait_for(action=ack).",
		],
		parameters: Type.Object({
			action: Type.Union([Type.Literal("start"), Type.Literal("list"), Type.Literal("ack")]),
			id: Type.Optional(Type.String({ description: "slug for the wait; required for start and ack" })),
			what: Type.Optional(Type.String({ description: "what you are waiting for, human text" })),
			check: Type.Optional(Type.String({ description: "shell command that exits 0 when the condition holds; required for start" })),
			every: Type.Optional(Type.Number({ description: "seconds between checks, >= 15; remote APIs >= 300" })),
			for: Type.Optional(Type.String({ description: "deadline, e.g. 20m, 2h, 1d (default 24h)" })),
		}),
		async execute(_id, params, _signal, _onUpdate, ctx) {
			const sid = sessionOf(ctx);
			const env = { ...process.env, WAIT_FOR_SESSION: sid, WAIT_FOR_BIN: BIN };
			if (params.action === "list" || params.action === "ack") {
				const args = params.action === "list" ? ["list"] : ["ack", params.id ?? ""];
				const r = spawnSync("bash", [BIN, ...args], { encoding: "utf8", env });
				return { content: [{ type: "text", text: (r.stdout + r.stderr).trim() }], details: undefined };
			}
			if (!params.id || !params.check) throw new Error("start needs id and check");
			const args = [BIN, "start", "--id", params.id, "--what", params.what ?? params.id,
				"--every", String(params.every ?? 120), "--for", params.for ?? "24h", "--", params.check];
			const logPath = path.join(os.tmpdir(), `wait-for-${sid}-${params.id}.log`);
			const log = fs.openSync(logPath, "w");
			const child = spawn("bash", args, { env, detached: true, stdio: ["ignore", log, log] });
			fs.closeSync(log);
			const readLog = () => { try { return fs.readFileSync(logPath, "utf8").trim(); } catch { return ""; } };
			child.on("exit", (code) => {
				if (code === 130 || code === 143 || !ctx.hasUI) return;
				try {
					pi.sendMessage(
						{ customType: "wait-for", content: `wait_for '${params.id}' finished (exit ${code}).\n${readLog()}`, display: true },
						{ triggerTurn: true, deliverAs: "followUp" },
					);
				} catch { /* session already gone; the settle hook reports unhandled waits on the next run */ }
			});
			child.unref();
			await new Promise((r) => setTimeout(r, 400));
			if (child.exitCode !== null && child.exitCode !== 0) throw new Error(readLog() || `wait-for exited ${child.exitCode}`);
			return { content: [{ type: "text", text: `${readLog() || `wait_for '${params.id}' started`}\nEnd your turn; you will be woken when it finishes.` }], details: undefined };
		},
	});
}
