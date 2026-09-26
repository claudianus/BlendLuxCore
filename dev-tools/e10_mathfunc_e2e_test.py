# SPDX-License-Identifier: Apache-2.0
#
# E2E: Cycles ShaderNodeMath -> SuperLuxCore mathfunc texture -> real render.
#
# Builds an emissive cube whose strength is SINE(Generated.x * 6.28),
# renders with the SuperLuxCore engine and asserts the output brightness
# oscillates (bright and dark columns both exist) — proving the node
# chain survived scene export, scene parse, and kernel evaluation.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e10_mathfunc_e2e_test.py

import os
import sys

import bpy

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

# The extension registers the SUPERLUXCORE engine at Blender startup; do NOT
# call read_factory_settings() — it tears the registration down.

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS" if ok else "FAIL") + f" {name} {detail}", flush=True)


def decode_png(path):
    import struct, zlib
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n"
    pos, w, h, idat = 8, 0, 0, b""
    while pos < len(d):
        ln = int.from_bytes(d[pos:pos + 4], "big")
        typ = d[pos + 4:pos + 8]
        if typ == b"IHDR":
            w = int.from_bytes(d[pos + 8:pos + 12], "big")
            h = int.from_bytes(d[pos + 12:pos + 16], "big")
        elif typ == b"IDAT":
            idat += d[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    px = bytearray()
    prev = bytearray(stride - 1)
    for y in range(h):
        row = bytearray(raw[y * stride + 1:(y + 1) * stride])
        f = raw[y * stride]
        for i in range(len(row)):
            a = row[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if f == 1:
                row[i] = (row[i] + a) & 255
            elif f == 2:
                row[i] = (row[i] + b) & 255
            elif f == 3:
                row[i] = (row[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 255
        px += row
        prev = row
    return w, h, px


def main():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    scene = bpy.context.scene
    scene.render.engine = "SUPERLUXCORE"

    # Two cubes side by side: left gets emission=sin(pi/2)=1 (bright),
    # right gets emission=sin(0)=0 (dark). A/B proves mathfunc survives
    # export -> scene parse -> kernel eval.
    def sine_cube(x, arg):
        bpy.ops.mesh.primitive_cube_add(size=1.5, location=(x, 0, 0))
        cube = bpy.context.active_object
        mat = bpy.data.materials.new(f"mf{x}")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (1.0, 0.0, 0.0, 1.0)
        sine = nt.nodes.new("ShaderNodeMath")
        sine.operation = "SINE"
        sine.inputs[0].default_value = arg
        nt.links.new(sine.outputs[0], em.inputs["Strength"])
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
        cube.data.materials.append(mat)
        return cube

    sine_cube(-1.5, 1.5707963)   # sin(pi/2) = 1 -> bright red
    sine_cube(1.5, 0.0)          # sin(0) = 0 -> dark

    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, -4, 0)
    cam.rotation_euler = (1.5707963, 0, 0)
    scene.camera = cam

    scene.render.engine = "SUPERLUXCORE"
    scene.superluxcore.config.engine = "PATH"
    scene.superluxcore.config.device = "OCL"
    scene.render.resolution_x = 128
    scene.render.resolution_y = 128
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_samples = True
    scene.superluxcore.halt.samples = 32
    out_png = "/tmp/e10_mathfunc_e2e.png"
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)

    w, h, px = decode_png(out_png)

    def patch_mean_red(cx, cy, r=8):
        s = n = 0
        for y in range(cy - r, cy + r):
            for x in range(cx - r, cx + r):
                i = (y * w + x) * 4
                s += px[i]
                n += 1
        return s / n

    midy = h // 2
    left = patch_mean_red(w // 4, midy)     # sin(pi/2) cube
    right = patch_mean_red(7 * w // 8, midy)  # sin(0) cube
    check("sin(pi/2) cube bright (left)", left > 60,
          f"left red mean={left:.0f}")
    check("sin(0) cube dark (right)", right < 30,
          f"right red mean={right:.0f}")


main()

fails = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
sys.exit(1 if fails else 0)
