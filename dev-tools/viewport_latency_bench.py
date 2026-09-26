"""
Viewport edit->pixel latency benchmark, run inside real Blender:

    Blender -b --python dev-tools/viewport_latency_bench.py

Measures the end-to-end latency of the orbit path with real pyluxcore:
  camera props edit -> SessionWorker._do_edit -> render-thread frame
  boundary -> film reset -> first post-reset pass completes.

This is the number that makes viewport navigation feel snappy or
sluggish; the GL upload and redraw cadence add a small constant on top.
"""

import statistics
import sys
import time

import bpy
import pyluxcore

import bl_ext.user_default.blendluxcore as blendluxcore  # noqa: F401
from bl_ext.user_default.blendluxcore import export as blc_export
from bl_ext.user_default.blendluxcore.engine.session_worker import (
    SessionWorker,
)


class FakeEngine:
    def __init__(self):
        import threading

        self.session = None
        self.viewport_start_time = None
        self.session_lock = threading.Lock()

    def tag_redraw(self):
        pass


def pass_count(session):
    session.UpdateStats()
    return session.GetStats().Get("stats.renderengine.pass").GetInt()


def sample_count(session):
    """Film's total accumulated sample count - the metric that matters for
    'first visible frame': stats.renderengine.pass counts full-resolution
    EQUIVALENT samples, so a 1/64-res preview pass doesn't move it. The
    raw count ticks the moment the first post-reset pass splats."""
    return session.GetFilm().GetStats().Get(
        "stats.film.total.samplecount"
    ).GetInt()


def main():
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()

    exporter = blc_export.Exporter()
    result = exporter.export_scene(depsgraph, None)
    if result is None:
        print("FAIL export_scene returned None")
        sys.exit(1)
    luxcore_scene, config_props = result
    print("base engine:", config_props.Get("renderengine.type").GetString())

    engine_type = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "RTPATHOCL"
    config_props.Set(pyluxcore.Property("renderengine.type", [engine_type]))
    if engine_type == "RTPATHOCL":
        config_props.Set(pyluxcore.Property("sampler.type", ["TILEPATHSAMPLER"]))
        # Match the viewport path (export/config.py): coarse preview so
        # the first pass after a reset lands fast.
        config_props.Set(pyluxcore.Property(
            "rtpath.resolutionreduction.preview", [8]))
        config_props.Set(pyluxcore.Property(
            "rtpath.resolutionreduction.preview.step", [2]))
        config_props.Set(pyluxcore.Property(
            "rtpath.resolutionreduction",
            [int(__import__("sys").argv[-1]) if __import__("sys").argv[-1].isdigit() else 4]))
    print("bench engine:", engine_type)

    engine = FakeEngine()
    worker = SessionWorker(engine)
    worker.submit_start(luxcore_scene, config_props)

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if engine.session is not None and not worker.is_starting:
            break
        time.sleep(0.05)
    if engine.session is None:
        print("FAIL session never started", worker.pop_error())
        sys.exit(1)
    print("session started")

    # Warmup
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if pass_count(engine.session) > 2:
                break
        except Exception:
            pass
        time.sleep(0.05)
    print("warmup pass:", pass_count(engine.session))

    # "dyn" arg: enable the runtime resolution-reduction override
    # (viewport interaction path) for the whole edit burst.
    dyn = "dyn" in sys.argv
    if dyn:
        engine.session.SetRuntimeResolutionReduction(16)
        time.sleep(0.2)
        print("runtime resolution override: 16")

    lat_submit_to_apply = []
    lat_apply_to_pass = []
    lat_total = []
    N = 30
    fails = 0
    for i in range(N):
        props = pyluxcore.Properties()
        props.Set(pyluxcore.Property(
            "scene.camera.screenwindow",
            [-1.0, 1.0, -0.5 - 1e-4 * i, 0.5 + 1e-4 * i],
        ))

        mut_before = worker.mutation_seq
        t0 = time.monotonic()
        worker.submit_edit([("Parse", (props,), {})])

        while worker.mutation_seq == mut_before:
            if time.monotonic() - t0 > 15:
                print("FAIL edit never applied")
                fails += 1
                break
            time.sleep(0.001)
        if fails:
            break
        t_apply = time.monotonic()

        # Wait for the first post-reset sample: the film resets at the
        # next frame boundary after the edit lands, so samplecount drops
        # (possibly between polls) then climbs once the first preview
        # pass splats. That climb is the earliest a readback could show
        # post-edit content.
        prev = sample_count(engine.session)
        seen_reset = False
        t_pass = None
        trace = []
        while time.monotonic() - t_apply < 15:
            try:
                p = sample_count(engine.session)
            except Exception:
                p = prev
            trace.append(p)
            # A drop between consecutive polls = the film reset
            # landed; the next positive count is the first post-reset
            # sample (the earliest a readback could show new content).
            if p < prev:
                seen_reset = True
            elif seen_reset and p > 0:
                t_pass = time.monotonic()
                break
            prev = p
            # No sleep: at high reductions a reset+refill can complete
            # between 1 ms polls, hiding the dip entirely.
        if t_pass is None:
            try:
                st = engine.session.GetStats()
                print("engine pass:", st.Get("stats.renderengine.pass").GetInt(),
                      "done:", engine.session.HasDone(),
                      "paused:", engine.session.IsInPause())
            except Exception as e:
                print("stat read failed:", e)
            print("sample trace:", trace[:40], "... min",
                  min(trace) if trace else "-")
            if "hang" in sys.argv:
                print("WEDGED - sleeping 120s for debugger attach",
                      flush=True)
                time.sleep(120)
        if t_pass is None:
            print(f"FAIL no new samples after edit {i}")
            fails += 1
            break

        lat_submit_to_apply.append((t_apply - t0) * 1000)
        lat_apply_to_pass.append((t_pass - t_apply) * 1000)
        lat_total.append((t_pass - t0) * 1000)
        time.sleep(0.05)

    worker.shutdown()

    if fails:
        print(f"{fails} failures")
        sys.exit(1)

    def fmt(xs):
        return (
            f"median {statistics.median(xs):.0f}ms  "
            f"min {min(xs):.0f}ms  max {max(xs):.0f}ms"
        )

    print(f"\nedit submit->apply : {fmt(lat_submit_to_apply)}")
    print(f"edit apply->pass   : {fmt(lat_apply_to_pass)}")
    print(f"total submit->pass : {fmt(lat_total)}")
    print(f"{N} camera edits")
    print("DONE")
    sys.exit(0)


main()
