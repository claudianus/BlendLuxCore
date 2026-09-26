import bpy
from bpy.props import FloatProperty, EnumProperty
from ..base import SuperLuxCoreNodeTexture
from ... import utils
from ...utils import node as utils_node


class SuperLuxCoreNodeTexPointiness(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Pointiness"
    bl_width_default = 180

    curvature_items = [
        ("concave", "Concave", "Only use dents"),
        ("convex", "Convex", "Only use hills"),
        ("both", "Both", "Use both hills and dents"),
    ]
    curvature_mode: EnumProperty(update=utils_node.force_viewport_update, items=curvature_items, default="both")

    def init(self, context):
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Multiplier", 10)
        
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "Value")
        
        # This node potentially requires a mesh re-export in viewport,
        # because it depends on a SuperLuxCore shape to pre-process the data.
        # (id_data is None while the node is being created.)
        if self.id_data is not None:
            utils_node.force_viewport_mesh_update2(self.id_data)

    def draw_buttons(self, context, layout):
        layout.prop(self, "curvature_mode", expand=True)

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        # Pointiness is a hitpointalpha texture behind the scenes, just that it implicitly enables pointiness
        # calculation on the mesh (handled in superluxcore object export) and has some nice wrapping to get only part of
        # the pointiness information (see code below)
        
        definitions = {
            "type": "hitpointalpha",
        }

        superluxcore_name = self.create_props(props, definitions, superluxcore_name)

        if self.curvature_mode == "both":
            # Pointiness values are in [-1..1] range originally
            name_abs = superluxcore_name + "_abs"
            helper_prefix = "scene.textures." + name_abs + "."
            helper_defs = {
                "type": "abs",
                "texture": superluxcore_name,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            superluxcore_name = name_abs

        elif self.curvature_mode == "concave":
            # Only use the positive values of the pointiness information
            name_clamp = superluxcore_name + "_clamp"
            helper_prefix = "scene.textures." + name_clamp + "."
            helper_defs = {
                "type": "clamp",
                "texture": superluxcore_name,
                "min": 0,
                "max": 1,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            superluxcore_name = name_clamp

        elif self.curvature_mode == "convex":
            # Only use the negative values of the pointiness information by first flipping the values
            name_flip = superluxcore_name + "_flip"
            helper_prefix = "scene.textures." + name_flip + "."
            helper_defs = {
                "type": "scale",
                "texture1": superluxcore_name,
                "texture2": -1,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            name_clamp = superluxcore_name + "_clamp"
            helper_prefix = "scene.textures." + name_clamp + "."
            helper_defs = {
                "type": "clamp",
                "texture": name_flip,
                "min": 0,
                "max": 1,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            superluxcore_name = name_clamp

        multiplier = self.inputs["Multiplier"].export(exporter, depsgraph, props)

        if multiplier != 1:
            multiplier_name = superluxcore_name + "_multiplier"
            helper_prefix = "scene.textures." + multiplier_name + "."
            helper_defs = {
                "type": "scale",
                "texture1": superluxcore_name,
                "texture2": multiplier,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            superluxcore_name = multiplier_name

        return superluxcore_name
