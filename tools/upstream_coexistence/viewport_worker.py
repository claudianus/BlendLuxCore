"""Persistent isolated viewport worker for LuxCoreRender Upstream.

Invoked as:
    Blender --factory-startup -b --python viewport_worker.py -- <workdir>

Requires LUXCORE_UP_WORKER=1 so luxloader loads the real bundled
pyluxcore, and LUXCORE_UP_PARENT_PID so the worker exits with its parent.

Protocol (all inside <workdir>):
  cmd.json    main -> worker, atomically replaced: scene.blend path,
              viewport size + view matrix/lens snapshot (or {"stop": true})
  frame.bin   worker -> main, atomically replaced: 64-byte header
              (magic UCV1, seq, w, h, channels, samples, elapsed) +
              float32 RGB(A) film output
  status.txt  worker -> main: one-line status for the stats display

The worker keeps one Blender process alive across scene updates and only
re-opens the .blend + rebuilds the session, so kernel compilation caches
stay warm between reloads.
"""

import json
import os
import struct
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace as NS

MAGIC = b"UCV1"
HDR_FMT = "<4sQIIIQd"
HDR_SIZE = 64

import bpy
import numpy as np


def _status(workdir, text):
    try:
        (workdir / "status.txt").write_text(text + "\n")
    except OSError:
        pass
    print("[vp-worker]", text, flush=True)


def _read_cmd(workdir):
    try:
        return json.loads((workdir / "cmd.json").read_text())
    except (OSError, ValueError):
        return None


def _write_frame(workdir, seq, w, h, ch, samples, elapsed, data):
    hdr = bytearray(HDR_SIZE)
    struct.pack_into(HDR_FMT, hdr, 0, MAGIC, seq, w, h, ch, samples, elapsed)
    tmp = workdir / "frame.tmp"
    with open(tmp, "wb") as f:
        f.write(hdr)
        f.write(data.tobytes())
    os.replace(tmp, workdir / "frame.bin")


def _build_context(scene, cmd):
    """Duck-typed viewport context for the add-on's export path."""
    from mathutils import Matrix, Vector

    m = cmd["view_matrix"]
    region_data = NS(
        view_matrix=Matrix((m[0:4], m[4:8], m[8:12], m[12:16])),
        view_perspective=cmd["view_perspective"],
        view_distance=cmd["view_distance"],
        view_camera_zoom=cmd["view_camera_zoom"],
        view_camera_offset=Vector(cmd["view_camera_offset"]),
    )
    space_data = NS(
        lens=cmd["lens"],
        clip_start=cmd["clip_start"],
        clip_end=cmd["clip_end"],
        shading=NS(type=cmd.get("shading", "RENDERED")),
        use_render_border=cmd.get("use_render_border", False),
        render_border_min_x=cmd.get("render_border_min_x", 0.0),
        render_border_max_x=cmd.get("render_border_max_x", 1.0),
        render_border_min_y=cmd.get("render_border_min_y", 0.0),
        render_border_max_y=cmd.get("render_border_max_y", 1.0),
        local_view=None,
    )
    return NS(
        region=NS(width=cmd["w"], height=cmd["h"]),
        region_data=region_data,
        space_data=space_data,
        scene=scene,
        preferences=bpy.context.preferences,
        window=None,
        screen=None,
        area=None,
        engine="LUXCOREUP",
    )


class _FakeEngine:
    """No-op stand-in for the RenderEngine during worker-side export."""

    is_preview = False
    is_animation = False
    aov_imagepipelines = {}

    def __getattr__(self, _name):
        # Any engine hook the exporter calls (update_progress, report,
        # update_stats, error_set, tag_*) becomes a no-op callable.
        return lambda *_a, **_k: None

    def test_break(self):
        return False


def _parent_alive():
    pid = int(os.environ.get("LUXCORE_UP_PARENT_PID", "0"))
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True  # exists, just not signalable
    except OSError:
        return False


def main():
    workdir = Path(sys.argv[sys.argv.index("--") + 1])
    _status(workdir, "booting")

    bpy.ops.preferences.addon_enable(module="bl_ext.user_default.blendluxcore_up")
    import pyluxcore_upstream as plc
    import bl_ext.user_default.blendluxcore_up as pkg
    from bl_ext.user_default.blendluxcore_up.utils import (
        view_layer as utils_view_layer,
    )

    _status(workdir, "ready, waiting for scene")
    last_seq = 0
    session = None
    film = None
    transparent = False

    while _parent_alive():
        cmd = _read_cmd(workdir)
        if cmd is None:
            time.sleep(0.05)
            continue
        if cmd.get("stop"):
            break

        if cmd["seq"] != last_seq:
            last_seq = cmd["seq"]
            if session is not None:
                try:
                    session.Stop()
                except Exception:
                    pass
                session = None
                film = None
            _status(workdir, "loading scene")
            try:
                bpy.ops.wm.open_mainfile(
                    filepath=cmd["blend"], load_ui=False
                )
                # open_mainfile resets add-on registration under
                # --factory-startup; re-enable so scene.luxcore_up exists.
                bpy.ops.preferences.addon_enable(
                    module="bl_ext.user_default.blendluxcore_up"
                )
                scene = bpy.context.scene
                scene.render.engine = "LUXCOREUP"
                scene.render.resolution_x = cmd["w"]
                scene.render.resolution_y = cmd["h"]
                scene.render.resolution_percentage = 100

                transparent = (
                    scene.camera is not None
                    and scene.camera.type == "CAMERA"
                    and cmd.get("shading") != "MATERIAL"
                    and scene.camera.data.luxcore_up.imagepipeline
                    .transparent_film
                )

                depsgraph = bpy.context.evaluated_depsgraph_get()
                ctx = _build_context(scene, cmd)
                utils_view_layer.State.active_view_layer = (
                    depsgraph.view_layer_eval.name
                )
                _status(workdir, "exporting + starting session")
                exporter = pkg.export.Exporter()
                session = exporter.create_session(
                    depsgraph, ctx, _FakeEngine(), depsgraph.view_layer_eval
                )
                if session is None:
                    _status(workdir, "export produced no session")
                    continue
                session.Start()
                film = session.GetFilm()
                _status(workdir, "rendering")
            except Exception as error:
                _status(workdir, f"error: {error}")
                traceback.print_exc()
                session = None
                film = None
                continue

        if session is not None and film is not None:
            try:
                session.UpdateStats()
                stats = session.GetStats()
                samples = stats.Get("stats.renderengine.pass").GetInt()
                elapsed = time.time() - boot_t
                w, h = cmd["w"], cmd["h"]
                ch = 4 if transparent else 3
                out = (plc.FilmOutputType.RGBA_IMAGEPIPELINE
                       if transparent
                       else plc.FilmOutputType.RGB_IMAGEPIPELINE)
                buf = np.empty(w * h * ch, dtype=np.float32)
                film.GetOutputFloat(out, buf, 0, True)
                _write_frame(workdir, last_seq, w, h, ch,
                             samples, elapsed, buf)
            except Exception as error:
                _status(workdir, f"error: {error}")
                traceback.print_exc()

        time.sleep(0.12)

    if session is not None:
        try:
            session.Stop()
        except Exception:
            pass
    print("[vp-worker] exit", flush=True)


boot_t = time.time()
main()
