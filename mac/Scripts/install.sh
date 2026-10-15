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

# 1. Icon: Resources/TapSpace.icon (Icon Composer format: a white fill, white in
#    dark mode too, and one flat blue dot) compiled by actool into Assets.car plus
#    a TapSpace.icns made from it. macOS 26 sets a plain .icns inside a dark
#    plate, so the app names the Assets.car icon (CFBundleIconName).
echo "==> Compiling the icon"
ICON="$SCRATCH/icon"
rm -rf "${ICON:?}"
mkdir -p "$ICON"
xcrun actool "Resources/$NAME.icon" --compile "$ICON" --output-partial-info-plist "$ICON/partial.plist" \
    --app-icon "$NAME" --include-all-app-icons --enable-on-demand-resources NO \
    --target-device mac --platform macosx --minimum-deployment-target 26.0 >/dev/null

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
cp "$ICON/Assets.car" "$ICON/$NAME.icns" "$APP/Contents/Resources/"
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
