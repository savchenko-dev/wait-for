# wait-for

Make your coding agent (**Claude Code, Codex, OpenCode, pi**) finish the job even when it has
to wait for something it doesn't control: review comments on a PR, an access grant, a deploy landing, a CI
check, a file or table that will appear later, a reply in a tracker.

Without this, the agent writes *"I'll check back once the reviewer replies"* and stops.
The task is stranded until you come back to the keyboard.

With this, the agent starts a **background poller** and ends its turn. When the poller
exits, the agent is woken up, handles the result and carries on. A **stop hook** refuses to
let a turn end while a wait is unhandled, and a **tool guard** turns hand-rolled
`for … sleep` loops into the helper.

```text
agent:  the file is not there yet → wait-for.sh start --id data --every 60 --for 20m -- 'test -f data.json'
        (turn ends; poller runs in the background)
        … 3 minutes later the poller exits 0 …
agent:  (re-invoked) reads data.json, reports the answer, wait-for.sh ack data
```

## Install

| Harness | How | Wake-up mechanism |
|---|---|---|
| **Claude Code** | `/plugin install wait-for --marketplace savchenko-dev/wait-for` | `run_in_background` Bash; the harness re-invokes the agent when the poller exits |
| **Codex** | `codex plugin marketplace add savchenko-dev/wait-for`, then `codex plugin add wait-for@savchenko-dev`, then trust the hooks in `/hooks` | No background wake-up in Codex. The `Stop` hook runs in `--hold` mode: it keeps the turn open, watching the wait, and continues the turn when the poller finishes |
| **OpenCode** | `./install.sh opencode`, or copy `opencode/wait-for.mjs` into `~/.config/opencode/plugins/` and list it under `"plugin"` in `opencode.json` | Native `wait_for` tool spawns the poller; on exit the plugin prompts the session. The idle event re-checks open waits |
| **pi** | `pi install git:github.com/savchenko-dev/wait-for`, or `./install.sh pi` | Native `wait_for` tool; on exit `pi.sendMessage(…, {triggerTurn: true})`. In print mode (`-p`) the `agent_before_settle` hook holds the run instead |

Manual install for any mix of harnesses, no plugin system needed:

```bash
git clone https://github.com/savchenko-dev/wait-for
./wait-for/install.sh claude codex opencode pi     # pick the ones you use
./wait-for/install.sh claude codex opencode pi --uninstall
```

Shared files land in `~/.config/wait-for`; each harness gets its adapter and the skill.
Needs `bash` and `python3`. macOS and Linux.

## What is inside

| File | Role |
|---|---|
| `bin/wait-for.sh` | The poller. `start`, `list`, `ack`, `cancel`. State in `~/.claude/state/waits/<session>/`. |
| `hooks/stop_while_waiting.py` | The decision engine, shared by every harness. Blocks the turn when a wait finished but was not acknowledged, when a wait is open but its poller died, or when the last message says "waiting for …" and no poller exists (fires once, never loops). `--hold` keeps the turn open on harnesses without background wake-up; `--check` is the CLI the JS adapters call. |
| `hooks/block_poll_loops.py` | Denies `for/while/until … sleep N` loops and points to the helper. Same JSON contract on Claude Code and Codex; the JS adapters feed it the command. |
| `hooks/claude-hooks.json`, `hooks/codex-hooks.json` | Hook wiring for the two JSON-hook harnesses. |
| `opencode/wait-for.mjs` | OpenCode plugin (v1 API): `wait_for` tool, bash guard, idle handler, per-session env. |
| `pi/wait-for.ts` | pi extension: `wait_for` tool, bash guard, `agent_before_settle` handler. |
| `skills/wait-for/SKILL.md` | Teaches the agent when and how to use it: intervals, deadlines, acknowledgement. Loaded as a skill by all four harnesses. |

### The helper

```bash
wait-for.sh start --id pr-123-comments --what "new review comments on PR 123" \
  --every 300 --for 2d -- 'gh pr view 123 --json comments --jq ".comments|length" | grep -qvx 2'
```

- Runs the check command via `bash -c` every `--every` seconds (minimum 15).
- Exits **0** when the check exits 0 (status `done`), **2** at the `--for` deadline (status
  `timeout`). Durations: `90`, `90s`, `15m`, `2h`, `1d`. Default deadline is 24h.
- `ack <id>` forgets the wait and stops a live poller. `list` shows what is open.
- Each Claude session (and each subagent) has its own state directory, so waits never
  collide.

The agent is expected to run `start` via the Bash tool with `run_in_background: true`.
That is what makes the harness wake it up on exit.

### Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `WAIT_FOR_STATE_DIR` | `$CLAUDE_CONFIG_DIR/state/waits` | Where waits are recorded |
| `WAIT_FOR_MIN_EVERY` | `15` | Smallest allowed interval, seconds |
| `WAIT_FOR_MIN_LOOP_SLEEP` | `3` | Loops with `sleep` at or above this are denied |
| `WAIT_FOR_BIN` | `~/.claude/hooks/wait-for.sh` | Helper path shown in hook messages (manual install) |

## Does the agent remember to use it?

Measured on 2026-11-19. Claude Code subagents first, then the other harnesses on the same
delayed-file scenario. Two scenarios: a data file that
appears after 150s, and a reviewer comment file that is empty for 180s. The prompt never
mentioned the helper; the agent only had the skill in context.

| Setup | Model | Used the helper | Finished the task |
|---|---|---|---|
| skill only | haiku | file: yes · review: no, `sleep 10` loop | yes |
| skill only | sonnet | file: yes (plus a hand loop) · review: no, `sleep 10` loop | yes |
| skill only | opus | yes | yes |
| skill + loop guard | haiku | yes, 60s interval | yes |
| skill + loop guard | sonnet | yes, 60s interval | yes |

Nobody gave up or made up a value. Without the loop guard the smaller models improvised
`for … sleep 10` loops in a third of the runs; with it, every model used the helper. Idle
subagents were re-invoked when their background poller exited.

On **pi** (gpt-oss-120b, print mode) the model called `wait_for` on its first turn with a 30s
interval and a 5-minute deadline, the settle hook held the run until the file appeared, and the
model read the value and acknowledged the wait. On **OpenCode** (same model, a `serve` session
driven over HTTP) the model called `wait_for`, was prompted by the plugin when the poller
exited, acknowledged the wait and read the file. In one-shot `opencode run` the process exits
at idle, so the wake-up has nothing to prompt; use a session (TUI or `serve`). **Codex** was not available on the test machine; its hooks share the exact JSON contract
with Claude Code's, and the hold mode was exercised directly against the engine.

Known gaps: a model can still poll from `python -c … time.sleep`; nothing catches that.
Pollers freeze when the laptop sleeps, so keep it awake for long waits (`caffeinate -i` on
macOS, `systemd-inhibit` on Linux). The phrase detector in the Stop hook knows English and
Russian.

## Why not a cron / scheduled agent?

Scheduled runs restart from scratch and lose the session's context. A background poller
keeps the same session: the agent that opened the PR is the one that handles its comments.

## License

MIT
