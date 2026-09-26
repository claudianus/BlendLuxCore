"""
Repeat-render leak hunt (standalone pysuperluxcore).

    python3 dev-tools/leak_hunt_test.py

Renders the same scene N times in a loop, creating a fresh
RenderConfig + RenderSession each round while the Scene stays alive,
then samples ru_maxrss after every Stop(). A healthy engine plateaus:
everything allocated per render must be released at Stop/destruction.

  PASS  max growth after round 1 <= 60MB total
  FAIL  otherwise (prints per-round RSS for the slope)

Also repeats Scene.Parse on a fresh Scene each round to catch leaks
in scene parsing itself (imagemap cache, mesh cache).
"""

import resource
import sys

import pysuperluxcore

ROUNDS = 5
RES = (1280, 720)
PLY = "/tmp/memstage_src.ply"   # reuse the heightfield from memory_stages


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def scene_props():
    p = pysuperluxcore.Properties()
    p.SetFromString(f"""
scene.objects.quad.ply = "{PLY}"
scene.objects.quad.material = "mat"
scene.materials.mat.type = "matte"
scene.materials.mat.kd = 0.55 0.6 0.5
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
film.width = {RES[0]}
film.height = {RES[1]}
batch.haltspp = 2
""")
    return c


def main():
    import os
    if not os.path.exists(PLY):
        sys.exit(f"missing {PLY} — run memory_stages_test.py first")

    pysuperluxcore.Init()
    base = rss_mb()
    print(f"[LeakHunt] baseline after Init: {base:.0f} MB")

    # Round A: fresh Scene each iteration (parse-time leaks)
    scene_rss = []
    for i in range(ROUNDS):
        s = pysuperluxcore.Scene()
        s.Parse(scene_props())
        del s
        scene_rss.append(rss_mb())
    print("[LeakHunt] scene-parse rounds: " +
          " ".join(f"{v:.0f}" for v in scene_rss))

    # Round B: shared scene, fresh config+session each iteration
    scene = pysuperluxcore.Scene()
    scene.Parse(scene_props())
    render_rss = []
    for i in range(ROUNDS):
        rc = pysuperluxcore.RenderConfig(cfg_props(), scene)
        session = pysuperluxcore.RenderSession(rc)
        session.Start()
        session.Stop()
        del session, rc
        render_rss.append(rss_mb())
    print("[LeakHunt] render rounds:    " +
          " ".join(f"{v:.0f}" for v in render_rss))

    ok = True
    for name, series, limit in (
        ("scene-parse", scene_rss, 80),
        ("render", render_rss, 60),
    ):
        growth = series[-1] - series[0]
        stat = "PASS" if growth <= limit else "FAIL"
        if growth > limit:
            ok = False
        print(f"[LeakHunt] {name} growth r0->r{ROUNDS-1}: "
              f"{growth:.0f} MB (limit {limit}) {stat}")

    print("[LeakHunt]", "DONE" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
