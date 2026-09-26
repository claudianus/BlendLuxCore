#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Sync a freshly built pyluxcore + the working-tree add-on sources into the
# installed Blender extension, without a full scikit-build wheel rebuild.
#
# What it does:
#   1. Copies the built pyluxcore .so into the extension's site-packages
#      and rewrites its @rpath refs to @loader_path/.dylibs, matching the
#      layout the wheel installer produces.
#   2. Syncs the runtime dylibs (OIDN/embree/tbb) into
#      site-packages/pyluxcore/.dylibs.
#   3. Updates the cached wheel the add-on reinstalls from, so a reinstall
#      does not downgrade the binary.
#   4. rsyncs the add-on Python sources from this repo into the extension
#      directory (everything except wheels/, .git, caches, dev files).
#   5. Smoke-imports pyluxcore under Blender's bundled Python.
#
# Env overrides:
#   LUXCORE_REPO   LuxCore checkout (default: ../LuxCore next to this repo)
#   LUXCORE_BUILD  dir holding the built .so
#                  (default: $LUXCORE_REPO/out/build/src/pyluxcore/Release)
#   BLENDER_VER    Blender version dir (default: 5.2)
#   EXT_ID         extension dir name (default: blendluxcore)
#
# Usage: dev-tools/sync_dev_install.sh

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
LUXCORE_REPO="${LUXCORE_REPO:-$(cd "$HERE/../LuxCore" 2>/dev/null && pwd)}"
LUXCORE_BUILD="${LUXCORE_BUILD:-$LUXCORE_REPO/out/build/src/pyluxcore/Release}"
LIB_DIR="$LUXCORE_REPO/out/install/Release/lib"
BLENDER_VER="${BLENDER_VER:-5.2}"
EXT_ID="${EXT_ID:-blendluxcore}"

EXT_BASE="$HOME/Library/Application Support/Blender/$BLENDER_VER/extensions"
EXT_DIR="$EXT_BASE/user_default/$EXT_ID"
SITE_PKG="$EXT_BASE/.local/lib/python3.13/site-packages"
WHEELS_DIR="$EXT_DIR/wheels"
BLENDER_PY="/Applications/Blender.app/Contents/Resources/$BLENDER_VER/python/bin/python3.13"

SO="$(ls "$LUXCORE_BUILD"/pyluxcore.cpython-*.so 2>/dev/null | head -1)"
[ -n "$SO" ] || { echo "ERROR: no pyluxcore .so in $LUXCORE_BUILD"; exit 1; }
echo "== pyluxcore build: $SO"

# 1) site-packages .so + .dylibs
rm -rf "$SITE_PKG/pyluxcore/.dylibs"
mkdir -p "$SITE_PKG/pyluxcore/.dylibs"
DEST_SO="$SITE_PKG/pyluxcore/$(basename "$SO")"
cp "$SO" "$DEST_SO"

# Collect the .so's own rpath dirs — each @rpath dep is resolved against
# them at build time, so the same dirs give us the matching dylib versions
# (install/lib may lag behind a dep refresh).
RPATHS="$(otool -l "$DEST_SO" | awk '
    /LC_RPATH/ {getline; getline; gsub(/^[ \t]+path /,""); gsub(/ \(offset.*/,""); print}')"
# rpath dirs first (they carry the dep versions the build actually linked);
# install/lib may lag behind a dep refresh.
SEARCH_DIRS=""
for rp in $RPATHS; do
    [ -d "$rp" ] && SEARCH_DIRS="$SEARCH_DIRS $rp"
done
SEARCH_DIRS="$SEARCH_DIRS $LIB_DIR"

# Copy every @rpath dep into .dylibs (basename match, first dir wins),
# then rewrite the refs to @loader_path — both on the extension and for
# transitive refs inside .dylibs.
DEPS="$(otool -L "$DEST_SO" | awk '/@rpath\// {gsub(/^[ \t]+/,""); print $1}')"
for ref in $DEPS; do
    lib="$(basename "$ref")"
    for d in $SEARCH_DIRS; do
        if [ -f "$d/$lib" ]; then
            cp "$d/$lib" "$SITE_PKG/pyluxcore/.dylibs/$lib"
            break
        fi
    done
    install_name_tool -change "$ref" "@loader_path/.dylibs/$lib" "$DEST_SO"
done
for dylib in "$SITE_PKG/pyluxcore/.dylibs/"*.dylib; do
    otool -L "$dylib" | awk '/@rpath\// {gsub(/^[ \t]+/,""); print $1}' | \
    while read -r ref; do
        lib="$(basename "$ref")"
        install_name_tool -change "$ref" "@loader_path/$lib" "$dylib" \
            2>/dev/null || true
    done
    install_name_tool -id "@loader_path/$(basename "$dylib")" "$dylib" \
        2>/dev/null || true
done
echo "== site-packages <- $DEST_SO (dylibs + rpath rewrite done)"

# 2) Wheel the add-on reinstalls from. luxloader's LOCAL mode backs up
# WHEEL_DL_FOLDER before `pip download`, so a path_to_wheel inside wheels/
# is always moved away mid-install — point the settings at a stable copy
# in the LuxCore build tree instead.
DEV_WHEEL_DIR="$LUXCORE_REPO/out/install/Release/wheel"
mkdir -p "$DEV_WHEEL_DIR"
DEV_WHEEL="$DEV_WHEEL_DIR/pyluxcore-2.11.2-cp313-cp313-macosx_14_0_arm64.whl"
BASE_WHEEL="$(ls "$WHEELS_DIR"/pyluxcore-*.whl 2>/dev/null | head -1)"
[ -z "$BASE_WHEEL" ] && BASE_WHEEL="$DEV_WHEEL"
if [ -f "$BASE_WHEEL" ]; then
    python3 - "$BASE_WHEEL" "$DEV_WHEEL" "$DEST_SO" "$SITE_PKG/pyluxcore/.dylibs" <<'PYEOF'
import os, sys, zipfile
wheel, dev_wheel, so, dylibs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
tmp = dev_wheel + ".tmp"
soname = os.path.basename(so)
with zipfile.ZipFile(wheel) as zin, \
     zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    names = set()
    for item in zin.infolist():
        if item.filename == f"pyluxcore/{soname}" or \
                item.filename.startswith("pyluxcore/.dylibs/"):
            continue  # replaced below
        zout.writestr(item, zin.read(item.filename))
    zout.write(so, f"pyluxcore/{soname}")
    for f in sorted(os.listdir(dylibs)):
        src = os.path.join(dylibs, f)
        if os.path.isfile(src):
            zout.write(src, f"pyluxcore/.dylibs/{f}")
os.replace(tmp, dev_wheel)
print(f"== dev wheel updated: {dev_wheel}")
PYEOF
    # Point the loader's LOCAL wheel source at the stable dev wheel
    SETTINGS="$HOME/Library/Application Support/Blender/$BLENDER_VER/config/blendluxcore/blc_settings.json"
    mkdir -p "$(dirname "$SETTINGS")"
    python3 - "$SETTINGS" "$DEV_WHEEL" <<'PYEOF'
import json, sys
path, wheel = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except (OSError, ValueError):
    cfg = {}
cfg.update({"wheel_source": 1, "path_to_wheel": wheel})
with open(path, "w") as f:
    json.dump(cfg, f, indent=4)
print(f"== blc_settings.json -> {wheel}")
PYEOF
else
    echo "== no base wheel found — skipped wheel update"
fi

# 3) add-on sources
#
# Guard: never sync this repo into an extension dir that belongs to a
# different package. The upstream-coexistence extension (blendluxcore_up)
# is a *generated* artifact — rsyncing untransformed fork sources over it
# previously destroyed it (duplicate LUXCORE engine registration).
REPO_ID="$(awk -F'"' '/^id =/ {print $2; exit}' "$HERE/blender_manifest.toml")"
TARGET_MANIFEST="$EXT_DIR/blender_manifest.toml"
if [ -f "$TARGET_MANIFEST" ]; then
    TARGET_ID="$(awk -F'"' '/^id =/ {print $2; exit}' "$TARGET_MANIFEST")"
    if [ "$TARGET_ID" != "$REPO_ID" ]; then
        echo "ERROR: target extension id '$TARGET_ID' != repo id '$REPO_ID'." >&2
        echo "  $EXT_DIR is a generated/different package — refusing to overwrite." >&2
        echo "  Rebuild blendluxcore_up via tools/upstream_coexistence/build.sh." >&2
        exit 1
    fi
fi
rsync -a --delete \
    --exclude 'wheels' --exclude '.git' --exclude '__pycache__' \
    --exclude '.git*' --exclude 'dev-tools' --exclude 'doc' \
    "$HERE/" "$EXT_DIR/"
echo "== add-on sources synced -> $EXT_DIR"

# 4) smoke import under Blender's Python
"$BLENDER_PY" - "$SITE_PKG" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
import pyluxcore
import pyluxcore.pyluxcore as plc
print("== pyluxcore", plc.Version(),
      "| SetStrandsVertexMotion:",
      hasattr(plc.Scene, "SetStrandsVertexMotion"))
PYEOF

echo "== done"
