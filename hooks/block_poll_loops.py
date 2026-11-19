#!/usr/bin/env python3
"""PreToolUse:Bash guard: a hand-rolled poll loop (`for/while/until ... sleep N`) must go through
wait-for.sh, which records the wait for the Stop hook and has a deadline. Fails open."""
import json
import os
import re
import sys

LOOP = re.compile(r"(?:^|[;&|(\s])(?:for|while|until)\s+[^\n;]*?(?:;|\n)\s*do\b", re.S)
SLEEP = re.compile(r"(?:^|[;&|(\s])sleep\s+(\d+(?:\.\d+)?)([smhd]?)\b")
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
MIN_LOOP_SLEEP = float(os.environ.get("WAIT_FOR_MIN_LOOP_SLEEP", "3"))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
CFG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
HELPER = (os.path.join(PLUGIN_ROOT, "bin", "wait-for.sh") if PLUGIN_ROOT
          else os.environ.get("WAIT_FOR_BIN") or os.path.join(CFG, "hooks", "wait-for.sh"))

REASON = (
    "Blocked: a shell poll loop with `sleep` inside. Waiting on external state goes through the helper, "
    "so the wait is recorded and has a deadline:\n"
    f"  {HELPER} start --id <slug> --what '<what>' --every <sec> --for <duration> -- '<check cmd, exits 0 when ready>'\n"
    "run via Bash with run_in_background=true. The harness re-invokes you when it exits."
)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if "wait-for.sh" in cmd or not LOOP.search(cmd):
        return
    for m in SLEEP.finditer(cmd):
        if float(m.group(1)) * UNIT[m.group(2).lower()] >= MIN_LOOP_SLEEP:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                  "permissionDecision": "deny", "permissionDecisionReason": REASON}}))
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
