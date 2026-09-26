from ... import utils
from . import caches, config, debug, denoiser, devices, errorlog, halt, image_resize_policy, sampling, simple, tools, viewport

classes = (
    simple.SUPERLUXCORE_RENDER_PT_simple,
    # Sampling (Cycles-style first panel: samples + denoise + sampler)
    sampling.SUPERLUXCORE_RENDER_PT_sampling,
    denoiser.SUPERLUXCORE_RENDER_PT_denoiser,
    denoiser.SUPERLUXCORE_RENDER_PT_denoiser_temporal,
    denoiser.SUPERLUXCORE_RENDER_PT_denoiser_bcd_advanced,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_tiled_multipass,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_adaptivity,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_pixel_filtering,
    halt.SUPERLUXCORE_RENDER_PT_halt_conditions,
    halt.SUPERLUXCORE_RENDERLAYER_PT_halt_conditions,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_advanced,
    # Light Paths
    config.SUPERLUXCORE_RENDER_PT_lightpaths,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_bounces,
    config.SUPERLUXCORE_RENDER_PT_add_light_tracing,
    config.SUPERLUXCORE_RENDER_PT_bidir_focus,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_strategy,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_guiding,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_clamping,
    # Render Caches
    caches.SUPERLUXCORE_RENDER_PT_caches,
    caches.SUPERLUXCORE_RENDER_PT_caches_photongi,
    caches.SUPERLUXCORE_RENDER_PT_caches_photongi_indirect,
    caches.SUPERLUXCORE_RENDER_PT_caches_photongi_caustic,
    caches.SUPERLUXCORE_RENDER_PT_caches_photongi_persistence,
    caches.SUPERLUXCORE_RENDER_PT_caches_envlight,
    caches.SUPERLUXCORE_RENDER_PT_caches_envlight_persistence,
    caches.SUPERLUXCORE_RENDER_PT_caches_DLSC,
    caches.SUPERLUXCORE_RENDER_PT_caches_DLSC_advanced,
    caches.SUPERLUXCORE_RENDER_PT_caches_DLSC_persistence,
    # Performance
    devices.SUPERLUXCORE_RENDER_PT_devices,
    devices.SUPERLUXCORE_RENDER_PT_gpu_devices,
    devices.SUPERLUXCORE_RENDER_PT_cpu_devices,
    devices.SUPERLUXCORE_RENDER_PT_memory,
    image_resize_policy.SUPERLUXCORE_RENDER_PT_image_resize_policy,
    tools.SUPERLUXCORE_RENDER_PT_geospill,
    tools.SUPERLUXCORE_RENDER_PT_autoproxy,
    # Viewport
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings,
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings_denoiser,
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings_advanced,
    # Utilities
    tools.SUPERLUXCORE_RENDER_PT_tools,
    tools.SUPERLUXCORE_RENDER_PT_filesaver,
    tools.SUPERLUXCORE_RENDER_PT_external,
    # Diagnostics
    errorlog.SUPERLUXCORE_RENDER_PT_error_log,
    debug.SUPERLUXCORE_RENDER_PT_debug_settings,
)

submodules = (config,)


def register():
    utils.register_module("UI.Render", classes, submodules)


def unregister():
    utils.unregister_module("UI.Render", classes, submodules)
