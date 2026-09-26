"""Legacy bridge smoke test: loads HALL_BENCH.blend (upstream
BlendLuxCore-authored) and verifies that stored 'luxcore' data is
readable through the 'superluxcore' API without touching the file.

Run:  blender -b --factory-startup -P dev-tools/e46_legacy_bridge_test.py
"""
import bpy
import os
import sys

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

FILE = "/Users/modumaru/Downloads/HallBench/HALL_BENCH.blend"

results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, extra, flush=True)

try:
    bpy.context.scene.render.engine = "SUPERLUXCORE"
except TypeError:
    bpy.ops.preferences.addon_enable(module="superluxcore")

bpy.ops.wm.open_mainfile(filepath=FILE)

from superluxcore.utils import misc

# 1. Node trees restored (no undefined trees among luxcore material trees)
trees = list(bpy.data.node_groups)
undefined = [t for t in trees if t.bl_idname == "NodeTreeUndefined"]
legacy = [t for t in trees if t.bl_idname.startswith("luxcore")]
check("node trees loaded", len(trees) > 0, f"{len(trees)} trees")
check("no undefined trees", len(undefined) == 0, f"undefined={len(undefined)}")
check("legacy trees bound", len(legacy) > 0, f"legacy={len(legacy)}")

# 2. Material bridge: luxcore node_tree resolves through superluxcore API
mats_with_tree = [m for m in bpy.data.materials if m.superluxcore.node_tree is not None]
check("materials resolve luxcore node_tree", len(mats_with_tree) > 0,
      f"{len(mats_with_tree)}/{len(bpy.data.materials)}")
if mats_with_tree:
    m = mats_with_tree[0]
    check("resolved tree is legacy-typed", m.superluxcore.node_tree.bl_idname == "luxcore_material_nodes")
    out = None
    for n in m.superluxcore.node_tree.nodes:
        if n.bl_idname == "LuxCoreNodeMatOutput":
            out = n
            break
    check("legacy output node found", out is not None)

# 3. World bridge reads original values
w = bpy.data.worlds.get("World")
check("world exists", w is not None)
if w:
    check("world light=sky2", w.superluxcore.light == "sky2", w.superluxcore.light)
    check("world gain=1e-4", abs(w.superluxcore.gain - 1e-4) < 1e-9, w.superluxcore.gain)
    check("world turbidity=4", abs(w.superluxcore.turbidity - 4.0) < 1e-6, w.superluxcore.turbidity)
    check("world resolves native", not misc.use_cycles_compat(w.superluxcore))
    check("no superluxcore props materialized",
          "superluxcore" not in w or len(w["superluxcore"]) == 0)
    # Shadowing: an authored superluxcore value wins over legacy, and
    # the stored legacy value is untouched by the read.
    w.superluxcore.gain = 5.0
    check("authored superluxcore shadows legacy",
          abs(w.superluxcore.gain - 5.0) < 1e-6, w.superluxcore.gain)
    check("legacy storage intact", abs(w["luxcore"]["gain"] - 1e-4) < 1e-9,
          w["luxcore"]["gain"])
    # Clearing the authored value must not resurrect it as a stored
    # default - property_unset drops the mark, legacy shows again.
    w.superluxcore.property_unset("gain")
    check("unset falls back to legacy",
          abs(w.superluxcore.gain - 1e-4) < 1e-9, w.superluxcore.gain)
    check("world still resolves native", not misc.use_cycles_compat(w.superluxcore))

# 4. Fresh datablock (no luxcore data) resolves to cycles fallback
w2 = bpy.data.worlds.new("CyclesWorldProbe")
check("fresh world resolves cycles", misc.use_cycles_compat(w2.superluxcore))
bpy.data.worlds.remove(w2)

fails = [n for n, ok in results if not ok]
print("RESULT", "ALL_PASS" if not fails else f"FAILURES={fails}", flush=True)
sys.exit(0 if not fails else 1)
