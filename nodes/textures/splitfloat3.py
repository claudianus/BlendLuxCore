import bpy
from ..base import SuperLuxCoreNodeTexture


class SuperLuxCoreNodeTexSplitFloat3(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Split RGB"
    bl_width_default = 100

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Color", (1, 1, 1))
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "R")
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "G")
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "B")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        if output_socket == self.outputs["R"]:
            channel = 0
        elif output_socket == self.outputs["G"]:
            channel = 1
        elif output_socket == self.outputs["B"]:
            channel = 2
        else:
            raise Exception("Unknown output socket in splitfloat3 texture")

        definitions = {
            "type": "splitfloat3",
            "texture": self.inputs[0].export(exporter, depsgraph, props),
            "channel": channel,
        }

        return self.create_props(props, definitions, superluxcore_name)
