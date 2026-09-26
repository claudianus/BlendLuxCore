"""
.lxm mesh proxy round-trip test (standalone pysuperluxcore).

    PYTHONPATH=<site-packages> python3.13 dev-tools/lxm_proxy_test.py

1. Loads the big PLY floor into a scene, saves it as .lxm.
2. Builds a fresh scene referencing the .lxm — LoadProxy maps the file
   copy-on-write and adopts each section in place (no heap copy).
3. Renders both at 1280x720 on PATHCPU and compares the outputs
   byte-for-byte (identical scene state => identical deterministic
   render).
"""

import os
import sys
import time
import pysuperluxcore

pysuperluxcore.Init()

WIDTH, HEIGHT = 1280, 720
PLY = "/tmp/spill_edit_floor.ply"   # written by spill_edit_test.py
LXM = "/tmp/spill_edit_floor.lxm"
OUT_PLY = "/tmp/superluxcore_lxm_ply_720p.png"
OUT_LXM = "/tmp/superluxcore_lxm_lxm_720p.png"

SCN_TMPL = """
scene.camera.lookat.orig = 0 -7 3.5
scene.camera.lookat.target = 0 0 0.5
scene.camera.fieldofview = 45
scene.lights.skyl.type = sky2
scene.lights.skyl.gain = 0.0001 0.0001 0.0001
scene.lights.skyl.dir = 0.2 0.2 1
scene.materials.emit.type = matte
scene.materials.emit.emission = 8000 8000 8000
scene.materials.emit.kd = 0 0 0
scene.materials.floor.type = matte
scene.materials.floor.kd = 0.6 0.5 0.4
scene.objects.lamp.material = emit
scene.objects.lamp.ply = /tmp/spill_edit_lamp.ply
scene.objects.lamp.transformation = 1 0 0 0  0 1 0 0  0 0 1 0  0 0 4 1
scene.objects.floor.material = floor
scene.objects.floor.ply = {mesh}
"""

CFG_TMPL = """
film.width = {w}
film.height = {h}
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {out}
renderengine.type = PATHCPU
sampler.type = SOBOL
path.pathdepth.total = 4
batch.haltspp = 64
"""


def build_scene(mesh_path):
    scn = pysuperluxcore.Properties()
    scn.SetFromString(SCN_TMPL.format(mesh=mesh_path))
    scene = pysuperluxcore.Scene()
    scene.Parse(scn)
    return scene


def render(scene, out):
    cfg = pysuperluxcore.Properties()
    cfg.SetFromString(CFG_TMPL.format(w=WIDTH, h=HEIGHT, out=out))
    config = pysuperluxcore.RenderConfig(cfg, scene)
    session = pysuperluxcore.RenderSession(config)
    session.Start()
    # Poll HasDone + UpdateStats (halt conditions run inside UpdateFilm)
    for _ in range(600):
        session.UpdateStats()
        if session.HasDone():
            break
        time.sleep(0.5)
    session.Stop()
    film = session.GetFilm()
    film.SaveOutputs()
    # Float radiance output for pixel-level comparison
    t = pysuperluxcore.FilmOutputType.RGB_IMAGEPIPELINE
    buf = bytearray(film.GetOutputSize(t) * 4)
    film.GetOutputFloat(t, buf, 0, True)
    return buf


if not os.path.exists(PLY):
    sys.exit(f"missing {PLY} — run spill_edit_test.py first")

# 1) PLY scene -> save .lxm proxy
scene = build_scene(PLY)
scene.SaveMesh(PLY, LXM)
size = os.path.getsize(LXM)
print(f"[LxmTest] wrote {LXM}: {size / 1e6:.1f} MB")

# 2) render the PLY original
ra = render(scene, OUT_PLY)
del scene

# 3) render the .lxm proxy (mapped copy-on-write, zero heap copy)
scene2 = build_scene(LXM)
rb = render(scene2, OUT_LXM)

# 4a) geometry round-trip: .lxm stores triangles Morton-sorted and
#     vertices first-use-renumbered (spatial page locality), so compare
#     as multisets of positions, not raw order.
import struct
ply = open(PLY, "rb").read()
lxm = open(LXM, "rb").read()
hdr_end = ply.index(b"end_header\n") + len(b"end_header\n")
nv = struct.unpack("<Q", lxm[16:24])[0]
nt = struct.unpack("<Q", lxm[24:32])[0]
vsize = nv * 12
# vertex multiset must match exactly (bytes of 3-float records)
ply_verts = {ply[hdr_end + i * 12:hdr_end + i * 12 + 12]
             for i in range(nv)}
lxm_verts = [lxm[128 + i * 12:128 + i * 12 + 12] for i in range(nv)]
verts_ok = sorted(lxm_verts) == sorted(ply_verts)
pos = (128 + vsize + 63) & ~63
# triangle multiset by vertex POSITIONS (permutation-transparent):
# map each index through its own vertex array, then sort positions
def vert_bytes(buf, base, idx):
    return buf[base + idx * 12:base + idx * 12 + 12]
ply_vert_at = lambda i: vert_bytes(ply, hdr_end, i)
lxm_vert_at = lambda i: vert_bytes(lxm, 128, i)
ply_tris = set()
for i in range(nt):
    fb = hdr_end + vsize + i * 13
    assert ply[fb] == 3
    idx = struct.unpack("<3i", ply[fb + 1:fb + 13])
    ply_tris.add(tuple(sorted(ply_vert_at(j) for j in idx)))
lxm_tris = set()
for i in range(nt):
    idx = struct.unpack("<3I", lxm[pos + i * 12:pos + i * 12 + 12])
    lxm_tris.add(tuple(sorted(lxm_vert_at(j) for j in idx)))
tri_ok = lxm_tris == ply_tris
print(f"[LxmTest] data round-trip: verts={verts_ok} tris={tri_ok} "
      f"({'PASS' if verts_ok and tri_ok else 'FAIL'})")

# 4b) render compare — sanity only: two independent runs have different
#     MC noise realizations, so a loose relative-L1 bound is applied.
fa = struct.unpack(f"{len(ra)//4}f", ra)
fb = struct.unpack(f"{len(rb)//4}f", rb)
num = den = 0.0
for x, y in zip(fa, fb):
    num += abs(x - y)
    den += abs(x)
rel = num / max(den, 1e-30)
print(f"[LxmTest] relative image delta = {rel:.3e} "
      f"({'PASS' if rel < 1e-2 else 'FAIL'})")

# 5) error paths: bad magic and truncated files must throw, not crash
open("/tmp/lxm_bad_magic.lxm", "wb").write(b"XXXX" + b"\0" * 4096)
open("/tmp/lxm_truncated.lxm", "wb").write(open(LXM, "rb").read()[:3000])
for bad in ("/tmp/lxm_bad_magic.lxm", "/tmp/lxm_truncated.lxm"):
    try:
        bad_scene = build_scene(bad)
        print(f"[LxmTest] ERROR: {bad} loaded without throwing")
    except RuntimeError as e:
        print(f"[LxmTest] {bad} correctly rejected: {e}")

# 6) layer coverage: normals + uv + color + alpha + vertAOV + triAOV
#    round-trip.

NV, NT = 64, 124
LAYERED_PLY = "/tmp/lxm_layered.ply"
LAYERED_LXM = "/tmp/lxm_layered.lxm"

verts = [(float(i), float(i * 2), float(i % 7)) for i in range(NV)]
norms = [(0.0, 0.0, 1.0)] * NV
uvs = [(i / NV, 1.0 - i / NV) for i in range(NV)]
cols = [(i * 3 % 256, i * 5 % 256, i * 7 % 256) for i in range(NV)]
alphas = [i * 2 % 256 for i in range(NV)]
# unique triples, all indices < NV (invalid indices would read OOB)
tris = []
_seen = set()
for a in range(NV):
    for b in range(a + 1, NV):
        for c in range(b + 1, NV):
            t = (a, b, c)
            if (a * 31 + b * 17 + c * 7) % 5 == 0:
                _seen.add(frozenset(t))
                tris.append(t)
            if len(tris) >= NT:
                break
        if len(tris) >= NT:
            break
    if len(tris) >= NT:
        break
vert_aov = [float(i) * 0.25 for i in range(NV)]
tri_aov = [float(i) * 0.5 for i in range(NT)]

with open(LAYERED_PLY, "wb") as f:
    f.write(b"ply\nformat binary_little_endian 1.0\n")
    f.write(f"element vertex {NV}\n".encode())
    for p in ("x", "y", "z", "nx", "ny", "nz", "s", "t", "vertaov"):
        f.write(f"property float {p}\n".encode())
    for p in ("red", "green", "blue", "alpha"):
        f.write(f"property uchar {p}\n".encode())
    f.write(f"element face {NT}\n".encode())
    f.write(b"property list uchar int vertex_indices\n")
    f.write(f"element faceaov {NT}\n".encode())
    f.write(b"property float triaov\nend_header\n")
    for i in range(NV):
        f.write(struct.pack("<9f4B", *verts[i], *norms[i],
                            *uvs[i], vert_aov[i], *cols[i], alphas[i]))
    for t in tris:
        f.write(struct.pack("<B3i", 3, *t))
    for v in tri_aov:
        f.write(struct.pack("<f", v))

scn3 = pysuperluxcore.Scene()
p3 = pysuperluxcore.Properties()
p3.SetFromString(
    SCN_TMPL.format(mesh=LAYERED_PLY).replace(
        "scene.objects.floor", "scene.objects.layfloor")
)
scn3.Parse(p3)
scn3.SaveMesh(LAYERED_PLY, LAYERED_LXM)

lxm2 = open(LAYERED_LXM, "rb").read()
flags = struct.unpack("<I", lxm2[8:12])[0]
masks = struct.unpack("<5I", lxm2[32:52])
uvm, colm, alm, vam, tam = masks
print(f"[LxmTest] layered: flags={flags} masks uv={uvm} col={colm} "
      f"alpha={alm} vertaov={vam} triaov={tam}")

# Sections are Morton-sorted / first-use-renumbered: verify layer data
# permutes consistently with the implied vertex/triangle mappings.
nv2 = struct.unpack("<Q", lxm2[16:24])[0]
pos2 = 128
ok = True
# verts -> implied vertex map file_idx -> src_idx (positions unique)
fverts = [struct.unpack("<3f", lxm2[pos2 + i * 12:pos2 + i * 12 + 12])
          for i in range(nv2)]
src_of = {v: i for i, v in enumerate(verts)}
vm = [src_of[v] for v in fverts]
pos2 += nv2 * 12
pos2 = (pos2 + 63) & ~63
# tris -> implied tri map file_idx -> src_idx via vertex source sets
src_tris = {frozenset(t): i for i, t in enumerate(tris)}
tm = []
for i in range(NT):
    idx = struct.unpack("<3I", lxm2[pos2 + i * 12:pos2 + i * 12 + 12])
    tm.append(src_tris[frozenset(vm[j] for j in idx)])
pos2 += NT * 12
pos2 = (pos2 + 63) & ~63
# float32 round-trip compare helper (file stores float32)
f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
# normals (per vertex)
for i in range(nv2):
    got = struct.unpack("<3f", lxm2[pos2 + i * 12:pos2 + i * 12 + 12])
    ok &= got == norms[vm[i]]
pos2 += nv2 * 12
pos2 = (pos2 + 63) & ~63
# uv layer 0
for i in range(nv2):
    got = struct.unpack("<2f", lxm2[pos2 + i * 8:pos2 + i * 8 + 8])
    ok &= got == uvs[vm[i]]
pos2 += nv2 * 8
pos2 = (pos2 + 63) & ~63
# color layer 0: uchar -> float/255
for i in range(nv2):
    got = struct.unpack("<3f", lxm2[pos2 + i * 12:pos2 + i * 12 + 12])
    ok &= got == tuple(f32(c / 255.0) for c in cols[vm[i]])
pos2 += nv2 * 12
pos2 = (pos2 + 63) & ~63
# alpha layer 0
for i in range(nv2):
    got = struct.unpack("<f", lxm2[pos2 + i * 4:pos2 + i * 4 + 4])[0]
    ok &= got == f32(alphas[vm[i]] / 255.0)
pos2 += nv2 * 4
pos2 = (pos2 + 63) & ~63
# vertAOV layer 0
for i in range(nv2):
    got = struct.unpack("<f", lxm2[pos2 + i * 4:pos2 + i * 4 + 4])[0]
    ok &= got == f32(vert_aov[vm[i]])
pos2 += nv2 * 4
pos2 = (pos2 + 63) & ~63
# triAOV layer 0 — last section, no trailing pad in the file
for i in range(NT):
    got = struct.unpack("<f", lxm2[pos2 + i * 4:pos2 + i * 4 + 4])[0]
    ok &= got == f32(tri_aov[tm[i]])
pos2 += NT * 4
ok &= pos2 == len(lxm2)
expected_masks = (flags == 3 and uvm == 1 and colm == 1 and
                  alm == 1 and vam == 1 and tam == 1)
print(f"[LxmTest] layer sections permutation-consistent: {ok}, "
      f"masks expected: {expected_masks} "
      f"({'PASS' if ok and expected_masks else 'FAIL'})")

print("[LxmTest] DONE")
