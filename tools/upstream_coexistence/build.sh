#!/bin/bash
# Build the coexisting upstream BlendLuxCore extension (blendluxcore_up).
#
# Produces dist/blendluxcore_up-<version>-macos_arm64.zip installable via
# Blender -> Edit -> Preferences -> Extensions -> "Install from Disk".
#
# Usage:
#   build.sh [--workdir DIR] [--install] [--keep-work]
#
# Steps: clone upstream BlendLuxCore (pinned) -> identifier transform ->
# post-patch -> fetch + vendor pyluxcore wheel -> strip tbbmalloc_proxy
# (macOS allocator interposer, crashes Blender) -> ad-hoc codesign -> zip.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# ---- pinned inputs -------------------------------------------------------
UPSTREAM_REPO="https://github.com/LuxCoreRender/BlendLuxCore.git"
UPSTREAM_COMMIT="5e204a72e0a2a9b71c04eb7175be0d10ed29b79e"  # BlendLuxCore 2.11.1
ADDON_ID="blendluxcore_up"
ADDON_VERSION="2.11.1"
PYLUXCORE_VERSION="2.11.2"
WHEEL_TAG="cp313-cp313-macosx_14_0_arm64"

WORKDIR=""
INSTALL=0
KEEP=0
while [ $# -gt 0 ]; do
    case "$1" in
        --workdir) WORKDIR="$2"; shift 2;;
        --install) INSTALL=1; shift;;
        --keep-work) KEEP=1; shift;;
        *) echo "unknown arg: $1" >&2; exit 1;;
    esac
done
WORKDIR="${WORKDIR:-$(mktemp -d /tmp/blc_up_build.XXXXXX)}"
SRC="$WORKDIR/upstream_repo"
STAGE="$WORKDIR/stage"
PKG="$STAGE/$ADDON_ID"
DIST="$HERE/dist"
mkdir -p "$SRC" "$STAGE" "$DIST"

echo "==> 1/6 clone upstream BlendLuxCore @ $UPSTREAM_COMMIT"
if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"
    git -C "$SRC" remote add origin "$UPSTREAM_REPO"
fi
git -C "$SRC" fetch -q --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$SRC" checkout -q FETCH_HEAD

echo "==> 2/6 transform identifiers"
rm -rf "$PKG"
python3 "$HERE/transform_upstream.py" "$SRC" "$PKG"

echo "==> 3/6 post-patch (loader, engine dispatch, manifest)"
bash "$HERE/post_patch.sh" "$PKG"

echo "==> 4/6 fetch pyluxcore $PYLUXCORE_VERSION wheel"
WHEEL_JSON=$(curl -fsSL "https://pypi.org/pypi/pyluxcore/$PYLUXCORE_VERSION/json")
WHEEL_URL=$(WHEEL_TAG="$WHEEL_TAG" python3 - "$WHEEL_JSON" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
import os
tag = os.environ["WHEEL_TAG"]
for f in data["urls"]:
    if tag in f["filename"]:
        print(f["url"])
        break
else:
    raise SystemExit("no wheel matching " + tag)
PY
)
WHEEL="$WORKDIR/pyluxcore.whl"
curl -fSL --progress-bar "$WHEEL_URL" -o "$WHEEL"
rm -rf "$PKG/bin"
mkdir -p "$PKG/bin"
python3 -c "import zipfile; zipfile.ZipFile('$WHEEL').extractall('$PKG/bin')"

echo "==> 5/6 strip tbbmalloc_proxy + codesign"
SO="$PKG/bin/pyluxcore/pyluxcore.cpython-313-darwin.so"
# The proxy dylib interposes malloc process-wide and crashes Blender on
# macOS. Repoint the load command at plain tbbmalloc (dyld dedupes the
# double reference), then drop the file.
install_name_tool -change \
    "@loader_path/.dylibs/libtbbmalloc_proxy.2.18.dylib" \
    "@loader_path/.dylibs/libtbbmalloc.2.18.dylib" \
    "$SO"
rm -f "$PKG/bin/pyluxcore/.dylibs/"libtbbmalloc_proxy*.dylib
find "$PKG/bin" \( -name "*.dylib" -o -name "*.so" \) \
    -exec codesign -f -s - {} \;

echo "==> syntax gate"
python3 -m compileall -q "$PKG" >/dev/null

echo "==> 6/6 zip"
ZIP="$DIST/$ADDON_ID-$ADDON_VERSION-macos_arm64.zip"
rm -f "$ZIP"
(cd "$STAGE" && zip -qr "$ZIP" "$ADDON_ID" -x "**/__pycache__/*")
echo "built: $ZIP ($(du -h "$ZIP" | cut -f1))"

if [ "$INSTALL" = 1 ]; then
    TARGET="$HOME/Library/Application Support/Blender/5.2/extensions/user_default/$ADDON_ID"
    mkdir -p "$(dirname "$TARGET")"
    rsync -a --delete --exclude "__pycache__" "$PKG/" "$TARGET/"
    echo "installed -> $TARGET (restart Blender)"
fi

if [ "$KEEP" = 0 ]; then
    rm -rf "$WORKDIR"
else
    echo "workdir kept: $WORKDIR"
fi
echo "DONE"
