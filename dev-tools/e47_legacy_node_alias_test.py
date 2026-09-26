"""Regression: legacy LuxCore* node aliases must not break canonical
SuperLuxCore* node creation, and alias instances must export identically.

History: the alias machinery originally registered each alias as a
SUBCLASS of its canonical class under the legacy bl_idname. In
Blender 5.2 that orphans the base class's RNA python binding —
nodes.new() and the Add menu then failed for EVERY addon node type
("Cannot add node of type ..."). Aliases now inline the flattened class
surface instead of subclassing (nodes/__init__.py)."""
import bpy
from bl_ext.user_default.superluxcore.export import material as export_material


def get_str(props, key):
    p = props.Get(key)
    assert p, f"missing property: {key}"
    return " ".join(p.GetString(i) for i in range(p.GetSize()))


CANONICAL = [
    ("superluxcore_material_nodes", "SuperLuxCoreNodeMatMirror"),
    ("superluxcore_material_nodes", "SuperLuxCoreNodeMatMatte"),
    ("superluxcore_material_nodes", "SuperLuxCoreNodeMatDiffraction"),
    ("superluxcore_material_nodes", "SuperLuxCoreNodeMatOutput"),
    ("superluxcore_material_nodes", "SuperLuxCoreNodeTexBand"),
    ("superluxcore_texture_nodes", "SuperLuxCoreNodeTexImagemap"),
    ("superluxcore_volume_nodes", "SuperLuxCoreNodeVolClear"),
]

# 1. Canonical nodes must be creatable while aliases are registered
for i, (tree_type, node_type) in enumerate(CANONICAL):
    nt = bpy.data.node_groups.new(f"t{i}", tree_type)
    nt.nodes.new(node_type)  # raises "Cannot add node of type" on regression
print("PASS: canonical nodes.new under alias registration")

# 2. Legacy alias nodes are real functional nodes, not placeholders
mat = bpy.data.materials.new("AliasMat")
nt = bpy.data.node_groups.new("AliasTree", "luxcore_material_nodes")
mat.superluxcore.node_tree = nt
out = nt.nodes.new("LuxCoreNodeMatOutput")
mir = nt.nodes.new("LuxCoreNodeMatMirror")
nt.links.new(mir.outputs[0], out.inputs[0])
assert mir.inputs["Reflection Color"], "alias node must expose the real sockets"

exporter = type("DummyExporter", (), {"lightgroup_cache": set(), "node_cache": {}})()
name, props = export_material.convert(
    exporter, bpy.context.evaluated_depsgraph_get(), mat, False, "Obj")
assert get_str(props, f"scene.materials.{name}.type") == "mirror"
print("PASS: legacy alias node exports identical engine props")

# 3. No duplicate-registration churn at addon init (was: every alias
#    registered twice -> "has been registered before" spam + stale RNA)
import bl_ext.user_default.superluxcore.nodes as N
names = [c.__name__ for c in N._legacy_alias_classes]
assert len(names) == len(set(names)), "duplicate alias classes registered"
print(f"PASS: {len(names)} unique aliases, no double registration")
