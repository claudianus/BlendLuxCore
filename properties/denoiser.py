from bpy.types import PropertyGroup
from bpy.props import (
    BoolProperty, IntProperty, FloatProperty, EnumProperty, StringProperty,
)

REFRESH_DESC = (
    "Update the denoised image (takes a few seconds to minutes, "
    "progress is shown in the status bar)"
)
SCALES_DESC = (
    "Try to use more scales if the denoised image shows blotchiness "
    "(higher values increase computation time and RAM usage)"
)
HIST_DIST_THRESH_DESC = (
    "A lower value will make the output sharper while a value higher "
    "than 1.0 will make the result more blurry"
)
SEARCH_WINDOW_RADIUS_DESC = (
    "Higher values improve the denoiser result, but lead to longer computation time"
)
FILTER_SPIKES_DESC = "Filter outliers from the input samples"
MAX_MEMORY_DESC = (
    "Approximate maximum amount of memory to use in megabytes (actual memory usage "
    "may be higher). Limiting memory usage may cause slower denoising due to internally "
    "splitting the image into overlapping tiles"
)


class SuperLuxCoreDenoiser(PropertyGroup):
    refresh = False

    enabled: BoolProperty(name="", default=False, description="Enable/disable denoiser")
    type_items = [
        ("BCD", "Bayesian Collaborative Denoiser", "", 0),
        ("OIDN", "Intel Open Image Denoiser", "", 1),
    ]
    type: EnumProperty(name="Type", items=type_items, default="OIDN")

    # BCD settings
    scales: IntProperty(name="Scales", default=3, min=1, soft_max=5,
                         description=SCALES_DESC)
    hist_dist_thresh: FloatProperty(name="Histogram Distance Threshold", default=1, min=0, soft_max=3,
                                     description=HIST_DIST_THRESH_DESC)
    # TODO: description for patch radius
    patch_radius: IntProperty(name="Patch Radius", default=1, min=1, soft_max=3)
    search_window_radius: IntProperty(name="Search Window Radius", default=6, min=1, soft_max=9,
                                       description=SEARCH_WINDOW_RADIUS_DESC)
    filter_spikes: BoolProperty(name="Remove Fireflies", default=False,
                                 description=FILTER_SPIKES_DESC)

    # OIDN settings
    max_memory_MB: IntProperty(name="Max. Memory (MB)", default=6000, min=100, soft_min=1000,
                               description=MAX_MEMORY_DESC)
    albedo_specular_passthrough_modes = [
        ("REFLECT_TRANSMIT", "Reflect and Transmit", "The albedo AOV will contain reflected and transmitted colors from specular materials", 0),
        ("ONLY_REFLECT", "Reflect", "The albedo AOV will contain only reflected colors from specular materials", 1),
        ("ONLY_TRANSMIT", "Transmit", "The albedo AOV will contain only transmitted colors from specular materials", 2),
        ("NO_REFLECT_TRANSMIT", "None", "Specular materials will be a flat white in the albedo AOV", 3),
    ]
    albedo_specular_passthrough_mode: EnumProperty(name="Albedo Specular Passthrough", items=albedo_specular_passthrough_modes,
                                                  default="REFLECT_TRANSMIT", description="How to treat specular materials in the albedo AOV")
    prefilter_AOVs: BoolProperty(name="Prefilter Auxiliary AOVs", default=True,
                                 description="Denoise the albedo and avg. shading normal AOVs before using them to denoise the main image")

    oidn_mode_items = [
        ("COMBINED", "Combined",
         "Denoise the beauty image in a single pass (classic OIDN behaviour)", 0),
        ("COMPONENTS", "Components",
         "Denoise each lighting component (direct/indirect, diffuse/glossy/specular) "
         "separately and recombine. Better preserves texture detail and edges", 1),
    ]
    oidn_mode: EnumProperty(name="Denoise Mode", items=oidn_mode_items, default="COMPONENTS",
                            description="OIDN input decomposition recipe")
    oidn_demodulate: BoolProperty(name="Albedo Demodulation", default=True,
                                  description="Components mode: filter the indirect diffuse component "
                                  "in illumination space (radiance divided by albedo) and multiply the "
                                  "albedo back afterwards - texture detail can not be blurred")
    oidn_denoise_emission: BoolProperty(name="Denoise Emission", default=False,
                                        description="Components mode: also denoise the emission component. "
                                        "Leave off to keep light source silhouettes crisp")
    oidn_firefly_sigma: FloatProperty(name="Firefly Suppression", default=0.0, min=0.0, soft_max=8.0,
                                      description="Components mode: clamp isolated outlier pixels before "
                                      "denoising, in units of local robust sigma (0 disables). Useful for "
                                      "scenes with sparse fireflies; may flatten dense noise textures")

    # Temporal accumulation (animation denoising, final renders only)
    temporal_enabled: BoolProperty(name="Temporal Accumulation", default=False,
                                   description="Reproject and blend previous frames through motion "
                                   "vectors before denoising - kills animation flicker. Enable the "
                                   "animated seed (Render > Sampling > Seed) so frames carry "
                                   "independent noise that can actually converge")
    temporal_history: FloatProperty(name="History Length", default=16, min=1, soft_max=64,
                                    description="Maximum number of frames folded into the accumulation. "
                                    "Higher values are smoother but lag more on lighting changes")
    temporal_clip_sigma: FloatProperty(name="Clip Sigma", default=2.5, min=0, soft_max=8,
                                       description="Neighbourhood clamp of the reprojected history, in "
                                       "combined spatial + sample standard deviations. Lower values "
                                       "suppress ghosting more aggressively; 0 disables clipping")
    temporal_depth_threshold: FloatProperty(name="Depth Threshold", default=0.05, min=0, soft_max=0.5,
                                            description="Relative depth difference that marks a history "
                                            "sample as disoccluded (stale)")
    temporal_normal_threshold: FloatProperty(name="Normal Threshold", default=0.6, min=-1, max=1,
                                             description="Minimum dot product between previous and current "
                                             "shading normals for a history sample to be reused")
    temporal_statedir: StringProperty(name="State Directory", default="//superluxcore_temporal/",
                                      subtype='DIR_PATH',
                                      description="Where the temporal history EXRs are stored. Cleared "
                                      "automatically when rendering from the first frame")
