#!/bin/bash
# Runs the app with --demo --fps for N seconds against a server, then
# summarizes the display link: frames per second while animating, and whether
# it pauses when everything is still. Also the crash check: the app must still
# be alive when the run ends.
#
#   Scripts/fps.sh <server-url> [seconds=30] [binary]
#   (binary defaults to the debug build in .build; never point it at :4747
#    unless you only want to watch: --demo never writes.)
set -euo pipefail
cd "$(dirname "$0")/.."
URL="${1:?usage: fps.sh <server-url> [seconds] [binary]}"
SECS="${2:-30}"
BIN="${3:-.build/debug/TapSpace}"
[[ -x "$BIN" ]] || { echo "no binary at $BIN (swift build first)" >&2; exit 1; }
LOG="$(mktemp -t tapspace-fps).log"

TAPSPACE_URL="$URL" "$BIN" --demo --fps >"$LOG" 2>&1 &
PID=$!
sleep "$SECS"
if kill -0 "$PID" 2>/dev/null; then
    ALIVE=yes
    kill "$PID"
    wait "$PID" 2>/dev/null || true
else
    ALIVE=no
fi

python3 - "$LOG" "$ALIVE" "$SECS" <<'PY'
import re, statistics, sys
log, alive, secs = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(log, errors="replace").read().splitlines()
pat = re.compile(r"(main|pill)=(\d+) (still|moving)( paused)?")
rows = []
for l in lines:
    if not l.startswith("FPS"):
        continue
    d = {m.group(1): (int(m.group(2)), m.group(3), bool(m.group(4))) for m in pat.finditer(l)}
    if "main" in d:
        rows.append(d)

def summary(name):
    fr = [r[name] for r in rows if name in r]
    # A second counts as "animating" when the link was moving at both ends of it.
    full = [b[0] for a, b in zip(fr, fr[1:]) if a[1] == "moving" and b[1] == "moving"]
    any_moving = [f for f, s, _ in fr if f > 0]
    still = [(f, p) for f, s, p in fr if s == "still"]
    idle = [f for (f, p), (g, q) in zip(still, still[1:]) if p and q]
    out = f"{name}: {len(fr)} s logged"
    if full:
        full.sort()
        out += (f" | animating seconds {len(full)}: median {statistics.median(full):.0f} fps,"
                f" min {full[0]}, p10 {full[max(0, len(full) // 10 - 1)]}")
    if any_moving:
        out += f" | best second {max(any_moving)}"
    paused = sum(1 for _, _, p in fr if p)
    out += f" | paused when still: {paused}/{len(still)} still samples"
    if idle:
        out += f", frames in fully idle seconds {max(idle)} max"
    return out

print(summary("main"))
print(summary("pill"))
steps = [l for l in lines if l.startswith("DEMO")]
print(f"demo steps logged: {len(steps)} (loops: {sum(1 for l in steps if l.endswith(' loop'))})")
crash = [l for l in lines if "Fatal error" in l or "fatalError" in l or "Abort" in l]
print(f"alive after {secs} s: {alive}" + (f"  CRASH: {crash[0]}" if crash else ""))
print(f"log: {log}")
PY
