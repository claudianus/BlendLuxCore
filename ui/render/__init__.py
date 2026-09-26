from ... import utils
from . import caches, config, debug, denoiser, devices, errorlog, halt, image_resize_policy, sampling, tools, viewport

classes = (
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
    config.SUPERLUXCORE_RENDER_PT_lightpaths,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_bounces,
    config.SUPERLUXCORE_RENDER_PT_add_light_tracing,
    config.SUPERLUXCORE_RENDER_PT_bidir_focus,
    config.SUPERLUXCORE_RENDER_PT_lightpaths_clamping,
    debug.SUPERLUXCORE_RENDER_PT_debug_settings,
    denoiser.SUPERLUXCORE_RENDER_PT_denoiser,
    denoiser.SUPERLUXCORE_RENDER_PT_denoiser_bcd_advanced,
    devices.SUPERLUXCORE_RENDER_PT_devices,
    devices.SUPERLUXCORE_RENDER_PT_gpu_devices,
    devices.SUPERLUXCORE_RENDER_PT_cpu_devices,
    errorlog.SUPERLUXCORE_RENDER_PT_error_log,
    halt.SUPERLUXCORE_RENDER_PT_halt_conditions,
    halt.SUPERLUXCORE_RENDERLAYER_PT_halt_conditions,
    sampling.SUPERLUXCORE_RENDER_PT_sampling,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_tiled_multipass,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_adaptivity,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_pixel_filtering,
    sampling.SUPERLUXCORE_RENDER_PT_sampling_advanced,
    image_resize_policy.SUPERLUXCORE_RENDER_PT_image_resize_policy,
    tools.SUPERLUXCORE_RENDER_PT_tools,
    tools.SUPERLUXCORE_RENDER_PT_filesaver,
    tools.SUPERLUXCORE_RENDER_PT_external,
    tools.SUPERLUXCORE_RENDER_PT_geospill,
    tools.SUPERLUXCORE_RENDER_PT_autoproxy,
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings,
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings_denoiser,
    viewport.SUPERLUXCORE_RENDER_PT_viewport_settings_advanced,
)

submodules = (config,)

def register():
    utils.register_module("UI.Render", classes, submodules)

def unregister():
    utils.unregister_module("UI.Render", classes, submodules)
