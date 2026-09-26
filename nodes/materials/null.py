import bpy
from ..base import SuperLuxCoreNodeMaterial


class SuperLuxCoreNodeMatNull(SuperLuxCoreNodeMaterial, bpy.types.Node):
    bl_label = "Null Material"
    bl_width_default = 160

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Transmission Color", (1, 1, 1))

        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "null",
        }

        # This is a neat trick to get a colored transparent material:
        # Use a color or texture on the transparency property.
        # We only use it when we need it.
        transparency = self.inputs["Transmission Color"].export(exporter, depsgraph, props)
        if transparency != 1.0 and transparency != [1.0, 1.0, 1.0]:
            definitions["transparency"] = transparency

        return self.create_props(props, definitions, superluxcore_name)
