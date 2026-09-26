"""
Ray-driven residency proof for .lxm v2 cluster index.

    python3 dev-tools/cluster_residency_test.py   (with pysuperluxcore on
    PYTHONPATH + DYLD_LIBRARY_PATH)

Builds a large mesh, saves .lxm (v2 clustered), loads it and creates a
RenderSession. Resident-set growth across accel construction must be
far below the mesh's data size: the cluster-index build reads only
cluster bounds, never the mapped vertex/triangle pages.
"""

import os, math, struct, time, subprocess

import pysuperluxcore

PLY = "/tmp/cluster_residency_src.ply"
LXM = "/tmp/cluster_residency.lxm"


def rss_mb():
    # Live resident set (not the ru_maxrss watermark) — only faulted-in
    # pages count, which is exactly what residency must track.
    return int(subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(os.getpid())]).strip()) / 1024.0


def make_ply(n=1200):
    verts = []
    faces = []
    for iy in range(n + 1):
        y = iy / n * 10.0 - 5.0
        for ix in range(n + 1):
            x = ix / n * 10.0 - 5.0
            z = 0.5 * math.sin(x * 1.4) * math.cos(y * 1.1)
            verts.append((x, y, z))
    for iy in range(n):
        for ix in range(n):
            a = iy * (n + 1) + ix
            faces.append((a, a + 1, a + n + 2))
            faces.append((a, a + n + 2, a + n + 1))
    with open(PLY, "w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(verts)}\n"
                "property float x\nproperty float y\nproperty float z\n")
        f.write(f"element face {len(faces)}\n"
                "property list uchar int vertex_indices\nend_header\n")
        for v in verts:
            f.write(f"{v[0]} {v[1]} {v[2]}\n")
        for fa in faces:
            f.write(f"3 {fa[0]} {fa[1]} {fa[2]}\n")


def scene_props(mesh_path):
    p = pysuperluxcore.Properties()
    p.SetFromString(f"""
scene.camera.lookat.orig = 0 -14 6
scene.camera.lookat.target = 0 0 0
scene.camera.up = 0 0 1
scene.lights.sky.type = "sky2"
scene.lights.sky.gain = 0.001 0.001 0.001
scene.materials.mat.type = "matte"
scene.materials.mat.kd = 0.5 0.4 0.3
scene.objects.m1.ply = "{mesh_path}"
scene.objects.m1.material = "mat"
""")
    return p


def main():
    if not os.path.exists(PLY):
        make_ply()

    # --- bake .lxm v2 ---
    sc = pysuperluxcore.Scene()
    sc.Parse(scene_props(PLY))
    # The mesh name for a file-backed object is the file path itself
    sc.SaveMesh(PLY, LXM)
    sz = os.path.getsize(LXM) / 1e6
    with open(LXM, "rb") as f:
        head = f.read(128)
    ver, flags = struct.unpack_from("<II", head, 4)
    nc, = struct.unpack_from("<I", head, 64)
    print(f"[residency] .lxm {sz:.1f}MB v{ver} flags={flags:x} clusters={nc}")
    del sc

    # --- measure residency across load + accel build ---
    rss0 = rss_mb()
    sc2 = pysuperluxcore.Scene()
    sc2.Parse(scene_props(LXM))
    rss_load = rss_mb()
    print(f"[residency] scene+map: +{rss_load - rss0:.1f}MB "
          f"(file {sz:.1f}MB)")

    cfg = pysuperluxcore.Properties()
    cfg.SetFromString("""
renderengine.type = "PATHCPU"
sampler.type = "SOBOL"
film.width = 1280
film.height = 720
batch.haltspp = 1000000
batch.halttime = 15
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = /tmp/cluster_residency.png
film.imagepipelines.0.0.type = TONEMAP_AUTOLINEAR
""")
    rc = pysuperluxcore.RenderConfig(cfg, sc2)
    sess = pysuperluxcore.RenderSession(rc)
    sess.Start()
    rss_build = rss_mb()
    print(f"[residency] after accel+start: +{rss_build - rss_load:.1f}MB "
          f"(mesh ~{sz:.1f}MB)")

    time.sleep(8)
    sess.UpdateStats()
    rss_render = rss_mb()
    print(f"[residency] after 8s render: +{rss_render - rss_build:.1f}MB")

    sess.Stop()
    print("[residency] DONE")


if __name__ == "__main__":
    main()
