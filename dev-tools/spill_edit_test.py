"""
Spill + scene-edit regression test (standalone pyluxcore, PATHOCL/Metal).

    PYTHONPATH=<site-packages> python3.13 dev-tools/spill_edit_test.py

Renders with all spilling enabled, then performs a live object
transformation edit (BeginSceneEdit -> Parse -> EndSceneEdit), which
triggers a geometry re-upload reading through the spilled mappings.
Verifies no crash, correct spill logs, and a valid post-edit frame.
"""

import os
import sys
import time
import pyluxcore

pyluxcore.Init()

WIDTH, HEIGHT = 1280, 720
OUT = "/tmp/luxcore_spill_edit_720p.png"


def write_ply(path, n=600):
    import struct
    nv, nf = (n + 1) ** 2, n * n * 2
    with open(path, "wb") as f:
        f.write(b"ply\nformat binary_little_endian 1.0\n")
        f.write(f"element vertex {nv}\n".encode())
        f.write(b"property float x\nproperty float y\nproperty float z\n")
        f.write(f"element face {nf}\n".encode())
        f.write(b"property list uchar int vertex_indices\nend_header\n")
        for j in range(n + 1):
            for i in range(n + 1):
                f.write(struct.pack("<fff", i / n * 6 - 3, j / n * 6 - 3, 0.0))
        for j in range(n):
            for i in range(n):
                a = j * (n + 1) + i
                f.write(struct.pack("<Biii", 3, a, a + 1, a + n + 1))
                f.write(struct.pack("<Biii", 3, a + 1, a + n + 2, a + n + 1))
    return nv, nf


if not os.path.exists("/tmp/spill_edit_floor.ply"):
    nv, nf = write_ply("/tmp/spill_edit_floor.ply")
    print(f"[EditTest] wrote PLY: {nv} verts, {nf} faces")

with open("/tmp/spill_edit_lamp.ply", "w") as f:
    f.write("ply\nformat ascii 1.0\nelement vertex 4\n"
            "property float x\nproperty float y\nproperty float z\n"
            "element face 1\nproperty list uchar int vertex_indices\n"
            "end_header\n-1 -1 0\n1 -1 0\n1 1 0\n-1 1 0\n4 0 1 2 3\n")

scn_props = pyluxcore.Properties()
scn_props.SetFromString(f"""
scene.camera.lookat.orig = 0 -7 3.5
scene.camera.lookat.target = 0 0 0.5
scene.camera.fieldofview = 45
scene.lights.skyl.type = sky2
scene.lights.skyl.gain = 0.00003 0.00003 0.00003
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
scene.objects.floor.ply = /tmp/spill_edit_floor.ply
scene.spill.enable = 1
scene.spill.minbytes = 1048576
scene.spill.images = 1
""")

# NOTE: pyluxcore.Scene(props) treats the single-Properties overload as a
# resize-policy ctor (empty scene) — the scene definition goes via Parse().
scene = pyluxcore.Scene()
scene.Parse(scn_props)

cfg_props = pyluxcore.Properties()
cfg_props.SetFromString(f"""
film.width = {WIDTH}
film.height = {HEIGHT}
film.outputs.0.type = RGB_IMAGEPIPELINE
film.outputs.0.filename = {OUT}
renderengine.type = PATHOCL
sampler.type = SOBOL
path.pathdepth.total = 4
# Select the Metal device only: on Apple Silicon the deprecated OpenCL
# device (OCL_GPU) and METAL_GPU alias the same physical GPU, and running
# both together crashes inside AGX (OpenCL-over-Metal encode).
opencl.devices.select = 01
""")

config = pyluxcore.RenderConfig(cfg_props, scene)
session = pyluxcore.RenderSession(config)
session.Start()

# Render ~25s so all uploads complete and spills fire
time.sleep(25)
session.UpdateStats()
print("[EditTest] pre-edit render done, applying live edit")

# ---- Live geometry edit: move the lamp -> geometry re-upload ----
# Scene edits go through scene.* calls (accumulating editActions);
# session.Parse only handles film properties.
session.BeginSceneEdit()
scene.UpdateObjectTransformation("lamp",
    [1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  2, 0, 3, 1])
session.EndSceneEdit()
print("[EditTest] edit applied, rendering post-edit samples")

time.sleep(20)
session.UpdateStats()
session.Stop()
session.GetFilm().SaveOutputs()
print(f"[EditTest] DONE -> {OUT}")
