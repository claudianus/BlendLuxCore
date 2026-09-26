"""UX restructure regression test.

    Blender -b --python dev-tools/ux_restructure_test.py

Verifies the Cycles-aligned render-properties reorganisation:

  * every SUPERLUXCORE panel class registers without error
  * every bl_parent_id chain resolves to a registered parent
  * every prop() the reorganised panels draw resolves to a real RNA
    property (catches config vs config.path / config.tile typos)
  * the Cycles-style layout order: Sampling(10) < Light Paths(20) <
    Render Caches(30) < Performance(40) < Viewport(50) < Utilities(60)
"""

import os
import re
import sys

import bpy

EXT_MODULE = "bl_ext.user_default.superluxcore"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = []


def fail(msg):
    failures.append(msg)
    print(f"[UXTest] FAIL: {msg}")


# --- 1) enable extension & engine ------------------------------------------
try:
    bpy.ops.preferences.extension_enable(module=EXT_MODULE)
except Exception:
    pass  # already enabled

try:
    bpy.context.scene.render.engine = "SUPERLUXCORE"
except TypeError:
    fail("engine SUPERLUXCORE not registered")
    sys.exit(1)

scene = bpy.context.scene
sl = scene.superluxcore


# --- 2) panel registration + parent chains ----------------------------------
panels = {}
for name in dir(bpy.types):
    if name.startswith("SUPERLUXCORE"):
        cls = getattr(bpy.types, name, None)
        if isinstance(cls, type) and issubclass(cls, bpy.types.Panel):
            panels[name] = cls

print(f"[UXTest] {len(panels)} SUPERLUXCORE panels registered")

EXPECTED_RENDER_PANELS = [
    "SUPERLUXCORE_RENDER_PT_simple",
    "SUPERLUXCORE_RENDER_PT_sampling",
    "SUPERLUXCORE_RENDER_PT_denoiser",
    "SUPERLUXCORE_RENDER_PT_denoiser_temporal",
    "SUPERLUXCORE_RENDER_PT_denoiser_bcd_advanced",
    "SUPERLUXCORE_RENDER_PT_sampling_tiled_multipass",
    "SUPERLUXCORE_RENDER_PT_sampling_adaptivity",
    "SUPERLUXCORE_RENDER_PT_sampling_pixel_filtering",
    "SUPERLUXCORE_RENDER_PT_halt_conditions",
    "SUPERLUXCORE_RENDER_PT_sampling_advanced",
    "SUPERLUXCORE_RENDER_PT_lightpaths",
    "SUPERLUXCORE_RENDER_PT_lightpaths_bounces",
    "SUPERLUXCORE_RENDER_PT_add_light_tracing",
    "SUPERLUXCORE_RENDER_PT_bidir_focus",
    "SUPERLUXCORE_RENDER_PT_lightpaths_strategy",
    "SUPERLUXCORE_RENDER_PT_lightpaths_guiding",
    "SUPERLUXCORE_RENDER_PT_lightpaths_clamping",
    "SUPERLUXCORE_RENDER_PT_caches",
    "SUPERLUXCORE_RENDER_PT_devices",
    "SUPERLUXCORE_RENDER_PT_gpu_devices",
    "SUPERLUXCORE_RENDER_PT_cpu_devices",
    "SUPERLUXCORE_RENDER_PT_memory",
    "SUPERLUXCORE_RENDER_PT_image_resize_policy",
    "SUPERLUXCORE_RENDER_PT_geospill",
    "SUPERLUXCORE_RENDER_PT_autoproxy",
    "SUPERLUXCORE_RENDER_PT_viewport_settings",
    "SUPERLUXCORE_RENDER_PT_tools",
    "SUPERLUXCORE_RENDER_PT_filesaver",
    "SUPERLUXCORE_RENDER_PT_external",
    "SUPERLUXCORE_RENDER_PT_error_log",
    "SUPERLUXCORE_RENDER_PT_debug_settings",
]
for name in EXPECTED_RENDER_PANELS:
    if name not in panels:
        fail(f"panel {name} not registered")

# parent chains resolve + terminate at a top-level panel. Parents may be
# SUPERLUXCORE panels or Blender built-ins (e.g. SCENE_PT_unit).
def _any_registered_panel(name):
    return name in panels or hasattr(bpy.types, name)


for name, cls in panels.items():
    seen = set()
    cur = cls
    while getattr(cur, "bl_parent_id", None):
        parent = cur.bl_parent_id
        if parent in seen:
            fail(f"{name}: bl_parent_id cycle at {parent}")
            break
        seen.add(parent)
        if not _any_registered_panel(parent):
            fail(f"{name}: bl_parent_id {parent} not registered")
            break
        if parent not in panels:
            break  # built-in parent — chain ends here
        cur = panels[parent]

# layout order sanity (top-level render panels only)
order = {
    "SUPERLUXCORE_RENDER_PT_simple": 0,
    "SUPERLUXCORE_RENDER_PT_sampling": 10,
    "SUPERLUXCORE_RENDER_PT_lightpaths": 20,
    "SUPERLUXCORE_RENDER_PT_caches": 30,
    "SUPERLUXCORE_RENDER_PT_devices": 40,
    "SUPERLUXCORE_RENDER_PT_viewport_settings": 50,
    "SUPERLUXCORE_RENDER_PT_tools": 60,
}
for name, want in order.items():
    if name in panels and panels[name].bl_order != want:
        fail(f"{name}: bl_order {panels[name].bl_order}, expected {want}")

# Cycles-aligned subpanel homes
parent_of = {
    "SUPERLUXCORE_RENDER_PT_denoiser": "SUPERLUXCORE_RENDER_PT_sampling",
    "SUPERLUXCORE_RENDER_PT_halt_conditions": "SUPERLUXCORE_RENDER_PT_sampling",
    "SUPERLUXCORE_RENDER_PT_add_light_tracing":
        "SUPERLUXCORE_RENDER_PT_lightpaths",
    "SUPERLUXCORE_RENDER_PT_lightpaths_strategy":
        "SUPERLUXCORE_RENDER_PT_lightpaths",
    "SUPERLUXCORE_RENDER_PT_lightpaths_guiding":
        "SUPERLUXCORE_RENDER_PT_lightpaths",
    "SUPERLUXCORE_RENDER_PT_memory": "SUPERLUXCORE_RENDER_PT_devices",
    "SUPERLUXCORE_RENDER_PT_image_resize_policy":
        "SUPERLUXCORE_RENDER_PT_devices",
    "SUPERLUXCORE_RENDER_PT_geospill": "SUPERLUXCORE_RENDER_PT_devices",
    "SUPERLUXCORE_RENDER_PT_autoproxy": "SUPERLUXCORE_RENDER_PT_devices",
}
for child, parent in parent_of.items():
    if child in panels and panels[child].bl_parent_id != parent:
        fail(f"{child}: bl_parent_id {panels[child].bl_parent_id}, "
             f"expected {parent}")


# --- 3) RNA prop references used by the new draw code ------------------------
config = sl.config
path = config.path
halt = sl.halt

PROP_CHECKS = [
    # Sampling top-level (Cycles-style limits)
    (halt, "use_samples"), (halt, "samples"),
    (halt, "use_noise_thresh"), (halt, "noise_thresh"),
    (halt, "use_time"), (halt, "time"),
    (halt, "use_light_samples"), (halt, "light_samples"),
    (halt, "noise_thresh_warmup"), (halt, "noise_thresh_step"),
    (halt, "enable"),
    # Light Paths: spectral headline + caustics (LT + MNEE)
    (config, "spectral_enable"),
    (path, "hybridbackforward_enable"),
    (path, "hybridbackforward_lightpartition"),
    (path, "hybridbackforward_lightpartition_opencl"),
    (path, "hybridbackforward_adaptivecaustic"),
    (path, "hybridbackforward_terminalglossiness"),
    (path, "hybridbackforward_connectprob"),
    (path, "hybridbackforward_glossinessthresh"),
    (path, "lighttracing_only"),
    (path, "vertex_connection"), (path, "vertex_connection_connects"),
    (path, "vertex_connection_pool"), (path, "vertex_connection_adaptive"),
    (path, "vertex_connection_merge_radius"),
    (path, "vertex_connection_reuse"),
    (path, "lighttracing_focus"), (path, "lighttracing_focus_ratio"),
    (path, "lighttracing_focus_radius"),
    (config, "mnee_enable"), (config, "mnee_maxspecular"),
    (config, "mnee_maxiterations"), (config, "mnee_seedcache"),
    # Light strategy + guiding subpanels
    (config, "light_strategy"),
    (config, "restir_temporal_enable"), (config, "restir_spatial_enable"),
    (config, "restir_visibility_enable"), (config, "restir_candidates"),
    (config, "restir_gi_enable"), (config, "restir_gi_temporal_enable"),
    (config, "restir_gi_spatial_enable"), (config, "restir_gi_candidates"),
    (config, "guiding_enable"), (config, "guiding_tablefile"),
    (config, "guiding_ris_k"), (config, "portal_weight"),
    # Performance > Memory
    (config, "out_of_core"), (config, "out_of_core_mode"),
    (config, "out_of_core_supersampling"),
    (config, "free_blender_image_buffers"),
    (config, "low_vram"),  # method
    # Integrator/device in context draw
    (config, "engine"), (config, "device"), (config, "bidir_device"),
    (config, "effective_device"),  # method
    # Sampling internals kept
    (config, "use_tiles"), (config.tile, "size"),
    (config.tile, "path_sampling_aa_size"),
    (config, "sampler"), (config, "sampler_gpu"),
    (config, "sampler_pattern"),
    (config, "sobol_adaptive_strength"), (config, "sobol_owen_enable"),
    (config, "sobol_owen_tile_enable"), (config, "sobol_bluenoise_enable"),
    (config, "sobol_adaptive_moments_enable"),
    (config, "sobol_adaptive_relerr"),
    (config.noise_estimation, "warmup"), (config.noise_estimation, "step"),
    (config, "seed"), (config, "use_animated_seed"),
    (config, "filter_enabled"), (config, "filter"),
    (config, "filter_width"), (config, "gaussian_alpha"),
    (config, "sinc_tau"),
    (config, "metropolis_largesteprate"),
    (config, "metropolis_maxconsecutivereject"),
    (config, "metropolis_imagemutationrate"),
    (config, "get_sampler"),  # method
    # Denoise subpanel
    (sl.denoiser, "enabled"), (sl.denoiser, "type"),
    (sl.denoiser, "temporal_enabled"), (sl.denoiser, "temporal_history"),
    (sl.denoiser, "temporal_clip_sigma"),
    (sl.denoiser, "temporal_depth_threshold"),
    (sl.denoiser, "temporal_normal_threshold"),
    (sl.denoiser, "temporal_statedir"),
    (sl.denoiser, "oidn_mode"), (sl.denoiser, "oidn_demodulate"),
    (sl.denoiser, "oidn_denoise_emission"),
    (sl.denoiser, "oidn_firefly_sigma"),
    (sl.denoiser, "max_memory_MB"),
    (sl.denoiser, "albedo_specular_passthrough_mode"),
    (sl.denoiser, "prefilter_AOVs"),
    (sl.denoiser, "filter_spikes"), (sl.denoiser, "hist_dist_thresh"),
    (sl.denoiser, "search_window_radius"),
    (sl.denoiser, "scales"), (sl.denoiser, "patch_radius"),
    # Viewport
    (sl.viewport, "device"), (sl.viewport, "halt_time"),
    (sl.viewport, "add_light_tracing"), (sl.viewport, "use_bidir"),
    (sl.viewport, "use_denoiser"), (sl.viewport, "denoiser"),
    (sl.viewport, "min_samples"), (sl.viewport, "denoise_interactive"),
    (sl.viewport, "reduce_resolution_on_edit"),
    (sl.viewport, "resolution_reduction"),
    (sl.viewport, "pixel_size"), (sl.viewport, "mag_filter"),
    # Utilities / caches / resize policy
    (config, "use_filesaver"), (config, "filesaver_format"),
    (config, "filesaver_path"), (config, "external_process"),
    (config, "spill_geometry"), (config, "spill_geometry_minmb"),
    (config, "spill_images"), (config, "proxy_auto"),
    (config, "proxy_auto_mintris"), (config, "proxy_cluster_stride"),
    (config.image_resize_policy, "enabled"),
    (config.image_resize_policy, "type"),
    (config.image_resize_policy, "scale"),
    (config.image_resize_policy, "min_size"),
    (config.dls_cache, "enabled"),
    (config.photongi, "enabled"), (config.photongi, "debug"),
    (config.envlight_cache, "enabled"),
    # Devices
    (sl.devices, "use_native_cpu"), (sl.devices, "devices"),
    # Bounces
    (path, "depth_total"), (path, "depth_diffuse"),
    (path, "depth_glossy"), (path, "depth_specular"),
    (path, "use_clamping"), (path, "clamping"),
    (path, "auto_clamping"), (path, "suggested_clamping_value"),
    (config, "bidir_path_maxdepth"), (config, "bidir_light_maxdepth"),
]
for obj, name in PROP_CHECKS:
    if not hasattr(obj, name):
        fail(f"missing RNA prop: {type(obj).__name__}.{name}")

# prop labels artists see
if config.bl_rna.properties["engine"].name != "Integrator":
    fail(f"engine label '{config.bl_rna.properties['engine'].name}', "
         "expected 'Integrator'")
if config.bl_rna.properties["use_tiles"].name != "Tiled Rendering":
    fail("use_tiles label changed unexpectedly")
if path.bl_rna.properties["hybridbackforward_enable"].name != "Light Tracing":
    fail("hybridbackforward_enable label changed unexpectedly")


# --- 3b) optimal defaults (artist-first: good renders out of the box) ---------
def _pg_default(pg, prop_name):
    prop = pg.bl_rna.properties.get(prop_name)
    if prop is None:
        fail(f"{type(pg).__name__}.{prop_name} not in RNA")
        return None
    return prop.default


cam = bpy.data.cameras.new("UXTestCam") if not bpy.data.cameras \
    else bpy.data.cameras[0]
light_data = bpy.data.lights.new("UXTestLight", type="POINT")
world = scene.world if scene.world else bpy.data.worlds.new("UXTestWorld")
view_layer_0 = scene.view_layers[0]

DEFAULT_CHECKS = [
    # Quick Setup is the front door (Corona-style quality slider)
    (sl.config.simple, "enabled", True),
    (sl.config.simple, "denoise", True),
    (sl.config.simple, "detect_features", True),
    (sl.config.simple, "time_limit", 0),
    # Pixel filtering: Blackman-Harris AA costs nothing
    (config, "filter_enabled", True),
    (config, "filter", "BLACKMANHARRIS"),
    # Path guiding on by default (matches Quick Setup Standard profile)
    (config, "guiding_enable", True),
    # Per-frame seeds: independent noise for animation/temporal denoise
    (config, "use_animated_seed", True),
    # Filesaver: single binary .bcf beats multi-file text
    (config, "filesaver_format", "BIN"),
    # Memory safety nets: no-op on small scenes, save big ones from OOM
    (config, "spill_geometry", True),
    (config, "spill_images", True),
    (config.image_resize_policy, "enabled", True),
    (config.image_resize_policy, "type", "MINMEM"),
    # Viewport: fast RT path engine by default
    (sl.viewport, "use_bidir", False),
    (sl.viewport, "add_light_tracing", False),
    (sl.viewport, "use_denoiser", True),
    (sl.viewport, "denoise_interactive", True),
    # First render looks right: auto exposure
    (cam.superluxcore.imagepipeline.tonemapper, "use_autolinear", True),
    # Cycles-compatible authoring surfaces by default
    (world.superluxcore, "use_cycles_settings", True),
    (light_data.superluxcore, "use_cycles_settings", True),
    # Standard 180-degree shutter when motion blur is enabled
    (cam.superluxcore.motion_blur, "shutter", 0.5),
    # Stop conditions: scene on (renders terminate), view-layer
    # override off (opt-in; a default-on layer halt silently replaced
    # every global setting, e.g. batch.halttime never reached the engine)
    (sl.halt, "enable", True),
    (view_layer_0.superluxcore.halt, "enable", False),
]
for pg, prop_name, want in DEFAULT_CHECKS:
    got = _pg_default(pg, prop_name)
    if got is not None and got != want:
        fail(f"default {type(pg).__name__}.{prop_name} = {got!r}, "
             f"expected {want!r}")


# --- 3c) halt source selection: layer override is opt-in ----------------------
import importlib
slx = importlib.import_module(EXT_MODULE)
slx.utils.view_layer.State.active_view_layer = view_layer_0.name
if slx.utils.get_halt_conditions(scene) != sl.halt:
    fail("get_halt_conditions ignored scene halt despite override off")
view_layer_0.superluxcore.halt.enable = True
if slx.utils.get_halt_conditions(scene) != view_layer_0.superluxcore.halt:
    fail("get_halt_conditions ignored enabled view-layer override")
view_layer_0.superluxcore.halt.enable = False
slx.utils.view_layer.State.active_view_layer = ""


# --- 3d) Quick Setup: smooth quality map + scene analysis ---------------------
simple = sl.config.simple
simple.quality = 0.5
m05 = simple.quality_map()
simple.quality = 1.0
m1 = simple.quality_map()
simple.quality = 0.0
m0 = simple.quality_map()
simple.quality = 0.6
if not (m0["halt_samples"] < m05["halt_samples"] < m1["halt_samples"]):
    fail(f"quality_map samples not monotonic: "
         f"{m0['halt_samples']} {m05['halt_samples']} {m1['halt_samples']}")
if not (m0["depth_total"] <= m05["depth_total"] <= m1["depth_total"]):
    fail("quality_map depth not monotonic")
if m0["guiding"] or not m1["guiding"]:
    fail("guiding threshold broken in quality_map")
if m0["noise_thresh"] <= m1["noise_thresh"]:
    fail("noise threshold should tighten with quality")

prof = slx.utils.scene_analysis.analyze_scene(scene)
for k in ("transmissive", "dispersion", "sss", "emission", "volume",
          "lights", "emitters", "polys", "animated"):
    if k not in prof:
        fail(f"analyze_scene missing key {k}")
chips = slx.utils.scene_analysis.describe_auto_features(prof)
if not isinstance(chips, list):
    fail("describe_auto_features did not return a list")


# --- 4) poll() smoke on the reorganised panels -------------------------------
ctx = bpy.context
for name in EXPECTED_RENDER_PANELS:
    cls = panels.get(name)
    if cls is None:
        continue
    try:
        cls.poll(ctx)
    except Exception as e:
        fail(f"{name}.poll() raised: {e}")


if failures:
    print(f"[UXTest] {len(failures)} failure(s)")
    sys.exit(1)

print("[UXTest] OK: panel tree, RNA props and Cycles-aligned order verified")
