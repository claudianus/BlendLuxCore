import math
import bpy
from bpy.props import IntProperty, BoolProperty, FloatProperty, PointerProperty, StringProperty, EnumProperty
from .clear import VOLUME_PRIORITY_DESC
from ..base import SuperLuxCoreNodeVolume, COLORDEPTH_DESC
from ... import utils
from ...utils import node as utils_node
from ...utils.light_descriptions import LIGHTGROUP_DESC
from ... import icons


STEP_SIZE_DESCRIPTION = (
    "Used to specify the granularity of the volume sampling steps. "
    "If the step size is too large, artifacts will appear. "
    "If it is too small, the rendering will be much slower"
)

MULTISCATTERING_DESC = (
    "Simulate multiple scattering events per ray. "
    "Makes volumes with high scattering scale appear more realistic, "
    "but leads to slower rendering performance"
)

TRACKING_DESC = (
    "Volume integration method. Delta tracking (null-collision) is unbiased, "
    "does not need a step size and skips empty space - faster in most scenes. "
    "Ray marching is the legacy fixed-step method"
)

MAJORANT_RES_DESC = (
    "Resolution of the majorant grid used to accelerate delta tracking. "
    "Higher values bound the density field more tightly (fewer wasted "
    "samples) at the cost of a slightly longer scene preparation"
)

PHASE_DESC = (
    "Scattering phase function. Henyey-Greenstein is the physically exact "
    "model for the asymmetry parameter. Schlick is a fast approximation "
    "(legacy default) with nearly identical cost"
)


class SuperLuxCoreNodeVolHeterogeneous(SuperLuxCoreNodeVolume, bpy.types.Node):
    bl_label = "Heterogeneous Volume"
    bl_width_default = 190

    # TODO: get name, default, description etc. from super class or something
    priority: IntProperty(update=utils_node.force_viewport_update, name="Priority", default=0, min=0,
                          description=VOLUME_PRIORITY_DESC)
    color_depth: FloatProperty(update=utils_node.force_viewport_update, name="Absorption Depth", default=1.0, min=0.000001,
                                subtype="DISTANCE", unit="LENGTH",
                                description=COLORDEPTH_DESC)
    lightgroup: StringProperty(update=utils_node.force_viewport_update, name="Light Group", description=LIGHTGROUP_DESC)

    step_size: FloatProperty(update=utils_node.force_viewport_update, name="Step Size", default=0.1, min=0.0001,
                              soft_min=0.01, soft_max=1,
                              subtype="DISTANCE", unit="LENGTH",
                              description=STEP_SIZE_DESCRIPTION)
    maxcount: IntProperty(update=utils_node.force_viewport_update, name="Max. Steps", default=1024, min=0,
                           description="Maximum Step Count for Volume Integration")
    auto_step_settings: BoolProperty(update=utils_node.force_viewport_update, name="Auto Step Settings", default=False,
                                      description="Enable when using a smoke domain. "
                                                  "Automatically calculates the correct step size and maximum steps")

    def poll_domain(self, obj):
        # Only allow objects with a smoke modifier in domain mode to be picked
        return utils.find_smoke_domain_modifier(obj)

    domain: PointerProperty(update=utils_node.force_viewport_update, name="Domain", type=bpy.types.Object, poll=poll_domain,
                             description="Domain object for calculating the step size and maximum steps settings")

    multiscattering: BoolProperty(update=utils_node.force_viewport_update, name="Multiscattering", default=False,
                                   description=MULTISCATTERING_DESC)

    tracking: EnumProperty(update=utils_node.force_viewport_update, name="Tracking", default="delta",
                            items=[
                                ("delta", "Delta Tracking", TRACKING_DESC),
                                ("march", "Ray Marching (Legacy)", TRACKING_DESC),
                            ],
                            description=TRACKING_DESC)
    majorant_res: IntProperty(update=utils_node.force_viewport_update, name="Majorant Resolution", default=32,
                               min=1, soft_max=256,
                               description=MAJORANT_RES_DESC)
    phase: EnumProperty(update=utils_node.force_viewport_update, name="Phase Function", default="schlick",
                         items=[
                             ("schlick", "Schlick (Fast)", PHASE_DESC),
                             ("hg", "Henyey-Greenstein (Exact)", PHASE_DESC),
                         ],
                         description=PHASE_DESC)

    def init(self, context):
        self.add_common_inputs()
        self.add_input("SuperLuxCoreSocketColor", "Scattering", (1, 1, 1))
        self.add_input("SuperLuxCoreSocketFloatPositive", "Scattering Scale", 1.0)
        self.add_input("SuperLuxCoreSocketVolumeAsymmetry", "Asymmetry", (0, 0, 0))

        self.outputs.new("SuperLuxCoreSocketVolume", "Volume")

    def draw_buttons(self, context, layout):
        layout.prop(self, "multiscattering")
        layout.prop(self, "phase")

        layout.prop(self, "tracking")
        if self.tracking == "delta":
            layout.prop(self, "majorant_res")
        else:
            layout.prop(self, "auto_step_settings")
            if self.auto_step_settings:
                layout.prop(self, "domain")

                if self.domain and not utils.find_smoke_domain_modifier(self.domain):
                    layout.label(text="Not a smoke domain!", icon=icons.WARNING)
                elif self.domain is None:
                    layout.label(text="Select the smoke domain object", icon=icons.WARNING)
            else:
                layout.prop(self, "step_size")
                layout.prop(self, "maxcount")

        self.draw_common_buttons(context, layout)

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "heterogeneous",
            "asymmetry": self.inputs["Asymmetry"].export(exporter, depsgraph, props),
            "multiscattering": self.multiscattering,
            "phase": self.phase,
            "tracking": self.tracking,
            "majorantres": self.majorant_res,
        }

        if self.tracking == "delta":
            # Step settings are unused by delta tracking
            self.export_common_inputs(exporter, depsgraph, props, definitions)
            return self.create_props(props, definitions, superluxcore_name)

        if self.auto_step_settings and self.domain:
            # Search smoke domain target for smoke modifiers
            domain_eval = self.domain.evaluated_get(depsgraph)
            smoke_domain_mod = utils.find_smoke_domain_modifier(domain_eval)

            if smoke_domain_mod is None:
                msg = 'Object "%s" is not a smoke domain' % domain_eval.name
                raise Exception(msg)

            settings = smoke_domain_mod.domain_settings
            # A list with 3 elements (resolution in x, y, z directions)
            resolutions = list(settings.domain_resolution)
            if bpy.app.version[:2] < (2, 82):
                if settings.use_high_resolution:
                    resolutions = [res * (settings.amplify + 1) for res in resolutions]
            else:
                if settings.use_noise:
                    resolutions = [res * settings.noise_scale for res in resolutions]

            dimensions = [dim for dim in domain_eval.dimensions]

            # The optimal step size on each axis
            step_sizes = [dim / res for dim, res in zip(dimensions, resolutions)]
            # Use the smallest step size in SuperLuxCore
            step_size = min(step_sizes)
            definitions["steps.size"] = step_size

            # Find the max. count required in the worst case
            diagonal = domain_eval.dimensions.length
            worst_case_maxcount = math.ceil(diagonal / step_size)
            definitions["steps.maxcount"] = worst_case_maxcount

            print('INFO: "%s" in volume node tree "%s" Auto Step Settings:' % (self.name, self.id_data.name))
            print("  Using steps.size = %.3f m" % step_size)
            print("  Using steps.maxcount =", worst_case_maxcount)
        else:
            definitions["steps.size"] = self.step_size
            definitions["steps.maxcount"] = self.maxcount

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
