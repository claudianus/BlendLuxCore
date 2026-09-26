"""
e15: automatic configuration defaults (B-UX roadmap item).

Validates, without rendering:

* light_strategy == "AUTO" resolves to LOG_POWER on a sparse scene and to
  RESTIR_DI once the emitter count exceeds
  AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD.
* AUTO never selects RESTIR_DI on engines that do not support it (the
  engine throws a hard error for that combination).
* An explicit light_strategy choice always wins over AUTO.
* The DLS-cache checkbox still overrides everything.
* auto_clamping applies the suggested clamp value of a previous render,
  and manual clamping takes precedence.
* Sensible production defaults on a fresh scene: denoiser on, halt
  conditions on with a convergence stop and a sample cap.

Run:
  Blender -b --python dev-tools/e15_auto_config_test.py
"""

import bpy
import sys

from bl_ext.user_default.superluxcore.export import (
    config as export_config,
)
from bl_ext.user_default.superluxcore.export.config import (
    AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD,
    _auto_light_strategy,
    _count_emitters,
)


results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def fresh_scene():
    bpy.ops.scene.new(type="EMPTY")
    scene = bpy.context.scene
    scene.render.engine = "SUPERLUXCORE"
    return scene


def add_point_light(scene, name):
    data = bpy.data.lights.new(name, type="POINT")
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)


def light_strategy_of(scene):
    props = export_config.convert(None, scene)
    return props.Get("lightstrategy.type").GetString()


# --- emitter counting -------------------------------------------------

scene = fresh_scene()
check("emitters.empty", _count_emitters(scene) == 0)

for i in range(3):
    add_point_light(scene, f"L{i}")
check("emitters.lights", _count_emitters(scene) == 3)

# lit world counts as one emitter
world = bpy.data.worlds.new("W")
world.use_nodes = True
scene.world = world
check("emitters.world", _count_emitters(scene) == 4)

# emissive mesh weighted by polygon count
mesh = bpy.data.meshes.new("emitmesh")
mesh.from_pydata(
    [(0, 0, 0)] * 4, [], [(0, 1, 2, 3)]
)
mat = bpy.data.materials.new("emitmat")
mat.use_nodes = True
em = mat.node_tree.nodes.new("ShaderNodeEmission")
emit_obj = bpy.data.objects.new("emitobj", mesh)
emit_obj.data.materials.append(mat)
scene.collection.objects.link(emit_obj)
check("emitters.meshlight", _count_emitters(scene) == 5)

# --- AUTO resolution --------------------------------------------------

sparse = _auto_light_strategy(scene, "PATHCPU")
check("auto.sparse", sparse == "LOG_POWER", sparse)

for i in range(AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD):
    add_point_light(scene, f"extra{i}")
dense = _auto_light_strategy(scene, "PATHCPU")
check("auto.dense", dense == "RESTIR_DI", dense)

check(
    "auto.unsupported-engine",
    _auto_light_strategy(scene, "BIDIRCPU") == "LOG_POWER",
)

# --- end-to-end through config.convert --------------------------------

scene2 = fresh_scene()
scene2.superluxcore.config.engine = "PATH"
scene2.superluxcore.config.device = "CPU"
check(
    "convert.sparse-logpower",
    light_strategy_of(scene2) == "LOG_POWER",
)

for i in range(AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD + 1):
    add_point_light(scene2, f"M{i}")
check(
    "convert.dense-restir",
    light_strategy_of(scene2) == "RESTIR_DI",
)

# explicit user choice wins
scene2.superluxcore.config.light_strategy = "POWER"
check(
    "convert.explicit-wins",
    light_strategy_of(scene2) == "POWER",
)
scene2.superluxcore.config.light_strategy = "AUTO"

# DLS cache overrides AUTO
scene2.superluxcore.config.dls_cache.enabled = True
check(
    "convert.dlsc-override",
    light_strategy_of(scene2) == "DLS_CACHE",
)
scene2.superluxcore.config.dls_cache.enabled = False

# --- auto clamping ----------------------------------------------------
# These checks exercise the manual/auto clamp plumbing, which Quick
# Setup intentionally overrides with the quality slider — pin it off.

scene2.superluxcore.config.simple.enabled = False
props = export_config.convert(None, scene2)
check(
    "clamp.off-no-suggestion",
    not props.IsDefined("path.clamping.variance.maxvalue"),
)

scene2.superluxcore.config.path.suggested_clamping_value = 42.0
# Auto-clamp only applies while the scene signature matches the one
# stamped when the suggestion was measured (5ab09647).
from bl_ext.user_default.superluxcore.utils.render import (
    compute_clamp_signature,
)
scene2.superluxcore.config.path.suggested_clamping_sig = compute_clamp_signature(
    scene2
)
props = export_config.convert(None, scene2)
check(
    "clamp.auto-applied",
    props.IsDefined("path.clamping.variance.maxvalue")
    and abs(props.Get("path.clamping.variance.maxvalue").GetFloat() - 42.0)
    < 1e-6,
)

scene2.superluxcore.config.path.use_clamping = True
scene2.superluxcore.config.path.clamping = 7.0
props = export_config.convert(None, scene2)
check(
    "clamp.manual-wins",
    abs(props.Get("path.clamping.variance.maxvalue").GetFloat() - 7.0)
    < 1e-6,
)

# Adaptive Robust Clamping settings are emitted alongside maxvalue
check(
    "clamp.arc-props",
    props.IsDefined("path.clamping.variance.adaptive")
    and props.Get("path.clamping.variance.adaptive").GetBool()
    and props.Get("path.clamping.variance.scope").GetString() == "indirect"
    and abs(props.Get("path.clamping.variance.sigma").GetFloat() - 6.0)
    < 1e-6,
)
scene2.superluxcore.config.path.clamp_scope = "ALL"
scene2.superluxcore.config.path.clamp_adaptive = False
scene2.superluxcore.config.path.clamp_sigma = 3.0
props = export_config.convert(None, scene2)
check(
    "clamp.arc-custom",
    not props.Get("path.clamping.variance.adaptive").GetBool()
    and props.Get("path.clamping.variance.scope").GetString() == "all"
    and abs(props.Get("path.clamping.variance.sigma").GetFloat() - 3.0)
    < 1e-6,
)
scene2.superluxcore.config.path.clamp_scope = "INDIRECT"
scene2.superluxcore.config.path.clamp_adaptive = True
scene2.superluxcore.config.path.clamp_sigma = 6.0
scene2.superluxcore.config.path.use_clamping = False

scene2.superluxcore.config.path.auto_clamping = False
props = export_config.convert(None, scene2)
check(
    "clamp.auto-off",
    not props.IsDefined("path.clamping.variance.maxvalue"),
)
scene2.superluxcore.config.path.auto_clamping = True

# --- Quick Setup scene analysis ---------------------------------------
# analyze_scene scans bpy.data.materials GLOBALLY, not per-scene slots:
# a flagged-but-unused material still enables the feature (slower but
# never wrong) — deliberate conservative bias. Purge leftover materials
# from the sections above so the baseline is deterministic.
from bl_ext.user_default.superluxcore.utils import scene_analysis

for m in list(bpy.data.materials):
    bpy.data.materials.remove(m)

scene4 = bpy.data.scenes.new("scan-scene")
scene4.render.engine = "SUPERLUXCORE"
simple4 = scene4.superluxcore.config.simple

prof = scene_analysis.analyze_scene(scene4)
check(
    "scan.plain-clean",
    not prof["transmissive"] and not prof["volume"]
    and not prof["emission"],
)

# A Cycles Glass BSDF anywhere in bpy.data.materials flags the scene
# transmissive — detected in the profile, but Quick Setup must NOT
# enable pre-pass caches (PhotonGI/env-light/light tracing): the
# engine's unbiased path already covers what caches accelerated.
mat_glass = bpy.data.materials.new("glass-mat")
mat_glass.use_nodes = True
nt = mat_glass.node_tree
nt.nodes.clear()
glass_node = nt.nodes.new("ShaderNodeBsdfGlass")
out_node = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(glass_node.outputs[0], out_node.inputs["Surface"])

mat_vol = bpy.data.materials.new("vol-mat")
mat_vol.use_nodes = True
ntv = mat_vol.node_tree
ntv.nodes.clear()
vol_node = ntv.nodes.new("ShaderNodeVolumePrincipled")
outv = ntv.nodes.new("ShaderNodeOutputMaterial")
ntv.links.new(vol_node.outputs[0], outv.inputs["Volume"])

prof = scene_analysis.analyze_scene(scene4)
check(
    "scan.glass-detected",
    prof["transmissive"],
    f"profile={prof}",
)
check("scan.volume-detected", prof["volume"])

simple4.apply_scene_scan(scene4)
cfg4 = scene4.superluxcore.config
check(
    "scan.glass-no-cache",
    not cfg4.photongi.enabled and not cfg4.photongi.caustic_enabled
    and not cfg4.envlight_cache.enabled
    and not cfg4.path.hybridbackforward_enable,
)

# Auto Scene Settings off -> the scan is a strict no-op.
scene5 = bpy.data.scenes.new("scan-off-scene")
scene5.render.engine = "SUPERLUXCORE"
scene5.superluxcore.config.simple.detect_features = False
scene5.superluxcore.config.simple.apply_scene_scan(scene5)
check(
    "scan.off-noop",
    not scene5.superluxcore.config.spectral_enable,
)

# Animation on an object -> temporal denoise accumulation.
cam_data = bpy.data.cameras.new("cam")
cam_obj = bpy.data.objects.new("cam", cam_data)
scene4.collection.objects.link(cam_obj)
cam_obj.keyframe_insert("location", frame=1)
cam_obj.keyframe_insert("location", frame=10)
prof = scene_analysis.analyze_scene(scene4)
check("scan.animated", prof["animated"])
# Post-restore consumers read the predicates, not mutated properties.
check(
    "scan.temporal-denoise",
    scene_analysis.wants_temporal_denoise(simple4, scene4),
)
simple4.detect_features = False
check(
    "scan.temporal-gated",
    not scene_analysis.wants_temporal_denoise(simple4, scene4),
)
simple4.detect_features = True

# Mesh proxy predicate (threshold monkeypatched so the small test
# scene qualifies).
orig_tris = scene_analysis.PROXY_AUTO_TRIS
try:
    scene_analysis.PROXY_AUTO_TRIS = 0
    check(
        "scan.mesh-proxy",
        scene_analysis.wants_mesh_proxy(simple4, scene4),
    )
finally:
    scene_analysis.PROXY_AUTO_TRIS = orig_tris

# Halt export reads the quality map directly (post-restore safe).
from bl_ext.user_default.superluxcore.export import halt as export_halt
simple4.time_limit = 3
hprops = export_halt.convert(scene4)
check(
    "scan.halt-time",
    hprops.Get("batch.halttime").GetInt() == 180,
    f"got {hprops.Get('batch.halttime').GetInt()}",
)
check(
    "scan.halt-noise",
    abs(hprops.Get("batch.haltthreshold").GetFloat() - 5 / 256) < 1e-6,
    f"got {hprops.Get('batch.haltthreshold').GetFloat()}",
)
check(
    "scan.halt-spp",
    hprops.Get("batch.haltspp").GetInt() == 192,
    f"got {hprops.Get('batch.haltspp').GetInt()}",
)
simple4.time_limit = 0

# snapshot/restore round-trips the user's own values.
cfg4.path.depth_total = 7
token = simple4.snapshot(scene4)
cfg4.path.depth_total = 3
simple4.restore(token)
check("scan.snapshot-restore", cfg4.path.depth_total == 7)

# --- production defaults on a fresh scene -----------------------------
# NOTE: bpy.ops.scene.new copies the active scene's addon properties even
# for type="EMPTY" — use bpy.data.scenes.new to get real defaults.

scene3 = bpy.data.scenes.new("defaults-scene")
scene3.render.engine = "SUPERLUXCORE"
halt = scene3.superluxcore.halt
check("defaults.denoiser", scene3.superluxcore.denoiser.enabled)
check("defaults.halt-enabled", halt.enable)
check("defaults.halt-noise", halt.use_noise_thresh)
check("defaults.halt-samples-cap", halt.use_samples and halt.samples >= 256)
check("defaults.auto-clamp", scene3.superluxcore.config.path.auto_clamping)
check("defaults.strategy-auto", scene3.superluxcore.config.light_strategy == "AUTO")
check(
    "defaults.device-auto",
    scene3.superluxcore.config.device == "AUTO",
    f"got {scene3.superluxcore.config.device}",
)

# --- device AUTO resolution -------------------------------------------

cfg3 = scene3.superluxcore.config
resolved = cfg3.effective_device()
check(
    "device.resolves",
    resolved in ("CPU", "OCL"),
    f"resolved={resolved}",
)
# Whatever it resolved to, convert() must emit a concrete engine tag
props = export_config.convert(None, scene3)
engine_tag = props.Get("renderengine.type").GetString()
expected = "PATH" + resolved
check(
    "device.engine-tag",
    engine_tag == expected,
    f"engine={engine_tag}",
)
# explicit CPU still wins
cfg3.device = "CPU"
props = export_config.convert(None, scene3)
check(
    "device.explicit-cpu",
    props.Get("renderengine.type").GetString() == "PATHCPU",
)
cfg3.device = "AUTO"

# low_vram() is bool and consistent with using_out_of_core()
lv = cfg3.low_vram()
check("device.low-vram-bool", isinstance(lv, bool))
expected_ooc = resolved == "OCL" and (
    lv or (cfg3.out_of_core and cfg3.out_of_core_mode == "EVERYTHING")
)
check("device.ooc-consistency", cfg3.using_out_of_core() == expected_ooc)

# --- low-resource profile ---------------------------------------------

# Without a detected low-VRAM GPU the wavefront task count stays at the
# SuperLuxCore default (property left undefined)
props = export_config.convert(None, scene3)
check(
    "lowres.taskcount-untouched",
    not props.IsDefined("opencl.task.count"),
)

# Force the low-resource profile: the wavefront task count is capped so
# the per-task buffers fit small GPUs
orig_low_vram = type(cfg3).low_vram
try:
    type(cfg3).low_vram = lambda self: True
    props = export_config.convert(None, scene3)
    check(
        "lowres.taskcount-capped",
        props.IsDefined("opencl.task.count")
        and props.Get("opencl.task.count").GetInt()
        == cfg3.LOW_RESOURCE_TASK_COUNT,
        f"got {props.Get('opencl.task.count').GetInt() if props.IsDefined('opencl.task.count') else 'unset'}",
    )
finally:
    type(cfg3).low_vram = orig_low_vram

# --- ReSTIR visibility export + convergence stat ----------------------

cfg3.light_strategy = "RESTIR_DI"
cfg3.restir_visibility_enable = True
props = export_config.convert(None, scene3)
check(
    "restir.visibility-exported",
    props.IsDefined("lightstrategy.restir.visibility.enable")
    and props.Get("lightstrategy.restir.visibility.enable").GetBool(),
)
cfg3.restir_visibility_enable = False
props = export_config.convert(None, scene3)
check(
    "restir.visibility-default-off",
    props.IsDefined("lightstrategy.restir.visibility.enable")
    and not props.Get("lightstrategy.restir.visibility.enable").GetBool(),
)
cfg3.light_strategy = "AUTO"

# ReSTIR GI export: PATHCPU and the pathoclbase GPU engines
# (PATHOCL/TILEPATHOCL) implement the reservoir machinery; RTPATHOCL
# and BIDIR* must not see the property (silent no-op).
cfg3.device = "CPU"
cfg3.restir_gi_enable = True
cfg3.restir_gi_candidates = 8
props = export_config.convert(None, scene3)
check(
    "restir.gi-exported-cpu",
    props.IsDefined("path.restir.gi.enable")
    and props.Get("path.restir.gi.enable").GetBool()
    and props.Get("path.restir.gi.candidates").GetInt() == 8,
)
cfg3.device = "OCL" if resolved == "OCL" else "CPU"
props = export_config.convert(None, scene3)
check(
    "restir.gi-exported-ocl",
    (resolved != "OCL")
    or (props.IsDefined("path.restir.gi.enable")
        and props.Get("path.restir.gi.enable").GetBool()),
    f"resolved={resolved}",
)
cfg3.restir_gi_enable = False
cfg3.restir_gi_candidates = 0
cfg3.device = "AUTO"
props = export_config.convert(None, scene3)
check(
    "restir.gi-default-off",
    not props.IsDefined("path.restir.gi.enable"),
)

from bl_ext.user_default.superluxcore.properties.statistics import (
    SuperLuxCoreRenderStats,
)
slot_stats = SuperLuxCoreRenderStats()
check(
    "stats.convergence-na-default",
    str(slot_stats.convergence) == "n/a",
    str(slot_stats.convergence),
)

# _init_stats: the convergence row must light up whenever a convergence
# test actually runs — TILE engines always, PATH engines when the
# noise-threshold halt (batch.haltthreshold) is configured.
import pysuperluxcore
from bl_ext.user_default.superluxcore.export import Exporter


def init_convergence(engine_type, extra=None):
    props = pysuperluxcore.Properties()
    props.Set(pysuperluxcore.Property("renderengine.type", [engine_type]))
    props.Set(pysuperluxcore.Property("sampler.type", ["SOBOL"]))
    for key, value in (extra or {}).items():
        props.Set(pysuperluxcore.Property(key, [value]))
    stats = SuperLuxCoreRenderStats()
    Exporter._init_stats(None, stats, props, scene3)
    return stats.convergence.value


check(
    "stats.convergence-tile",
    init_convergence("TILEPATHOCL") == 0.0,
)
check(
    "stats.convergence-path-na",
    init_convergence("PATHOCL") == -1.0,
)
check(
    "stats.convergence-path-noise-halt",
    init_convergence("PATHOCL", {"batch.haltthreshold": "0.02"}) == 0.0,
)
check(
    "stats.convergence-pathcpu-noise-halt",
    init_convergence("PATHCPU", {"batch.haltnoisethreshold": "0.02"})
    == 0.0,
)

print()
n_pass = sum(1 for _, ok in results if ok)
print(f"===== auto config: {n_pass}/{len(results)} PASS =====")
sys.exit(0 if n_pass == len(results) else 1)
