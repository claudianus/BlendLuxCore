import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from ..base import SuperLuxCoreNodeTexture
from ... import utils
from ... import icons
from .math import MIX_DESCRIPTION
from ...utils import node as utils_node


class SuperLuxCoreNodeTexColorMix(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Color Math"
    bl_width_default = 200

    mode_items = [
        ("scale", "Multiply", ""),
        ("add", "Add", ""),
        ("subtract", "Subtract", ""),
        ("mix", "Mix", MIX_DESCRIPTION),
        ("clamp", "Clamp", "Clamp the input so it is between min and max values"),
        ("abs", "Absolute", "Take the absolute value (remove minus sign)"),
    ]

    def change_mode(self, context):
        id = self.inputs.find("Color 2")
        if self.mode in {"clamp", "abs"}:
            self.inputs[1].name = "Color"
            self.inputs[id].enabled = False
        else:
            self.inputs[1].name = "Color 1"
            self.inputs[id].enabled = True
        
        id = self.inputs.find("Fac")
        if self.mode == "mix":
            self.inputs[id].enabled = True
        else:
            self.inputs[id].enabled = False
        utils_node.force_viewport_update(self, context)

    mode: EnumProperty(name="Mode", items=mode_items, default="mix", update=change_mode)
    clamp_output: BoolProperty(update=utils_node.force_viewport_update, name="Clamp", default=False, description="Limit the output value to 0..1 range")

    mode_clamp_min: FloatProperty(update=utils_node.force_viewport_update, name="Min", description="", default=0)
    mode_clamp_max: FloatProperty(update=utils_node.force_viewport_update, name="Max", description="", default=1)

    def draw_label(self):
        # Use the name of the selected operation as displayed node name
        for elem in self.mode_items:
            if self.mode in elem:
                return elem[1]

    def init(self, context):
        self.add_input("SuperLuxCoreSocketFloat0to1", "Fac", 1)
        self.add_input("SuperLuxCoreSocketColor", "Color 1", (0.7, 0.7, 0.7))
        self.add_input("SuperLuxCoreSocketColor", "Color 2", (0.04, 0.04, 0.04))

        self.outputs.new("SuperLuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        layout.prop(self, "mode")

        if self.mode != "clamp":
            layout.prop(self, "clamp_output")

        if self.mode == "clamp":
            layout.prop(self, "mode_clamp_min")
            layout.prop(self, "mode_clamp_max")

            if self.mode_clamp_min > self.mode_clamp_max:
                layout.label(text="Min should be smaller than max!", icon=icons.WARNING)

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": self.mode,
        }
        
        if self.mode == "abs":
            definitions["texture"] = self.inputs["Color"].export(exporter, depsgraph, props)
        elif self.mode == "clamp":
            definitions["texture"] = self.inputs["Color"].export(exporter, depsgraph, props)
            definitions["min"] = self.mode_clamp_min
            definitions["max"] = self.mode_clamp_max
        else:
            definitions["texture1"] = self.inputs["Color 1"].export(exporter, depsgraph, props)
            definitions["texture2"] = self.inputs["Color 2"].export(exporter, depsgraph, props)

            if self.mode == "mix":
                definitions["amount"] = self.inputs["Fac"].export(exporter, depsgraph, props)

        superluxcore_name = self.create_props(props, definitions, superluxcore_name)

        if self.clamp_output and self.mode != "clamp":
            # Implicitly create a clamp texture with unique name
            tex_name = superluxcore_name + "_clamp"
            helper_prefix = "scene.textures." + tex_name + "."
            helper_defs = {
                "type": "clamp",
                "texture": superluxcore_name,
                "min": 0,
                "max": 1,
            }
            props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

            # The helper texture gets linked in front of this node
            return tex_name
        else:
            return superluxcore_name
