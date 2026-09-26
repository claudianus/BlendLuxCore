import bpy
from bpy.props import FloatProperty
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node
from ..base import COLORDEPTH_DESC

class SuperLuxCoreNodeTexColorAtDepth(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Color at depth"
    bl_width_default = 200
    
    color_depth: FloatProperty(update=utils_node.force_viewport_update, name="Absorption Depth", default=1.0, min=0,
                                subtype="DISTANCE", unit="LENGTH",
                                description=COLORDEPTH_DESC)

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Absorption", (1, 1, 1))
        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        layout.prop(self, "color_depth")
    
    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        abs_col = self.inputs["Absorption"].export(exporter, depsgraph, props)

        definitions = {
            "type": "colordepth",
            "kt": abs_col,
            "depth": self.color_depth,
        }
        
        return self.create_props(props, definitions, superluxcore_name)
