import bpy
from bpy.props import IntProperty
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node


class SuperLuxCoreNodeTexRandom(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Random"
    bl_width_default = 200

    seed: IntProperty(name="Seed", default=0, min=0,
                      update=utils_node.force_viewport_update)
    
    def init(self, context):
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Value", 0)
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "Value")

    def draw_buttons(self, context, layout):
        layout.label(text="Computationally expensive!")
        layout.prop(self, "seed")
    
    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "random",
            "texture": self.inputs["Value"].export(exporter, depsgraph, props),
            "seed": self.seed,
        }
        return self.create_props(props, definitions, superluxcore_name)
