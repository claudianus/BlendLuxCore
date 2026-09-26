from time import time

_needs_reload = "bpy" in locals()

import bpy
import pyluxcore
from .. import export
from .. import draw
from ..draw.viewport import FrameBuffer
from .. import utils
from ..utils import render as utils_render
from ..utils import get_addon_preferences
from ..utils.log import LuxCoreLog
from ..utils.errorlog import LuxCoreErrorLog
from ..export.config import convert_viewport_engine
from . import session_worker

if _needs_reload:
    import importlib

    importlib.reload(session_worker)
    importlib.reload(export)
    importlib.reload(utils)
    importlib.reload(draw)


def _worker(engine):
    """Lazily create the session worker (one per RenderEngine)."""
    worker = getattr(engine, "session_worker", None)
    if worker is None:
        # Attribute access (not the stale `from`-import) so addon reload
        # picks up the reloaded class.
        worker = session_worker.SessionWorker(engine)
        engine.session_worker = worker
    return worker


_LOCK_BUSY = object()

# Dynamic resolution switching: while the user interacts (scene edits
# arriving), RTPATHOCL runs sparse coverage passes - every pixel carries
# weight 1.0 regardless of reduction, so passes stay unbiased but finish
# in ~1/16 of the time. That shrinks both the frame-boundary wait before
# an edit applies and the first-post-reset-sample latency. Once edits
# stop arriving for _DYN_RES_TAIL_S the configured reduction is restored.
_DYN_RES_VALUE = 16
_DYN_RES_TAIL_S = 0.4


def _set_stats(engine, text, sub):
    """update_stats() with dedup: each call triggers a stats-region
    redraw, and alternating/identical writes every frame make the
    viewport text flicker. Only write when the text actually changed."""
    new = (text, sub)
    if new != getattr(engine, "_vp_stats_shown", None):
        engine._vp_stats_shown = new
        engine.update_stats(text, sub)


def _submit_jobs(engine, worker, framebuffer, jobs, has_camera):
    """Forward exporter jobs to the session worker, pacing scene edits.

    Every applied edit resets the film at the next frame boundary, and
    the film stays empty until the first preview pass lands (~20-80 ms).
    Submitting edits faster than that (a camera orbit or object drag
    fires one per draw, ~16 ms) leaves the film perpetually empty - the
    viewport renders black for the entire drag. While a reset is still
    waiting for content, edits are deferred (newest wins) and flushed
    once the film has a frame again, so the effective edit rate adapts
    to scene speed. Deferral is bounded by the pending deadline, so a
    starved film (paused session, empty scene) still releases the job.
    """
    # Sparse fast passes for the whole interaction burst - also while the
    # edit sits deferred behind a pending reset, so that reset resolves
    # faster and the deferred edit lands sooner.
    worker.submit_resolution_reduction(_DYN_RES_VALUE)
    engine._dyn_res_until = time() + _DYN_RES_TAIL_S
    engine._dyn_res_active = True
    if (
        framebuffer is not None
        and framebuffer._pending_reset
        and time() < framebuffer._pending_reset_deadline
    ):
        engine._deferred_edit_jobs = (jobs, has_camera)
        return
    for kind, payload in jobs:
        if kind == "edit":
            worker.submit_edit(payload)
        elif kind == "parse":
            worker.submit_parse(payload)
    # The edit resumes a paused session on the worker: re-anchor the
    # halt timer so the resumed render isn't instantly re-paused when
    # halt_time already elapsed (paused-lockup / black viewport).
    engine.viewport_start_time = time()
    if framebuffer is not None:
        # Film was reset by the edit: hold the last frame until the new
        # render has produced visible samples (no black flash on orbit).
        # Camera moves get a shorter hold: the old viewpoint is wrong.
        framebuffer.begin_reset(
            FrameBuffer.HOLD_LAST_FRAME_CAMERA_S if has_camera else None,
            engine=engine,
        )
        framebuffer.reset_denoiser()


def _locked_session_call(engine, fn, *args):
    """Call ``fn(*args)`` while holding the worker's session_lock.

    Non-blocking: returns _LOCK_BUSY when the worker is mid-mutation
    (scene edit / Stop / Parse), so view_draw skips the call this frame
    instead of freezing the UI behind a full tile pass. Serializing every
    session call matters for correctness too: an unsynchronized read
    while the worker stops the session can crash Blender (the Solid ->
    Rendered teardown race).
    """
    worker = getattr(engine, "session_worker", None)
    lock = getattr(worker, "session_lock", None) if worker else None
    if lock is None:
        return fn(*args)
    if not lock.acquire(blocking=False):
        return _LOCK_BUSY
    try:
        return fn(*args)
    finally:
        lock.release()


def _drain_worker_error(engine):
    """Surface async worker failures to the user (error log + stats).

    Start/config failures are persistent (bad config, missing kernels):
    latch them so the viewport stops retrying and shows the error
    instead of silently looping. Edit/parse failures already caused the
    worker to drop the session - the next view_update re-exports.
    """
    worker = getattr(engine, "session_worker", None)
    if worker is None:
        return
    err = worker.pop_error()
    if err is None:
        return
    kind, error = err
    LuxCoreErrorLog.add_error(error)
    engine.update_stats("Error: ", str(error))
    if kind in ("start", "config"):
        engine.viewport_fatal_error = str(error)


def force_session_restart(engine):
    """
    https://github.com/LuxCoreRender/BlendLuxCore/issues/577
    Mutating the RenderConfig of a running session leaks (every stopped
    session kept its copy of the scene) and used to crash. The worker
    stops the session off-thread; the next view_update() re-exports.
    """
    if getattr(engine, "session_worker", None) is not None:
        engine.session_worker.submit_stop()
    elif engine.session is not None:
        # No worker (final render / preview): stop directly.
        engine.session.Stop()
    # Drop the main-thread reference so the stopped session's scene copy
    # is freed instead of accumulating until the next GC run.
    engine.session = None


def view_update(engine, context, depsgraph, changes=None):
    worker = _worker(engine)
    _drain_worker_error(engine)

    if worker.is_starting or engine.viewport_fatal_error:
        # Prevent deadlock: a start job is in flight (or failed
        # persistently) - depsgraph updates accumulate meanwhile and are
        # consumed by the first get_changes() after the session lands.
        return

    LuxCoreErrorLog.clear(force_ui_update=False)

    if engine.session is None:
        # A fresh start clears any earlier fatal error (recovery beats a
        # permanently dead viewport; genuinely fatal errors reappear).
        engine.viewport_fatal_error = None
        engine.kernel_check_cache = None
        if not engine.viewport_starting_message_shown:
            # Let one engine.view_draw() happen so it shows a message in the UI
            return

        if not engine.is_first_viewport_start:
            filmsize = utils.calc_filmsize(depsgraph.scene_eval, context)
            was_resized = engine.last_viewport_size != filmsize
            engine.last_viewport_size = filmsize

            if was_resized:
                engine.time_of_last_viewport_resize = time()
                return
            elif (
                time() - engine.time_of_last_viewport_resize
                < engine.VIEWPORT_RESIZE_TIMEOUT
            ):
                # Don't re-export the session before the timeout is done, to
                # prevent constant re-exports while resizing
                return
        else:
            display_luxcore_logs = get_addon_preferences(
                context
            ).display_luxcore_logs
            if display_luxcore_logs:
                pyluxcore.SetLogHandler(LuxCoreLog.add)
            else:
                pyluxcore.SetLogHandler(LuxCoreLog.silent)

        try:
            print("=" * 50)
            print("[Engine/Viewport] New session")
            engine.exporter = export.Exporter()
            engine.viewport_phase = "Exporting scene"
            engine.is_first_viewport_start = False
            engine.last_viewport_size = utils.calc_filmsize(
                depsgraph.scene_eval, context
            )
            # Scene export needs the depsgraph: it has to stay on the main
            # thread. Everything from RenderConfig onward (kernel compile,
            # session start) runs on the worker so the UI never stalls.
            result = engine.exporter.export_scene(
                depsgraph, context, engine=engine
            )
            if result is not None:
                luxcore_scene, config_props = result
                engine.viewport_phase = ""
                worker.submit_start(luxcore_scene, config_props)
        except Exception as error:
            engine.session = None
            # Reset the exporter to invalidate all caches
            engine.exporter = None
            engine.viewport_fatal_error = str(error)

            engine.update_stats("Error: ", str(error))
            LuxCoreErrorLog.add_error(error)

            import traceback

            traceback.print_exc()
        return

    changes = engine.exporter.get_changes(depsgraph, context, changes)

    if not changes:
        return

    # Config changes restart the session on the *reused* LuxCore scene -
    # no scene re-export. Runs on the worker; the framebuffer keeps the
    # last frame until the new session produces samples (begin_reset).
    if changes & export.Change.CONFIG:
        worker.submit_config(engine.exporter.config_cache.props)
        changes &= ~export.Change.CONFIG
        if engine.framebuffer:
            engine.framebuffer.begin_reset(engine=engine)
            engine.framebuffer.reset_denoiser()

    if changes & (
        export.Change.REQUIRES_SCENE_EDIT
        | export.Change.REQUIRES_SESSION_PARSE
    ):
        try:
            jobs = engine.exporter.update(depsgraph, context, changes)
        except Exception as error:
            # A half-recorded edit must not be replayed; rebuild the
            # scene from scratch instead.
            LuxCoreErrorLog.add_error(error)
            import traceback

            traceback.print_exc()
            force_session_restart(engine)
            return
        _submit_jobs(
            engine, worker, engine.framebuffer, jobs,
            has_camera=bool(changes & export.Change.CAMERA),
        )


def view_draw(engine, context, depsgraph):
    scene = depsgraph.scene_eval
    worker = _worker(engine)
    _drain_worker_error(engine)

    if worker.is_starting:
        # Show what the worker is doing (export > config > kernels >
        # session start) instead of a frozen window.
        phase = worker.phase or getattr(engine, "viewport_phase", "")
        _set_stats(engine, "Starting viewport render", phase)
        if engine.framebuffer:
            engine.framebuffer.draw(context, scene)
        engine.tag_redraw()
        return

    if engine.viewport_fatal_error:
        _set_stats(engine, "Error:", engine.viewport_fatal_error)
        engine.tag_redraw()
        return

    if engine.session is None:
        config = scene.luxcore.config
        definitions = {}
        luxcore_engine, _ = convert_viewport_engine(
            context, scene, definitions, config
        )
        message = ""

        if luxcore_engine.endswith("OCL"):
            # The dummy Scene/RenderConfig below costs a full kernel-config
            # build per redraw; HasCachedKernels() only changes when the
            # config does, so cache it on the engine for the sessionless
            # phase (cleared on every fresh start above).
            if engine.kernel_check_cache is None:
                # Create dummy renderconfig to check if we have to compile OpenCL kernels
                luxcore_scene = pyluxcore.Scene()
                definitions = {
                    "scene.camera.type": "perspective",
                }
                luxcore_scene.Parse(utils.luxutils.create_props("", definitions))

                devices = scene.luxcore.devices
                definitions = {
                    "renderengine.type": "RTPATHOCL",
                    "sampler.type": "TILEPATHSAMPLER",
                    "scene.epsilon.min": config.min_epsilon,
                    "scene.epsilon.max": config.max_epsilon,
                    "opencl.devices.select": devices.devices_to_selection_string(),
                }
                config_props = utils.luxutils.create_props("", definitions)
                renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)
                engine.kernel_check_cache = (
                    renderconfig.HasCachedKernels(),
                    utils.get_addon_preferences(context).gpu_backend,
                )
            has_cached_kernels, gpu_backend = engine.kernel_check_cache
            if not has_cached_kernels:
                message = (
                    f"Compiling {gpu_backend} kernels ("
                    "((just once, usually takes 15-30 minutes)"
                )

        phase = worker.phase if worker is not None else ""
        _set_stats(
            engine,
            "Starting viewport render",
            phase or getattr(engine, "viewport_phase", "") or message,
        )
        engine.viewport_starting_message_shown = True
        # Keep showing the previous session's last frame while the new
        # session exports/starts instead of flashing black.
        if engine.framebuffer:
            engine.framebuffer.draw(context, scene)
        engine.tag_update()
        engine.tag_redraw()
        return

    if not engine.framebuffer or engine.framebuffer.needs_replacement(
        context, scene
    ):
        engine.framebuffer = FrameBuffer._transition_from(
            engine.framebuffer, engine, context, scene
        )

    framebuffer = engine.framebuffer

    # Check for changes because some actions in Blender (e.g. moving the viewport
    # camera) do not trigger a view_update() call, but only a view_draw() call.
    changes = engine.exporter.get_viewport_changes(depsgraph, context)

    if changes & export.Change.CONFIG:
        # Config-only change detected during draw (e.g. viewport resize):
        # the worker rebuilds the RenderConfig on the existing scene. The
        # framebuffer keeps the last image on screen (begin_reset), so the
        # user sees the resized old frame instead of a black flash.
        worker.submit_config(engine.exporter.config_cache.props)
        changes &= ~export.Change.CONFIG
        engine.viewport_start_time = time()
        framebuffer.begin_reset(engine=engine)
        framebuffer.reset_denoiser()
        if not changes:
            engine.tag_redraw()
            framebuffer.draw(context, scene)
            return

    if changes & (export.Change.CAMERA | export.Change.MATERIAL):
        # Only update in view_draw if it is a camera/material update,
        # for everything else we call view_update(). The ops are replayed
        # by the worker inside one scene-edit block.
        try:
            jobs = engine.exporter.update(depsgraph, context, changes)
        except Exception as error:
            LuxCoreErrorLog.add_error(error)
            import traceback

            traceback.print_exc()
            force_session_restart(engine)
            engine.tag_redraw()
            return
        _submit_jobs(
            engine, worker, framebuffer, jobs,
            has_camera=bool(changes & export.Change.CAMERA),
        )
    elif changes:
        # Non-camera changes noticed in draw: let view_update handle them.
        engine.tag_update()

    # Snapshot the session: the worker may publish a new one (or None
    # during a restart) at any time; a stale-but-alive object degrades
    # gracefully to RuntimeError while engine.session itself may turn
    # into None and raise AttributeError mid-draw.
    session = engine.session

    # Flush a camera edit deferred while the film was still empty. The
    # pending reset clears when post-reset content lands (or on the
    # bounded deadline), so this fires as soon as the last edit produced
    # a frame - the effective camera-edit rate adapts to scene speed.
    deferred = getattr(engine, "_deferred_edit_jobs", None)
    if deferred is not None:
        still_waiting = (
            framebuffer._pending_reset
            and time() < framebuffer._pending_reset_deadline
        )
        if still_waiting:
            # Keep draws flowing until the deferred edit can fire: while
            # paused, nothing tags a redraw and the final camera position
            # would never be submitted.
            engine.tag_redraw()
        else:
            engine._deferred_edit_jobs = None
            deferred_jobs, deferred_has_camera = deferred
            for kind, payload in deferred_jobs:
                if kind == "edit":
                    worker.submit_edit(payload)
                elif kind == "parse":
                    worker.submit_parse(payload)
            # Deferred edit finally firing: still interacting.
            worker.submit_resolution_reduction(_DYN_RES_VALUE)
            engine._dyn_res_until = time() + _DYN_RES_TAIL_S
            engine._dyn_res_active = True
            engine.viewport_start_time = time()
            framebuffer.begin_reset(
                FrameBuffer.HOLD_LAST_FRAME_CAMERA_S
                if deferred_has_camera
                else None,
                engine=engine,
            )
            framebuffer.reset_denoiser()

    # Interaction settled: restore the configured (dense) reduction so
    # steady-state passes converge at full quality.
    if getattr(engine, "_dyn_res_active", False) and (
        time() > getattr(engine, "_dyn_res_until", 0)
    ):
        engine._dyn_res_active = False
        worker.submit_resolution_reduction(0)

    if utils.in_material_shading_mode(context):
        paused = (
            session is not None
            and _locked_session_call(engine, session.IsInPause) is True
        )
        if not paused:
            # Non-blocking: draw whatever the film holds instead of waiting
            # for a full frame (avoids UI freezes on session restarts)
            try:
                if session is not None:
                    _locked_session_call(engine, session.UpdateStats)
                    framebuffer.update_async(session, engine)
            except Exception:
                pass
            _set_stats(engine, "", "")

            samples = 0
            if session is not None:
                try:
                    stats = _locked_session_call(engine, session.GetStats)
                    samples = stats.Get("stats.renderengine.pass").GetInt()
                except Exception:
                    pass

            if samples >= 5:
                _locked_session_call(engine, session.Pause)
            else:
                engine.tag_redraw()
        framebuffer.draw(context, scene)
        return

    # Check if we need to pause the viewport render
    # (note: the LuxCore stat "stats.renderengine.time" is not reliable here)
    rendered_time = time() - engine.viewport_start_time
    halt_time = scene.luxcore.viewport.halt_time
    status_message = worker.phase if worker is not None else ""

    if rendered_time > halt_time:
        # Put in pause... (non-blocking: if the worker is mid-edit the
        # pause is simply retried on the next draw instead of freezing)
        paused = (
            session is not None
            and _locked_session_call(engine, session.IsInPause) is True
        )
        if not paused and session is not None:
            print("[Engine/Viewport] Pausing session")
            _locked_session_call(engine, session.Pause)
        status_message = status_message or "(Paused)"

        # ...and denoise
        use_oidn = context.scene.luxcore.viewport.get_denoiser(context) == "OIDN"
        if use_oidn and not framebuffer.denoised:
            # The background worker only computes OIDN; the upload/draw
            # happens here on the main thread (GL-safe).
            if framebuffer.consume_denoise_result(engine):
                pass  # fresh denoised image is already uploaded
            elif not framebuffer.is_denoiser_active():
                print("Starting OIDN denoiser...")
                framebuffer.start_denoiser(engine, session)
            status_message = "(Paused, OIDN Denoiser Working ...)"
            engine.tag_redraw()
        else:
            if use_oidn:
                status_message = "(Paused, OIDN Denoiser Done)"
            else:
                status_message = "(Paused)"

    else:
        # Not in pause yet, keep drawing.
        # WaitNewFrame() blocks the UI thread until the render session has
        # produced a full frame - after a config change or session restart
        # this can take a noticeable moment during which the viewport would
        # freeze. Instead of blocking, we render what we already have and let
        # the next view_draw() pick up the new frame (it is called again
        # because of tag_redraw() below).
        try:
            if session is not None:
                _locked_session_call(engine, session.UpdateStats)
                vp = scene.luxcore.viewport
                interactive = (
                    vp.denoise_interactive
                    and vp.get_denoiser(context) == "OIDN"
                    and not utils.in_material_shading_mode(context)
                )
                handled = (
                    framebuffer.interactive_denoise_tick(
                        engine, vp.min_samples
                    )
                    if interactive
                    else False
                )
                if not handled:
                    # Async film readback: the device-queue drain runs on a
                    # worker thread, the finished frame is consumed here.
                    framebuffer.update_async(session, engine)
        except RuntimeError:
            # Session not started yet / no film available: keep showing the
            # last framebuffer contents instead of flashing black
            pass
        engine.tag_redraw()

    framebuffer.draw(context, scene)

    # Show formatted statistics in Blender UI
    try:
        if session is None:
            raise RuntimeError("no session")
        config = _locked_session_call(engine, session.GetRenderConfig)
        stats = _locked_session_call(engine, session.GetStats)
        if config is _LOCK_BUSY or stats is _LOCK_BUSY:
            raise RuntimeError("session busy")
        pretty_stats = utils_render.get_pretty_stats(
            config, stats, scene, context
        )
        engine._vp_stats_cache = (pretty_stats, status_message)
    except Exception:
        # Session being swapped by the worker mid-draw: keep the last
        # real stats instead of flashing a phase/blank line (the stats
        # text flickers badly if it alternates every frame).
        cached = getattr(engine, "_vp_stats_cache", None)
        pretty_stats = cached[0] if cached else (
            worker.phase if worker is not None else ""
        )
    _set_stats(engine, pretty_stats, status_message)
