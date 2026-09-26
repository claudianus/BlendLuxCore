import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from ..base import SuperLuxCoreNodeTexture
from .imagemap import NORMAL_SCALE_DESC
from ...utils import node as utils_node


class SuperLuxCoreNodeTexNormalmap(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Normalmap"

    scale: FloatProperty(update=utils_node.force_viewport_update, name="Height", default=1, min=0, soft_max=5,
                          description=NORMAL_SCALE_DESC)

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Color", [0.5, 0.5, 1])

        self.outputs.new("SuperLuxCoreSocketBump", "Bump")

    def draw_buttons(self, context, layout):
        layout.prop(self, "scale")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "normalmap",
            "texture": self.inputs["Color"].export(exporter, depsgraph, props),
            "scale": self.scale,
        }

        return self.create_props(props, definitions, superluxcore_name)
