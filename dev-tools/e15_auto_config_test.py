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

from bl_ext.user_default.blendluxcore.export import (
    config as export_config,
)
from bl_ext.user_default.blendluxcore.export.config import (
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
    scene.render.engine = "LUXCORE"
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
scene2.luxcore.config.engine = "PATH"
scene2.luxcore.config.device = "CPU"
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
scene2.luxcore.config.light_strategy = "POWER"
check(
    "convert.explicit-wins",
    light_strategy_of(scene2) == "POWER",
)
scene2.luxcore.config.light_strategy = "AUTO"

# DLS cache overrides AUTO
scene2.luxcore.config.dls_cache.enabled = True
check(
    "convert.dlsc-override",
    light_strategy_of(scene2) == "DLS_CACHE",
)
scene2.luxcore.config.dls_cache.enabled = False

# --- auto clamping ----------------------------------------------------

props = export_config.convert(None, scene2)
check(
    "clamp.off-no-suggestion",
    not props.IsDefined("path.clamping.variance.maxvalue"),
)

scene2.luxcore.config.path.suggested_clamping_value = 42.0
props = export_config.convert(None, scene2)
check(
    "clamp.auto-applied",
    props.IsDefined("path.clamping.variance.maxvalue")
    and abs(props.Get("path.clamping.variance.maxvalue").GetFloat() - 42.0)
    < 1e-6,
)

scene2.luxcore.config.path.use_clamping = True
scene2.luxcore.config.path.clamping = 7.0
props = export_config.convert(None, scene2)
check(
    "clamp.manual-wins",
    abs(props.Get("path.clamping.variance.maxvalue").GetFloat() - 7.0)
    < 1e-6,
)
scene2.luxcore.config.path.use_clamping = False

scene2.luxcore.config.path.auto_clamping = False
props = export_config.convert(None, scene2)
check(
    "clamp.auto-off",
    not props.IsDefined("path.clamping.variance.maxvalue"),
)
scene2.luxcore.config.path.auto_clamping = True

# --- production defaults on a fresh scene -----------------------------
# NOTE: bpy.ops.scene.new copies the active scene's addon properties even
# for type="EMPTY" — use bpy.data.scenes.new to get real defaults.

scene3 = bpy.data.scenes.new("defaults-scene")
scene3.render.engine = "LUXCORE"
halt = scene3.luxcore.halt
check("defaults.denoiser", scene3.luxcore.denoiser.enabled)
check("defaults.halt-enabled", halt.enable)
check("defaults.halt-noise", halt.use_noise_thresh)
check("defaults.halt-samples-cap", halt.use_samples and halt.samples >= 256)
check("defaults.auto-clamp", scene3.luxcore.config.path.auto_clamping)
check("defaults.strategy-auto", scene3.luxcore.config.light_strategy == "AUTO")
check(
    "defaults.device-auto",
    scene3.luxcore.config.device == "AUTO",
    f"got {scene3.luxcore.config.device}",
)

# --- device AUTO resolution -------------------------------------------

cfg3 = scene3.luxcore.config
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
# LuxCore default (property left undefined)
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

from bl_ext.user_default.blendluxcore.properties.statistics import (
    LuxCoreRenderStats,
)
slot_stats = LuxCoreRenderStats()
check(
    "stats.convergence-na-default",
    str(slot_stats.convergence) == "n/a",
    str(slot_stats.convergence),
)

# _init_stats: the convergence row must light up whenever a convergence
# test actually runs — TILE engines always, PATH engines when the
# noise-threshold halt (batch.haltthreshold) is configured.
import pyluxcore
from bl_ext.user_default.blendluxcore.export import Exporter


def init_convergence(engine_type, extra=None):
    props = pyluxcore.Properties()
    props.Set(pyluxcore.Property("renderengine.type", [engine_type]))
    props.Set(pyluxcore.Property("sampler.type", ["SOBOL"]))
    for key, value in (extra or {}).items():
        props.Set(pyluxcore.Property(key, [value]))
    stats = LuxCoreRenderStats()
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
