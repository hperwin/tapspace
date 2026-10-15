#!/bin/bash
# Starts a SCRATCH TapSpace server and seeds it, so the app (and the web page, and Glint) can be
# driven and rendered without ever touching the live server on :4747.
#
#   Scripts/seed.sh <port> [data-dir]          start + seed (below); prints the PID and how to render
#   Scripts/seed.sh --link <data-dir>          the two seeded masters exchange one message; the link
#                                              stays active for 10 minutes, so run it right before a render
#   Scripts/seed.sh --copy <port> [data-dir]   start on a COPY of the live cards, chat and agents
#   Scripts/seed.sh --stop <data-dir>          stop the server seed.sh started there (by PID)
#
# Seeds: cards made from REAL screenshots in ~/orchestration/tapspace/data/shots (one with a v2
# update so the change highlights show, two short clips made from them, two sent back with notes),
# and two masters in the Chat tab: "Mac app polish" mid-conversation and "Expert takes v2" thinking
# on an unanswered question. Each master owns its cards (by the session that posts them, or --owner)
# and publishes a goal card. Taps are read live from GitHub by the scratch server (read-only).
#
# A scratch server never pushes to a phone (it has no APNs keys), a seeded one reads only its own
# Claude Code folder, and seed.sh only ever wipes a folder it made itself.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
LIVE="$HOME/orchestration/tapspace/data"
SHOTS="${TAPSPACE_SHOTS:-$LIVE/shots}"
M="Claude · main"
F="Claude · fantasy"

# A folder seed.sh may wipe: new, empty, or one it marked itself. Never anything else.
fresh() {
    if [[ -e "$1" && ! -f "$1/.tapspace-scratch" && -n "$(ls -A "$1" 2>/dev/null)" ]]; then
        echo "refusing: $1 is not empty and was not made by seed.sh. Give a new folder." >&2
        exit 2
    fi
    rm -rf "$1"
    mkdir -p "$1"
    : >"$1/.tapspace-scratch"
}

scratch_port() {
    [[ "$1" =~ ^[0-9]+$ ]] || { echo "not a port: $1" >&2; exit 2; }
    if [[ "$1" == "4747" ]]; then
        echo "refusing: 4747 is the live server. Pick a scratch port." >&2
        exit 2
    fi
    if curl -fs -o /dev/null "http://127.0.0.1:$1/api/state"; then
        echo "port $1 is already serving. Stop it first (seed.sh --stop <its data dir>)." >&2
        exit 1
    fi
}

# This branch's server.py on a scratch port. No APNs keys (that folder does not exist), so it never
# pushes to a phone. TAPSPACE_CLAUDE_DIR names the Claude Code folder its link detection reads
# (unset: this Mac's own, read-only).
serve() {   # serve <port> <data-dir> [claude-dir]
    if [[ -n "${3:-}" ]]; then export TAPSPACE_CLAUDE_DIR="$3"; else unset TAPSPACE_CLAUDE_DIR; fi
    TAPSPACE_PORT="$1" TAPSPACE_DATA="$2" TAPSPACE_APNS_DIR="$2/no-apns" \
        nohup python3 "$REPO/server.py" >"$2/server.log" 2>&1 &
    echo "$!" >"$2/server.pid"
    for _ in $(seq 1 50); do
        curl -fs -o /dev/null "http://127.0.0.1:$1/api/state" && return 0
        sleep 0.2
    done
    echo "server did not come up; see $2/server.log" >&2
    exit 1
}

# The seeded server's own Claude Code folder. Per seeded master: the registry file Claude Code keeps
# for a running session, and a transcript whose first line joins the chat (tap.py listen --as NAME).
# `link` appends one cross-session message the way Claude Code writes it, both halves joined by
# msg_id: the sender's SendMessage call and its result, the receiver's delivered message.
claude_seed() {   # claude_seed init|link <claude-dir> <main session> <fantasy session>
    python3 - "$@" "$REPO/tap.py" "$M" "$F" <<'PY'
import json, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

mode, cd, sid_m, sid_f, tap, name_m, name_f = sys.argv[1:8]
cd = Path(cd)
seats = {sid_m: (90001, "scratch-main", name_m), sid_f: (90002, "scratch-fantasy", name_f)}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def add(sid, *rows):
    f = cd / "projects" / "-scratch" / (sid + ".jsonl")
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        for r in rows:   # compact JSON, as Claude Code writes it
            fh.write(json.dumps(dict(r, sessionId=sid), separators=(",", ":"), ensure_ascii=False) + "\n")


now = time.time()
if mode == "init":
    (cd / "sessions").mkdir(parents=True, exist_ok=True)
    for sid, (pid, peer, name) in seats.items():
        (cd / "sessions" / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "sessionId": sid, "name": peer, "startedAt": int((now - 60) * 1000), "kind": "interactive"}))
        add(sid, {"type": "assistant", "timestamp": iso(now), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:24], "name": "Bash",
             "input": {"command": f'python3 {tap} listen --as "{name}" --timeout 3600'}}]}})
else:
    mid, call = str(uuid.uuid4()), "toolu_" + uuid.uuid4().hex[:24]
    pid_m, peer_m, _ = seats[sid_m]
    peer_f = seats[sid_f][1]
    ask = "Is the expert feed live yet?"
    add(sid_m,
        {"type": "assistant", "timestamp": iso(now), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": call, "name": "SendMessage",
             "input": {"to": peer_f, "summary": ask, "message": ask + " The Mac panel reads it next.",
                       "type": "message", "recipient": peer_f, "recipient_kind": "name"}}]}},
        {"type": "user", "timestamp": iso(now + 0.2), "message": {"role": "user", "content": [
            {"tool_use_id": call, "type": "tool_result", "content": [{"type": "text", "text": "{\"success\":true}"}]}]},
         "toolUseResult": {"success": True, "message": "sent", "msg_id": mid}})
    add(sid_f,
        {"type": "user", "timestamp": iso(now + 0.4), "isMeta": True, "turnOrigin": "peer",
         "message": {"role": "user", "content": "Another Claude session sent a message."},
         "origin": {"kind": "peer", "from": f"uds:/tmp/cc-socks/{pid_m}.sock", "verifiedPeerPid": pid_m,
                    "msg_id": mid, "name": peer_m}})
PY
}

if [[ "${1:-}" == "--stop" ]]; then
    DATA="${2:?usage: seed.sh --stop <data-dir>}"
    PID="$(cat "$DATA/server.pid" 2>/dev/null || true)"
    if [[ ! -f "$DATA/.tapspace-scratch" ]]; then
        echo "not a seed.sh folder: $DATA (server.pid ${PID:-none}). Stop that server by its PID if it is yours." >&2
        exit 2
    fi
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" && echo "stopped scratch server $PID"
    else
        echo "no scratch server running for $DATA"
    fi
    rm -f "$DATA/server.pid"
    exit 0
fi

if [[ "${1:-}" == "--link" ]]; then
    DATA="${2:?usage: seed.sh --link <data-dir>}"
    [[ -f "$DATA/.tapspace-scratch" && -f "$DATA/seed.env" ]] || { echo "not a seeded scratch folder: $DATA" >&2; exit 2; }
    . "$DATA/seed.env"
    claude_seed link "$DATA/claude" "$SID_M" "$SID_F"
    python3 - "$URL" "$M" "$F" <<'PY'
import json, sys, time, urllib.request
url, m, f = sys.argv[1:4]
end = time.time() + 20
while time.time() < end:
    try:
        d = json.loads(urllib.request.urlopen(url + "/api/agents", timeout=5).read())
    except Exception:
        d = {}
    title = {r.get("name"): r.get("title") or "Claude" for r in d.get("agents") or []}
    for l in d.get("links") or []:
        if {l.get("a"), l.get("b")} == {m, f} and l.get("active"):
            to = l["b"] if l.get("from") == l.get("a") else l["a"]
            print(f"    link: {title.get(l['from'])} -> {title.get(to)}, {l.get('summary')}")
            sys.exit(0)
    time.sleep(1)
sys.exit("    no active link after 20 s: the server did not read its scratch Claude folder (TAPSPACE_CLAUDE_DIR)")
PY
    exit 0
fi

if [[ "${1:-}" == "--copy" ]]; then
    PORT="${2:?usage: seed.sh --copy <port> [data-dir]}"
    scratch_port "$PORT"
    DATA="${3:-${TMPDIR:-/tmp}/tapspace-copy-$PORT}"
    [[ -f "$LIVE/tickets.json" ]] || { echo "no live data at $LIVE" >&2; exit 1; }
    fresh "$DATA"
    python3 - "$LIVE" "$DATA" <<'PY'
import json, shutil, sys
from pathlib import Path
live, out = Path(sys.argv[1]), Path(sys.argv[2])
# What the server reads, copied (never moved). Never the phone's token, pairing route or push
# devices, nor logs, backups or archives.
for name in ("tickets.json", "agents.json", "chat.jsonl", "chat-seq", "INBOX.jsonl",
             "sessions.json", "links.jsonl", "tap-via.json"):
    if (live / name).is_file():
        shutil.copy2(live / name, out / name)
tickets = [t for t in json.loads((out / "tickets.json").read_text())["tickets"] if not t.get("archived")]
for t in tickets:
    if (live / "shots" / t["id"]).is_dir():
        shutil.copytree(live / "shots" / t["id"], out / "shots" / t["id"])
if (live / "chat-media").is_dir():
    shutil.copytree(live / "chat-media", out / "chat-media")
agents = json.loads((out / "agents.json").read_text()) if (out / "agents.json").is_file() else {}
print(f"    copied {len(tickets)} live cards, {len(agents)} agents and the chat")
PY
    serve "$PORT" "$DATA"
    cat <<EOF

Scratch TapSpace on a COPY of the live data: http://127.0.0.1:$PORT  (PID $(cat "$DATA/server.pid"), data $DATA)
Links come from this Mac's Claude Code transcripts (read-only), between the real masters.
Render the app on it (offscreen, never takes focus):
  TAPSPACE_URL=http://127.0.0.1:$PORT .build/debug/TapSpace --snapshot <dir>
Stop it:
  $0 --stop $DATA
EOF
    exit 0
fi

PORT="${1:?usage: seed.sh <port> [data-dir]}"
scratch_port "$PORT"
DATA="${2:-${TMPDIR:-/tmp}/tapspace-scratch-$PORT}"
URL="http://127.0.0.1:$PORT"
export TAPSPACE_URL="$URL"
for f in 2a987b7a7e/1.png 2a987b7a7e/2.png 8d1e93788e/1.png e63e4daa8a/1.png; do
    [[ -f "$SHOTS/$f" ]] || { echo "missing real screenshot $SHOTS/$f (set TAPSPACE_SHOTS)" >&2; exit 1; }
done
command -v ffmpeg >/dev/null || { echo "ffmpeg makes the seeded clips: brew install ffmpeg" >&2; exit 1; }
fresh "$DATA"

# The space deletes a video the moment it is decided, so no recording survives in the live shots:
# each seeded clip is four seconds of a real screenshot.
clip() {   # clip <png> <mp4>
    ffmpeg -loglevel error -y -loop 1 -t 4 -i "$1" -vf "scale='min(1280,iw)':-2,format=yuv420p" \
        -r 30 -c:v libx264 -movflags +faststart "$2"
}
clip "$SHOTS/8d1e93788e/1.png" "$DATA/clip-nba.mp4"
clip "$SHOTS/e63e4daa8a/1.png" "$DATA/clip-injuries.mp4"

# tap.py tells the server which Claude Code session runs it (X-Claude-Session, trusted when
# CLAUDE_PID is an ancestor). Each seeded master speaks as its own scratch session (CLAUDE_PID is
# this script, an ancestor of every tap.py it starts); "-" speaks as no session. Never as the Claude
# Code session that happens to run this script.
SID_M="$(uuidgen | tr 'A-Z' 'a-z')"
SID_F="$(uuidgen | tr 'A-Z' 'a-z')"
printf 'SID_M=%s\nSID_F=%s\nURL=%s\n' "$SID_M" "$SID_F" "$URL" >"$DATA/seed.env"
tap() {   # tap <session id | -> <tap.py args...>
    local sid="$1"
    shift
    if [[ "$sid" == "-" ]]; then
        env -u CLAUDE_PID -u CLAUDE_CODE_SESSION_ID -u CODEX_COMPANION_SESSION_ID python3 "$REPO/tap.py" "$@"
    else
        env -u CODEX_COMPANION_SESSION_ID CLAUDE_PID="$$" CLAUDE_CODE_SESSION_ID="$sid" python3 "$REPO/tap.py" "$@"
    fi
}
# Hayden's side of a chat and of a card, on the SCRATCH server only (the port guard above).
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

claude_seed init "$DATA/claude" "$SID_M" "$SID_F"
serve "$PORT" "$DATA" "$DATA/claude"
PID="$(cat "$DATA/server.pid")"

echo "==> Masters"
(cd "$REPO/mac" && tap "$SID_M" listen --as "$M" --timeout 2 >/dev/null || true)
(cd "$HOME/Developer" 2>/dev/null && tap "$SID_F" listen --as "$F" --timeout 2 >/dev/null || true)
tap "$SID_M" title --as "$M" "Mac app polish" >/dev/null
tap "$SID_F" title --as "$F" "Expert takes v2" >/dev/null

echo "==> Cards"
CAMPAIGN=2026-10-12-fantasy-p0
# The v2 pair changes about 11% of the frame, so the blue change boxes show
# (a pair that changes more than 55% is a redesign and is shown without them).
EXPERT=$(tap "$SID_M" post --agent L3k-ios --campaign "$CAMPAIGN" --title "Sunday glance: inactives, the swap, their side" \
    --note "Inactives at 8:30, the one swap before 10:00, Barkley live for them." \
    --image "$SHOTS/2a987b7a7e/1.png")
tap "$SID_M" update "$EXPERT" --image "$SHOTS/2a987b7a7e/2.png" \
    --note "v2: Models tab bar, week label and Ask pill under VS, pill Claims." >/dev/null
NBA=$(tap - post --agent L3n-ios --campaign "$CAMPAIGN" --owner "$M" \
    --title "NBA weekly league: a card with no move no longer says to make moves" \
    --image "$SHOTS/8d1e93788e/1.png")
VID=$(tap "$SID_M" post --agent L3n-ios --campaign "$CAMPAIGN" \
    --title "NBA weekly league card, as a short clip" --note "Four seconds of the real screen." \
    --video "$DATA/clip-nba.mp4")
INJ=$(tap "$SID_F" post --agent L3m-ios --campaign "$CAMPAIGN" \
    --title "NBA weekly league: Injuries no longer says to move an Out player" \
    --note "Real Sleeper Lock-In league, the Injuries card in Analyze." \
    --image "$SHOTS/e63e4daa8a/1.png")
INJCLIP=$(tap "$SID_F" post --agent L3m-ios --campaign "$CAMPAIGN" \
    --title "Injuries card, as a short clip" --video "$DATA/clip-injuries.mp4")
decide_change "$INJ" "Close. Tighten the spacing under the header and keep the player names on one line." >/dev/null
decide_change "$INJCLIP" "Hold the first frame a beat longer before it moves." >/dev/null
echo "    Mac app polish: $EXPERT (v2) $NBA $VID (clip)   Expert takes v2: $INJ $INJCLIP (clip), both sent back with notes"

echo "==> Chat"
tap "$SID_F" reply --as "$F" "Expert takes v2 is up on the board. The expert pages now load from the server's recorded list, so the pictures match the people." >/dev/null
sleep 1
say "$M" "How is the Mac app coming along?" >/dev/null
sleep 1
tap "$SID_M" reply --as "$M" "The **Visual** tab is up: cards spring into the grid, and Approve draws its check before the card collapses like liquid. The pill is next." >/dev/null
sleep 1
say "$M" "Make the pill feel exactly like SnappWAU." >/dev/null
sleep 1
tap "$SID_M" reply --as "$M" $'On it. Same motion language:\n- the `Spring` presets (spread 300/25 out, retract 330/34 back)\n- width leads height on the way out\n- the droplet wobbles on a metaball neck\n\nI will post a recording when it lands.' >/dev/null
sleep 1
say "$F" "Does it handle a league with no matched experts?" >/dev/null

echo "==> Goal cards"
tap "$SID_M" goal --as "$M" "The Mac app shows every master's goal, steps and notes" \
    --steps "Audit|Spec|Build|Review|Open the PR" --current 3 >/dev/null
tap "$SID_F" goal --as "$F" "Expert takes v2 on the fantasy board" --steps "Spec|Build|Verify|Ship" --current 2 >/dev/null
tap "$SID_F" step --as "$F" --note "Matching each expert to their picture" >/dev/null

cat <<EOF

Scratch TapSpace is up: $URL  (PID $PID, data $DATA)
Masters: Mac app polish (goal at 3 of 5), Expert takes v2 (thinking, goal at 2 of 4, two cards sent back with notes)
Right before a render, let them exchange a message (the link stays active for 10 minutes):
  $0 --link $DATA
Render the app on it (offscreen, never takes focus):
  TAPSPACE_URL=$URL .build/debug/TapSpace --snapshot <dir>
Stop it:
  $0 --stop $DATA
EOF
