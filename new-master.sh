#!/bin/bash
# new-master.sh <dir> <tapspace-name> [parent] [order-file]
# parent: the lead NAME it reports to (named in its first prompt; the server stores it on the row). order-file: the
# child's work; with one, its first prompt starts it on that order at once (named and carded from it). Pass "" as
# parent to give only an order.
# Starts a NEW master agent (a normal interactive Claude Code session) in <dir>, headless in a detached tmux
# session, same claude flags and cswap account as session-relay cars. It joins TapSpace chat under <tapspace-name>.
# Called by the space's "New session" button (POST /api/agents/new, folder ~): the agent picks its project folder
# itself once Hayden says what to work on. tap.py new adds the parent and an order. Peek: tmux attach -t <session> (ctrl-b d).
set -uo pipefail
DIR="$1"; NAME="$2"; PARENT="${3:-}"; ORDER="${4:-}"
TMUX_BIN="$(command -v tmux || echo /opt/homebrew/bin/tmux)"
[ -x "$TMUX_BIN" ] || { echo "tmux missing"; exit 1; }
[ -d "$DIR" ] || { echo "no such folder: $DIR"; exit 1; }
[ -z "$ORDER" ] || [ -f "$ORDER" ] || { echo "no such order file: $ORDER"; exit 1; }
# the server picks the session id and tmux name (TAPSPACE_MASTER_UUID / _TMUX) so it can answer with them
UUID="${TAPSPACE_MASTER_UUID:-}"; [[ "$UUID" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || UUID=$(uuidgen | tr 'A-Z' 'a-z')
BASE="$(basename "$DIR")"
TS="master-${BASE//[^A-Za-z0-9_-]/-}-${UUID:0:6}"
[[ "${TAPSPACE_MASTER_TMUX:-}" =~ ^master-[A-Za-z0-9_-]+-[0-9a-f]{6}$ ]] && TS="$TAPSPACE_MASTER_TMUX"
W="$HOME/.claude/ctx/master-${UUID:0:8}"; mkdir -p "$W"
TAP="$HOME/orchestration/tapspace/tap.py"
# A plain master asks Hayden what to work on. An ordered child already has its work: it names itself and publishes
# its goal card from the order, says who started it, and starts.
if [ -z "$ORDER" ]; then
  OPEN="You are a new master agent Hayden started from TapSpace. You start in $DIR; when Hayden says what to work on, work in that project's folder (find it yourself; his repos live in ~/Developer and ~/Developer/SnappStats)."
  FIRST="Then post one short line with: python3 $TAP reply --as \"$NAME\" \"Ready. What should we work on?\" . When Hayden says what to work on, name yourself after that work (2 to 4 plain words, it is all he sees): python3 $TAP title --as \"$NAME\" \"<title>\" ."
  THEN=""
else
  OPEN="You are a new master agent${PARENT:+ $PARENT} started from TapSpace with an order, which ends this prompt. You start in $DIR; work in the project folder the order names (find it yourself when it names none; Hayden's repos live in ~/Developer and ~/Developer/SnappStats)."
  FIRST="Then name yourself after the order's work (2 to 4 plain words, it is all Hayden sees): python3 $TAP title --as \"$NAME\" \"<title>\" ."
  THEN=" Then post one short line with: python3 $TAP reply --as \"$NAME\" \"${PARENT:-TapSpace} started me on <the work, in a few plain words>.\" , and start the order right away."
fi
cat > "$W/prompt.txt" <<PROMPT
$OPEN Your TapSpace chat name is "$NAME" (use exactly this name, ignore any other suggested name). First, join the chat: arm a background listener (Bash, run_in_background) with: python3 $TAP listen --as "$NAME" --timeout 3600 . $FIRST Then publish your goal card, which TapSpace and Glint show: python3 $TAP goal --as "$NAME" "<the outcome he will see, one plain line under 90 characters>" --steps "<3 to 7 short steps in order, like Audit|Spec|Build|Review|PR open>" .$THEN Each time a step really finishes, move it with: python3 $TAP step --as "$NAME" next (add --note "<where inside the step>" when it helps). When the listener fires, answer with tap.py reply --as "$NAME" and re-arm it; follow the tapspace-chat skill. You run headless (tmux $TS), so TapSpace chat is the only way Hayden reaches you. You can start taps (cloud pilots) with the pilot-chat skill and your own agents with workflows. When your context reaches 60 percent, run the session-relay skill and keep the same chat name.
PROMPT
[ -z "$PARENT" ] || printf '\nYour lead is %s: it started you and sees your goal card, so keep it current.\n' "$PARENT" >> "$W/prompt.txt"
if [ -n "$ORDER" ]; then printf '\nYour first order%s:\n' "${PARENT:+, from $PARENT}" >> "$W/prompt.txt"; cat "$ORDER" >> "$W/prompt.txt"; fi
cat > "$W/launch.sh" <<LAUNCH
#!/bin/zsh -l
cd "$DIR"
export TAPSPACE_URL="${TAPSPACE_URL:-http://127.0.0.1:4747}"
# a fresh session: never another session's identity (tmux and the server can pass on stale ones)
unset CLAUDE_CODE_SESSION_ID CLAUDE_PID CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN CODEX_COMPANION_SESSION_ID CODEX_COMPANION_TRANSCRIPT_PATH TAPSPACE_MASTER_UUID TAPSPACE_MASTER_TMUX
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
