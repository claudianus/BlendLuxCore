# SPDX-License-Identifier: Apache-2.0
#
# E2E: Cycles ShaderNodeTexIES -> LuxCore mappoint + iesblob.
#
# A point light's Emission node is driven by an IES texture node whose
# profile lives in a Text datablock. The same scene is then rendered
# with a LuxCore-native light (light.luxcore.ies, file path) using the
# identical profile. Both should produce the same directional falloff
# on the floor; a plain point-light control render must differ.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e13_ies_light_test.py

import os
import sys

import bpy

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS" if ok else "FAIL") + f" {name} {detail}", flush=True)


# Minimal asymmetric IES profile (Type C, 5 vertical x 1 horizontal).
# Candela decays steeply with the vertical angle: bright straight down,
# nearly dark beyond 45 degrees.
IES_TEXT = """IESNA:LM-63-2002
[TEST] e13 asymmetric downlight
TILT=NONE
1 -1 1.0 5 1 1 1 1 1 1
1.0 1.0 0.0
0 22.5 45 67.5 90
0
1000 500 10 1 1
"""

IES_PATH = "/tmp/e13_test.ies"


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


def patch_mean(px, w, cx, cy, r=10):
    s = n = 0
    for y in range(cy - r, cy + r):
        for x in range(cx - r, cx + r):
            s += px[(y * w + x) * 4]
            n += 1
    return s / n


def build_scene(light_setup):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    for li in list(bpy.data.lights):
        bpy.data.lights.remove(li)

    scene = bpy.context.scene
    scene.render.engine = "LUXCORE"

    # Floor plane
    bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, 0))
    floor = bpy.context.active_object
    mat = bpy.data.materials.new("floor")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
    floor.data.materials.append(mat)

    # Light high above the floor, pointing down (-Z local)
    ld = bpy.data.lights.new("lt", type="POINT")
    ld.energy = 200
    ld.use_nodes = True
    lo = bpy.data.objects.new("lt", ld)
    lo.location = (0, 0, 4)
    lo.rotation_euler = (0, 0, 0)
    scene.collection.objects.link(lo)
    light_setup(ld)

    # Top-down camera
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, 0, 9.5)
    cam.rotation_euler = (0, 0, 0)
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.luxcore.config.engine = "PATH"
    scene.luxcore.config.device = "OCL"
    scene.render.resolution_x = 128
    scene.render.resolution_y = 128
    scene.luxcore.halt.enable = True
    scene.luxcore.halt.use_samples = True
    scene.luxcore.halt.samples = 64
    return scene


def setup_cycles_ies(ld):
    nt = ld.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputLight")
    em = nt.nodes.new("ShaderNodeEmission")
    ies = nt.nodes.new("ShaderNodeTexIES")
    text = bpy.data.texts.get("e13_ies") or bpy.data.texts.new("e13_ies")
    text.clear()
    text.write(IES_TEXT)
    ies.mode = "INTERNAL"
    ies.ies = text
    ies.inputs["Strength"].default_value = 1.0
    nt.links.new(ies.outputs["Factor"], em.inputs["Strength"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    ld.luxcore.use_cycles_settings = True


def setup_luxcore_ies(ld):
    ld.luxcore.use_cycles_settings = False
    ld.luxcore.ies.use = True
    ld.luxcore.ies.file_type = "PATH"
    ld.luxcore.ies.file_path = IES_PATH
    # Same convention as the Cycles mapping: nadir points down (-Z).
    ld.luxcore.ies.flipz = True
    # Match the Cycles path's gain (light.energy = 200).
    ld.luxcore.gain = 200


def setup_plain(ld):
    ld.use_nodes = False
    ld.luxcore.use_cycles_settings = True


def render(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return decode_png(path)


def main():
    with open(IES_PATH, "w") as f:
        f.write(IES_TEXT)

    scene = build_scene(setup_cycles_ies)
    w, h, px_cycles = render(scene, "/tmp/e13_cycles_ies.png")

    scene = build_scene(setup_luxcore_ies)
    w, h, px_lux = render(scene, "/tmp/e13_luxcore_ies.png")

    scene = build_scene(setup_plain)
    w, h, px_plain = render(scene, "/tmp/e13_plain.png")

    # Center patch: directly under the light (bright for a downlight IES)
    c_cycles = patch_mean(px_cycles, w, w // 2, h // 2)
    c_lux = patch_mean(px_lux, w, w // 2, h // 2)
    c_plain = patch_mean(px_plain, w, w // 2, h // 2)

    # Corner patch: far from the light axis (IES profile is dark there)
    e_cycles = patch_mean(px_cycles, w, 10, 10)
    e_lux = patch_mean(px_lux, w, 10, 10)
    e_plain = patch_mean(px_plain, w, 10, 10)

    print(f"center: cycles={c_cycles:.1f} lux={c_lux:.1f} plain={c_plain:.1f}")
    print(f"corner: cycles={e_cycles:.1f} lux={e_lux:.1f} plain={e_plain:.1f}")

    check("cycles IES lights the floor", c_cycles > 5,
          f"center={c_cycles:.1f}")
    check("cycles IES vs luxcore IES center parity",
          abs(c_cycles - c_lux) / max(1.0, c_lux) < 0.35,
          f"cycles={c_cycles:.1f} lux={c_lux:.1f}")
    check("cycles IES vs luxcore IES corner parity",
          abs(e_cycles - e_lux) / max(1.0, e_lux) < 0.35,
          f"cycles={e_cycles:.1f} lux={e_lux:.1f}")
    # The downlight profile concentrates flux below the light, so the
    # center/corner ratio must exceed the plain point light's falloff.
    r_ies = c_cycles / max(1.0, e_cycles)
    r_plain = c_plain / max(1.0, e_plain)
    check("IES profile concentrates light downward",
          r_ies > r_plain * 1.2,
          f"ies ratio={r_ies:.2f} plain ratio={r_plain:.2f}")


main()

print()
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print(f"{len(fails)} FAILURES: {fails}")
    sys.exit(1)
print("ALL IES LIGHT TESTS PASSED")
