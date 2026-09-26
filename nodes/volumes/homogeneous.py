import bpy
from bpy.props import IntProperty, BoolProperty, FloatProperty, StringProperty, EnumProperty
from .clear import VOLUME_PRIORITY_DESC
from ..base import SuperLuxCoreNodeVolume, COLORDEPTH_DESC
from ...utils import node as utils_node
from ...utils.light_descriptions import LIGHTGROUP_DESC

PHASE_DESC = (
    "Scattering phase function. Henyey-Greenstein is the physically exact "
    "model for the asymmetry parameter. Schlick is a fast approximation "
    "(legacy default) with nearly identical cost"
)

DISTSAMP_DESC = (
    "Scattering distance sampling strategy. 'Equiangular + Transmittance' "
    "mixes two distributions with MIS: vertices are preferentially placed "
    "in the glow around point/spot lights (god rays, candle-lit fog), "
    "which lowers noise there. Only active when point-like lights exist; "
    "CPU engines only, GPU rendering falls back to transmittance sampling. "
    "'Transmittance' is the classic exponential sampler"
)


PARAM_DESC = (
    "Scattering parametrization. 'Coefficients' uses raw absorption/"
    "scattering cross-sections. 'Albedo / Mean Free Path' takes the "
    "diffuse surface albedo and the scattering mean free path (as in "
    "OpenPBR subsurface) and derives sigma_a/sigma_s internally via the "
    "d'Eon closed-form inversion - the physical choice for skin, wax, "
    "marble and other subsurface materials"
)


class SuperLuxCoreNodeVolHomogeneous(SuperLuxCoreNodeVolume, bpy.types.Node):
    bl_label = "Homogeneous Volume"
    bl_width_default = 175

    def _set_enabled(self, names, enabled):
        for name in names:
            self.inputs[self.inputs.find(name)].enabled = enabled

    def update_parametrization(self, context):
        albedo = self.parametrization == "albedo"
        self._set_enabled(("Scattering", "Scattering Scale"), not albedo)
        self._set_enabled(("Albedo", "Mean Free Path"), albedo)

    # TODO: get name, default, description etc. from super class or something
    priority: IntProperty(update=utils_node.force_viewport_update, name="Priority", default=0, min=0,
                          description=VOLUME_PRIORITY_DESC)
    color_depth: FloatProperty(update=utils_node.force_viewport_update, name="Absorption Depth", default=1.0, min=0.000001,
                                subtype="DISTANCE", unit="LENGTH",
                                description=COLORDEPTH_DESC)
    lightgroup: StringProperty(update=utils_node.force_viewport_update, name="Light Group", description=LIGHTGROUP_DESC)

    parametrization: EnumProperty(update=update_parametrization, name="Parametrization", default="coefficients",
                         items=[
                             ("coefficients", "Coefficients", PARAM_DESC),
                             ("albedo", "Albedo / Mean Free Path", PARAM_DESC),
                         ],
                         description=PARAM_DESC)
    multiscattering: BoolProperty(update=utils_node.force_viewport_update, name="Multiscattering", default=False)
    phase: EnumProperty(update=utils_node.force_viewport_update, name="Phase Function", default="schlick",
                         items=[
                             ("schlick", "Schlick (Fast)", PHASE_DESC),
                             ("hg", "Henyey-Greenstein (Exact)", PHASE_DESC),
                         ],
                         description=PHASE_DESC)
    distance_sampling: EnumProperty(update=utils_node.force_viewport_update, name="Distance Sampling", default="equiangular",
                         items=[
                             ("equiangular", "Equiangular + Transmittance (MIS)", DISTSAMP_DESC),
                             ("transmittance", "Transmittance (Classic)", DISTSAMP_DESC),
                         ],
                         description=DISTSAMP_DESC)

    def init(self, context):
        self.add_common_inputs()
        self.add_input("SuperLuxCoreSocketColor", "Scattering", (1, 1, 1))
        self.add_input("SuperLuxCoreSocketFloatPositive", "Scattering Scale", 1.0)
        self.add_input("SuperLuxCoreSocketColor", "Albedo", (0.5, 0.5, 0.5), enabled=False)
        self.add_input("SuperLuxCoreSocketFloatPositive", "Mean Free Path", 0.1, enabled=False)
        self.add_input("SuperLuxCoreSocketVolumeAsymmetry", "Asymmetry", (0, 0, 0))

        self.outputs.new("SuperLuxCoreSocketVolume", "Volume")

    def draw_buttons(self, context, layout):
        layout.prop(self, "parametrization")
        layout.prop(self, "multiscattering")
        layout.prop(self, "phase")
        layout.prop(self, "distance_sampling")
        self.draw_common_buttons(context, layout)

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "homogeneous",
            "asymmetry": self.inputs["Asymmetry"].export(exporter, depsgraph, props),
            "multiscattering": self.multiscattering,
            "phase": self.phase,
            "distancesampling": self.distance_sampling,
        }
        if self.parametrization == "albedo":
            definitions["sssalbedo"] = self.inputs["Albedo"].export(exporter, depsgraph, props)
            definitions["sssmfp"] = self.inputs["Mean Free Path"].export(exporter, depsgraph, props)
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
