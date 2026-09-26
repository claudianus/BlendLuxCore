import bpy
from bpy.props import FloatProperty, BoolProperty
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node


class SuperLuxCoreNodeTexBombing(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Bombing"
    bl_width_default = 200

    random_scale: FloatProperty(name="Scale Randomness", default=0, min=0, soft_max=1,
                                update=utils_node.force_viewport_update)
    use_random_rotation: BoolProperty(name="Random Rotation", default=True,
                                      update=utils_node.force_viewport_update)

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Background", [0.3, 0.3, 0.3])
        self.add_input("SuperLuxCoreSocketColor", "Bullet", [0.7, 0, 0])
        self.add_input("SuperLuxCoreSocketFloat0to1", "Mask", 1)
        self.add_input("SuperLuxCoreSocketMapping2D", "2D Mapping")
        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        if not self.inputs["2D Mapping"].is_linked:
            utils_node.draw_uv_info(context, layout)

        layout.prop(self, "random_scale", slider=True)
        layout.prop(self, "use_random_rotation")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        # TODO support for multiple bullets?
        definitions = {
            "type": "bombing",
            "background": self.inputs["Background"].export(exporter, depsgraph, props),
            "bullet": self.inputs["Bullet"].export(exporter, depsgraph, props),
            "bullet.mask": self.inputs["Mask"].export(exporter, depsgraph, props),
            "bullet.randomscale.range": self.random_scale * 5,
            "bullet.randomrotation.enable": self.use_random_rotation,
        }
        definitions.update(self.inputs["2D Mapping"].export(exporter, depsgraph, props))
        return self.create_props(props, definitions, superluxcore_name)
