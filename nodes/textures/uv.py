import bpy
from ..base import SuperLuxCoreNodeTexture

class SuperLuxCoreNodeTexUV(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "UV Test"

    def init(self, context):
        self.add_input("SuperLuxCoreSocketMapping2D", "2D Mapping")

        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "uv",
        }
        definitions.update(self.inputs["2D Mapping"].export(exporter, depsgraph, props))
        return self.create_props(props, definitions, superluxcore_name)
