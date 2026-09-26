import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexBrightContrast(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Brightness/Contrast"

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Color", [1, 1, 1])
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Brightness", 0)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Contrast", 0)

        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "brightcontrast",
            "texture": self.inputs["Color"].export(exporter, depsgraph, props),
            "brightness": self.inputs["Brightness"].export(exporter, depsgraph, props),
            "contrast": self.inputs["Contrast"].export(exporter, depsgraph, props),
        }
        return self.create_props(props, definitions, superluxcore_name)
