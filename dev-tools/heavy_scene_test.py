"""
Heavy-scene integration test (standalone pysuperluxcore).

    python3 dev-tools/heavy_scene_test.py

Combines every out-of-core mechanism in one render:
  * .lxm proxy mesh (file-backed, demand-paged)
  * geometry spilling on a second PLY mesh (scene.spill.*)
  * 8192x4096 HDRI infinite light with cdfdim cap
  * 1280x720 PATHCPU render, image verified non-empty

Prints ru_maxrss at the end for tracking.
"""

import os
import resource
import sys

import numpy as np
import pysuperluxcore

LXM = "/tmp/memstage_mesh.lxm"      # from memory_stages_test.py
PLY = "/tmp/memstage_src.ply"       # same source mesh as plain PLY
IMG = "/tmp/envcdf_hdri.png"        # from envcdf_test.py
OUT = "/tmp/heavy_scene.png"


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def main():
    for f in (LXM, PLY, IMG):
        if not os.path.exists(f):
            sys.exit(f"missing {f} — run memory_stages_test.py and "
                     "envcdf_test.py first")

    pysuperluxcore.Init()
    base = rss_mb()

    scn = pysuperluxcore.Properties()
    scn.SetFromString(f"""
scene.objects.terrain.ply = "{LXM}"
scene.objects.terrain.material = "terra"
scene.objects.mound.ply = "{PLY}"
scene.objects.mound.material = "terra2"
scene.objects.mound.transformation = 0.6 0 0 0  0 0.6 0 0  0 0 0.6 0  0 0 1.2 1
scene.materials.terra.type = "matte"
scene.materials.terra.kd = 0.45 0.4 0.3
scene.materials.terra2.type = "matte"
scene.materials.terra2.kd = 0.55 0.35 0.25
scene.lights.env.type = "infinite"
scene.lights.env.file = "{IMG}"
scene.lights.env.gamma = 1.0
scene.lights.env.gain = 3.0 3.0 3.0
scene.lights.env.cdfdim = 512
scene.camera.lookat.orig = 0 -9 4
scene.camera.lookat.target = 0 0 0.6
scene.camera.up = 0 0 1
""")
    scene = pysuperluxcore.Scene()
    scene.Parse(scn)

    spill = pysuperluxcore.Properties()
    spill.Set(pysuperluxcore.Property("scene.spill.enable", True))
    spill.Set(pysuperluxcore.Property("scene.spill.minbytes", 1))
    scene.Parse(spill)

    cfg = pysuperluxcore.Properties()
    cfg.SetFromString(f"""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = 1280
film.height = 720
batch.haltspp = 16
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {OUT}
""")
    rc = pysuperluxcore.RenderConfig(cfg, scene)
    session = pysuperluxcore.RenderSession(rc)
    session.Start()

    import time
    while not session.HasDone():
        session.UpdateStats()
        time.sleep(0.3)
    session.Stop()
    session.GetFilm().SaveOutputs()

    print(f"[HeavyScene] base={base:.0f} peak={rss_mb():.0f} MB")

    from PIL import Image
    a = np.asarray(Image.open(OUT), np.float32)
    ok = a.mean() > 1.0 and a.std() > 1.0
    print(f"[HeavyScene] img mean={a.mean():.1f} std={a.std():.1f} "
          + ("DONE" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
