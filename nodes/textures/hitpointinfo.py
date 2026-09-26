import bpy
from bpy.props import EnumProperty
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node


class SuperLuxCoreNodeTexHitpointInfo(SuperLuxCoreNodeTexture, bpy.types.Node):
    """ Access to various hitpoint attributes """
    bl_label = "Hitpoint Info"
    bl_width_default = 150

    def init(self, context):
        self.outputs.new("SuperLuxCoreSocketVector", "Shading Normal")
        self.outputs.new("SuperLuxCoreSocketVector", "Position")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {}
        if output_socket == self.outputs["Shading Normal"]:
            definitions["type"] = "shadingnormal"
        elif output_socket == self.outputs["Position"]:
            definitions["type"] = "position"
        else:
            raise Exception("Unknown output socket:", output_socket)
        return self.create_props(props, definitions, superluxcore_name)
