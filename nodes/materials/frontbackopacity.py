import bpy
from ..base import SuperLuxCoreNodeMaterial
from ...utils import node as utils_node


class SuperLuxCoreNodeMatFrontBackOpacity(SuperLuxCoreNodeMaterial, bpy.types.Node):
    """
    Not a standalone material, but a node that offers
    optional extra properties for all materials, to
    avoid cluttering all other material nodes.
    """
    bl_label = "Front/Back Opacity"
    bl_width_default = 160

    def init(self, context):
        self.add_input("SuperLuxCoreSocketMaterial", "Material")
        self.add_input("SuperLuxCoreSocketFloat0to1", "Front Opacity", 1)
        self.add_input("SuperLuxCoreSocketFloat0to1", "Back Opacity", 1)

        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        input_mat_name = utils_node.export_material_input(self.inputs["Material"], exporter, depsgraph, props, superluxcore_name)
        definitions = {
            "transparency.front": self.inputs["Front Opacity"].export(exporter, depsgraph, props),
            "transparency.back": self.inputs["Back Opacity"].export(exporter, depsgraph, props),
        }
        return self.create_props(props, definitions, input_mat_name)
