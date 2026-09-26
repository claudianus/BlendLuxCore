_needs_reload = "bpy" in locals()

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

import math
import threading
import time
import os
import numpy as np
import tempfile
from shutil import which
from os.path import dirname
import pyluxcore
from .. import utils
from ..utils import pfm

if _needs_reload:
    import importlib

    importlib.reload(pyluxcore)
    importlib.reload(utils)


def run_denoiser(framebuffer, engine):
    """Denoiser worker (background thread).

    Only the OIDN compute runs here. Everything touching GL or Blender
    (framebuffer.update/draw, tag_redraw) must happen on the main thread,
    so the worker only raises a flag that view_draw() consumes. (Calling
    .run() instead of .start() here used to freeze the whole UI for the
    full OIDN run on every pause.)
    """
    try:
        session = engine.session
        film = session.GetFilm()
        film.ApplyOIDN(0)  # Apply on first stage in pipeline
    except Exception:
        import traceback

        traceback.print_exc()
        return
    framebuffer._denoise_done = True


class FrameBuffer:
    """FrameBuffer used for viewport render"""

    def __init__(self, engine, context, scene):
        filmsize = utils.calc_filmsize(scene, context)
        self._width, self._height = filmsize
        self._border = utils.calc_blender_border(scene, context)
        self._offset_x, self._offset_y = self._calc_offset(
            context, scene, self._border
        )
        self._pixel_size = int(scene.luxcore.viewport.pixel_size)

        self._transparent = self._initialize_transparency(scene, context)
        bufferdepth = 4 if self._transparent else 3
        self._output_type = (
            pyluxcore.FilmOutputType.RGBA_IMAGEPIPELINE
            if self._transparent
            else pyluxcore.FilmOutputType.RGB_IMAGEPIPELINE
        )

        self.buffer = gpu.types.Buffer(
            "FLOAT", [self._width * self._height * bufferdepth]
        )
        self._init_opengl()

        # Denoiser
        self.denoised = False  # Set to true after denoising
        self._denoiser_thread = None
        self._denoise_done = False
        self._denoise_session = None
        self._texture = None
        self._pixels_dirty = True
        self._last_update = 0.0
        self._last_pixels = None  # Last rendered pixels (h, w, depth), used for resize transitions
        self._from_transition = False  # True while showing a resampled frame from before a restart
        # Hold-last-frame: after a scene/camera edit the film is reset to
        # black; uploading that empty film flashes the viewport black. While
        # _pending_reset is set, updates only swap in once real samples exist.
        self._pending_reset = True
        self._pending_reset_deadline = 0.0
        # Async readback: GetOutputFloat (film download + imagepipeline) runs
        # on a worker thread so the device-queue drain doesn't stall the UI.
        self._read_thread = None
        self._read_done = False
        self._read_data = None
        self._read_session = None
        # Interactive denoise: while True, denoised frames own the display and
        # raw film updates are suppressed (avoids raw/denoised alternation).
        self._interactive_denoise = False
        self._last_interactive_denoise = 0.0

    @classmethod
    def _transition_from(cls, old_framebuffer, engine, context, scene):
        """Create a replacement FrameBuffer that starts out showing the
        previous framebuffer's last image, resampled to the new size.

        This keeps the viewport from flashing black during resizes: the
        previous image is stretched over the new area while the render
        session restarts, then converges normally."""
        new_fb = cls(engine, context, scene)
        if (
            old_framebuffer is not None
            and getattr(old_framebuffer, "_last_pixels", None) is not None
            and new_fb._width > 0
            and new_fb._height > 0
        ):
            try:
                old_h, old_w = old_framebuffer._last_pixels.shape[:2]
                old_depth = old_framebuffer._last_pixels.shape[2]
                new_depth = 4 if new_fb._transparent else 3
                if old_depth == new_depth and old_h > 0 and old_w > 0:
                    # Nearest-neighbor resample of the last rendered frame
                    ys = (np.arange(new_fb._height) * old_h / new_fb._height).astype(np.intp)
                    xs = (np.arange(new_fb._width) * old_w / new_fb._width).astype(np.intp)
                    resampled = old_framebuffer._last_pixels[np.ix_(ys, xs)]
                    new_fb.buffer = gpu.types.Buffer(
                        "FLOAT",
                        [new_fb._width * new_fb._height * new_depth],
                        resampled.reshape(-1).astype(np.float32),
                    )
                    new_fb._from_transition = True
            except Exception:
                # Any failure here just falls back to the default (black) buffer
                pass
        return new_fb

    def _initialize_transparency(self, scene, context):
        if utils.is_valid_camera(
            scene.camera
        ) and not utils.in_material_shading_mode(context):
            return scene.camera.data.luxcore.imagepipeline.transparent_film
        return False

    def _init_opengl(self):
        width, height = (
            self._width * self._pixel_size,
            self._height * self._pixel_size,
        )
        x, y = self._offset_x, self._offset_y

        position = [
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
            (x, y),
            (x + width, y + height),
        ]

        self.shader = gpu.shader.from_builtin("IMAGE")
        self.batch = batch_for_shader(
            self.shader,
            "TRIS",
            {
                "pos": position,
                "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0), (1, 1)],
            },
        )

    def __del__(self):
        del self.buffer

    def needs_replacement(self, context, scene):
        if (self._width, self._height) != utils.calc_filmsize(scene, context):
            return True
        valid_cam = utils.is_valid_camera(scene.camera)
        if valid_cam:
            if (
                self._transparent
                != scene.camera.data.luxcore.imagepipeline.transparent_film
            ):
                return True
        elif self._transparent:
            # By default (if no camera is available), the film is not transparent
            return True
        new_border = utils.calc_blender_border(scene, context)
        if self._border != new_border:
            return True
        if (self._offset_x, self._offset_y) != self._calc_offset(
            context, scene, new_border
        ):
            return True
        if self._pixel_size != int(scene.luxcore.viewport.pixel_size):
            return True
        return False

    def _calc_offset(self, context, scene, border):
        region_size = context.region.width, context.region.height
        view_camera_offset = list(context.region_data.view_camera_offset)
        view_camera_zoom = context.region_data.view_camera_zoom
        zoom = 0.25 * ((math.sqrt(2) + view_camera_zoom / 50) ** 2)

        render = scene.render
        region_width, region_height = region_size
        border_min_x, border_max_x, border_min_y, border_max_y = border

        if (
            context.region_data.view_perspective == "CAMERA"
            and render.use_border
        ):
            # Offset is only needed if viewport is in camera mode and uses
            # border rendering
            sensor_fit = scene.camera.data.sensor_fit

            aspectratio, aspect_x, aspect_y = utils.calc_aspect(
                render.resolution_x * render.pixel_aspect_x,
                render.resolution_y * render.pixel_aspect_y,
                sensor_fit,
            )

            base = 0.5 * zoom
            if sensor_fit == "AUTO":
                base *= max(region_width, region_height)
            elif sensor_fit == "HORIZONTAL":
                base *= region_width
            elif sensor_fit == "VERTICAL":
                base *= region_height

            offset_x = self._cam_border_offset(
                aspect_x,
                base,
                border_min_x,
                region_width,
                view_camera_offset[0],
                zoom,
            )
            offset_y = self._cam_border_offset(
                aspect_y,
                base,
                border_min_y,
                region_height,
                view_camera_offset[1],
                zoom,
            )

        else:
            offset_x = region_width * border_min_x + 1
            offset_y = region_height * border_min_y + 1

        # offset_x, offset_y are in pixels
        return int(offset_x), int(offset_y)

    def _cam_border_offset(
        self, aspect, base, border_min, region_width, view_camera_offset, zoom
    ):
        return (
            0.5 - 2 * zoom * view_camera_offset
        ) * region_width + aspect * base * (2 * border_min - 1)

    def start_denoiser(self, engine):
        self._denoise_done = False
        self._denoise_session = engine.session
        self._denoiser_thread = threading.Thread(
            target=run_denoiser,
            args=(self, engine),
            daemon=True,
            name="LuxCoreViewportDenoise",
        )
        self._denoiser_thread.start()

    def is_denoiser_active(self):
        return self._denoiser_thread and self._denoiser_thread.is_alive()

    def reset_denoiser(self):
        self.denoiser_result_cached = False  # TODO
        print("RESET DENOISER")
        self.denoised = False
        # No join: a stale worker only flips _denoise_done, which
        # consume_denoise_result() ignores unless the session still matches.
        self._denoise_session = None
        self._denoise_done = False
        self._denoiser_thread = None
        self._interactive_denoise = False

    # Max time to keep showing the previous frame after a film reset before
    # falling back to whatever the (possibly still-empty) film holds.
    HOLD_LAST_FRAME_MAX_S = 0.5
    # Min interval between interactive OIDN runs during rendering.
    INTERACTIVE_DENOISE_INTERVAL = 1.0

    def begin_reset(self):
        """Mark that the film was just cleared by a scene/camera edit.

        While set, update() keeps displaying the previous frame until the
        new render has produced visible samples (bounded by
        HOLD_LAST_FRAME_MAX_S so genuinely black scenes still display).
        """
        self._pending_reset = True
        if self._pending_reset_deadline < time.time():
            # Deadline anchored at the FIRST reset: during a sustained orbit
            # (resets every draw) the hold expires once and live low-sample
            # frames take over instead of pinning a stale view forever.
            self._pending_reset_deadline = (
                time.time() + self.HOLD_LAST_FRAME_MAX_S
            )
        # The denoised frames are stale now; raw updates resume until the
        # interactive denoiser re-engages.
        self._interactive_denoise = False

    def consume_denoise_result(self, engine, final=True):
        """Apply a finished background denoise on the MAIN thread.

        Returns True when a fresh denoised image was uploaded (caller
        should tag_redraw). Stale workers (session replaced since) are
        ignored. final=False for interactive (mid-render) denoise results:
        the paused-state denoiser stays armed so a fresh denoise still runs
        on the converged film once rendering pauses.
        """
        if not getattr(self, "_denoise_done", False):
            return False
        self._denoise_done = False
        # The worker finished (result available): drop the thread reference
        # so interactive_denoise_tick() can tell "finished" from "died".
        self._denoiser_thread = None
        if getattr(self, "_denoise_session", None) is not engine.session:
            return False
        try:
            engine.session.UpdateStats()
        except RuntimeError:
            return False
        self.update(engine.session, execute_imagepipeline=False, force=True)
        if final:
            self.denoised = True
        return True

    def _fetch_pixels(self, luxcore_session, execute_imagepipeline):
        """Blocking film readback + imagepipeline. Runs the device-queue
        drain; with the GIL released by pyluxcore this is safe to call on a
        worker thread."""
        bufferdepth = 4 if self._transparent else 3
        size = self._width * self._height * bufferdepth
        data = np.empty(size, dtype=np.float32)
        luxcore_session.GetFilm().GetOutputFloat(
            self._output_type,
            data,
            0,  # index
            execute_imagepipeline
        )
        # The gpu buffer uses 16-bit float. Values >= 65520 get cast to
        # infinity, leading to a black viewport.
        data[data > 65519] = 65519
        return data

    def _accept_pixels(self, data, force=False):
        """Swap newly fetched pixels into the GPU buffer (main thread)."""
        now = time.time()
        if self._pending_reset and not force:
            if np.any(data > 0.0) or now >= self._pending_reset_deadline:
                self._pending_reset = False
            else:
                # Film still empty after the reset: keep the previous frame.
                return False
        if self._interactive_denoise and not force:
            # Denoised frames own the display; raw uploads would alternate
            # with them and flicker.
            return False
        bufferdepth = 4 if self._transparent else 3
        # Keep a copy of the last rendered pixels so a replacement framebuffer
        # can show them (resampled) while the new session starts up
        self._last_pixels = data.reshape(self._height, self._width, bufferdepth).copy()
        self.buffer = gpu.types.Buffer(
            "FLOAT", [self._width * self._height * bufferdepth], data
        )
        self._pixels_dirty = True
        return True

    def update(self, luxcore_session, execute_imagepipeline=True, force=False):
        # Throttle film readback: GetOutputFloat() stalls the device
        # pipeline (a full-film download + imagepipeline run per draw) and
        # view_draw() runs at display rate. 10 Hz is plenty for a progressive
        # preview and keeps the GPU rendering instead of stalling. The
        # denoise path forces an immediate upload.
        now = time.time()
        if not force and now - self._last_update < 0.1:
            return False
        self._last_update = now
        data = self._fetch_pixels(luxcore_session, execute_imagepipeline)
        return self._accept_pixels(data, force)

    def start_async_update(self, luxcore_session, execute_imagepipeline=True):
        """Kick off a background film readback; the result lands in
        _read_data and is consumed by consume_async_update() on the main
        thread. No-op while a read is already in flight."""
        if self._read_thread is not None and self._read_thread.is_alive():
            return False
        self._read_session = luxcore_session
        self._read_done = False
        self._last_update = time.time()

        def work():
            try:
                self._read_data = self._fetch_pixels(
                    luxcore_session, execute_imagepipeline
                )
            except Exception:
                self._read_data = None
                import traceback

                traceback.print_exc()
            self._read_done = True

        self._read_thread = threading.Thread(
            target=work, daemon=True, name="LuxCoreViewportReadback"
        )
        self._read_thread.start()
        return True

    def consume_async_update(self, luxcore_session):
        """Main thread: pick up a finished async readback. Returns True when
        a fresh frame was swapped into the buffer."""
        if not self._read_done:
            return False
        self._read_done = False
        data, self._read_data = self._read_data, None
        self._read_thread = None
        if self._read_session is not luxcore_session or data is None:
            return False
        return self._accept_pixels(data)

    def update_async(self, luxcore_session, execute_imagepipeline=True):
        """Non-blocking readback driver for view_draw: consumes a finished
        read, otherwise starts a new one at the 10 Hz cadence."""
        if self.consume_async_update(luxcore_session):
            return True
        now = time.time()
        if now - self._last_update < 0.1:
            return False
        return self.start_async_update(luxcore_session, execute_imagepipeline)

    def interactive_denoise_tick(self, engine, min_samples):
        """Periodic OIDN while the render is still running (interactive
        denoise, Octane-style). Returns True when the denoise pipeline owns
        the display this frame (worker busy or a denoised frame just landed),
        False when the caller should fall back to raw film updates."""
        if self._pending_reset:
            return False
        # A finished worker uploads its denoised frame here (main thread).
        # final=False: mid-render results don't disarm the pause denoiser.
        if self.consume_denoise_result(engine, final=False):
            self._interactive_denoise = True
            self._last_interactive_denoise = time.time()
            return True
        if self.is_denoiser_active():
            return self._interactive_denoise
        if (
            self._interactive_denoise
            and self._denoiser_thread is not None
            and not self._denoise_done
        ):
            # Last worker exited without producing a result: disarm so raw
            # updates resume instead of freezing on the last denoised frame.
            self._interactive_denoise = False
        if not self._interactive_denoise:
            # Not engaged yet: start once the film has some samples.
            try:
                stats = engine.session.GetStats()
                pass_count = stats.Get("stats.renderengine.pass").GetInt()
            except RuntimeError:
                return False
            if pass_count < max(1, min_samples):
                return False
        if (
            time.time() - self._last_interactive_denoise
            < self.INTERACTIVE_DENOISE_INTERVAL
        ):
            return self._interactive_denoise
        self.start_denoiser(engine)
        # Keep the current frame up until the first denoised result lands.
        return self._interactive_denoise

    def draw(self):
        format = "RGBA16F" if self._transparent else "RGB16F"
        # Re-create the GPU texture only when new pixels arrived; drawing
        # the same texture every frame churns VRAM for nothing (and the old
        # texture was never explicitly freed).
        if getattr(self, "_pixels_dirty", True) or getattr(self, "_texture", None) is None:
            self._texture = gpu.types.GPUTexture(
                size=(self._width, self._height),
                layers=0,
                is_cubemap=False,
                format=format,
                data=self.buffer,
            )
            self._pixels_dirty = False
        self.shader.uniform_sampler("image", self._texture)
        self.batch.draw(self.shader)
