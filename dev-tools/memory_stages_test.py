"""
Stage-wise memory regression test (standalone pysuperluxcore).

    python3 dev-tools/memory_stages_test.py

Builds a scene with a .lxm proxy mesh (mapped, not copied) plus an
8192^2 texture under a FIXED resize policy (streamed decode), then
samples ru_maxrss after each stage:

  S0  pysuperluxcore.Init()
  S1  scene asset creation (.ply -> .lxm bake, texture write)
  S2  Scene.Parse of the proxy mesh + texture object
  S3  RenderConfig + session Start (BVH build, imagemap preprocess)
  S4  a few seconds of PATHCPU 1280x720 rendering

Assertions (macOS ru_maxrss is in bytes):
  * S2 - S1 < 120MB   — proxy mapping must not page the whole mesh
  * S3 - S2 < 400MB   — BVH + 256^2 streamed texture, not 8192^2
  * S4 - S3 < 900MB   — film + task buffers only
Fails loudly on regression; prints the stage table either way.
"""

import os
import resource
import sys

import numpy as np
import pysuperluxcore

RES = (1280, 720)
PLY = "/tmp/memstage_src.ply"
LXM = "/tmp/memstage_mesh.lxm"
IMG = "/tmp/memstage_tex.png"

NV = 200 * 200 + 1          # 200x200 subdiv grid
NT = 200 * 200 * 2


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


def write_ply():
    # grid with UVs; deterministic heights
    xs = np.linspace(-4, 4, 201, dtype=np.float32)
    ys = np.linspace(-4, 4, 201, dtype=np.float32)
    gx, gy = np.meshgrid(xs, ys)
    verts = np.stack(
        [gx.ravel(), gy.ravel(),
         (0.3 * np.sin(gx * 2) * np.cos(gy * 2)).ravel().astype(np.float32)],
        axis=1,
    )
    uvs = np.stack(
        [(gx.ravel() + 4) / 8, (gy.ravel() + 4) / 8], axis=1
    ).astype(np.float32)
    ii, jj = np.meshgrid(np.arange(200), np.arange(200))
    i0 = (jj * 201 + ii).ravel()
    tris = np.stack(
        [
            np.stack([i0, i0 + 201, i0 + 1], axis=1),
            np.stack([i0 + 1, i0 + 201, i0 + 202], axis=1),
        ]
    ).reshape(-1, 3).astype(np.int32)

    with open(PLY, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(f"element vertex {len(verts)}\n".encode())
        f.write(b"property float x\nproperty float y\nproperty float z\n")
        f.write(b"property float u\nproperty float v\n")
        f.write(f"element face {len(tris)}\n".encode())
        f.write(b"property list uchar int vertex_indices\nend_header\n")
        f.write(np.concatenate([verts, uvs], axis=1).astype("<f4").tobytes())
        face = np.empty(len(tris), dtype=[("n", "u1"), ("i", "<i4", 3)])
        face["n"] = 3
        face["i"] = tris
        f.write(face.tobytes())


def write_texture():
    from PIL import Image

    x = np.linspace(0, 1, 8192, dtype=np.float32)
    y = np.linspace(0, 1, 8192, dtype=np.float32)[:, None]
    img = np.stack(
        [
            np.broadcast_to(x[None, :], (8192, 8192)),
            np.broadcast_to(y, (8192, 8192)),
            np.broadcast_to(x[None, :] * y, (8192, 8192)),
        ],
        axis=-1,
    )
    Image.fromarray((img * 255).astype(np.uint8)).save(IMG)


def bake_lxm():
    s = pysuperluxcore.Scene()
    s.Parse(build_scene_props(PLY))
    s.SaveMesh(PLY, LXM)


def build_scene_props(mesh_path):
    props = pysuperluxcore.Properties()
    props.SetFromString(f"""
scene.objects.quad.ply = "{mesh_path}"
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
    return props


def main():
    pysuperluxcore.Init()
    s0 = rss_mb()

    write_ply()
    write_texture()
    bake_lxm()
    s1 = rss_mb()

    scene = pysuperluxcore.Scene()
    scene.Parse(build_scene_props(LXM))
    s2 = rss_mb()

    cfg = pysuperluxcore.Properties()
    cfg.SetFromString(f"""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = {RES[0]}
film.height = {RES[1]}
batch.haltspp = 4
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = /tmp/memstage_render.png
scene.images.resizepolicy.type = "FIXED"
scene.images.resizepolicy.scale = 64
""")
    rc = pysuperluxcore.RenderConfig(cfg, scene)
    session = pysuperluxcore.RenderSession(rc)
    session.Start()
    s3 = rss_mb()

    import time
    time.sleep(6)
    session.Pause()
    s4 = rss_mb()
    session.GetFilm().SaveOutputs()

    lxm_mb = os.path.getsize(LXM) / 1e6
    print(f"[MemStage] lxm file: {lxm_mb:.1f} MB, verts={NV}, tris={NT}")
    print(f"[MemStage] S0 init={s0:.0f} S1 assets={s1:.0f} "
          f"S2 parse={s2:.0f} S3 start={s3:.0f} S4 render={s4:.0f} MB")

    ok = True
    for name, delta, limit in (
        ("S2-S1 parse proxy+tex", s2 - s1, 120),
        ("S3-S2 bvh+imagemap", s3 - s2, 400),
        ("S4-S3 render", s4 - s3, 900),
    ):
        stat = "PASS" if delta < limit else "FAIL"
        if delta >= limit:
            ok = False
        print(f"[MemStage] {name}: {delta:.0f} MB (limit {limit}) {stat}")

    session.Stop()
    print("[MemStage]", "DONE" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
