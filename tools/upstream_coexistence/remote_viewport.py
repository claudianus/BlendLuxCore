"""Remote viewport render for the upstream coexistence build.

Two pyluxcore builds can never share one process (dyld weak-symbol
coalescing -> EXC_ARM_DA_ALIGN), so the upstream engine's viewport render
runs in a persistent --factory-startup Blender worker that owns only the
upstream pyluxcore. This module lives in the main process:

  view_update()      -> mark scene dirty
  view_draw()        -> snapshot view params, arm the save timer,
                        draw the newest frame the worker produced
  _save_timer()      -> (bpy timer, main thread) save .blend copy + cmd.json
  shutdown()         -> stop the worker (engine __del__ / unregister)

IPC (inside a per-session temp dir):
  cmd.json    written atomically by the timer: view params + blend path
  scene.blend written by save_as_mainfile(copy=True) next to it
  frame.bin   written atomically by the worker (os.replace): fixed header
              + float32 RGB(A) film output
  status.txt  free-form worker status line for the stats display
"""

import json
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path

import bpy
import gpu
import numpy as np

from . import luxloader
from .draw.viewport import FrameBuffer

MAGIC = b"UCV1"
HDR_FMT = "<4sQIIIQd"  # magic, seq, w, h, channels, samples, elapsed
HDR_PACKED = struct.calcsize(HDR_FMT)  # 40
HDR_SIZE = 64  # padded header area; pixel data starts at this offset
# Trailing-edge debounce: keep collecting changes while the user
# interacts, ship the .blend once they pause.
DEBOUNCE = 0.45
TIMER_INTERVAL = 0.15
WORKER_BOOT_TIMEOUT = 120.0


class _State:
    proc = None
    workdir = None
    log_fp = None
    cmd_seq = 0
    want_cmd = None          # pending view-params snapshot
    last_change = 0.0        # last time want_cmd was refreshed
    last_view_key = None
    timer_registered = False
    frame_seq_seen = 0
    status = "starting worker"


_S = _State()


class RemoteFrameBuffer(FrameBuffer):
    """FrameBuffer whose pixels come from the worker instead of a session."""

    def update_pixels(self, data):
        data[data > 65519] = 65519
        depth = 4 if self._transparent else 3
        self.buffer = gpu.types.Buffer(
            "FLOAT", [self._width * self._height * depth], data
        )


def _collect_cmd(context):
    rd = context.region_data
    sd = context.space_data
    vm = rd.view_matrix
    return {
        "w": context.region.width,
        "h": context.region.height,
        "view_perspective": rd.view_perspective,
        "view_matrix": [vm[i][j] for i in range(4) for j in range(4)],
        "view_distance": getattr(rd, "view_distance", 10.0),
        "view_camera_zoom": getattr(rd, "view_camera_zoom", 0.0),
        "view_camera_offset": list(
            getattr(rd, "view_camera_offset", (0.0, 0.0))
        ),
        "lens": getattr(sd, "lens", 50.0),
        "clip_start": getattr(sd, "clip_start", 0.1),
        "clip_end": getattr(sd, "clip_end", 1000.0),
        "shading": getattr(getattr(sd, "shading", None), "type", "RENDERED"),
        "use_render_border": getattr(sd, "use_render_border", False),
        "render_border_min_x": getattr(sd, "render_border_min_x", 0.0),
        "render_border_max_x": getattr(sd, "render_border_max_x", 1.0),
        "render_border_min_y": getattr(sd, "render_border_min_y", 0.0),
        "render_border_max_y": getattr(sd, "render_border_max_y", 1.0),
    }


def _view_key(context):
    rd = context.region_data
    vm = rd.view_matrix
    return (
        context.region.width,
        context.region.height,
        rd.view_perspective,
        tuple(vm[i][j] for i in range(4) for j in range(4)),
    )


def _ensure_worker():
    if _S.proc is not None and _S.proc.poll() is None:
        return
    shutdown()
    workdir = Path(tempfile.mkdtemp(prefix="luxcore_up_vp_"))
    worker = Path(__file__).resolve().parent / "viewport_worker.py"
    env = os.environ.copy()
    env[luxloader.WORKER_ENV_VAR] = "1"
    env["LUXCORE_UP_PARENT_PID"] = str(os.getpid())
    log_fp = open(workdir / "worker.log", "w")
    _S.proc = subprocess.Popen(
        [
            bpy.app.binary_path, "--factory-startup", "-b",
            "--python", str(worker), "--", str(workdir),
        ],
        env=env, stdout=log_fp, stderr=subprocess.STDOUT,
    )
    _S.workdir = workdir
    _S.log_fp = log_fp
    _S.frame_seq_seen = 0
    _S.status = "starting worker"


def _save_timer():
    """Runs on Blender's main thread via bpy.app.timers."""
    if _S.proc is None or _S.proc.poll() is not None or _S.workdir is None:
        _S.timer_registered = False
        return None  # worker gone -> stop timer
    if _S.want_cmd is not None and \
            time.time() - _S.last_change > DEBOUNCE:
        cmd = _S.want_cmd
        _S.want_cmd = None
        try:
            blend = _S.workdir / "scene.blend"
            _S.status = "exporting scene"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), copy=True)
            _S.cmd_seq += 1
            cmd = dict(cmd, seq=_S.cmd_seq, blend=str(blend))
            tmp = _S.workdir / "cmd.tmp"
            tmp.write_text(json.dumps(cmd))
            tmp.replace(_S.workdir / "cmd.json")
            _S.status = "scene sent, worker loading"
        except Exception as error:  # noqa: BLE001 - report, keep polling
            _S.status = f"export failed: {error}"
    return TIMER_INTERVAL


def _ensure_timer():
    if not _S.timer_registered:
        bpy.app.timers.register(_save_timer, first_interval=TIMER_INTERVAL)
        _S.timer_registered = True


def _read_status():
    try:
        return (_S.workdir / "status.txt").read_text().strip()
    except OSError:
        return _S.status


def view_update(engine, context, depsgraph):
    # Any depsgraph change means the worker needs a fresh scene.blend;
    # the next view_draw collects a new snapshot (resets the debounce).
    _S.last_view_key = None


def view_draw(engine, context, depsgraph):
    _ensure_worker()
    now = time.time()

    key = _view_key(context)
    if key != _S.last_view_key or _S.want_cmd is None:
        _S.last_view_key = key
        _S.want_cmd = _collect_cmd(context)
        _S.last_change = now
        _ensure_timer()

    frame = None
    fp = _S.workdir / "frame.bin"
    try:
        raw = fp.read_bytes()
    except OSError:
        raw = b""
    if len(raw) > HDR_SIZE and raw[:4] == MAGIC:
        magic, seq, w, h, ch, samples, elapsed = struct.unpack_from(
            HDR_FMT, raw
        )
        need = w * h * ch * 4
        if len(raw) >= HDR_SIZE + need:
            frame = (w, h, ch, samples, elapsed,
                     np.frombuffer(raw, np.float32,
                                   count=w * h * ch, offset=HDR_SIZE))

    scene = context.scene
    fb = engine.framebuffer
    if fb is None or not isinstance(fb, RemoteFrameBuffer) \
            or fb.needs_replacement(context, scene):
        fb = engine.framebuffer = RemoteFrameBuffer(engine, context, scene)

    if frame is not None:
        w, h, ch, samples, elapsed, data = frame
        if (w, h) == (fb._width, fb._height) and \
                ch == (4 if fb._transparent else 3):
            fb.update_pixels(data.copy())
            _S.frame_seq_seen = seq
            status = f"upstream isolated | {samples} samples | {elapsed:.1f}s"
        else:
            status = "upstream isolated | worker resyncing"
    else:
        status = _read_status()

    fb.draw()
    engine.update_stats("LuxCore Upstream viewport", status)

    # Keep the poll loop alive; small sleep so we don't spin the draw thread.
    engine.tag_redraw()
    time.sleep(0.05)


def shutdown():
    proc = _S.proc
    _S.proc = None
    if proc is not None and proc.poll() is None:
        try:
            _S.cmd_seq += 1
            tmp = _S.workdir / "cmd.tmp"
            tmp.write_text(json.dumps({"seq": _S.cmd_seq, "stop": True}))
            tmp.replace(_S.workdir / "cmd.json")
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    if _S.log_fp is not None:
        try:
            _S.log_fp.close()
        except Exception:
            pass
        _S.log_fp = None
    _S.workdir = None
    _S.want_cmd = None
    _S.last_view_key = None
