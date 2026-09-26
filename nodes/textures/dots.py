import bpy
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node

class SuperLuxCoreNodeTexDots(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Dots"
    bl_width_default = 200
    
    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Inside", (1.0, 1.0, 1.0))
        self.add_input("SuperLuxCoreSocketColor", "Outside", (0.0, 0.0, 0.0))
        self.add_input("SuperLuxCoreSocketMapping2D", "2D Mapping")
        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        if not self.inputs["2D Mapping"].is_linked:
            utils_node.draw_uv_info(context, layout)
    
    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "dots",
            "inside": self.inputs["Inside"].export(exporter, depsgraph, props),
            "outside": self.inputs["Outside"].export(exporter, depsgraph,  props),
        }
        definitions.update(self.inputs["2D Mapping"].export(exporter, depsgraph, props))
        return self.create_props(props, definitions, superluxcore_name)
