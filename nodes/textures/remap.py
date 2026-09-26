import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexRemap(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Remap"
    bl_width_default = 160

    def init(self, context):
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Value", 0.5)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Source Min", 0)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Source Max", 1)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Target Min", 0)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Target Max", 1)

        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "Value")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "remap",
            "value": self.inputs["Value"].export(exporter, depsgraph, props),
            "sourcemin": self.inputs["Source Min"].export(exporter, depsgraph, props),
            "sourcemax": self.inputs["Source Max"].export(exporter, depsgraph, props),
            "targetmin": self.inputs["Target Min"].export(exporter, depsgraph, props),
            "targetmax": self.inputs["Target Max"].export(exporter, depsgraph, props),
        }
        return self.create_props(props, definitions, superluxcore_name)
