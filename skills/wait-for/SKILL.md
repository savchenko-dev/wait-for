---
name: wait-for
description: Blocked on something external (PR review comments, an access grant, a deploy, a CI check, a file or table that will appear later, a reply in a tracker)? Do not end the turn with "I'll wait". Start a background poller with wait-for.sh, and the harness wakes you when the condition holds. Use whenever a task cannot proceed until an external event happens.
---

# Wait for an external event instead of ending the turn

An agent that writes "I'll check back once the reviewer replies" and stops leaves the task
stranded until the user returns. The harness can wait for you: a background command re-invokes
the agent when it exits. Use that.

## How

1. Write a check command that exits 0 only when you can proceed. A real probe, not `true`:
   `curl -sf` on the resource, an API call compared against a count you saved before starting,
   `test -f`, a SQL count, `gh pr view --json comments --jq 'length' | grep -qx 3`.
2. Start the poller via Bash with `run_in_background: true`:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/bin/wait-for.sh start --id pr-123-comments --what "new review comments on PR 123" \
     --every 300 --for 2d -- '<check command>'
   ```

3. End the turn, saying what you are waiting for: condition, interval, deadline.
4. When it exits (0 = condition met, 2 = deadline) you are re-invoked. Read its output, do the
   next step, then run `${CLAUDE_PLUGIN_ROOT}/bin/wait-for.sh ack <id>`. On a timeout, tell the
   user and stop; never poll forever.

## Rules

- Intervals: remote APIs and trackers 300s or more, access or permission checks 600s or more,
  local files and deploy status 60s or more. The helper refuses anything under 15s.
- Every wait has a deadline. Default 24h; pick what the task really allows.
- No hand-rolled `for … sleep` loops and no blind long `sleep`: hooks deny them, and they leave
  no record and no deadline.
- One `start` per condition, distinct `--id`s. `wait-for.sh list` shows what is open.
- The Stop hook refuses to end a turn while a wait is finished but not acknowledged, or while a
  wait is open but its poller is dead.
- Long waits need the machine awake (`caffeinate` on macOS, `systemd-inhibit` on Linux).
