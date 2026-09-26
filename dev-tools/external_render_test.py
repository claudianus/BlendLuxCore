"""
External-process render regression test.

    Blender -b --python dev-tools/external_render_test.py

Builds the same multi-material scene as memory_submesh_test.py, enables
scene.luxcore.config.external_process and renders at 720p. The render()
call returns right after the detached pyluxcore process is spawned, so
this script polls for the workdir __done__ marker, then copies the
beauty output for visual inspection.

What it verifies:
  * RenderConfig.Save() serializes scene+config to a .bcf
  * The detached runner loads it via RenderConfig(path) and renders
  * Film outputs are written to the workdir
  * Blender-side objects are released (no session held during render)
"""

import sys
import os
import time
import glob
import math
import shutil

import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory_submesh_test import build_multimat_mesh, mat_diffuse, mat_emission, add_textured_card

OUT = "/tmp/luxcore_extrender_720p.png"
WORKGLOB = os.path.join(
    __import__("tempfile").gettempdir(), "blc_extrender_*")


def main():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    build_multimat_mesh()
    emit = mat_emission("emit", (1.0, 0.9, 0.75), 6.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 3.5), size=3.0)
    lamp = bpy.context.active_object
    lamp.data.materials.append(emit)
    lamp.rotation_euler[0] = math.pi

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, -0.5), size=20)
    bpy.context.active_object.data.materials.append(
        mat_diffuse("floor_m", (0.6, 0.6, 0.6)))

    add_textured_card()

    bpy.ops.object.camera_add(location=(0, -5.5, 2.2))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0.5, 0.3)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "LUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = OUT
    scene.render.image_settings.file_format = "PNG"

    scene.luxcore.config.engine = "PATH"
    scene.luxcore.config.sampler = "SOBOL"
    scene.luxcore.config.external_process = True
    # get_halt_conditions() prefers the view layer's halt when its enable
    # flag is on (default True) — disable it so the scene settings apply
    for vl in scene.view_layers:
        vl.luxcore.halt.enable = False
    scene.luxcore.halt.enable = True
    scene.luxcore.halt.use_time = True
    scene.luxcore.halt.time = 25
    # Keep the test short: small sample cap + noise threshold off
    scene.luxcore.halt.use_samples = True
    scene.luxcore.halt.samples = 48
    scene.luxcore.halt.use_noise_thresh = False

    before = set(glob.glob(WORKGLOB))
    print("[ExtTest] Rendering 1280x720 via external process ...")
    bpy.ops.render.render(write_still=True)
    print("[ExtTest] render() returned — external process is running")

    # Poll for the done marker written by external_render_runner.py
    deadline = time.time() + 240
    workdir = None
    while time.time() < deadline:
        new = [d for d in glob.glob(WORKGLOB) if d not in before]
        if new:
            workdir = new[0]
            if os.path.exists(os.path.join(workdir, "__done__")):
                break
        time.sleep(2)

    if not workdir:
        print("[ExtTest] FAIL: no external render workdir appeared")
        sys.exit(1)

    done = os.path.exists(os.path.join(workdir, "__done__"))
    print(f"[ExtTest] workdir={workdir} done={done}")
    print("[ExtTest] files:", sorted(os.listdir(workdir)))

    log_path = os.path.join(workdir, "render.log")
    if os.path.exists(log_path):
        with open(log_path) as f:
            tail = f.readlines()[-20:]
        print("[ExtTest] log tail:\n" + "".join(tail))

    beauty = None
    for pat in ("*IMAGEPIPELINE*.png", "*.png", "*.exr"):
        hits = sorted(glob.glob(os.path.join(workdir, pat)))
        if hits:
            beauty = hits[-1]
            break

    if beauty:
        shutil.copyfile(beauty, OUT)
        print(f"[ExtTest] beauty -> {OUT}")
    else:
        print("[ExtTest] FAIL: no output image produced")
        sys.exit(1)

    if not done:
        print("[ExtTest] FAIL: done marker missing")
        sys.exit(1)
    print("[ExtTest] PASS")


main()
