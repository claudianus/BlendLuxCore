# Benchmark: .lxm cluster stride vs render time — measures how
# triangles-per-cluster affects Embree cluster-leaf intersection cost.
import os, sys, math, time
sys.path.insert(0, os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/.local/lib/python3.13/site-packages"))
import pysuperluxcore as plc

N = 600

def terrain_mesh():
    verts, tris = [], []
    for j in range(N):
        for i in range(N):
            verts.append((i / N * 4 - 2,
                          j / N * 4 - 2,
                          math.sin(i * 0.05) * math.cos(j * 0.05) * 0.4))
    for j in range(N - 1):
        for i in range(N - 1):
            a = j * N + i
            tris.append((a, a + 1, a + N + 1))
            tris.append((a, a + N + 1, a + N))
    return verts, tris

verts, tris = terrain_mesh()
print(f"mesh: {len(tris)} tris")

PLY = "/tmp/stride_bench_src.ply"
with open(PLY, "w") as f:
    f.write("ply\nformat ascii 1.0\n")
    f.write(f"element vertex {len(verts)}\n")
    f.write("property float x\nproperty float y\nproperty float z\n")
    f.write(f"element face {len(tris)}\n")
    f.write("property list uchar int vertex_indices\n")
    f.write("end_header\n")
    for v in verts:
        f.write(f"{v[0]} {v[1]} {v[2]}\n")
    for t in tris:
        f.write(f"3 {t[0]} {t[1]} {t[2]}\n")

def bake(stride):
    sc = plc.Scene()
    p = plc.Properties()
    p.SetFromString(f"scene.objects.m1.ply = {PLY}\n"
                    "scene.objects.m1.material = mat\n"
                    "scene.materials.mat.type = matte\n"
                    "scene.materials.mat.kd = 0.7 0.7 0.7\n")
    sc.Parse(p)
    path = f"/tmp/stride_bench_{stride}.lxm"
    sc.SaveMeshClusterStride(PLY, path, stride)
    return path

def render(path, secs=8):
    sc = plc.Scene()
    p = plc.Properties()
    p.SetFromString(f"scene.lights.sky.type = sky2\n"
                    f"scene.lights.sky.gain = 0.001 0.001 0.001\n"
                    f"scene.camera.lookat.orig = 0 -6 1.5\n"
                    f"scene.camera.lookat.target = 0 0 0\n"
                    f"scene.camera.fieldofview = 45\n"
                    f"scene.objects.m1.ply = {path}\n"
                    f"scene.objects.m1.material = mat\n"
                    f"scene.materials.mat.type = matte\n"
                    f"scene.materials.mat.kd = 0.7 0.7 0.7\n")
    sc.Parse(p)
    c = plc.Properties()
    c.SetFromString("renderengine.type = PATHCPU\n"
                    "sampler.type = METROPOLIS\n"
                    "film.outputs.1.type = RGB_IMAGEPIPELINE\n"
                    "film.outputs.1.filename = /tmp/stride_bench.png\n"
                    "film.width = 1280\nfilm.height = 720\n"
                    "film.imagepipelines.0.0.type = NOP\n"
                    "film.imagepipelines.0.1.type = TONEMAP_LINEAR\n"
                    "film.imagepipelines.0.1.scale = 0.5\n"
                    "film.imagepipelines.0.2.type = GAMMA_CORRECTION\n"
                    "film.imagepipelines.0.2.value = 2.2\n")
    rc = plc.RenderConfig(c, sc)
    sess = plc.RenderSession(rc)
    sess.Start()
    t0 = time.time()
    while time.time() - t0 < secs:
        sess.UpdateStats()
        time.sleep(0.5)
    sess.UpdateStats()
    stats = sess.GetStats()
    # samples/sec: stats.renderengine.total.samplesec exists on some
    # builds; fall back to samples count if the key is absent.
    try:
        ss = float(stats.Get("stats.renderengine.total.samplesec")
                .GetString())
    except RuntimeError:
        ss = float(stats.Get("stats.renderengine.total.samplecount")
                .GetString())
    sess.Stop()
    return ss

for stride in (4, 8, 16):
    path = bake(stride)
    sz = os.path.getsize(path) / 1e6
    t0 = time.time()
    ss = render(path)
    print(f"stride {stride}: {sz:.1f}MB | {ss/1e6:.2f} Msamples/s "
          f"({time.time()-t0:.1f}s)")
print("DONE")
