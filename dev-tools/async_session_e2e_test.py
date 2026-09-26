"""
E2E test for the async session worker, run inside real Blender:

    Blender -b --python dev-tools/async_session_e2e_test.py

Exercises the real split used by the viewport:
  export_scene()  (main thread, real depsgraph)
  -> SessionWorker.submit_start()  (RenderConfig + kernels + Start)
  -> submit_edit()  (RecordedScene ops replayed in Begin/EndSceneEdit)
  -> submit_config() (session rebuild on the reused scene)
  -> submit_stop()
Verifies the worker publishes the session, edits land, and the film
actually accumulates samples.
"""

import sys
import time

import bpy

# Import via the extension namespace so addon-preferences lookups
# (context.preferences.addons[...]) resolve against the installed key.
# The extension is auto-registered on Blender startup; do not re-register.
import bl_ext.user_default.superluxcore as superluxcore

from bl_ext.user_default.superluxcore import export as blc_export
from bl_ext.user_default.superluxcore.export.recorded_scene import (
    RecordedScene,
)
from bl_ext.user_default.superluxcore.engine.session_worker import (
    SessionWorker,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name, detail)


class FakeEngine:
    """RenderEngine stand-in: publish target for the worker."""

    def __init__(self):
        self.session = None
        self.viewport_start_time = None
        self.session_lock = __import__("threading").Lock()

    def tag_redraw(self):
        pass


def wait_until(pred, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def film_has_samples(session):
    try:
        session.UpdateStats()
        stats = session.GetStats()
        return stats.Get("stats.renderengine.pass").GetInt() > 0
    except Exception:
        return False


def main():
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()

    exporter = blc_export.Exporter()
    result = exporter.export_scene(depsgraph, None)
    check("export_scene returns (scene, props)", result is not None)
    if result is None:
        sys.exit(1)
    superluxcore_scene, config_props = result
    engine_type = config_props.Get("renderengine.type").GetString()
    print("  engine:", engine_type)

    # --- worker start -------------------------------------------------
    engine = FakeEngine()
    worker = SessionWorker(engine)
    worker.submit_start(superluxcore_scene, config_props)

    ok = wait_until(
        lambda: engine.session is not None and not worker.is_starting
    )
    check("worker publishes started session",
          ok and engine.session.IsStarted())
    err = worker.pop_error()
    check("no worker error on start", err is None, err)

    ok = wait_until(lambda: film_has_samples(engine.session), timeout=30)
    check("film accumulates samples", ok)

    # --- scene edit via RecordedScene ----------------------------------
    # Add a light through the recorded path: the proxy records the Parse,
    # the worker replays it inside BeginSceneEdit/EndSceneEdit.
    import pysuperluxcore
    rec = RecordedScene()
    light_props = pysuperluxcore.Properties()
    light_props.Set(pysuperluxcore.Property(
        "scene.lights.e2e_test_light.type", ["point"]))
    light_props.Set(pysuperluxcore.Property(
        "scene.lights.e2e_test_light.gain", [1.0, 1.0, 1.0]))
    light_props.Set(pysuperluxcore.Property(
        "scene.lights.e2e_test_light.position", [0.0, 0.0, 5.0]))
    rec.Parse(light_props)
    light_count_before = (
        engine.session.GetRenderConfig().GetScene().GetLightCount()
    )
    worker.submit_edit(rec.drain())
    ok = wait_until(
        lambda: engine.session is not None
        and engine.session.GetRenderConfig().GetScene().GetLightCount()
        > light_count_before
    )
    check("edit landed on live scene", ok)
    err = worker.pop_error()
    check("no worker error on edit", err is None, err)

    # --- config restart ------------------------------------------------
    import pysuperluxcore
    new_props = pysuperluxcore.Properties(config_props)
    new_props.Set(
        pysuperluxcore.Property("batch.halttime", [5])
    )
    worker.submit_config(new_props)
    ok = wait_until(
        lambda: engine.session is not None and worker.phase == "",
        timeout=60,
    )
    check("config restart publishes new session", ok)
    check("new session is started",
          ok and engine.session.IsStarted())

    # --- rapid burst: coalescing under fire ------------------------------
    # Simulate dragging a color picker / orbiting: many edits + configs
    # submitted back-to-back must coalesce, land in order, and leave a
    # live session.
    import pysuperluxcore
    for i in range(30):
        rec = RecordedScene()
        p = pysuperluxcore.Properties()
        p.Set(pysuperluxcore.Property(
            "scene.lights.e2e_test_light.position", [0.0, 0.0, 5.0 + i]))
        rec.Parse(p)
        worker.submit_edit(rec.drain())
    for i in range(5):
        burst_props = pysuperluxcore.Properties(config_props)
        burst_props.Set(
            pysuperluxcore.Property("batch.halttime", [60 + i]))
        worker.submit_config(burst_props)
    ok = wait_until(
        lambda: engine.session is not None
        and not worker._queue
        and worker.phase == "",
        timeout=90,
    )
    check("burst settles into a live session", ok)
    check("burst session started",
          ok and engine.session.IsStarted())
    err = worker.pop_error()
    check("no worker error after burst", err is None, err)
    ok = wait_until(lambda: film_has_samples(engine.session), timeout=30)
    check("film accumulates after burst", ok)

    # --- bad config: error surfaces, session dropped ----------------------
    bad_props = pysuperluxcore.Properties(config_props)
    bad_props.Set(
        pysuperluxcore.Property("renderengine.type", ["NO_SUCH_ENGINE"]))
    worker.submit_config(bad_props)
    # Peek without consuming: pop_error() clears the latched error.
    ok = wait_until(lambda: worker._error is not None, timeout=30)
    err = worker.pop_error()
    check("bad config surfaces an error",
          ok and err is not None, err)
    check("session dropped after bad config",
          engine.session is None)

    # --- stop ----------------------------------------------------------
    old_session = engine.session
    worker.submit_stop()
    ok = wait_until(lambda: engine.session is None)
    check("stop publishes None", ok)
    check("old session stopped",
          ok and (old_session is None or not old_session.IsStarted()))

    worker.shutdown()
    check("worker thread exits", wait_until(
        lambda: not worker._thread.is_alive(), timeout=10))

    failed = [n for n, ok in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    sys.exit(1 if failed else 0)


main()
