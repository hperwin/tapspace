#!/bin/bash
# Runs the app with --demo --fps for N seconds against a server and summarizes
# the display link while animating (also the crash check: the app must still
# be alive at the end), then runs it 12 s with --fps alone to prove the link
# sleeps when nothing moves (frames per second once settled).
#
#   Scripts/fps.sh <server-url> [seconds=30] [binary]
#   (binary defaults to the debug build in .build. --demo never writes, but
#    prefer a scratch server: see seed.sh.)
set -euo pipefail
cd "$(dirname "$0")/.."
URL="${1:?usage: fps.sh <server-url> [seconds] [binary]}"
SECS="${2:-30}"
BIN="${3:-.build/debug/TapSpace}"
[[ -x "$BIN" ]] || { echo "no binary at $BIN (swift build first)" >&2; exit 1; }
LOG="$(mktemp -t tapspace-fps).log"
IDLE="$(mktemp -t tapspace-idle).log"

run() {  # run <seconds> <log> <flags...>: prints yes/no (alive at the end)
    local secs=$1 log=$2
    shift 2
    TAPSPACE_URL="$URL" "$BIN" "$@" >"$log" 2>&1 &
    local pid=$!
    sleep "$secs"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        wait "$pid" 2>/dev/null || true
        echo yes
    else
        echo no
    fi
}
ALIVE=$(run "$SECS" "$LOG" --demo --fps)
run 12 "$IDLE" --fps >/dev/null

python3 - "$LOG" "$ALIVE" "$SECS" "$IDLE" <<'PY'
import re, statistics, sys
log, alive, secs, idle_log = sys.argv[1:5]
pat = re.compile(r"(main|pill)=(\d+) (still|moving)( paused)?")

def parse(path):
    rows = []
    for l in open(path, errors="replace").read().splitlines():
        if l.startswith("FPS"):
            d = {m.group(1): (int(m.group(2)), m.group(3), bool(m.group(4))) for m in pat.finditer(l)}
            if "main" in d:
                rows.append(d)
    return rows

rows = parse(log)
idle = parse(idle_log)[6:]   # skip launch and the first data landing

def summary(name):
    fr = [r[name] for r in rows if name in r]
    # A second counts as "animating" when the link was moving at both ends of it.
    full = sorted(b[0] for a, b in zip(fr, fr[1:]) if a[1] == "moving" and b[1] == "moving")
    out = f"{name}: {len(fr)} s logged"
    if full:
        out += (f" | animating seconds {len(full)}: median {statistics.median(full):.0f} fps,"
                f" p10 {full[max(0, len(full) // 10 - 1)]}, min {full[0]}")
    moving = [f for f, _, _ in fr if f > 0]
    if moving:
        out += f" | best second {max(moving)}"
    settled = [r[name] for r in idle if name in r]
    if settled:
        paused = sum(1 for _, _, p in settled if p)
        out += f" | settled, no demo: {max(f for f, _, _ in settled)} frames/s max, paused {paused}/{len(settled)} s"
    return out

print(summary("main"))
print(summary("pill"))
lines = open(log, errors="replace").read().splitlines()
steps = [l for l in lines if l.startswith("DEMO")]
print(f"demo steps logged: {len(steps)} (loops completed: {sum(1 for l in steps if l.endswith(' loop'))})")
crash = [l for l in lines if "Fatal error" in l or "Abort" in l]
print(f"alive after {secs} s of --demo: {alive}" + (f"  CRASH: {crash[0]}" if crash else ""))
print(f"logs: {log} {idle_log}")
PY
