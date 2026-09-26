# SPDX-License-Identifier: Apache-2.0
#
# E42: Light Path Expression adapter export test.
#
# Verifies the View Layer > Passes > Light Path Expressions UI data path:
# entries added to aovs.lpe_list export as film.lpe.N.expression/.name plus
# one film.outputs.N.type = LPE output whose .index selects the expression.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e42_lpe_export_test.py
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
    print(f"[E42-TEST] {'ok' if ok else 'FAIL'}: {name} {detail}", flush=True)


def ensure_superluxcore():
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
    except TypeError:
        bpy.ops.preferences.addon_enable(module="superluxcore")
        bpy.context.scene.render.engine = "SUPERLUXCORE"


def main():
    ensure_superluxcore()

    from superluxcore.export import aovs as export_aovs
    from superluxcore.utils import view_layer as utils_view_layer

    scene = bpy.context.scene
    layer = scene.view_layers[0]
    aovs = layer.superluxcore.aovs

    # Artist-facing: add two LPE entries (+ one empty - must be skipped)
    e0 = aovs.lpe_list.add()
    e0.name = "direct"
    e0.expression = "C<RD>L"
    e1 = aovs.lpe_list.add()
    e1.name = "indirect"
    e1.expression = "C<RD><RD>+L"
    aovs.lpe_list.add()  # empty expression -> skipped

    check("collection", len(aovs.lpe_list) == 3,
          "entries=%d" % len(aovs.lpe_list))

    # Export (final render: context=None). The export-time layer tracker
    # must point at the layer being exported.
    utils_view_layer.State.active_view_layer = layer.name

    class _Exporter:
        lightgroup_cache = set()

    class _Engine:
        is_preview = False
        aov_imagepipelines = {}

    props = export_aovs.convert(_Exporter(), scene, None, _Engine())

    check("film.lpe.0.expression", props.Get("film.lpe.0.expression").GetString() == "C<RD>L",
          repr(props.Get("film.lpe.0.expression")))
    check("film.lpe.0.name", props.Get("film.lpe.0.name").GetString() == "direct",
          repr(props.Get("film.lpe.0.name")))
    check("film.lpe.1.expression", props.Get("film.lpe.1.expression").GetString() == "C<RD><RD>+L",
          repr(props.Get("film.lpe.1.expression")))

    lpe_outputs = []
    for key in props.GetAllNames():
        if key.startswith("film.outputs.") and key.endswith(".type") \
                and props.Get(key).GetString() == "LPE":
            prefix = key[:-len(".type")]
            lpe_outputs.append((props.Get(prefix + ".index").GetInt(),
                                props.Get(prefix + ".filename").GetString()))
    lpe_outputs.sort()
    check("lpe outputs", [i for i, _ in lpe_outputs] == [0, 1],
          repr(lpe_outputs))

    utils_view_layer.State.active_view_layer = ""

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[E42-TEST] {len(RESULTS) - len(fails)}/{len(RESULTS)} passed", flush=True)
    sys.exit(1 if fails else 0)


main()
