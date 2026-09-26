#!/usr/bin/env python3.13
"""
Standalone: streamed image-map decode+downscale (width/height hint on a
non-mipped file) vs full-decode baseline.

- 8192x8192 RGB PNG (no mip chain) textured quad.
- Run A: scene.images.resizepolicy NONE  -> full decode (peak RSS baseline)
- Run B: MINMEM minsize=256            -> ctor hint path streams tiles via
        ImageCache, never materializing the 8192x8192 pixels.
- Reports peak RSS delta (mach resource_usage) and renders both at 720p
  for visual identity check.
"""
import os, resource, subprocess, sys, tempfile, time

pylux_dir = None
for c in [os.path.expanduser("~/Library/Application Support/Blender/5.2/extensions/.local/lib/python3.13/site-packages")]:
    if os.path.isdir(c):
        pylux_dir = c
if pylux_dir:
    sys.path.insert(0, pylux_dir)
import pyluxcore
pyluxcore.Init()

TD = tempfile.mkdtemp(prefix="imgstream")
IMG = os.path.join(TD, "big.png")

def make_image():
    # 8192x8192 RGB PNG, quadrant gradient (deterministic content)
    if os.path.exists(IMG):
        return
    import zlib, struct
    import numpy as np
    W = H = 8192
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    xg, yg = np.meshgrid(np.arange(W, dtype=np.uint32), np.arange(H, dtype=np.uint32))
    r = np.where(yg < H // 2, xg * 255 // W, 255 - xg * 255 // W)
    g = np.where(xg < W // 2, yg * 255 // H, 255 - yg * 255 // H)
    b = (xg + yg) * 255 // (W + H)
    img = np.stack([r, g, b], -1).astype(np.uint8)
    raw = zlib.compress(b"".join(b"\x00" + img[y].tobytes() for y in range(H)), 6)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    with open(IMG, "wb") as f:
        f.write(png)
    print(f"image: {IMG} ({len(png)/1e6:.1f} MB png, {W}x{H}x3 = {W*H*3/1e6:.0f} MB raw)")

def build_scene(policy, minsize=0):
    props = pyluxcore.Properties()
    props.Set(pyluxcore.Property("scene.camera.lookat.orig", [0, 0.001, 2.5]))
    props.Set(pyluxcore.Property("scene.camera.lookat.target", [0, 0, 0]))
    props.Set(pyluxcore.Property("scene.camera.fieldofview", [45]))
    props.Set(pyluxcore.Property("scene.camera.screenwindow", [0.0, 1.0, 0.0, 1.0]))
    props.Set(pyluxcore.Property("scene.lights.sky2.gain", [0.5, 0.5, 0.5]))
    props.Set(pyluxcore.Property("scene.lights.sky2.turbidity", [2.2]))
    props.Set(pyluxcore.Property("scene.textures.tex.type", "imagemap"))
    props.Set(pyluxcore.Property("scene.textures.tex.file", [IMG]))
    props.Set(pyluxcore.Property("scene.textures.tex.gamma", [2.2]))
    props.Set(pyluxcore.Property("scene.materials.mat.type", "matte"))
    props.Set(pyluxcore.Property("scene.materials.mat.kd", "tex"))
    props.Set(pyluxcore.Property("scene.objects.quad.material", "mat"))
    quad = os.path.join(TD, "quad.ply")
    with open(quad, "w") as f:
        f.write("""ply
format ascii 1.0
element vertex 4
property float x
property float y
property float z
property float s
property float t
element face 2
property list uchar int vertex_indices
end_header
-1.0 -1.0 0.0 0.0 0.0
1.0 -1.0 0.0 1.0 0.0
1.0 1.0 0.0 1.0 1.0
-1.0 1.0 0.0 0.0 1.0
3 0 1 2
3 0 2 3
""")
    props.Set(pyluxcore.Property("scene.objects.quad.ply", [quad]))
    props.Set(pyluxcore.Property("scene.objects.quad.transformation",
        [0.5,0,0,0, 0,0.5,0,0, 0,0,0.5,0, 0,0,0,1]))
    props.Set(pyluxcore.Property("film.width", [1280]))
    props.Set(pyluxcore.Property("film.height", [720]))
    props.Set(pyluxcore.Property("film.outputs.0.type", "RGB_IMAGEPIPELINE"))
    props.Set(pyluxcore.Property("film.outputs.0.filename", [os.path.join(TD, f"out_{policy}_{minsize}.png")]))
    props.Set(pyluxcore.Property("batch.haltspp", [8]))
    props.Set(pyluxcore.Property("path.pathdepth.total", [4]))
    rp = pyluxcore.Properties()
    rp.Set(pyluxcore.Property("scene.images.resizepolicy.type", [policy]))
    if minsize:
        rp.Set(pyluxcore.Property("scene.images.resizepolicy.minsize", [minsize]))
        rp.Set(pyluxcore.Property("scene.images.resizepolicy.scale", [minsize / 8192.0]))
    scene = pyluxcore.Scene(rp)
    scene.Parse(props)
    return pyluxcore.RenderConfig(props, scene)

def run(label, policy, minsize=0):
    rc = build_scene(policy, minsize)
    rs = pyluxcore.RenderSession(rc)
    rs.Start()
    t0 = time.time()
    while not rs.HasDone() and time.time() - t0 < 90:
        rs.UpdateStats()
        time.sleep(0.3)
    rs.Stop()
    rs.GetFilm().SaveOutputs()
    peak_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # RUSAGE_SELF for in-process python
    self_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"[{label}] done in {time.time()-t0:.1f}s  peakRSS self={self_kb/1024:.0f} MB")
    return self_kb

def main():
    make_image()
    # subprocess isolation: fork so each run gets its own RSS accounting
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "none":
            run("NONE", "NONE")
        elif mode == "minmem":
            run("MINMEM-256", "MINMEM", 256)
        elif mode == "fixed":
            run("FIXED-256", "FIXED", 256)
        elif mode.startswith("parse_"):
            # parse-only: isolate imagemap decode RSS (no engine/kernel JIT)
            pol = mode.split("_")[1]
            props = pyluxcore.Properties()
            props.Set(pyluxcore.Property("scene.textures.tex.type", "imagemap"))
            props.Set(pyluxcore.Property("scene.textures.tex.file", [IMG]))
            props.Set(pyluxcore.Property("scene.textures.tex.gamma", [2.2]))
            props.Set(pyluxcore.Property("scene.materials.mat.type", "matte"))
            props.Set(pyluxcore.Property("scene.materials.mat.kd", "tex"))
            quad = os.path.join(TD, "quad.ply")
            if not os.path.exists(quad):
                with open(quad, "w") as f:
                    f.write("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nproperty float s\nproperty float t\nelement face 2\nproperty list uchar int vertex_indices\nend_header\n-1.0 -1.0 0.0 0.0 0.0\n1.0 -1.0 0.0 1.0 0.0\n1.0 1.0 0.0 1.0 1.0\n-1.0 1.0 0.0 0.0 1.0\n3 0 1 2\n3 0 2 3\n")
            props.Set(pyluxcore.Property("scene.objects.quad.material", "mat"))
            props.Set(pyluxcore.Property("scene.objects.quad.ply", [quad]))
            props.Set(pyluxcore.Property("scene.camera.lookat.orig", [0, 0.001, 2.5]))
            props.Set(pyluxcore.Property("scene.camera.lookat.target", [0, 0, 0]))
            rp = pyluxcore.Properties()
            rp.Set(pyluxcore.Property("scene.images.resizepolicy.type", [pol.upper()]))
            if pol == "fixed":
                rp.Set(pyluxcore.Property("scene.images.resizepolicy.minsize", [256]))
                rp.Set(pyluxcore.Property("scene.images.resizepolicy.scale", [256 / 8192.0]))
            import threading, subprocess as sp
            peak = [0]; stop = threading.Event()
            pid = os.getpid()
            def sampler():
                while not stop.is_set():
                    try:
                        out = sp.check_output(["ps", "-o", "rss=", "-p", str(pid)])
                        peak[0] = max(peak[0], int(out.strip()))
                    except Exception:
                        pass
                    stop.wait(0.02)
            t = threading.Thread(target=sampler); t.start()
            rss0 = int(sp.check_output(["ps", "-o", "rss=", "-p", str(pid)]).strip())
            scene = pyluxcore.Scene(rp)
            scene.Parse(props)
            import time
            time.sleep(1.5)
            rss1 = int(sp.check_output(["ps", "-o", "rss=", "-p", str(pid)]).strip())
            fp = sp.check_output(["/usr/bin/vmmap", "-summary", str(pid)], text=True).splitlines()
            fp = [l for l in fp if "TOTAL" in l or "MALLOC" in l or "mapped file" in l][:6]
            stop.set(); t.join()
            print(f"[parse:{pol}] rss before={rss0/1024:.0f}MB after={rss1/1024:.0f}MB peak={peak[0]/1024:.0f}MB  fp={fp}")
        return
    py = "/opt/homebrew/bin/python3.13" if os.path.exists("/opt/homebrew/bin/python3.13") else sys.executable
    for mode, tag in [("none", "NONE"), ("minmem", "MINMEM-256"), ("fixed", "FIXED-256")]:
        print(f"--- run {tag} ---")
        r = subprocess.run([py, __file__, mode], capture_output=True, text=True)
        print(r.stdout[-3000:])
        if r.returncode != 0:
            print("STDERR:", r.stderr[-2000:])
    print("PASS done")

if __name__ == "__main__":
    main()
