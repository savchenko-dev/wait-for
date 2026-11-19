#!/usr/bin/env bash
# Manual install for people who do not use Claude Code plugins.
# Copies the helper and hooks into $CLAUDE_CONFIG_DIR (default ~/.claude) and registers the
# hooks in settings.json. Re-running is safe. `install.sh --uninstall` removes them.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CFG/hooks" "$CFG/skills/wait-for"
UNINSTALL=0; [ "${1:-}" = "--uninstall" ] && UNINSTALL=1
if [ "$UNINSTALL" = 0 ]; then
  cp "$HERE/bin/wait-for.sh" "$HERE/hooks/stop_while_waiting.py" "$HERE/hooks/block_poll_loops.py" "$CFG/hooks/"
  chmod +x "$CFG/hooks/wait-for.sh" "$CFG/hooks/"*.py
  sed "s#\${CLAUDE_PLUGIN_ROOT}/bin/wait-for.sh#$CFG/hooks/wait-for.sh#g" "$HERE/skills/wait-for/SKILL.md" > "$CFG/skills/wait-for/SKILL.md"
else
  rm -f "$CFG/hooks/wait-for.sh" "$CFG/hooks/stop_while_waiting.py" "$CFG/hooks/block_poll_loops.py" "$CFG/skills/wait-for/SKILL.md"
  rmdir "$CFG/skills/wait-for" 2>/dev/null || true
fi
python3 - "$CFG/settings.json" "$UNINSTALL" <<'PY'
import json, os, sys
path, uninstall = sys.argv[1], sys.argv[2] == "1"
d = json.load(open(path)) if os.path.exists(path) else {}
hooks = d.setdefault("hooks", {})
cfgvar = '"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/'
stop = {"type": "command", "command": f'python3 {cfgvar}stop_while_waiting.py"', "timeout": 10,
        "statusMessage": "open external waits?"}
pre = {"type": "command", "command": f'python3 {cfgvar}block_poll_loops.py"', "timeout": 10}
def strip(event):
    kept = []
    for g in hooks.get(event, []):
        g["hooks"] = [h for h in g.get("hooks", []) if "stop_while_waiting" not in h.get("command", "") and "block_poll_loops" not in h.get("command", "")]
        if g["hooks"]:
            kept.append(g)
    if kept: hooks[event] = kept
    elif event in hooks: del hooks[event]
for ev in ("Stop", "SubagentStop", "PreToolUse"):
    strip(ev)
if not uninstall:
    hooks.setdefault("Stop", []).insert(0, {"hooks": [stop]})
    hooks.setdefault("SubagentStop", []).insert(0, {"hooks": [stop]})
    hooks.setdefault("PreToolUse", []).append({"matcher": "Bash", "hooks": [pre]})
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False); f.write("\n")
os.replace(tmp, path)
print(("removed from " if uninstall else "registered in ") + path)
PY
[ "$UNINSTALL" = 0 ] && echo "installed: $CFG/hooks/wait-for.sh, hooks, skills/wait-for. Restart Claude Code to pick up the hooks." || echo "uninstalled."
