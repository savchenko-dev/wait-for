#!/usr/bin/env python3
"""Stop / SubagentStop hook: do not let a turn end while an external wait is unhandled.

Reads the state written by wait-for.sh. Blocks when:
  - a wait is `done` or `timeout` and has not been acknowledged (result not handled),
  - a wait is `waiting` but its poller process is dead (never ran in background, got killed),
  - no wait is recorded, but the last assistant message says it is waiting on something
    external (heuristic; fires once per stop thanks to stop_hook_active).
A `waiting` entry with a live poller lets the turn end: the harness re-invokes the agent when
the background poller exits. Fails open on any error.
"""
import glob
import json
import os
import re
import sys

CFG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ROOT = os.environ.get("WAIT_FOR_STATE_DIR") or os.path.join(CFG, "state", "waits")
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
HELPER = (os.path.join(PLUGIN_ROOT, "bin", "wait-for.sh") if PLUGIN_ROOT
          else os.environ.get("WAIT_FOR_BIN") or os.path.join(CFG, "hooks", "wait-for.sh"))

WAIT_PHRASES = re.compile(
    r"(\bwait(ing)? (for|until|on)\b|\bonce (access|the|it|they)\b.*\b(appears?|granted|arrives?|respond)|"
    r"\bwhen (access|the reviewer|the comments?|the data|it)\b.*\b(appears?|granted|arrives?|lands?)|"
    r"\bcheck back\b|\bre-?check later\b|\bpoll(ing)? (it|again) later\b|\bwill (poll|check) (it )?later\b|"
    r"подожд|дожд[её]мся|как только (появ|дадут|прид|ответ)|когда (появится|дадут|придут|ответит))",
    re.IGNORECASE,
)
NO_WAIT_PHRASES = re.compile(r"wait-for\.sh|wait-for\[|poller (is )?(running|registered|started)|поллер запущен", re.IGNORECASE)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def last_assistant_text(path):
    text = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                content = (o.get("message") or {}).get("content", [])
                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if parts:
                    text = "\n".join(parts)
    except Exception:
        return ""
    return text


def block(reason):
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    session = data.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    d = os.path.join(ROOT, session)
    dead, unhandled, live = [], [], []
    for p in glob.glob(os.path.join(d, "*.json")):
        try:
            s = json.load(open(p))
        except Exception:
            continue
        st = s.get("status")
        if st == "waiting":
            (live if alive(s.get("pid", 0)) else dead).append(s)
        elif st in ("done", "timeout"):
            unhandled.append(s)
    if unhandled:
        names = ", ".join(f'{s.get("id")} ({s.get("status")})' for s in unhandled)
        return block(
            f"A wait finished but its result was not handled: {names}. Read the poller output, do the next "
            f"step of the task (or report the timeout to the user), then run `{HELPER} ack <id>`. "
            f"If you need to keep waiting, start a new `{HELPER} start` in the background.")
    if dead:
        names = ", ".join(str(s.get("id")) for s in dead)
        return block(
            f"Wait {names} is marked waiting but its poller is not running. Restart it with "
            f"`{HELPER} start --id {dead[0].get('id')} ...` via Bash with run_in_background=true, "
            f"or `{HELPER} cancel {dead[0].get('id')}` if it is no longer needed.")
    if live or data.get("stop_hook_active"):
        return
    text = last_assistant_text(data.get("transcript_path") or "")
    if text and WAIT_PHRASES.search(text) and not NO_WAIT_PHRASES.search(text):
        return block(
            "You seem to be ending the turn while waiting on an external event (access, review comments, data), "
            f"but no poller is registered. Either start `{HELPER} start --id <slug> --what '...' --every <sec> "
            "--for <duration> -- '<check command that exits 0 when ready>'` via Bash with run_in_background=true "
            "and then end the turn, or state explicitly that there is nothing to wait for and the task is done.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
