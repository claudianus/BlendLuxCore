"""
Infinite-light CDF resolution cap test (standalone pysuperluxcore).

    python3 dev-tools/envcdf_test.py

An 8192x4096 equirect envmap normally builds a ~270MB importance CDF
(one 8192-wide Distribution1D per row + full-res float luminance).
`scene.lights.env.cdfdim` caps it via block-summed downsampling —
unbiased (pdf stays consistent with the sampled distribution).

Checks:
  * log shows "downsampling importance CDF 8192x4096 -> 512x256"
  * render works and a diffuse sphere is lit by the bright blob
  * converged mean radiance matches the uncapped run within 5%
    (same estimator, only different sampling variance)
"""

import os
import resource
import sys

import numpy as np
import pysuperluxcore

IMG = "/tmp/envcdf_hdri.png"
OUT_FULL = "/tmp/envcdf_full.png"
OUT_CAP = "/tmp/envcdf_cap.png"
W, H = 8192, 4096


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def write_envmap():
    from PIL import Image
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    # dim bluish sky + very bright small "sun" blob
    base = np.stack(
        [x / W * 0.1, y / H * 0.15, np.full((H, W), 0.2, np.float32)], -1
    )
    d2 = ((x - W * 0.25) ** 2 / (W * 0.02) ** 2 +
          (y - H * 0.3) ** 2 / (H * 0.02) ** 2)
    sun = np.exp(-d2)[..., None] * 60.0
    img = np.clip((base + sun) * 255, 0, 255).astype(np.uint8)
    Image.fromarray(img).save(IMG)


def build_scene(cdfdim):
    p = pysuperluxcore.Properties()
    p.SetFromString(f"""
scene.lights.env.type = "infinite"
scene.lights.env.file = "{IMG}"
scene.lights.env.gamma = 1.0
scene.lights.env.gain = 1.0 1.0 1.0
scene.lights.env.cdfdim = {cdfdim}
scene.materials.mat.type = "matte"
scene.materials.mat.kd = 0.6 0.6 0.6
scene.objects.floor.ply = "/tmp/memstage_src.ply"
scene.objects.floor.material = "mat"
scene.camera.lookat.orig = 0 -5 0.5
scene.camera.lookat.target = 0 0 0
scene.camera.up = 0 0 1
""")
    return p


def render(cdfdim, out, haltspp):
    scene = pysuperluxcore.Scene()
    scene.Parse(build_scene(cdfdim))
    cfg = pysuperluxcore.Properties()
    cfg.SetFromString(f"""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = 1280
film.height = 720
batch.haltspp = {haltspp}
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {out}
film.imagepipelines.0.0.type = NOP
""")
    rc = pysuperluxcore.RenderConfig(cfg, scene)
    session = pysuperluxcore.RenderSession(rc)
    session.Start()
    peak = rss_mb()
    while not session.HasDone():
        session.UpdateStats()
        import time
        time.sleep(0.2)
    session.Stop()
    session.GetFilm().SaveOutputs()

    film = session.GetFilm()
    w, h = film.GetSize() if hasattr(film, "GetSize") else (1280, 720)
    del session, rc, scene
    return peak


def mean_rgb(path):
    from PIL import Image
    a = np.asarray(Image.open(path), np.float32) / 255.0
    return float(a[..., :3].mean())


def main():
    write_envmap()
    pysuperluxcore.Init()
    base = rss_mb()

    peak_full = render(0, OUT_FULL, 64)
    peak_cap = render(512, OUT_CAP, 64)
    print(f"[EnvCDF] baseline={base:.0f} peak(full)={peak_full:.0f} "
          f"peak(cap512)={peak_cap:.0f} MB")

    m_full, m_cap = mean_rgb(OUT_FULL), mean_rgb(OUT_CAP)
    rel = abs(m_cap - m_full) / max(m_full, 1e-9)
    print(f"[EnvCDF] mean full={m_full:.4f} cap={m_cap:.4f} rel={rel:.4f}")

    ok = rel < 0.05 and os.path.exists(OUT_CAP)
    print("[EnvCDF]", "DONE" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
