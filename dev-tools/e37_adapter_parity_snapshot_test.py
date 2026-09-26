"""
e37: CPU/GPU adapter parity snapshot (LuxCore parity plan 3.3).

The same artist-facing settings must export an equivalent luxcore
property set on CPU and GPU devices. This test flips
config.device between "CPU" and "OCL" across a set of feature
profiles and diffs the exported definitions:

  * keys present on only one side  -> silent feature drop
  * values differing               -> semantic divergence

Known-intentional differences are allowlisted below; anything else
fails the test so new features cannot silently ship CPU-only (or
GPU-only) again.

Run:
  Blender -b --python dev-tools/e37_adapter_parity_snapshot_test.py
"""

import bpy
import sys

from bl_ext.user_default.blendluxcore.export import config as export_config

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def fresh_scene():
    # bpy.ops.scene.new copies the active scene's addon properties even
    # for type="EMPTY" - bpy.data.scenes.new gives real defaults
    # (same workaround as e15_auto_config_test.py)
    scene = bpy.data.scenes.new("e37")
    scene.render.engine = "LUXCORE"
    return scene


def props_to_dict(props):
    d = {}
    for name in props.GetAllNames():
        p = props.Get(name)
        try:
            d[name] = tuple(p.GetStrings())
        except Exception:
            d[name] = (p.GetString(),)
    return d


# Keys allowed to differ between CPU and GPU exports. Everything is
# either an engine tag, a GPU-only scheduling knob, or a CPU-only
# tile fallback.
ALLOWED_PREFIXES = (
    "renderengine.type",        # PATHCPU vs PATHOCL etc.
    "sampler.type",             # TILEPATHSAMPLER is GPU-only
    "opencl.",                  # device scheduling
    "native.threads.count",
    "tile.",                    # CPU tile fallback vs GPU tiling
    "path.hybridbackforward.",  # GPU splits tasks; CPU uses threads
    # The artist "Light Rays" toggle exports as hybridbackforward on
    # CPU (native light threads) and as the light-task fraction on GPU
    "path.lighttracing.",
    # Vertex connection is exported on GPU only (the CPU side has no
    # counterpart flag - BIDIRCPU is a separate engine there)
    "path.vertexconnection.",
)

ALLOWED_EXACT = {
    "batch.haltspp",            # per-engine halt cadence may differ
    "rtpathcpu.",               # namespace prefix guard
}


def allowed(key):
    if any(key.startswith(p) for p in ALLOWED_PREFIXES):
        return True
    return any(key.startswith(p) for p in ALLOWED_EXACT)


def add_light_portal(scene):
    """One quad flagged "Light Portal" -> path.portal.* on both sides
    (M5; the GPU port shares the same properties)."""
    mesh = bpy.data.meshes.new("portal")
    mesh.from_pydata([(-1., 0., -1.), (1., 0., -1.),
                      (1., 0., 1.), (-1., 0., 1.)], [], [(0, 1, 2, 3)])
    mesh.update()
    obj = bpy.data.objects.new("portal", mesh)
    scene.collection.objects.link(obj)
    obj.luxcore.is_light_portal = True


# Artist profiles: (name, mutator). Each mutator configures
# scene.luxcore.config the way a user would in the UI.
PROFILES = [
    ("default", lambda c, s: None),
    ("lighttracing", lambda c, s: setattr(
        c.path, "lighttracing_enable", True)),
    ("hybrid", lambda c, s: setattr(
        c.path, "hybridbackforward_enable", True)),
    ("guiding", lambda c, s: setattr(
        c, "guiding_enable", True)),
    ("spectral", lambda c, s: setattr(
        c, "spectral_enable", True)),
    ("tiles", lambda c, s: setattr(
        c, "use_tiles", True)),
    ("restir_gi", lambda c, s: setattr(
        c, "restir_gi_enable", True)),
    ("metropolis", lambda c, s: setattr(
        c, "sampler_gpu" if c.device == "OCL" else "sampler",
        "METROPOLIS")),
    ("denoiser", lambda c, s: setattr(
        s.luxcore.denoiser, "enabled", True)),
    ("light_portal", lambda c, s: add_light_portal(s)),
    # M6: vertex connection rides on the GPU light-task population; the
    # mutator mimics the UI flow (Light Tracing panel + VC toggle)
    ("vertex_connection", lambda c, s: (
        setattr(c.path, "hybridbackforward_enable", True),
        setattr(c.path, "vertex_connection", True))),
]


def export_with(device, mutator):
    scene = fresh_scene()
    config = scene.luxcore.config
    config.device = device
    mutator(config, scene)
    props = export_config.convert(None, scene)
    return props_to_dict(props)


def main():
    print("e37 adapter parity snapshot\n", flush=True)

    any_fail = False
    for profile, mutator in PROFILES:
        cpu = export_with("CPU", mutator)
        gpu = export_with("OCL", mutator)

        missing_cpu = sorted(set(gpu) - set(cpu))
        missing_gpu = sorted(set(cpu) - set(gpu))
        changed = sorted(k for k in set(cpu) & set(gpu)
                         if cpu[k] != gpu[k])

        bad_missing_cpu = [k for k in missing_cpu if not allowed(k)]
        bad_missing_gpu = [k for k in missing_gpu if not allowed(k)]
        bad_changed = [k for k in changed if not allowed(k)]

        ok = not (bad_missing_cpu or bad_missing_gpu or bad_changed)
        check(profile, ok)
        for k in bad_missing_cpu:
            print(f"   GPU-only key: {k} = {gpu[k]}")
        for k in bad_missing_gpu:
            print(f"   CPU-only key: {k} = {cpu[k]}")
        for k in bad_changed:
            print(f"   differs: {k}  cpu={cpu[k]}  gpu={gpu[k]}")
        if ok:
            ndiff = len(missing_cpu) + len(missing_gpu) + len(changed)
            print(f"   ({ndiff} allowlisted differences)")

        # Engine tag sanity on both sides
        check(profile + " engine",
              cpu.get("renderengine.type", (None,))[0].endswith("CPU")
              and not gpu.get("renderengine.type", (None,))[0].endswith("CPU"))

        any_fail |= not ok

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


main()
