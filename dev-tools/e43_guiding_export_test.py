# SPDX-License-Identifier: Apache-2.0
#
# E43: path-guiding P5 adapter export test.
#
# Verifies the Render Properties > Light Paths > Path Guiding controls
# export as path.guiding.* engine properties: defaults emit nothing
# (clean property sets), changed values map to the engine names.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e43_guiding_export_test.py
#
# Exits 0 on PASS, 1 on FAIL.

import os
import sys

import bpy

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"[E43-TEST] {'ok' if ok else 'FAIL'}: {name} {detail}", flush=True)


def ensure_superluxcore():
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
    except TypeError:
        bpy.ops.preferences.addon_enable(module="superluxcore")
        bpy.context.scene.render.engine = "SUPERLUXCORE"


def export_config(scene):
    # Import via the installed extension package so utils.base_package
    # resolves to the real addon key in preferences.addons.
    from bl_ext.user_default.superluxcore.export import (
        config as export_config_mod)

    class _Exporter:
        lightgroup_cache = set()

    class _Engine:
        is_preview = False
        aov_imagepipelines = {}

    return export_config_mod.convert(_Exporter(), scene, None, _Engine())


def main():
    ensure_superluxcore()

    # Fresh scene: bpy.data.scenes.new gives real addon defaults (a
    # bpy.ops.scene.new copy would inherit the active scene's config).
    scene = bpy.data.scenes.new("e43")
    scene.render.engine = "SUPERLUXCORE"
    config = scene.superluxcore.config
    config.engine = "PATH"
    config.device = "CPU"

    # Default: guiding enabled, everything else at defaults -> only the
    # enable flag exports.
    config.guiding_enable = True
    props = export_config(scene)
    check("default enable", props.Get("path.guiding.enable").GetBool(),
          repr(props.Get("path.guiding.enable")))
    leftover = [k for k in props.GetAllNames()
                if k.startswith("path.guiding.") and
                k != "path.guiding.enable"]
    check("defaults emit nothing else", not leftover, repr(leftover))

    # Artist tweaks -> engine property names.
    config.guiding_strength = 0.5
    config.guiding_diffuse = True
    config.guiding_min_depth = 3
    config.guiding_glossy_threshold = 0.45
    config.guiding_warmup = 512
    config.guiding_components = 2
    config.guiding_ris_k = 4
    config.guiding_savetable = "/tmp/e43_adt.pgt"
    config.guiding_freeze = False
    config.guiding_split = 0.01
    config.guiding_max_depth = 8
    config.guiding_max_leaves = 2048
    config.guiding_swaprecords = 50000
    config.guiding_debug = True
    props = export_config(scene)

    expect = {
        "path.guiding.strength": ("GetFloat", 0.5),
        "path.guiding.diffuse": ("GetBool", True),
        "path.guiding.mindepth": ("GetInt", 3),
        "path.guiding.glossythreshold": ("GetFloat", 0.45),
        "path.guiding.warmup": ("GetInt", 512),
        "path.guiding.components": ("GetInt", 2),
        "path.guiding.risk": ("GetInt", 4),
        "path.guiding.savetable": ("GetString", "/tmp/e43_adt.pgt"),
        "path.guiding.freeze": ("GetBool", False),
        "path.guiding.split": ("GetFloat", 0.01),
        "path.guiding.maxdepth": ("GetInt", 8),
        "path.guiding.maxleaves": ("GetInt", 2048),
        "path.guiding.swaprecords": ("GetInt", 50000),
        "path.guiding.debug": ("GetBool", True),
    }
    for key, (getter, want) in sorted(expect.items()):
        try:
            got = getattr(props.Get(key), getter)()
            # float32 RNA storage: compare floats with tolerance
            ok = (abs(got - want) < 1e-5 if isinstance(want, float)
                  else got == want)
            check(key, ok, f"got={got!r} want={want!r}")
        except Exception as exc:
            check(key, False, f"missing: {exc}")

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[E43-TEST] {len(RESULTS) - len(fails)}/{len(RESULTS)} passed",
          flush=True)
    sys.exit(1 if fails else 0)


main()
