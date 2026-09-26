import bpy
from bpy.props import FloatProperty
from ..base import LuxCoreNodeTexture
from ...utils import node as utils_node


class LuxCoreNodeTexBevel(LuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Bevel"
    bl_width_default = 160

    # Changes to this node require the edgedetectoraov shape wrapper
    radius: FloatProperty(
        update=utils_node.force_viewport_update, name="Radius",
        description="Width of the rounded edge effect (bump only, does not "
                    "change the mesh silhouette)", min=0, soft_max=0.5,
        subtype="DISTANCE", unit="LENGTH", default=0.025)

    def init(self, context):
        self.outputs.new("LuxCoreSocketBump", "Bump")

    def draw_buttons(self, context, layout):
        layout.prop(self, "radius")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "bevel",
            "radius": self.radius,
        }

        return self.create_props(props, definitions, luxcore_name)
