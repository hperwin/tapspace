#!/bin/bash
# new-master.sh <dir> <tapspace-name>
# Starts a NEW master agent (a normal interactive Claude Code session) in <dir>, headless in a detached tmux
# session, same claude flags and cswap account as session-relay cars. It joins TapSpace chat under <tapspace-name>.
# Called by the space's "New session" button (POST /api/agents/new, folder ~): the agent picks its project folder
# itself once Hayden says what to work on. Peek: tmux attach -t <session> (ctrl-b d).
set -uo pipefail
DIR="$1"; NAME="$2"
TMUX_BIN="$(command -v tmux || echo /opt/homebrew/bin/tmux)"
[ -x "$TMUX_BIN" ] || { echo "tmux missing"; exit 1; }
[ -d "$DIR" ] || { echo "no such folder: $DIR"; exit 1; }
UUID=$(uuidgen | tr 'A-Z' 'a-z')
BASE="$(basename "$DIR")"
TS="master-${BASE//[^A-Za-z0-9_-]/-}-${UUID:0:6}"
W="$HOME/.claude/ctx/master-${UUID:0:8}"; mkdir -p "$W"
TAP="$HOME/orchestration/tapspace/tap.py"
cat > "$W/prompt.txt" <<PROMPT
You are a new master agent Hayden started from TapSpace. You start in $DIR; when Hayden says what to work on, work in that project's folder (find it yourself; his repos live in ~/Developer and ~/Developer/SnappStats). Your TapSpace chat name is "$NAME" (use exactly this name, ignore any other suggested name). First, join the chat: arm a background listener (Bash, run_in_background) with: python3 $TAP listen --as "$NAME" --timeout 3600 . Then post one short line with: python3 $TAP reply --as "$NAME" "Ready. What should we work on?" . When Hayden says what to work on, name yourself after that work (2 to 4 plain words, it is all he sees): python3 $TAP title --as "$NAME" "<title>" . When the listener fires, answer with tap.py reply --as "$NAME" and re-arm it; follow the tapspace-chat skill. You run headless (tmux $TS), so TapSpace chat is the only way Hayden reaches you. You can start taps (cloud pilots) with the pilot-chat skill and your own agents with workflows. When your context reaches 60 percent, run the session-relay skill and keep the same chat name.
PROMPT
cat > "$W/launch.sh" <<LAUNCH
#!/bin/zsh -l
cd "$DIR"
export TAPSPACE_URL="${TAPSPACE_URL:-http://127.0.0.1:4747}"
exec claude --session-id $UUID --effort ultracode --dangerously-skip-permissions --settings '{"ultracode":true}' "\$(cat $W/prompt.txt)"
LAUNCH
chmod +x "$W/launch.sh"
"$TMUX_BIN" new-session -d -s "$TS" -x 200 -y 50 "$W/launch.sh" || { echo "TMUX-FAIL"; exit 1; }
# A folder Claude has not opened before asks "Do you trust this folder?": answer it inside tmux.
for i in $(seq 1 20); do
  sleep 1
  P="$("$TMUX_BIN" capture-pane -p -t "$TS" 2>/dev/null)"
  if grep -qi "trust" <<<"$P"; then
    # pick the Yes option whichever line the cursor starts on
    if grep -qE "❯ *[0-9]\. *Yes" <<<"$P"; then "$TMUX_BIN" send-keys -t "$TS" Enter
    else "$TMUX_BIN" send-keys -t "$TS" Down Enter; fi
    break
  fi
done
echo "MASTER-OK session=$UUID tmux=$TS"
