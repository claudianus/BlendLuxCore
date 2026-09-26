import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexInvert(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Invert"
    bl_width_default = 100

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Color", (1, 1, 1))
        self.add_input("SuperLuxCoreSocketFloatPositive", "Maximum", 1)

        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "subtract",
            "texture1": self.inputs["Maximum"].export(exporter, depsgraph, props),
            "texture2": self.inputs["Color"].export(exporter, depsgraph, props),
        }

        return self.create_props(props, definitions, superluxcore_name)
