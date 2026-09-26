"""
External-process render with a .lxm proxy mesh (Blender E2E).

    Blender -b --python dev-tools/external_proxy_test.py

Reuses the lxmproxy_e2e scene: bakes the heavy floor to .lxm, enables
scene.superluxcore.config.external_process and renders 1280x720. The export
serializes the RenderConfig to .bcf — the proxy mesh must be written
as a file reference and re-mapped inside the detached runner, which
never sees Blender's mesh data.

PASS requires: the .bcf was written (Blender log shows "Saving proxy
mesh as file reference"), the runner produced a beauty output, and the
image has real pixel variance (an un-mapped empty proxy would render
nothing but sky).
"""

import sys
import os
import time
import glob
import shutil
import tempfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lxmproxy_e2e_test import build_scene, LXM

OUT = "/tmp/superluxcore_extproxy_720p.png"
WORKGLOB = os.path.join(tempfile.gettempdir(), "blc_extrender_*")


def main():
    scene, floor = build_scene()

    bpy.context.view_layer.objects.active = floor
    floor.select_set(True)
    bpy.ops.superluxcore.bake_lxm_proxy(filepath=LXM)
    assert floor.superluxcore.proxy_filepath == LXM
    print(f"[ExtProxy] baked {LXM}: {os.path.getsize(LXM) / 1e6:.1f} MB")

    scene.superluxcore.config.external_process = True
    for vl in scene.view_layers:
        vl.superluxcore.halt.enable = False
    scene.superluxcore.halt.use_time = True
    scene.superluxcore.halt.time = 15

    before = set(glob.glob(WORKGLOB))
    bpy.ops.render.render(write_still=True)
    print("[ExtProxy] render() returned — polling for runner")

    deadline = time.time() + 240
    workdir = None
    while time.time() < deadline:
        new = [d for d in glob.glob(WORKGLOB) if d not in before]
        if new:
            workdir = new[0]
            if os.path.exists(os.path.join(workdir, "__done__")):
                break
        time.sleep(2)

    assert workdir, "no external render workdir appeared"
    bcf = os.path.join(workdir, "scene.bcf")
    assert os.path.exists(bcf), "scene.bcf missing"
    # The .lxm proxy serializes as a name-only stub: the mesh body
    # (~5 MB of verts/tris/normals/UVs) must NOT be inside the archive
    bcf_mb = os.path.getsize(bcf) / 1e6
    lxm_mb = os.path.getsize(LXM) / 1e6
    print(f"[ExtProxy] bcf={bcf_mb:.1f} MB lxm={lxm_mb:.1f} MB")
    assert bcf_mb < lxm_mb + 4, \
        "bcf looks like it embedded the proxy mesh body"

    beauty = None
    for pat in ("*IMAGEPIPELINE*.png", "*.png", "*.exr"):
        hits = sorted(glob.glob(os.path.join(workdir, pat)))
        if hits:
            beauty = hits[-1]
            break
    assert beauty, "no output image produced"
    shutil.copyfile(beauty, OUT)

    # Geometry sanity: an empty (un-remapped) proxy renders sky only,
    # which has far less pixel variance than lit terrain
    img = bpy.data.images.load(OUT)
    w, h = img.size
    px = img.pixels[:]
    mean = sum(px[::997]) / max(1, len(px[::997]))
    var = sum((v - mean) ** 2 for v in px[::997]) / max(1, len(px[::997]))
    print(f"[ExtProxy] beauty {w}x{h} var={var:.4f} "
          f"({os.path.getsize(OUT) / 1e3:.0f} KB)")
    assert var > 0.002, "render looks empty — proxy did not re-map"
    print("[ExtProxy] DONE")


main()
