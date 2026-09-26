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

if _needs_reload:
    import importlib

    importlib.reload(export)
    importlib.reload(utils)
    importlib.reload(draw)


# Executed in separate thread
def start_session(engine):
    try:
        engine.session.Start()
        engine.viewport_start_time = time()
    except ReferenceError:
        # Could not start render session because RenderEngine struct was deleted (caused
        # by the user cancelling the viewport render before this function is called)
        pass
    except Exception as error:
        engine.session = None
        # Reset the exporter to invalidate all caches
        engine.exporter = None

        engine.update_stats("Error: ", str(error))
        LuxCoreErrorLog.add_error(error)

        import traceback

        traceback.print_exc()
    finally:
        # Note: Due to CPython implementation details, it's not necessary to use a
        # lock here (this modification is atomic). It MUST run on every path:
        # the early return below used to skip it, permanently deadlocking
        # view_update() on starting_session == True.
        engine.starting_session = False


def force_session_restart(engine):
    """
    https://github.com/LuxCoreRender/BlendLuxCore/issues/577
    For unknown reasons, the old way of handling changes to the renderconfig,
    like a viewport resize or renderengine settings edit, now causes a memory
    leak. I have not been able to track it down. (The original code is in
    export/__init__.py in the method _update_config()) As a workaround, I stop
    and delete the session to trigger a full re-export of the scene and a fresh
    restart of the viewport render.
    """
    if engine.session is not None:
        engine.session.Stop()
        # Explicitly drop the last reference so the stopped session (and the
        # copy of the scene it holds) is freed immediately instead of
        # accumulating until the next garbage collection run
        del engine.session
        engine.session = None


def view_update(engine, context, depsgraph, changes=None):
    start = time()

    if engine.starting_session or engine.viewport_fatal_error:
        # Prevent deadlock
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
            engine.session = engine.exporter.create_session(
                depsgraph, context, engine=engine
            )
            # Start in separate thread to avoid blocking the UI
            engine.starting_session = True
            engine.is_first_viewport_start = False
            import threading

            session_thread = threading.Thread(
                target=start_session, args=(engine,), daemon=True,
                name="LuxCoreViewportStart",
            )
            session_thread.start()
        except Exception as error:
            if engine.session is not None:
                # in case engine.session was already set in the try block
                # before the error
                del engine.session
            engine.session = None
            # Reset the exporter to invalidate all caches
            engine.exporter = None
            engine.viewport_fatal_error = str(error)

            engine.update_stats("Error: ", str(error))
            LuxCoreErrorLog.add_error(error)

            import traceback

            traceback.print_exc()
        return

    s = time()
    changes = engine.exporter.get_changes(depsgraph, context, changes)

    if changes:
        if changes == export.Change.CONFIG:
            # Config-only change (e.g. viewport resize, engine settings):
            # rebuild just the RenderConfig on the existing LuxCore scene
            # instead of re-exporting the whole scene
            try:
                engine.session = engine.exporter._update_config(
                    engine.session, engine.exporter.config_cache.props
                )
                engine.viewport_start_time = time()

                if engine.framebuffer:
                    engine.framebuffer.begin_reset()
                    engine.framebuffer.reset_denoiser()
            except Exception as error:
                # Fall back to the safe full-restart path if the fast path
                # fails (e.g. unsupported config change in LuxCore)
                LuxCoreErrorLog.add_error(error)
                import traceback

                traceback.print_exc()
                force_session_restart(engine)
            return
        if changes & export.Change.REQUIRES_VIEW_UPDATE:
            # Only restart the session if the view transform didn't change by
            # itself
            if engine.framebuffer:
                # Keep the last frame on screen while the new session boots
                engine.framebuffer.begin_reset()
            force_session_restart(engine)
            return
        s = time()
        # We have to re-assign the session because it might have been replaced
        # due to filmsize change
        engine.session = engine.exporter.update(
            depsgraph, context, engine.session, changes
        )
        engine.viewport_start_time = time()

        if engine.framebuffer:
            engine.framebuffer.begin_reset()
            engine.framebuffer.reset_denoiser()


def view_draw(engine, context, depsgraph):
    scene = depsgraph.scene_eval

    if engine.starting_session:
        engine.tag_redraw()
        return

    if engine.viewport_fatal_error:
        engine.update_stats("Error:", engine.viewport_fatal_error)
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

        engine.update_stats("Starting viewport render", message)
        engine.viewport_starting_message_shown = True
        # Keep showing the previous session's last frame while the new
        # session exports/starts instead of flashing black.
        if engine.framebuffer:
            engine.framebuffer.draw()
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

    if changes == export.Change.CONFIG:
        # Config-only change detected during draw (e.g. viewport resize):
        # rebuild the RenderConfig on the existing scene right away. The
        # framebuffer transition above keeps the last image on screen, so
        # the user sees the resized old frame instead of a black flash, and
        # the new session replaces it as soon as it has its first frame.
        try:
            engine.session = engine.exporter._update_config(
                engine.session, engine.exporter.config_cache.props
            )
            engine.viewport_start_time = time()
            framebuffer.begin_reset()
            framebuffer.reset_denoiser()
        except Exception as error:
            LuxCoreErrorLog.add_error(error)
            import traceback

            traceback.print_exc()
            force_session_restart(engine)
        engine.tag_redraw()
        framebuffer.draw()
        return
    elif changes & export.Change.REQUIRES_VIEW_UPDATE:
        engine.tag_redraw()
        # Keep the last frame up while the session restarts (the session-None
        # branch in the next draw keeps drawing it until the new session's
        # first real samples arrive).
        framebuffer.begin_reset()
        # view_update(engine, context, depsgraph, changes)  # Disabled, see comment on force_session_restart()
        force_session_restart(engine)
        framebuffer.draw()
        return
    elif changes & (export.Change.CAMERA | export.Change.MATERIAL):
        # Only update in view_draw if it is a camera update,
        # for everything else we call view_update().
        # We have to re-assign the session because it might have been
        # replaced due to filmsize change.
        engine.session = engine.exporter.update(
            depsgraph, context, engine.session, changes
        )
        engine.viewport_start_time = time()
        # Film was reset by the edit: hold the last frame until the new
        # render has produced visible samples (no black flash on orbit).
        framebuffer.begin_reset()
        framebuffer.reset_denoiser()

    if utils.in_material_shading_mode(context):
        if not engine.session.IsInPause():
            # Non-blocking: draw whatever the film holds instead of waiting
            # for a full frame (avoids UI freezes on session restarts)
            try:
                engine.session.UpdateStats()
                framebuffer.update(engine.session)
            except RuntimeError:
                pass
            engine.update_stats("", "")

            stats = engine.session.GetStats()
            samples = stats.Get("stats.renderengine.pass").GetInt()

            if samples >= 5:
                engine.session.Pause()
            else:
                engine.tag_redraw()
        framebuffer.draw()
        return

    # Check if we need to pause the viewport render
    # (note: the LuxCore stat "stats.renderengine.time" is not reliable here)
    rendered_time = time() - engine.viewport_start_time
    halt_time = scene.luxcore.viewport.halt_time
    status_message = ""

    if rendered_time > halt_time:
        # Put in pause...
        if not engine.session.IsInPause():
            print("[Engine/Viewport] Pausing session")
            engine.session.Pause()
        status_message = "(Paused)"

        # ...and denoise
        use_oidn = context.scene.luxcore.viewport.get_denoiser(context) == "OIDN"
        if use_oidn and not framebuffer.denoised:
            # The background worker only computes OIDN; the upload/draw
            # happens here on the main thread (GL-safe).
            if framebuffer.consume_denoise_result(engine):
                pass  # fresh denoised image is already uploaded
            elif not framebuffer.is_denoiser_active():
                print("Starting OIDN denoiser...")
                framebuffer.start_denoiser(engine)
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
            engine.session.UpdateStats()
            vp = scene.luxcore.viewport
            interactive = (
                vp.denoise_interactive
                and vp.get_denoiser(context) == "OIDN"
                and not utils.in_material_shading_mode(context)
            )
            handled = (
                framebuffer.interactive_denoise_tick(engine, vp.min_samples)
                if interactive
                else False
            )
            if not handled:
                # Async film readback: the device-queue drain runs on a
                # worker thread, the finished frame is consumed here.
                framebuffer.update_async(engine.session)
        except RuntimeError:
            # Session not started yet / no film available: keep showing the
            # last framebuffer contents instead of flashing black
            pass
        engine.tag_redraw()

    framebuffer.draw()

    # Show formatted statistics in Blender UI
    config = engine.session.GetRenderConfig()
    stats = engine.session.GetStats()
    pretty_stats = utils_render.get_pretty_stats(config, stats, scene, context)
    engine.update_stats(pretty_stats, status_message)
