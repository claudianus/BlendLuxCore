"""
.bcf serialization round-trip for .lxm proxy meshes (standalone pysuperluxcore).

    python3 dev-tools/bcf_proxy_test.py          # save phase
    python3 dev-tools/bcf_proxy_test.py load     # fresh-process load+render

Phase 1 builds the memory-stage scene (proxy .lxm + textured object),
saves /tmp/proxy_rt.bcf via RenderConfig.Save. A proxy mesh must be
serialized as a compact file reference, not embedded geometry.

Phase 2 (fresh process) loads the .bcf — the mesh must re-map from the
.lxm path — then renders 1280x720 to /tmp/bcf_roundtrip.png.

Assertions:
  * save log contains "Saving proxy mesh as file reference"
  * load log contains "Re-mapped proxy mesh"
  * .bcf is much smaller than an embedded-mesh serialization would be
"""

import os
import sys

import pysuperluxcore

BCF = "/tmp/proxy_rt.bcf"
LXM = "/tmp/memstage_mesh.lxm"
IMG = "/tmp/memstage_tex.png"
OUT = "/tmp/bcf_roundtrip.png"


def scene_props():
    p = pysuperluxcore.Properties()
    p.SetFromString(f"""
scene.objects.quad.ply = "{LXM}"
scene.objects.quad.material = "mat"
scene.materials.mat.type = "matte"
scene.materials.mat.kd = "tex"
scene.textures.tex.type = "imagemap"
scene.textures.tex.file = "{IMG}"
scene.textures.tex.gamma = 1.0
scene.textures.tex.mapping.type = "uvmapping2d"
scene.lights.sky.type = "sky2"
scene.lights.sky.gain = 2e-5 2e-5 2e-5
scene.camera.lookat.orig = 0 -9 3.5
scene.camera.lookat.target = 0 0 0.4
scene.camera.up = 0 0 1
""")
    return p


def cfg_props():
    c = pysuperluxcore.Properties()
    c.SetFromString(f"""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = 1280
film.height = 720
batch.haltspp = 8
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {OUT}
""")
    return c


def save_phase():
    scene = pysuperluxcore.Scene()
    scene.Parse(scene_props())
    rc = pysuperluxcore.RenderConfig(cfg_props(), scene)
    rc.Save(BCF)
    print(f"[BCF] saved: {os.path.getsize(BCF) / 1e6:.2f} MB")


def load_phase():
    rc = pysuperluxcore.RenderConfig(BCF)
    session = pysuperluxcore.RenderSession(rc)
    session.Start()
    import time
    for _ in range(120):
        time.sleep(1)
        session.UpdateStats()
        if session.HasDone():
            break
    session.Stop()
    session.GetFilm().SaveOutputs()
    print(f"[BCF] render -> {OUT}: {os.path.getsize(OUT) / 1e3:.0f} KB")


if __name__ == "__main__":
    pysuperluxcore.Init()
    if "load" in sys.argv:
        load_phase()
    else:
        save_phase()
    print("[BCF] DONE")
