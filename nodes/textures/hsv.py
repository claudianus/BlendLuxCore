import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexHSV(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Hue Saturation Value"

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Color", [1, 1, 1])
        self.add_input("SuperLuxCoreSocketFloat0to1", "Hue", 0.5)
        self.add_input("SuperLuxCoreSocketFloat0to2", "Saturation", 1)
        self.add_input("SuperLuxCoreSocketFloat0to2", "Value", 1)

        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "hsv",
            "texture": self.inputs["Color"].export(exporter, depsgraph, props),
            "hue": self.inputs["Hue"].export(exporter, depsgraph, props),
            "saturation": self.inputs["Saturation"].export(exporter, depsgraph, props),
            "value": self.inputs["Value"].export(exporter, depsgraph, props),
        }
        return self.create_props(props, definitions, superluxcore_name)
