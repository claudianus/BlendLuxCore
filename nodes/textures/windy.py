import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexWindy(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Windy"
    bl_width_default = 200
    
    def init(self, context):
        self.add_input("SuperLuxCoreSocketMapping3D", "3D Mapping")
        self.outputs.new("SuperLuxCoreSocketColor", "Color")
    
    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "windy",
        }
        definitions.update(self.inputs["3D Mapping"].export(exporter, depsgraph, props))
        return self.create_props(props, definitions, superluxcore_name)
