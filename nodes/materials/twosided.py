import bpy
from ..base import SuperLuxCoreNodeMaterial
from ...utils import node as utils_node


class SuperLuxCoreNodeMatTwoSided(SuperLuxCoreNodeMaterial, bpy.types.Node):
    bl_label = "Two Sided Material"
    bl_width_default = 160

    def init(self, context):
        self.add_input("SuperLuxCoreSocketMaterial", "Front Material")
        self.add_input("SuperLuxCoreSocketMaterial", "Back Material")
        self.add_common_inputs()

        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        # Material inputs need special export because their sockets can't
        # construct a black fallback material in their export method
        front_mat = utils_node.export_material_input(self.inputs["Front Material"], exporter, depsgraph, props)
        back_mat = utils_node.export_material_input(self.inputs["Back Material"], exporter, depsgraph, props)

        definitions = {
            "type": "twosided",
            "frontmaterial": front_mat,
            "backmaterial": back_mat,
        }
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
