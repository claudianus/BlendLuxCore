# .lxm v4 verification: per-cluster contiguous vertex ranges
# (self-contained streaming payloads). Parses the file to check that
# every triangle index falls inside its cluster's [firstVert,
# firstVert+vertCount) range, then renders the proxy at 720p.
import os, sys, struct, math, subprocess

import pysuperluxcore as plc

PLY = "/tmp/lxmv4_src.ply"
LXM = "/tmp/lxmv4_test.lxm"
OUT = "/tmp/lxmv4_render.png"
N = 600
STRIDE = 16


def rss_mb():
    return int(subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(os.getpid())]).strip()) / 1024.0


# Terrain mesh -> PLY
verts, tris = [], []
for j in range(N):
    for i in range(N):
        verts.append((i / N * 4 - 2, j / N * 4 - 2,
                      math.sin(i * 0.05) * math.cos(j * 0.05) * 0.4))
for j in range(N - 1):
    for i in range(N - 1):
        a = j * N + i
        tris.append((a, a + 1, a + N + 1))
        tris.append((a, a + N + 1, a + N))
with open(PLY, "w") as f:
    f.write("ply\nformat ascii 1.0\n")
    f.write(f"element vertex {len(verts)}\n")
    f.write("property float x\nproperty float y\nproperty float z\n")
    f.write(f"element face {len(tris)}\n")
    f.write("property list uchar int vertex_indices\nend_header\n")
    for v in verts:
        f.write(f"{v[0]} {v[1]} {v[2]}\n")
    for t in tris:
        f.write(f"3 {t[0]} {t[1]} {t[2]}\n")
print(f"src: {len(verts)} verts {len(tris)} tris")

# Bake .lxm v4
sc = plc.Scene()
p = plc.Properties()
p.SetFromString(f"scene.objects.m1.ply = {PLY}\n"
                "scene.objects.m1.material = mat\n"
                "scene.materials.mat.type = matte\n"
                "scene.materials.mat.kd = 0.7 0.7 0.7\n")
sc.Parse(p)
sc.SaveMeshClusterStride(PLY, LXM, STRIDE)
sz = os.path.getsize(LXM)

# Parse header + cluster table
d = open(LXM, "rb").read(128)
magic, ver, flags = d[0:4], struct.unpack("<I", d[4:8])[0], \
    struct.unpack("<I", d[8:12])[0]
vertCount = struct.unpack("<Q", d[16:24])[0]
triCount = struct.unpack("<Q", d[24:32])[0]
clusterOff = struct.unpack("<Q", d[56:64])[0]
clusterCount = struct.unpack("<I", d[64:68])[0]
clusterStride = struct.unpack("<I", d[68:72])[0]
assert magic == b"LXM1" and ver == 4, f"bad header v{ver}"
assert clusterStride == STRIDE
print(f"v{ver} flags={flags:x} verts={vertCount} tris={triCount} "
      f"clusters={clusterCount}@{clusterOff} stride={clusterStride} "
      f"file={sz/1e6:.1f}MB (src verts {len(verts)})")

# v4: 40B cluster records — {bboxMin[3], bboxMax[3], firstTri,
# triCount, firstVert, vertCount}
with open(LXM, "rb") as f:
    f.seek(clusterOff)
    cdata = f.read(clusterCount * 40)
    # triangles section: after 128B header + padded vert section
    triOff = 128 + ((vertCount * 12 + 63) // 64) * 64
    f.seek(triOff)
    tdata = f.read(triCount * 12)

bad = 0
total_vrange = 0
for c in range(clusterCount):
    rec = cdata[c * 40:(c + 1) * 40]
    firstTri, tc, firstVert, vc = struct.unpack("<24x4I", rec)
    total_vrange += vc
    for t in range(firstTri, firstTri + tc):
        v0, v1, v2 = struct.unpack_from("<3I", tdata, t * 12)
        for v in (v0, v1, v2):
            if not (firstVert <= v < firstVert + vc):
                bad += 1
                break
        if bad:
            break
    if bad:
        print(f"FAIL: cluster {c} tris outside "
              f"[{firstVert},{firstVert + vc})")
        sys.exit(1)

print(f"self-containment OK: all {clusterCount} clusters index only "
      f"their own vert range (file verts {vertCount} vs src "
      f"{len(verts)} — boundary dup +{(vertCount - len(verts)) / len(verts):.1%})")

# Load + residency + render (v4 proxy vs live PLY)
def render(mesh_path, out):
    sc = plc.Scene()
    p = plc.Properties()
    p.SetFromString(f"scene.lights.sky.type = sky2\n"
                    "scene.lights.sky.gain = 0.001 0.001 0.001\n"
                    "scene.camera.lookat.orig = 0 -6 1.5\n"
                    "scene.camera.lookat.target = 0 0 0\n"
                    "scene.camera.fieldofview = 45\n"
                    f"scene.objects.m1.ply = {mesh_path}\n"
                    "scene.objects.m1.material = mat\n"
                    "scene.materials.mat.type = matte\n"
                    "scene.materials.mat.kd = 0.7 0.7 0.7\n")
    r0 = rss_mb()
    sc.Parse(p)
    r1 = rss_mb()
    print(f"[{os.path.basename(mesh_path)}] scene+map: +{r1 - r0:.1f}MB")
    cfg = plc.Properties()
    cfg.SetFromString("renderengine.type = PATHCPU\n"
                      "sampler.type = SOBOL\n"
                      "film.width = 1280\nfilm.height = 720\n"
                      "film.outputs.0.type = RGB_IMAGEPIPELINE\n"
                      f"film.outputs.0.filename = {out}\n"
                      "film.imagepipelines.0.0.type = NOP\n"
                      "film.imagepipelines.0.1.type = TONEMAP_AUTOLINEAR\n"
                      "film.imagepipelines.0.2.type = GAMMA_CORRECTION\n"
                      "film.imagepipelines.0.2.value = 2.2\n"
                      "batch.haltspp = 64\n")
    rc = plc.RenderConfig(cfg, sc)
    sess = plc.RenderSession(rc)
    sess.Start()
    import time
    done = False
    for _ in range(600):
        sess.UpdateStats()
        if sess.HasDone():
            done = True
            break
        time.sleep(0.5)
    sess.GetFilm().SaveOutputs()
    sess.Stop()
    print(f"  -> {out} (haltspp reached: {done})")


render(LXM, OUT)
render(PLY, "/tmp/lxmv4_live.png")

# Pixel compare via OpenImageIO (bundled with Blender's Python)
try:
    import OpenImageIO as oiio
    import numpy as np
    a = oiio.ImageBuf(OUT).get_pixels(oiio.FLOAT)
    b = oiio.ImageBuf("/tmp/lxmv4_live.png").get_pixels(oiio.FLOAT)
    d = np.abs(np.asarray(a) - np.asarray(b))
    print(f"mean abs diff {d.mean() * 255:.2f}/255, "
          f"max {d.max() * 255:.0f}/255, "
          f"p99 {np.percentile(d, 99) * 255:.1f}/255")
except ImportError:
    print("(OpenImageIO unavailable — compare images manually)")
print("PASS")
