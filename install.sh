#!/usr/bin/env bash
# Manual install (no plugin system needed).
#   install.sh [claude] [codex] [opencode] [pi] [--uninstall]      default: claude
# Shared files go to $WAIT_FOR_HOME (default ~/.config/wait-for); each harness gets its adapter:
#   claude    hooks registered in $CLAUDE_CONFIG_DIR/settings.json, skill in skills/wait-for
#   codex     hooks merged into $CODEX_HOME/hooks.json (Stop in --hold mode), skill in ~/.agents/skills
#   opencode  plugin copied to ~/.config/opencode/plugins/, skill in ~/.config/opencode/skills
#   pi        extension copied to ~/.pi/agent/extensions/, skill in ~/.agents/skills
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WFH="${WAIT_FOR_HOME:-$HOME/.config/wait-for}"
UN=0; TARGETS=()
for a in "$@"; do case "$a" in --uninstall) UN=1;; claude|codex|opencode|pi) TARGETS+=("$a");; *) echo "unknown: $a" >&2; exit 64;; esac; done
[ ${#TARGETS[@]} -gt 0 ] || TARGETS=(claude)

if [ "$UN" = 0 ]; then
  mkdir -p "$WFH/bin" "$WFH/hooks"
  cp "$HERE/bin/wait-for.sh" "$WFH/bin/"; cp "$HERE/hooks/"*.py "$WFH/hooks/"; chmod +x "$WFH/bin/wait-for.sh" "$WFH/hooks/"*.py
fi

skill_to() { # $1 dir, $2 helper path shown in the skill
  if [ "$UN" = 1 ]; then rm -rf "$1"; return; fi
  mkdir -p "$1"; sed "s#\${CLAUDE_PLUGIN_ROOT}/bin/wait-for.sh#$2#g" "$HERE/skills/wait-for/SKILL.md" > "$1/SKILL.md"
}

merge_hooks() { # $1 settings path, $2 python expr for hold arg ("" or " --hold 3300"), $3 plugin-root var
  python3 - "$1" "$UN" "$WFH" "$2" <<'PY'
import json, os, sys
path, un, wfh, hold = sys.argv[1], sys.argv[2] == "1", sys.argv[3], sys.argv[4]
d = json.load(open(path)) if os.path.exists(path) else {}
hooks = d.setdefault("hooks", {})
def strip(ev):
    kept = []
    for g in hooks.get(ev, []):
        g["hooks"] = [h for h in g.get("hooks", []) if "stop_while_waiting" not in h.get("command", "") and "block_poll_loops" not in h.get("command", "")]
        if g["hooks"]: kept.append(g)
    if kept: hooks[ev] = kept
    elif ev in hooks: del hooks[ev]
for ev in ("Stop", "SubagentStop", "PreToolUse"): strip(ev)
if not un:
    stop = {"type": "command", "command": f'python3 "{wfh}/hooks/stop_while_waiting.py"{hold}', "timeout": 3600 if hold else 10}
    pre = {"type": "command", "command": f'python3 "{wfh}/hooks/block_poll_loops.py"', "timeout": 10}
    hooks.setdefault("Stop", []).insert(0, {"hooks": [stop]})
    hooks.setdefault("SubagentStop", []).insert(0, {"hooks": [dict(stop)]})
    hooks.setdefault("PreToolUse", []).append({"matcher": "Bash", "hooks": [pre]})
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w") as f: json.dump(d, f, indent=2, ensure_ascii=False); f.write("\n")
os.replace(tmp, path)
print(("removed from " if un else "registered in ") + path)
PY
}

for t in "${TARGETS[@]}"; do
  case "$t" in
    claude)
      CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
      merge_hooks "$CFG/settings.json" ""
      skill_to "$CFG/skills/wait-for" "$WFH/bin/wait-for.sh";;
    codex)
      merge_hooks "${CODEX_HOME:-$HOME/.codex}/hooks.json" " --hold 3300"
      skill_to "$HOME/.agents/skills/wait-for" "$WFH/bin/wait-for.sh"
      [ "$UN" = 1 ] || echo "codex: open /hooks inside Codex and trust the two hooks.";;
    opencode)
      OC="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
      if [ "$UN" = 1 ]; then rm -f "$OC/plugins/wait-for.mjs"; else
        mkdir -p "$OC/plugins"; sed "s#process.env.WAIT_FOR_HOME ||#process.env.WAIT_FOR_HOME || \"$WFH\" ||#" "$HERE/opencode/wait-for.mjs" > "$OC/plugins/wait-for.mjs"; fi
      skill_to "$OC/skills/wait-for" "the wait_for tool";;
    pi)
      PI="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
      if [ "$UN" = 1 ]; then rm -f "$PI/extensions/wait-for.ts"; else
        mkdir -p "$PI/extensions"; sed "s#process.env.WAIT_FOR_HOME ||#process.env.WAIT_FOR_HOME || \"$WFH\" ||#" "$HERE/pi/wait-for.ts" > "$PI/extensions/wait-for.ts"; fi
      skill_to "$HOME/.agents/skills/wait-for" "the wait_for tool";;
  esac
  echo "$t: $([ "$UN" = 1 ] && echo uninstalled || echo installed)"
done
[ "$UN" = 1 ] && rm -rf "$WFH" || echo "shared files in $WFH. Restart the agent to pick up hooks."
