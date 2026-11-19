#!/usr/bin/env bash
# wait-for: block until an external condition holds, and record the wait so the Stop hook
# can refuse to end the agent's turn while it is unhandled.
#
#   wait-for.sh start --id <slug> --what "<human text>" [--every 120] [--for 24h] -- <check cmd...>
#       runs <check cmd> via `bash -c` every --every seconds until it exits 0 (-> exit 0, status done)
#       or the --for deadline passes (-> exit 2, status timeout). Run it in the background.
#   wait-for.sh list                  waits of this session
#   wait-for.sh ack <slug>            the result was handled; forget the wait (stops a live poller)
#   wait-for.sh cancel <slug>         same as ack, for a wait you no longer need
#
# State: $WAIT_FOR_STATE_DIR/<session>/<slug>.json
#   WAIT_FOR_STATE_DIR  default ${CLAUDE_CONFIG_DIR:-~/.claude}/state/waits
#   session             $WAIT_FOR_SESSION, else $CLAUDE_CODE_SESSION_ID, else "nosession"
set -u
CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ROOT="${WAIT_FOR_STATE_DIR:-$CFG/state/waits}"
SESSION="${WAIT_FOR_SESSION:-${CLAUDE_CODE_SESSION_ID:-nosession}}"
DIR="$ROOT/$SESSION"
MIN_EVERY="${WAIT_FOR_MIN_EVERY:-15}"
mkdir -p "$DIR"

die() { echo "wait-for: $*" >&2; exit 64; }

to_seconds() {
  local v="$1"
  case "$v" in
    *h) echo $(( ${v%h} * 3600 ));;
    *m) echo $(( ${v%m} * 60 ));;
    *d) echo $(( ${v%d} * 86400 ));;
    *s) echo "${v%s}";;
    ''|*[!0-9]*) die "bad duration: $v (use 90, 90s, 15m, 2h, 1d)";;
    *) echo "$v";;
  esac
}

write_state() {
  python3 - "$FILE" "$@" <<'PY'
import json, sys, os, time
path = sys.argv[1]
d = {}
if os.path.exists(path):
    try: d = json.load(open(path))
    except Exception: d = {}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    d[k] = int(v) if v.lstrip("-").isdigit() else v
d["updated"] = int(time.time())
tmp = path + ".tmp"
json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
os.replace(tmp, path)
PY
}

pid_of() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("pid",0))' "$1" 2>/dev/null || echo 0; }

cmd_start() {
  local id="" what="" every=120 for_="24h"
  while [ $# -gt 0 ]; do
    case "$1" in
      --id) id="$2"; shift 2;;
      --what) what="$2"; shift 2;;
      --every) every="$(to_seconds "$2")"; shift 2;;
      --for) for_="$2"; shift 2;;
      --) shift; break;;
      *) die "unknown option $1";;
    esac
  done
  [ -n "$id" ] || die "--id is required"
  [ $# -gt 0 ] || die "check command is required after --"
  [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || die "--id must be a slug (letters, digits, . _ -)"
  [ "$every" -ge "$MIN_EVERY" ] || die "--every below ${MIN_EVERY}s hammers the remote side"
  local check="$*" deadline=$(( $(date +%s) + $(to_seconds "$for_") ))
  FILE="$DIR/$id.json"
  if [ -f "$FILE" ]; then
    local old; old=$(pid_of "$FILE")
    [ "$old" -gt 0 ] && kill -0 "$old" 2>/dev/null && die "wait '$id' already has a live poller (pid $old); ack or cancel it first"
  fi
  write_state "id=$id" "what=$what" "cmd=$check" "every=$every" "started=$(date +%s)" \
    "deadline=$deadline" "pid=$$" "status=waiting" "polls=0" "last_exit=-1"
  echo "wait-for[$id]: polling every ${every}s until $(date -r "$deadline" '+%Y-%m-%d %H:%M' 2>/dev/null || date -d "@$deadline" '+%Y-%m-%d %H:%M'): $what"
  local polls=0 rc
  trap 'write_state status=cancelled; exit 130' INT TERM
  while :; do
    polls=$((polls + 1))
    [ -f "$FILE" ] || exit 130
    bash -c "$check" >/dev/null 2>&1; rc=$?
    write_state "polls=$polls" "last_exit=$rc" "last_check=$(date +%s)"
    if [ "$rc" -eq 0 ]; then
      write_state status=done
      echo "wait-for[$id]: condition met after $polls polls. Handle the result, then run: wait-for.sh ack $id"
      exit 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      write_state status=timeout
      echo "wait-for[$id]: deadline passed after $polls polls (last exit $rc). Escalate to the user, then: wait-for.sh ack $id"
      exit 2
    fi
    sleep "$every"
  done
}

cmd_list() {
  python3 - "$DIR" <<'PY'
import json, os, sys, time, glob
d = sys.argv[1]
rows = []
for p in sorted(glob.glob(os.path.join(d, "*.json"))):
    try: s = json.load(open(p))
    except Exception: continue
    alive = False
    try: os.kill(int(s.get("pid", 0)), 0); alive = True
    except Exception: pass
    age = int(time.time()) - int(s.get("started", time.time()))
    rows.append(f'{str(s.get("id") or os.path.basename(p)[:-5]):24} {str(s.get("status") or "?"):9} '
                f'poller={"alive" if alive else "dead":5} polls={s.get("polls",0):<4} age={age//60}m  {s.get("what","")}')
print("\n".join(rows) if rows else "no waits")
PY
}

cmd_ack() {
  [ -n "${1:-}" ] || die "slug required"
  local f="$DIR/$1.json" pid=0
  [ -f "$f" ] && pid=$(pid_of "$f")
  if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null; sleep 0.2; fi
  rm -f "$f" && echo "wait-for[$1]: acknowledged"
}

case "${1:-}" in
  start) shift; cmd_start "$@";;
  list) cmd_list;;
  ack|cancel) shift; cmd_ack "$@";;
  *) sed -n '2,15p' "$0" >&2; exit 64;;
esac
