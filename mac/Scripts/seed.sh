#!/bin/bash
# Starts a SCRATCH TapSpace server and seeds it with real data, so the app can
# be driven (decide, chat) without ever touching the live server on :4747.
#
#   Scripts/seed.sh <port> [data-dir]     start + seed; prints the PID and the app command
#   Scripts/seed.sh --stop <data-dir>     stop the server that seed.sh started there (by PID)
#
# Seeds: four cards made from REAL screenshots in ~/orchestration/tapspace/data/shots
# (one with a v2 update so the change highlights show, one video, one sent back
# with notes), and two agents in the Chat tab: "Claude · main" mid-conversation
# and "Claude · fantasy" thinking on an unanswered question. Taps are read live
# from GitHub by the scratch server (read-only).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SHOTS="${TAPSPACE_SHOTS:-$HOME/orchestration/tapspace/data/shots}"

if [[ "${1:-}" == "--stop" ]]; then
    DATA="${2:?usage: seed.sh --stop <data-dir>}"
    PID="$(cat "$DATA/server.pid" 2>/dev/null || true)"
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" && echo "stopped scratch server $PID"
    else
        echo "no scratch server running for $DATA"
    fi
    rm -f "$DATA/server.pid"
    exit 0
fi

PORT="${1:?usage: seed.sh <port> [data-dir]}"
if [[ "$PORT" == "4747" ]]; then
    echo "refusing: 4747 is the live server. Pick a scratch port." >&2
    exit 2
fi
DATA="${2:-${TMPDIR:-/tmp}/tapspace-scratch-$PORT}"
URL="http://127.0.0.1:$PORT"
export TAPSPACE_URL="$URL"

if curl -fs -o /dev/null "$URL/api/state"; then
    echo "port $PORT is already serving. Stop it first (seed.sh --stop <its data dir>)." >&2
    exit 1
fi
for f in 21d0610109/1.png 21d0610109/2.png 8d1e93788e/1.png e63e4daa8a/1.png f6e60f3b14/2.mp4; do
    [[ -f "$SHOTS/$f" ]] || { echo "missing real screenshot $SHOTS/$f (set TAPSPACE_SHOTS)" >&2; exit 1; }
done

rm -rf "$DATA"
mkdir -p "$DATA"
TAPSPACE_PORT="$PORT" TAPSPACE_DATA="$DATA" nohup python3 "$REPO/server.py" >"$DATA/server.log" 2>&1 &
PID=$!
echo "$PID" >"$DATA/server.pid"
for _ in $(seq 1 50); do
    curl -fs -o /dev/null "$URL/api/state" && break
    sleep 0.2
done
curl -fs -o /dev/null "$URL/api/state" || { echo "server did not come up; see $DATA/server.log" >&2; exit 1; }

tap() { python3 "$REPO/tap.py" "$@"; }
# Hayden's side of a chat, on the SCRATCH server only (the port guard above).
say() {
    python3 - "$URL" "$1" "$2" <<'PY'
import json, sys, urllib.request
url, agent, body = sys.argv[1:4]
req = urllib.request.Request(url + "/api/chat", data=json.dumps({"agent": agent, "body": body}).encode(),
                             headers={"content-type": "application/json"})
print(json.loads(urllib.request.urlopen(req, timeout=10).read())["ok"])
PY
}
decide_change() {
    python3 - "$URL" "$1" "$2" <<'PY'
import json, sys, urllib.request
url, tid, msg = sys.argv[1:4]
req = urllib.request.Request(url + "/api/decide", data=json.dumps({"id": tid, "action": "change", "message": msg}).encode(),
                             headers={"content-type": "application/json"})
print(json.loads(urllib.request.urlopen(req, timeout=10).read()).get("ok"))
PY
}

CAMPAIGN=2026-10-12-fantasy-p0
echo "==> Cards"
EXPERT=$(tap post --agent L3k-ios --campaign "$CAMPAIGN" --title "An expert's page" \
    --note "Tapping an expert's card zooms into their page: picture, name, and their takes this week." \
    --image "$SHOTS/21d0610109/1.png")
tap update "$EXPERT" --image "$SHOTS/21d0610109/2.png" \
    --note "v2: the expert pages now come from the server's recorded experts list." >/dev/null
NBA=$(tap post --agent L3n-ios --campaign "$CAMPAIGN" \
    --title "NBA weekly league: a card with no move no longer says to make moves" \
    --image "$SHOTS/8d1e93788e/1.png")
INJ=$(tap post --agent L3m-ios --campaign "$CAMPAIGN" \
    --title "NBA weekly league: Injuries no longer says to move an Out player" \
    --note "Real Sleeper Lock-In league, the Injuries card in Analyze." \
    --image "$SHOTS/e63e4daa8a/1.png")
VID=$(tap post --agent L3k-ios --campaign "$CAMPAIGN" \
    --title "Tap Expert takes, open an expert, go back (real speed)" \
    --note "1.0x on a slow network." --video "$SHOTS/f6e60f3b14/2.mp4")
decide_change "$INJ" "Close. Tighten the spacing under the header and keep the player names on one line." >/dev/null
echo "    $EXPERT (v2)  $NBA  $INJ (sent back with notes)  $VID (video)"

echo "==> Agents"
(cd "$REPO/mac" && tap listen --as "Claude · main" --timeout 2 >/dev/null || true)
(cd "$HOME/Developer" 2>/dev/null && tap listen --as "Claude · fantasy" --timeout 2 >/dev/null || true)
tap reply --as "Claude · fantasy" "Expert takes v2 is up on the board. The expert pages now load from the server's recorded list, so the pictures match the people." >/dev/null
sleep 1
say "Claude · main" "How is the Mac app coming along?" >/dev/null
sleep 1
tap reply --as "Claude · main" "The **Visual** tab is up: cards spring into the grid, and Approve draws its check before the card collapses like liquid. The pill is next." >/dev/null
sleep 1
say "Claude · main" "Make the pill feel exactly like SnappWAU." >/dev/null
sleep 1
tap reply --as "Claude · main" $'On it. Same motion language:\n- the `Spring` presets (spread 300/25 out, retract 330/34 back)\n- width leads height on the way out\n- the droplet wobbles on a metaball neck\n\nI will post a recording when it lands.' >/dev/null
sleep 1
say "Claude · fantasy" "Does it handle a league with no matched experts?" >/dev/null
echo "    Claude · main (here), Claude · fantasy (thinking)"

cat <<EOF

Scratch TapSpace is up: $URL  (PID $PID, data $DATA)
Run the app on it:
  TAPSPACE_URL=$URL swift run TapSpace
Stop it:
  $0 --stop $DATA
EOF
