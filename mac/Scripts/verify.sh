#!/bin/bash
# The Mac app's verification loop, on a scratch server and never on screen: build, seed real data,
# let the two seeded masters exchange a message, check what the server serves, render every scene
# and the link frames offscreen (the app must never come to the front or show a Dock icon), hold
# the masters data the app decoded against the server's, then stop the server.
#
#   Scripts/verify.sh [port=4748] [out-dir] [demo-seconds=30]
#   LINK=0 Scripts/verify.sh ...  skip the link frames and link.mp4
#   FPS=1 Scripts/verify.sh ...   also the --demo fps run, which shows a window and a second pill ON
#                                 SCREEN and takes focus: only with the go of whoever uses this Mac
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
PORT="${1:-4748}"
OUT="${2:-${TMPDIR:-/tmp}/tapspace-verify-$PORT}"
SECS="${3:-30}"
SCRATCH="${SCRATCH:-.build}"
DATA="$OUT/data"
URL="http://127.0.0.1:$PORT"
mkdir -p "$OUT"

echo "==> Building"
set +e
swift build --scratch-path "$SCRATCH" >"$OUT/build.log" 2>&1
BUILT=$?
set -e
grep -E "error|warning: .*Harness|Build complete" "$OUT/build.log" || true
[[ $BUILT -eq 0 ]] || { echo "build failed: $OUT/build.log" >&2; exit 1; }
BIN="$(swift build --scratch-path "$SCRATCH" --show-bin-path)/TapSpace"

Scripts/seed.sh "$PORT" "$DATA"
trap 'Scripts/seed.sh --stop "$DATA" >/dev/null' EXIT

echo "==> Waiting for the scratch server to read the taps from GitHub"
for _ in $(seq 1 60); do
    curl -fs "$URL/api/taps" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["fetched"] else 1)' && break
    sleep 1
done

echo "==> The two masters exchange a message"
Scripts/seed.sh --link "$DATA" || true

echo "==> What the server serves"
SEEDED=0
python3 - "$URL" "$OUT/served.json" <<'PY' || SEEDED=1
import json, sys, urllib.request
url, out = sys.argv[1:3]


def get(path):
    return json.loads(urllib.request.urlopen(url + path, timeout=10).read())


board, tickets = get("/api/agents"), get("/api/state")["tickets"]
rows, links = board.get("agents") or [], board.get("links") or []
served = {"agents": len(rows),
          "with_progress": sum(1 for r in rows if r.get("progress")),
          "notes": sum(len(r.get("notes") or []) for r in rows),
          "links": len(links),
          "links_active": sum(1 for l in links if l.get("active")),
          "owned_cards": sum(1 for t in tickets if t.get("owner"))}
with open(out, "w") as fh:
    json.dump(served, fh, indent=1)
print("    " + ", ".join(f"{k} {v}" for k, v in served.items()))
# What seed.sh put there: two masters with goal cards, all five cards owned, two notes under
# Expert takes v2 (an image card and a clip), one live link.
seeded = {"agents": 2, "with_progress": 2, "notes": 2, "links_active": 1, "owned_cards": 5}
bad = [f"{k} {served[k]} (seeded {v})" for k, v in seeded.items() if served[k] != v]
notes = [n for r in rows for n in r.get("notes") or []]
if sorted(bool(n.get("thumb")) for n in notes) != [False, True]:
    bad.append("expected one note with a thumb (the image card) and one without (the clip)")
for n in notes:
    if n.get("thumb"):
        try:
            with urllib.request.urlopen(url + n["thumb"], timeout=10) as r:
                ok = r.status == 200 and r.headers.get_content_type().startswith("image/")
        except Exception:
            ok = False
        if not ok:
            bad.append(f"note thumb {n['thumb']} does not load as an image")
if bad:
    sys.exit("    SEED CHECK FAILED: " + "; ".join(bad))
PY

# One offscreen run of the app (--snapshot), watched: it must never be the front app and never
# show a Dock icon. Sets RC and adds to QUIET.
QUIET=0
offscreen() {   # offscreen <dir> <log> [VAR=value ...]
    local dir="$1" log="$2" front=0 dock=0 kind="" asn t pid
    shift 2
    env TAPSPACE_URL="$URL" "$@" "$BIN" --snapshot "$dir" >"$log" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        asn="$(lsappinfo find pid="$pid" 2>/dev/null || true)"
        if [[ -n "$asn" ]]; then
            t="$(lsappinfo info -only applicationtype "$asn" 2>/dev/null | sed 's/.*="\(.*\)"/\1/')"
            [[ -n "$t" ]] && kind="$t"
            [[ "$t" == "Foreground" ]] && dock=1
        fi
        [[ "$(lsappinfo info -only pid "$(lsappinfo front)" 2>/dev/null | sed 's/.*=//')" == "$pid" ]] && front=1
        sleep 0.25
    done
    set +e
    wait "$pid"
    RC=$?
    set -e
    if [[ $front -eq 1 ]]; then echo "    IT CAME TO THE FRONT"; QUIET=1; fi
    if [[ $dock -eq 1 ]]; then echo "    IT HAD A DOCK ICON"; QUIET=1; fi
    if [[ $front -eq 0 && $dock -eq 0 ]]; then echo "    never in front (application type ${kind:-not registered})"; fi
}

echo "==> Snapshots -> $OUT/snapshots (offscreen)"
rm -rf "$OUT/snapshots"
offscreen "$OUT/snapshots" "$OUT/snapshot.log"
SNAP=$RC
grep -E "^SNAPSHOT" "$OUT/snapshot.log" || true

echo "==> The masters data the app decoded, against the server's"
MATCHED=0
python3 - "$OUT/served.json" "$OUT/snapshots/scenes.json" <<'PY' || MATCHED=1
import json, sys
served = json.load(open(sys.argv[1]))
app = json.load(open(sys.argv[2])).get("masters") or {}
diff = [f"{k}: server {v}, app {app.get(k)}" for k, v in served.items() if app.get(k) != v]
if app.get("notes_after_open"):
    diff.append(f"{app['notes_after_open']} notes still listed after opening that master's thread")
if diff:
    sys.exit("    APP DIFFERS: " + "; ".join(diff))
print("    the app shows what the server serves")
PY

FRAMES=0
if [[ "${LINK:-1}" == "1" ]]; then
    echo "==> Link frames -> $OUT/link (offscreen), then link.mp4 at 30 fps"
    rm -rf "$OUT/link"
    offscreen "$OUT/link" "$OUT/link.log" TAPSPACE_LINK_FRAMES=1
    FRAMES=$RC
    grep -E "^(LINK|SNAPSHOT)" "$OUT/link.log" | tail -5 || true
    if [[ $FRAMES -eq 0 && -f "$OUT/link/f000.png" ]]; then
        ffmpeg -loglevel error -y -framerate 30 -i "$OUT/link/f%03d.png" -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
            -c:v libx264 -pix_fmt yuv420p "$OUT/link/link.mp4"
        echo "    $OUT/link/link.mp4: $(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/link/link.mp4") s"
    else
        echo "    no link frames (exit $FRAMES); see $OUT/link.log"
        FRAMES=1
    fi
fi

if [[ "${FPS:-0}" == "1" ]]; then
    echo "==> --demo --fps for $SECS s (ON SCREEN)"
    Scripts/fps.sh "$URL" "$SECS" "$BIN"
fi

echo
echo "snapshots: $OUT/snapshots (exit $SNAP; scenes.json lists each scene and the masters data)"
if [[ $SNAP -ne 0 ]]; then exit "$SNAP"; fi
[[ $SEEDED -eq 0 && $MATCHED -eq 0 && $QUIET -eq 0 && $FRAMES -eq 0 ]] || exit 1
