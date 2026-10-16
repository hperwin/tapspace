#!/bin/bash
# Builds TapSpace (release), bundles TapSpace.app with its Info.plist and icon, ad-hoc signs it and
# installs it to /Applications/TapSpace.app. It never quits, relaunches or brings forward a running
# TapSpace: that copy keeps its build and shows Update ready; clicking it restarts on the new one.
#
#   Scripts/install.sh                  build and install
#   BUNDLE_ONLY=1 Scripts/install.sh    build and sign .build/TapSpace.app only; /Applications untouched
#   OPEN=1 Scripts/install.sh           also open it behind the front app, when no copy is running
#   OPEN=1 TAPSPACE_URL=http://127.0.0.1:4748 Scripts/install.sh   ... on another server
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

NAME=TapSpace
BUNDLE_ID=com.hayden.tapspace
DEST="/Applications/$NAME.app"
# Absolute paths throughout: actool hands its work to a long-lived ibtoold, which resolves relative
# paths against the folder it was first started from (another checkout's, after an earlier install).
SCRATCH="${SCRATCH:-.build}"
mkdir -p "$SCRATCH" && SCRATCH="$(cd "$SCRATCH" && pwd)"

# 1. Icon: Resources/TapSpace.icon (Icon Composer format: a white fill, white in
#    dark mode too, and one flat blue dot) compiled by actool into Assets.car plus
#    a TapSpace.icns made from it. macOS 26 sets a plain .icns inside a dark
#    plate, so the app names the Assets.car icon (CFBundleIconName).
echo "==> Compiling the icon"
ICON="$SCRATCH/icon"
rm -rf "${ICON:?}"
mkdir -p "$ICON"
xcrun actool "$PWD/Resources/$NAME.icon" --compile "$ICON" --output-partial-info-plist "$ICON/partial.plist" \
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
if [[ "${BUNDLE_ONLY:-0}" == "1" ]]; then
    echo "Bundled: $APP (not installed)"
    exit 0
fi

# 4. Install beside a running copy. rsync writes each file next to the old one and renames it into
#    place (never --inplace), so a running TapSpace keeps the executable it already mapped.
RUNNING="$(pgrep -f "$DEST/Contents/MacOS/$NAME" || true)"
echo "==> Installing $DEST"
mkdir -p "$DEST"
rsync -a --delete "$APP/" "$DEST/"
touch "$DEST"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST" >/dev/null 2>&1 || true

# 5. Open only when asked and nothing is running: behind the front app (open -g), and the app does
#    not activate itself either (TAPSPACE_BACKGROUND=1).
if [[ -n "$RUNNING" ]]; then
    echo "TapSpace is running (PID $(echo $RUNNING)): it shows Update ready and keeps its build until that is clicked."
elif [[ "${OPEN:-0}" == "1" ]]; then
    if [[ -n "${TAPSPACE_URL:-}" ]]; then
        open -g --env TAPSPACE_BACKGROUND=1 --env "TAPSPACE_URL=$TAPSPACE_URL" "$DEST"
    else
        open -g --env TAPSPACE_BACKGROUND=1 "$DEST"
    fi
fi
echo "Installed: $DEST ($BUNDLE_ID)"
