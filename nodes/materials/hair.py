import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from ..base import SuperLuxCoreNodeMaterial
from ...utils import node as utils_node

MODEL_ITEMS = [
    ("huang", "Huang", "Microfacet-based hair model (Huang et al. 2022). "
     "Physically-based elliptical cross-section, GGX lobes, far-field "
     "energy compensation", 0),
    ("chiang", "Chiang", "Principled hair model (Chiang et al. 2019). "
     "Near-field R/TT/TRT lobes with longitudinal/azimuthal roughness", 1),
]

COLORIZATION_ITEMS = [
    ("melanin", "Melanin", "Absorption from eumelanin/pheomelanin "
     "concentrations", 0),
    ("color", "Direct Color", "Approximate single-scatter color target", 1),
    ("sigma_a", "Absorption Coefficient",
     "Explicit absorption coefficient (section 4.2)", 2),
]


class SuperLuxCoreNodeMatHair(SuperLuxCoreNodeMaterial, bpy.types.Node):
    """Hair material node (Chiang/Huang models)"""
    bl_label = "Hair Material"
    bl_width_default = 200

    def update_model(self, context):
        huang = self.model == "huang"
        self.inputs["Roughness"].enabled = huang
        self.inputs["Aspect Ratio"].enabled = huang
        self.inputs["Longitudinal Roughness"].enabled = not huang
        self.inputs["Azimuthal Roughness"].enabled = not huang
        utils_node.force_viewport_update(self, context)

    def update_colorization(self, context):
        self.inputs["Color"].enabled = self.colorization == "color"
        self.inputs["Eumelanin"].enabled = self.colorization == "melanin"
        self.inputs["Pheomelanin"].enabled = self.colorization == "melanin"
        self.inputs["Absorption Coefficient"].enabled = \
            self.colorization == "sigma_a"
        utils_node.force_viewport_update(self, context)

    model: EnumProperty(name="Model", items=MODEL_ITEMS, default="huang",
                        update=update_model)
    colorization: EnumProperty(name="Colorization", items=COLORIZATION_ITEMS,
                               default="melanin", update=update_colorization)

    # Huang-only lobe energy scales (engine scalars, not textures)
    show_lobe_scales: BoolProperty(
        name="Lobe Scales",
        description="Per-lobe energy scales (Huang model): artistic control "
                    "over R/TT/TRT contributions, below 1.0 is non-physical",
        default=False)
    scale_r: FloatProperty(name="R Scale", default=1.0, min=0.0, max=1.0,
        description="Primary reflection lobe energy (white highlight)")
    scale_tt: FloatProperty(name="TT Scale", default=1.0, min=0.0, max=1.0,
        description="Transmit-transmit lobe energy (main body brightness)")
    scale_trt: FloatProperty(name="TRT Scale", default=1.0, min=0.0, max=1.0,
        description="Transmit-reflect-transmit lobe energy (colored sheen)")

    def init(self, context):
        # Colorization
        self.add_input("SuperLuxCoreSocketColor", "Color", (0.4, 0.2, 0.05))
        self.add_input("SuperLuxCoreSocketFloatPositive", "Eumelanin", 0.3)
        self.add_input("SuperLuxCoreSocketFloatPositive", "Pheomelanin", 0.0)
        self.add_input("SuperLuxCoreSocketColor", "Absorption Coefficient",
                       (0.0, 0.0, 0.0))
        # Shared geometry parameters
        self.add_input("SuperLuxCoreSocketIOR", "IOR", 1.55)
        self.add_input("SuperLuxCoreSocketFloatUnbounded", "Tilt", 2.0)
        # Chiang
        self.add_input("SuperLuxCoreSocketFloat0to1", "Longitudinal Roughness", 0.3)
        self.add_input("SuperLuxCoreSocketFloat0to1", "Azimuthal Roughness", 0.3)
        # Huang
        self.add_input("SuperLuxCoreSocketFloat0to1", "Roughness", 0.3)
        self.add_input("SuperLuxCoreSocketFloat0to2", "Aspect Ratio", 0.85)

        self.add_common_inputs()
        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

        self.update_model(context)
        self.update_colorization(context)

    def draw_buttons(self, context, layout):
        layout.prop(self, "model")
        layout.prop(self, "colorization")
        if self.model == "huang":
            layout.prop(self, "show_lobe_scales")
            if self.show_lobe_scales:
                col = layout.column(align=True)
                col.prop(self, "scale_r")
                col.prop(self, "scale_tt")
                col.prop(self, "scale_trt")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None,
                   output_socket=None):
        definitions = {
            "type": "hairmat",
            "model": self.model,
            "eta": self.inputs["IOR"].export(exporter, depsgraph, props),
            "alpha": self.inputs["Tilt"].export(exporter, depsgraph, props),
        }

        if self.colorization == "color":
            definitions["color"] = \
                self.inputs["Color"].export(exporter, depsgraph, props)
        elif self.colorization == "sigma_a":
            definitions["sigma_a"] = self.inputs["Absorption Coefficient"] \
                .export(exporter, depsgraph, props)
        else:
            definitions["eumelanin"] = \
                self.inputs["Eumelanin"].export(exporter, depsgraph, props)
            definitions["pheomelanin"] = \
                self.inputs["Pheomelanin"].export(exporter, depsgraph, props)

        if self.model == "huang":
            definitions["roughness"] = \
                self.inputs["Roughness"].export(exporter, depsgraph, props)
            definitions["aspectratio"] = \
                self.inputs["Aspect Ratio"].export(exporter, depsgraph, props)
            definitions["scale_r"] = self.scale_r
            definitions["scale_tt"] = self.scale_tt
            definitions["scale_trt"] = self.scale_trt
        else:
            definitions["beta_m"] = self.inputs["Longitudinal Roughness"] \
                .export(exporter, depsgraph, props)
            definitions["beta_n"] = self.inputs["Azimuthal Roughness"] \
                .export(exporter, depsgraph, props)

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
