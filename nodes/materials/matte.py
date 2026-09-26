import bpy
from bpy.props import FloatProperty
from ..base import SuperLuxCoreNodeMaterial
from ..sockets import SuperLuxCoreSocketFloat
from ...utils import node as utils_node

SIGMA_DESCRIPTION = "Surface roughness, 0 for pure Lambertian reflection"

class SuperLuxCoreSocketSigma(bpy.types.NodeSocket, SuperLuxCoreSocketFloat):
    default_value: FloatProperty(min=0, max=45, description=SIGMA_DESCRIPTION,
                                 update=utils_node.force_viewport_update)
    slider = True


class SuperLuxCoreNodeMatMatte(SuperLuxCoreNodeMaterial, bpy.types.Node):
    """(Rough) matte material node"""
    bl_label = "Matte Material"
    bl_width_default = 160

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Diffuse Color", (0.7, 0.7, 0.7))
        self.add_input("SuperLuxCoreSocketSigma", "Sigma", 0)
        self.add_common_inputs()

        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        sigma = self.inputs["Sigma"].export(exporter, depsgraph, props)
        mat_type = "matte" if sigma == 0 else "roughmatte"
        definitions = {
            "type": mat_type,
            "kd": self.inputs["Diffuse Color"].export(exporter, depsgraph, props),
        }
        if mat_type == "roughmatte":
            definitions["sigma"] = sigma
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
