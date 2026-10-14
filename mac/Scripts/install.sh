#!/bin/bash
# Builds TapSpace (release), bundles TapSpace.app with its Info.plist and icon,
# ad-hoc signs it, installs it to /Applications/TapSpace.app and opens it.
#
#   Scripts/install.sh              build, install, open
#   NO_OPEN=1 Scripts/install.sh    build and install only
#   TAPSPACE_URL=http://127.0.0.1:4748 Scripts/install.sh   open it on another server
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

NAME=TapSpace
BUNDLE_ID=com.hayden.tapspace
DEST="/Applications/$NAME.app"
SCRATCH="${SCRATCH:-.build}"

# 1. Icon: rendered from Scripts/make-icon.swift at every size (vector, so the
#    small sizes stay crisp), then packed by iconutil. Only when it changed.
ICNS=Resources/$NAME.icns
if [[ ! -f "$ICNS" || Scripts/make-icon.swift -nt "$ICNS" ]]; then
    echo "==> Rendering the icon"
    ICONTMP="$(mktemp -d)"
    SET="$ICONTMP/$NAME.iconset"
    mkdir -p "$SET"
    for pt in 16 32 128 256 512; do
        swift Scripts/make-icon.swift "$SET/icon_${pt}x${pt}.png" "$pt" >/dev/null
        swift Scripts/make-icon.swift "$SET/icon_${pt}x${pt}@2x.png" "$((pt * 2))" >/dev/null
    done
    iconutil -c icns "$SET" -o "$ICNS"
    rm -rf "${ICONTMP:?}"
fi

# 2. Release build.
echo "==> Building (release)"
swift build -c release --scratch-path "$SCRATCH"
BIN="$(swift build -c release --scratch-path "$SCRATCH" --show-bin-path)/$NAME"

# 3. Bundle and sign.
APP="$SCRATCH/$NAME.app"
rm -rf "${APP:?}"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$NAME"
cp Resources/Info.plist "$APP/Contents/Info.plist"
cp "$ICNS" "$APP/Contents/Resources/$NAME.icns"
printf 'APPL????' > "$APP/Contents/PkgInfo"
codesign --force --deep --sign - "$APP"
codesign --verify --strict "$APP"

# 4. Quit the running copy politely, then install.
if pgrep -x "$NAME" >/dev/null; then
    echo "==> Quitting the running $NAME"
    osascript -e "tell application id \"$BUNDLE_ID\" to quit" >/dev/null 2>&1 || true
    for _ in $(seq 1 50); do pgrep -x "$NAME" >/dev/null || break; sleep 0.1; done
fi
echo "==> Installing $DEST"
mkdir -p "$DEST"
rsync -a --delete "$APP/" "$DEST/"
touch "$DEST"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST" >/dev/null 2>&1 || true

# 5. Open it (on TAPSPACE_URL when set, else the default http://127.0.0.1:4747).
if [[ "${NO_OPEN:-0}" != "1" ]]; then
    if [[ -n "${TAPSPACE_URL:-}" ]]; then
        open --env "TAPSPACE_URL=$TAPSPACE_URL" "$DEST"
    else
        open "$DEST"
    fi
fi
echo "Installed: $DEST ($BUNDLE_ID)"
