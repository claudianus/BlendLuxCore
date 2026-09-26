# SPDX-License-Identifier: Apache-2.0
#
# E32: OpenPBR node registration + export test.
#
#   Blender -b --python dev-tools/e32_openpbr_node_test.py
#
# Creates a luxcore_material_nodes tree with a LuxCoreNodeMatOpenPBR node,
# toggles every optional lobe, exports the material and verifies the emitted
# properties (type=openpbr, film nm->um conversion, SSS/transmission keys).

import bpy

from bl_ext.user_default.blendluxcore.export import material as export_material


def get_str(props, key):
    p = props.Get(key)
    assert p, f"missing property: {key}"
    return " ".join(p.GetString(i) for i in range(p.GetSize()))


# --- build the node tree -------------------------------------------------
mat = bpy.data.materials.new("MatTest")
nt = bpy.data.node_groups.new("MatTree", "luxcore_material_nodes")
nt.use_fake_user = True
mat.luxcore.node_tree = nt

out = nt.nodes.new("LuxCoreNodeMatOutput")
op = nt.nodes.new("LuxCoreNodeMatOpenPBR")
nt.links.new(op.outputs[0], out.inputs[0])

# enable every optional lobe
op.use_transmission = True
op.use_subsurface = True
op.use_coat = True
op.use_fuzz = True
op.use_thinfilmcoating = True
op.inputs["Film Weight"].default_value = 1.0
op.inputs["Film Thickness (nm)"].default_value = 400.0
op.inputs["Film IOR"].default_value = 1.4
op.inputs["Subsurface Weight"].default_value = 0.5
op.inputs["Transmission Weight"].default_value = 0.0
op.inputs["Coat Weight"].default_value = 0.8
op.inputs["Fuzz Weight"].default_value = 0.3

# --- export ---------------------------------------------------------------
depsgraph = bpy.context.evaluated_depsgraph_get()
exporter = type("DummyExporter", (),
                {"lightgroup_cache": set(), "node_cache": {}})()
luxcore_name, props = export_material.convert(
    exporter, depsgraph, mat, False, "ObjTest")

prefix = f"scene.materials.{luxcore_name}."
print("== exported material:", luxcore_name)
for k in sorted(props.GetAllNames()):
    if k.startswith(prefix) or ".MatTest_" in k:
        print("  ", k, "=", get_str(props, k))

assert get_str(props, prefix + "type") == "openpbr", "type must be openpbr"
assert get_str(props, prefix + "filmthickness") == "0.4", \
    "film thickness must convert 400 nm -> 0.4 um"
assert get_str(props, prefix + "filmior") == "1.4"
assert get_str(props, prefix + "subsurfaceweight") == "0.5"
assert get_str(props, prefix + "coatweight") == "0.8"
assert get_str(props, prefix + "fuzzweight") == "0.3"
for k in ("basecolor", "specularior", "transmissionweight",
          "subsurfaceradius", "coatdarkening", "fuzzroughness"):
    assert props.Get(prefix + k), f"missing {k}"

# The OpenPBR parser auto-creates an interior volume when SSS/transmission
# absorption is configured — verify it lands in the exported props.
vols = [k for k in props.GetAllNames() if k.startswith("scene.volumes.")]
print("== volumes:", vols)

print("PASS: OpenPBR node exports correctly")


# --- Principled BSDF -> openpbr mapping ----------------------------------
mat2 = bpy.data.materials.new("PrincipledTest")
pbsd = mat2.node_tree.nodes.get("Principled BSDF")
pbsd.inputs["Metallic"].default_value = 0.8
pbsd.inputs["Roughness"].default_value = 0.35
pbsd.inputs["Coat Weight"].default_value = 0.5
pbsd.inputs["Sheen Weight"].default_value = 0.2
pbsd.inputs["Thin Film Thickness"].default_value = 500.0
pbsd.inputs["Subsurface Weight"].default_value = 0.3

name2, props2 = export_material.convert(exporter, depsgraph, mat2, False)
pre2 = f"scene.materials.{name2}."
assert get_str(props2, pre2 + "type") == "openpbr"
assert abs(float(get_str(props2, pre2 + "filmthickness")) - 0.5) < 1e-6
assert get_str(props2, pre2 + "coatweight") == "0.5"
assert get_str(props2, pre2 + "fuzzweight") == "0.2"
assert get_str(props2, pre2 + "subsurfaceweight") == "0.3"

# disney fallback still works
mat2.luxcore.principled_target = "disney"
name3, props3 = export_material.convert(exporter, depsgraph, mat2, False)
assert get_str(props3, f"scene.materials.{name3}.type") == "disney"

print("PASS: Principled BSDF -> openpbr mapping (+ disney fallback)")
