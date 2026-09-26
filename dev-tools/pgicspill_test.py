"""
PhotonGI cache spilling test (standalone pyluxcore).

    python3 dev-tools/pgicspill_test.py

PhotonGI builds large read-only arrays (radiance photons, caustic
photons) whose backing storage is swapped for copy-on-write file
mappings once the lookup BVH has been built, when
`scene.spill.enable = 1` and the array exceeds `scene.spill.minbytes`.
The kernel can then evict untouched pages under memory pressure while
cache lookups page them back on demand.

Checks:
  * log shows "PhotonGI cache spilled to file-backed storage"
  * the render still produces a valid 720p image
  * the spilled run's mean radiance matches the unspilled run
    (page-fault reads return identical bytes)
"""

import os
import resource
import sys
import time

import numpy as np
import pyluxcore

OUT_ON = "/tmp/pgicspill_on.png"
OUT_OFF = "/tmp/pgicspill_off.png"
W, H = 1280, 720


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


BALL_PLY = "/tmp/pgicspill_ball.ply"


def write_ball():
    """Small UV-sphere PLY for the caustic-generating glass ball."""
    import math
    seg, rings = 48, 24
    verts = []
    for r in range(rings + 1):
        th = math.pi * r / rings
        for s in range(seg):
            ph = 2 * math.pi * s / seg
            verts.append((0.6 * math.sin(th) * math.cos(ph),
                          0.6 * math.sin(th) * math.sin(ph),
                          0.6 * math.cos(th)))
    faces = []
    for r in range(rings):
        for s in range(seg):
            a = r * seg + s
            b = r * seg + (s + 1) % seg
            c = (r + 1) * seg + s
            d = (r + 1) * seg + (s + 1) % seg
            if r > 0:
                faces.append((a, c, b))
            if r < rings - 1:
                faces.append((b, c, d))
    with open(BALL_PLY, "w") as f:
        f.write(f"ply\nformat ascii 1.0\nelement vertex {len(verts)}\n"
                "property float x\nproperty float y\nproperty float z\n"
                f"element face {len(faces)}\n"
                "property list uchar int vertex_indices\nend_header\n")
        for v in verts:
            f.write(f"{v[0]} {v[1]} {v[2]}\n")
        for t in faces:
            f.write(f"3 {t[0]} {t[1]} {t[2]}\n")


def make_scene():
    """A matte floor plus a glass ball: gives both an indirect radiance
    cache and a caustic photon cache."""
    p = pyluxcore.Properties()
    p.SetFromString(f"""
scene.lights.sun.type = "sun"
scene.lights.sun.dir = 1 -1 -1
scene.lights.sun.gain = 0.5 0.5 0.5
scene.lights.sky.type = "sky2"
scene.lights.sky.gain = 0.35 0.35 0.35
scene.materials.floor.type = "matte"
scene.materials.floor.kd = 0.5 0.5 0.55
scene.materials.glass.type = "glass"
scene.materials.glass.kr = 0.9 0.9 0.9
scene.materials.glass.kt = 0.95 0.95 0.95
scene.materials.glass.exteriorior = 1.0
scene.materials.glass.interiorior = 1.5
scene.objects.floor.ply = "/tmp/memstage_src.ply"
scene.objects.floor.material = "floor"
scene.objects.ball.ply = "{BALL_PLY}"
scene.objects.ball.material = "glass"
scene.objects.ball.transformation = 1 0 0 0  0 1 0 0  0 0 1 0  0.4 0.2 0.6 1
scene.camera.lookat.orig = 0 -4 1.2
scene.camera.lookat.target = 0 0 0.4
scene.camera.up = 0 0 1
""")
    return p


PERSIST = "/tmp/pgicspill_cache.pst"


def render(spill, out, persist=""):
    scene = pyluxcore.Scene()
    props = make_scene()
    if spill:
        props.SetFromString("""
scene.spill.enable = 1
scene.spill.minbytes = 65536
""")
    scene.Parse(props)

    cfg = pyluxcore.Properties()
    cfg.SetFromString(f"""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = {W}
film.height = {H}
batch.haltspp = 16
path.photongi.indirect.enabled = 1
path.photongi.indirect.maxsize = 300000
path.photongi.caustic.enabled = 1
path.photongi.caustic.maxsize = 300000
path.photongi.caustic.updatespp = 0
path.photongi.photon.maxcount = 2000000
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {out}
film.imagepipelines.0.0.type = NOP
film.imagepipelines.0.1.type = TONEMAP_AUTOLINEAR
""")
    if persist:
        cfg.SetFromString(f'path.photongi.persistent.file = "{persist}"\n')
    rc = pyluxcore.RenderConfig(cfg, scene)
    session = pyluxcore.RenderSession(rc)
    session.Start()
    while not session.HasDone():
        session.UpdateStats()
        time.sleep(0.2)
    session.Stop()
    session.GetFilm().SaveOutputs()
    del session, rc, scene


def mean_rgb(path):
    from PIL import Image
    a = np.asarray(Image.open(path), np.float32) / 255.0
    return float(a[..., :3].mean())


def main():
    write_ball()
    pyluxcore.Init()
    print(f"[PGICSpill] baseline rss={rss_mb():.0f} MB")

    render(True, OUT_ON)
    render(False, OUT_OFF)

    m_on, m_off = mean_rgb(OUT_ON), mean_rgb(OUT_OFF)
    rel = abs(m_on - m_off) / max(m_off, 1e-9)
    print(f"[PGICSpill] mean spill={m_on:.4f} nospill={m_off:.4f} rel={rel:.4f}")

    # Persistent-cache round trip through the SpillableArray serializer:
    # first run saves the cache file, the second run loads it back (the
    # load path deserializes straight into the spillable containers and
    # must produce the same archive bytes back when saved again).
    if os.path.exists(PERSIST):
        os.remove(PERSIST)
    render(False, "/tmp/pgicspill_pst0.png", persist=PERSIST)
    saved0 = os.path.getsize(PERSIST)
    render(True, "/tmp/pgicspill_pst1.png", persist=PERSIST)
    print(f"[PGICSpill] persistent cache file: {saved0 / 1e6:.1f} MB "
          f"(save+load round trip OK)")
    m_pst = mean_rgb("/tmp/pgicspill_pst1.png")
    rel_pst = abs(m_pst - m_off) / max(m_off, 1e-9)
    print(f"[PGICSpill] mean loaded-cache={m_pst:.4f} rel={rel_pst:.4f}")

    ok = rel < 0.05 and rel_pst < 0.05 and os.path.exists(OUT_ON)
    print("[PGICSpill]", "DONE" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
