# SPDX-License-Identifier: Apache-2.0
#
# E34: "distribution" enum export test for glossy2 / metal2 / roughglass /
# glossycoating / glossytranslucent / carpaint.
#
#   Blender -b --python dev-tools/e34_distribution_export_test.py
#
# The legacy Schlick->GGX opt-in is exposed as a `distribution` enum
# (default "schlick" for compatibility, "ggx" opt-in). This verifies the
# nodes emit the engine-side property correctly.

import bpy

from bl_ext.user_default.blendluxcore.export import material as export_material


def get_str(props, key):
    p = props.Get(key)
    assert p, f"missing property: {key}"
    return " ".join(p.GetString(i) for i in range(p.GetSize()))


depsgraph = bpy.context.evaluated_depsgraph_get()
exporter = type("DummyExporter", (),
                {"lightgroup_cache": set(), "node_cache": {}})()


def export_node(node_cls, configure=None):
    mat = bpy.data.materials.new("Mat_" + node_cls)
    nt = bpy.data.node_groups.new("Tree_" + node_cls, "luxcore_material_nodes")
    nt.use_fake_user = True
    mat.luxcore.node_tree = nt
    out = nt.nodes.new("LuxCoreNodeMatOutput")
    n = nt.nodes.new(node_cls)
    nt.links.new(n.outputs[0], out.inputs[0])
    if configure:
        configure(n)
    name, props = export_material.convert(exporter, depsgraph, mat, False, "Obj")
    return n, name, props


fails = []

# --- glossy2 -------------------------------------------------------------
n, name, props = export_node("LuxCoreNodeMatGlossy2")
pre = f"scene.materials.{name}."
assert get_str(props, pre + "type") == "glossy2"
assert get_str(props, pre + "distribution") == "schlick", "default must be schlick"

n, name, props = export_node("LuxCoreNodeMatGlossy2",
                             lambda n: setattr(n, "distribution", "ggx"))
assert get_str(props, f"scene.materials.{name}.distribution") == "ggx", \
    "glossy2 ggx export failed"

# --- metal2 ---------------------------------------------------------------
n, name, props = export_node("LuxCoreNodeMatMetal",
                             lambda n: setattr(n, "distribution", "ggx"))
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "type") == "metal2"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append("metal2 distribution export failed")

def cfg_metal_mb(n):
    n.distribution = "ggx"
    n.multibounce = True

n, name, props = export_node("LuxCoreNodeMatMetal", cfg_metal_mb)
pre = f"scene.materials.{name}."
if get_str(props, pre + "multibounce") != "1":
    fails.append("metal2 multibounce export failed: "
                 f"{get_str(props, pre + 'multibounce')}")

# --- roughglass (glass node, rough mode) -----------------------------------
def cfg_glass(n):
    n.rough = True
    n.distribution = "ggx"

n, name, props = export_node("LuxCoreNodeMatGlass", cfg_glass)
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "type") == "roughglass"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append(f"roughglass distribution export failed: type={get_str(props, pre + 'type')}")

# smooth glass must not emit a distribution key
n, name, props = export_node("LuxCoreNodeMatGlass")
pre = f"scene.materials.{name}."
if any(k == pre + "distribution" for k in props.GetAllNames()):
    fails.append("smooth glass emitted distribution property")

# --- glossycoating (needs a linked base material) ---------------------------
def export_glossycoating(distribution):
    mat = bpy.data.materials.new("Mat_GC")
    nt = bpy.data.node_groups.new("Tree_GC", "luxcore_material_nodes")
    nt.use_fake_user = True
    mat.luxcore.node_tree = nt
    out = nt.nodes.new("LuxCoreNodeMatOutput")
    base = nt.nodes.new("LuxCoreNodeMatMatte")
    n = nt.nodes.new("LuxCoreNodeMatGlossyCoating")
    n.distribution = distribution
    nt.links.new(base.outputs[0], n.inputs["Base Material"])
    nt.links.new(n.outputs[0], out.inputs[0])
    name, props = export_material.convert(exporter, depsgraph, mat, False, "Obj")
    return name, props

name, props = export_glossycoating("ggx")
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "type") == "glossycoating"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append("glossycoating distribution export failed")

name, props = export_glossycoating("schlick")
if get_str(props, f"scene.materials.{name}.distribution") != "schlick":
    fails.append("glossycoating default distribution broken")

# --- glossytranslucent ------------------------------------------------------
n, name, props = export_node("LuxCoreNodeMatGlossyTranslucent",
                             lambda n: setattr(n, "distribution", "ggx"))
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "type") == "glossytranslucent"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append("glossytranslucent distribution export failed")

# --- carpaint (manual + preset paths) --------------------------------------
n, name, props = export_node("LuxCoreNodeMatCarpaint",
                             lambda n: setattr(n, "distribution", "ggx"))
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "type") == "carpaint"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append("carpaint distribution export failed")

def cfg_carpaint_preset(n):
    n.preset = "polaris_silber"
    n.distribution = "ggx"

n, name, props = export_node("LuxCoreNodeMatCarpaint", cfg_carpaint_preset)
pre = f"scene.materials.{name}."
if not (get_str(props, pre + "preset") == "polaris silber"
        and get_str(props, pre + "distribution") == "ggx"):
    fails.append("carpaint preset distribution export failed")

if fails:
    for f in fails:
        print("FAIL:", f)
    raise SystemExit(1)

print("PASS: distribution enum exports correctly "
      "(glossy2/metal2/roughglass/glossycoating/glossytranslucent/carpaint)")
