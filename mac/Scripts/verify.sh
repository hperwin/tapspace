#!/bin/bash
# The whole verification loop on a scratch server, never the live one:
# seed real data, build, render every scene to 2x PNGs, run the --demo fps
# measurement (which doubles as the crash check), then stop the server.
#
#   Scripts/verify.sh [port=4748] [out-dir] [demo-seconds=30]
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-4748}"
OUT="${2:-${TMPDIR:-/tmp}/tapspace-verify-$PORT}"
SECS="${3:-30}"
SCRATCH="${SCRATCH:-.build}"
DATA="$OUT/data"
URL="http://127.0.0.1:$PORT"

mkdir -p "$OUT"
Scripts/seed.sh "$PORT" "$DATA"
trap 'Scripts/seed.sh --stop "$DATA" >/dev/null' EXIT

echo "==> Building"
swift build --scratch-path "$SCRATCH" 2>&1 | grep -E "error|warning: .*Harness|Build complete" || true
BIN="$(swift build --scratch-path "$SCRATCH" --show-bin-path)/TapSpace"

echo "==> Waiting for the scratch server to read the taps from GitHub"
for _ in $(seq 1 60); do
    curl -fs "$URL/api/taps" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin)["fetched"] else 1)' && break
    sleep 1
done

echo "==> Snapshots -> $OUT/snapshots"
rm -rf "$OUT/snapshots"
set +e
TAPSPACE_URL="$URL" "$BIN" --snapshot "$OUT/snapshots" | grep -E "^SNAPSHOT"
SNAP=${PIPESTATUS[0]}
set -e

echo "==> --demo --fps for $SECS s"
Scripts/fps.sh "$URL" "$SECS" "$BIN"

echo
echo "snapshots: $OUT/snapshots (exit $SNAP; scenes.json lists each scene)"
exit "$SNAP"
