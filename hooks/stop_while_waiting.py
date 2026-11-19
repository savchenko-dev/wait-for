#!/usr/bin/env python3
"""Decision engine for "do not end the turn while an external wait is unhandled".

Modes
  (default)         Claude Code / Codex hook: reads the Stop|SubagentStop JSON on stdin, prints
                    {"decision":"block","reason":...} or {} (Codex requires JSON on allow).
  --hold SECONDS    Codex: there is no background wake-up, so while a poller is alive the hook
                    keeps the turn open, watching the state files until a wait finishes or the
                    hold expires. Then it blocks with the result, or with "still waiting, end
                    the turn again" so the next hold starts. Bounded by the waits' deadlines.
  --check           pi / OpenCode adapters: --session ID (repeatable) --text "last assistant
                    message" [--nudged]; prints the same JSON. --nudged plays the role of
                    stop_hook_active for the phrase heuristic.

Blocks when a wait is `done`/`timeout` and not acknowledged, when a wait is `waiting` but its
poller process is dead, or when no wait exists but the last assistant message says it is waiting
on something external (once per stop). A live poller lets the turn end on harnesses that wake
the agent when a background command exits. Fails open.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

CFG = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
ROOT = os.environ.get("WAIT_FOR_STATE_DIR") or os.path.join(CFG, "state", "waits")
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.environ.get("PLUGIN_ROOT")
HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = (os.environ.get("WAIT_FOR_BIN")
          or (os.path.join(PLUGIN_ROOT, "bin", "wait-for.sh") if PLUGIN_ROOT else None)
          or (os.path.join(HERE, "..", "bin", "wait-for.sh") if os.path.exists(os.path.join(HERE, "..", "bin", "wait-for.sh")) else None)
          or os.path.join(HERE, "wait-for.sh"))
HELPER = os.path.normpath(HELPER)

WAIT_PHRASES = re.compile(
    r"(\bwait(ing)? (for|until|on)\b|\bonce (access|the|it|they)\b.*\b(appears?|granted|arrives?|respond)|"
    r"\bwhen (access|the reviewer|the comments?|the data|it)\b.*\b(appears?|granted|arrives?|lands?)|"
    r"\bcheck back\b|\bre-?check later\b|\bpoll(ing)? (it|again) later\b|\bwill (poll|check) (it )?later\b|"
    r"\bstill (pending|running|in progress|waiting|open|building|deploying)\b|\bnot (yet )?(ready|merged|landed|active|done|finished|granted|mergeable|approved)\b|"
    r"\b(is|are|remains?) pending\b|\bhas(n't| not) (reached|landed|finished|replied|answered|been granted)\b|\bno (reply|answer|approval|ship-?it) yet\b|"
    r"\b(port|rule|hole|role|access|grant|permission)\b[^.\n]{0,80}\b(not|isn't|aren't|hasn't)\b[^.\n]{0,30}\b(in|open|opened|applied|active|granted|listed|there|present)\b|"
    r"(порт|дырк|роль|доступ|правил)[^.\n]{0,60}(не (открыт|выдан|активн|пророс|примен)|пока нет|ещё нет)|"
    r"подожд|дожд[её]мся|как только (появ|дадут|прид|ответ)|когда (появится|дадут|придут|ответит)|"
    r"ещё не (готов|доехал|замерж|активн|пророс|прош|ответ|выдан|пришл)|пока (нет|не (готов|доехал|прош|ответ|выдан|замерж))|не замержен|в ожидании|ожидает (ревью|аппрув|шип|деплой|ответ))",
    re.IGNORECASE,
)
NO_WAIT_PHRASES = re.compile(
    r"wait-for\.sh|wait-for\[|wait_for\b|poller (is )?(running|registered|started)|поллер запущен|ждать нечего|нечего ждать|задача закончена|nothing (to wait|left to wait)|no external event|task is (done|complete|finished)"
    r"|\b(sub-?agents?|teammates?|researchers?|workers?|background (task|agent|command|scan|job))\b"
    r"|\b(report|result)s? from (the )?(sub-?agents?|teammates?|researchers?)",
    re.IGNORECASE,
)


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


def scan(sessions):
    dead, unhandled, live = [], [], []
    for session in sessions:
        for p in glob.glob(os.path.join(ROOT, session, "*.json")):
            try:
                s = json.load(open(p))
            except Exception:
                continue
            st = s.get("status")
            if st == "waiting":
                (live if alive(s.get("pid", 0)) else dead).append(s)
            elif st in ("done", "timeout"):
                unhandled.append(s)
    return dead, unhandled, live


def decide(sessions, text, nudged, hold=0, start_hint=""):
    dead, unhandled, live = scan(sessions)
    if live and hold > 0 and not unhandled and not dead:
        end = time.time() + hold
        while time.time() < end:
            time.sleep(min(5, max(1, end - time.time())))
            dead, unhandled, live = scan(sessions)
            if unhandled or dead or not live:
                break
        else:
            names = ", ".join(str(s.get("id")) for s in live)
            return {"decision": "block", "reason":
                    f"Still waiting on {names}; nothing has happened yet. Do not poll yourself: "
                    f"just end your turn again and the wait continues."}
    if unhandled:
        names = ", ".join(f'{s.get("id")} ({s.get("status")})' for s in unhandled)
        return {"decision": "block", "reason":
                f"A wait finished but its result was not handled: {names}. Check the condition yourself "
                f"(re-run the check command or read the resource), do the next step of the task (or report "
                f"the timeout to the user), then run `{HELPER} ack <id>`. If you need to keep waiting, start "
                f"a new wait.{start_hint}"}
    if dead:
        names = ", ".join(str(s.get("id")) for s in dead)
        return {"decision": "block", "reason":
                f"Wait {names} is marked waiting but its poller is not running. Restart it "
                f"({HELPER} start --id {dead[0].get('id')} ...){start_hint}, or `{HELPER} cancel "
                f"{dead[0].get('id')}` if it is no longer needed."}
    if live or nudged:
        return {}
    if text and WAIT_PHRASES.search(text) and not NO_WAIT_PHRASES.search(text):
        return {"decision": "block", "reason":
                "You seem to be ending the turn while waiting on an external event (access, review comments, "
                f"data), but no poller is registered. Either start one: `{HELPER} start --id <slug> --what '...' "
                f"--every <sec> --for <duration> -- '<check command that exits 0 when ready>'`{start_hint}, then end "
                "the turn; or state explicitly that there is nothing to wait for and the task is done."}
    return {}


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--hold", type=int, default=0)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--session", action="append", default=[])
    ap.add_argument("--text", default="")
    ap.add_argument("--nudged", action="store_true")
    ap.add_argument("--start-hint", default="")
    a = ap.parse_args()
    if a.check:
        sessions = a.session or ["nosession"]
        print(json.dumps(decide(sessions, a.text, a.nudged, a.hold, a.start_hint), ensure_ascii=False))
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("{}")
        return
    session = data.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "nosession"
    text = data.get("last_assistant_message") or last_assistant_text(data.get("transcript_path") or "")
    hold = a.hold if not data.get("stop_hook_active") or a.hold else 0
    nudged = bool(data.get("stop_hook_active")) and not a.hold
    start_hint = ""
    if a.hold:
        start_hint = " (run it detached: append `>/dev/null 2>&1 &`; the Stop hook keeps the session alive until it finishes)"
    else:
        start_hint = " via Bash with run_in_background=true"
    print(json.dumps(decide([session], text, nudged, hold, start_hint), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
    sys.exit(0)
