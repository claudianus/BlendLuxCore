# SPDX-License-Identifier: Apache-2.0
#
# E2E: generic named attributes (Geometry Nodes "Store Named Attribute"
# output / mesh.attributes) -> SuperLuxCore vertex-AOV / triangle-AOV / color
# layers -> Cycles Attribute node shading.
#
# Scene A: a grid whose POINT-domain float attribute "hot" is 0 on
# verts with x<=0 and 200 on the right (a vertex column at x=0 makes the
# left half exactly 0); a POINT-domain vector attribute "tint" is
# constant (0,0,1). The material wires Attribute("hot").Fac into Emission
# Strength and Attribute("tint").Color into Emission Color. Expected:
# left half black, right half pure blue.
#
# Scene B: same grid with a FACE-domain float attribute "facehot" = 0 on
# left faces / 200 on right faces -> triangle AOV path; same expected
# left/right split (white emission).
#
# Both scenes render on PATHCPU and PATHOCL to cover the device AOV
# upload path.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e14_named_attribute_test.py

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


def patch_mean(px, w, cx, cy, r=6, ch=0):
    s = n = 0
    for y in range(cy - r, cy + r):
        for x in range(cx - r, cx + r):
            s += px[(y * w + x) * 4 + ch]
            n += 1
    return s / n


def reset_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    for me in list(bpy.data.meshes):
        bpy.data.meshes.remove(me)
    for ma in list(bpy.data.materials):
        bpy.data.materials.remove(ma)


def add_attr(mesh, name, data_type, domain, values):
    attr = mesh.attributes.new(name=name, type=data_type, domain=domain)
    prop = {"FLOAT": "value", "INT": "value", "BOOLEAN": "value",
            "FLOAT_VECTOR": "vector"}[data_type]
    for i, v in enumerate(values):
        setattr(attr.data[i], prop, v)
    return attr


def build_material(attr_name, tint_attr=None):
    mat = bpy.data.materials.new("m")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = attr_name
    # Attribute values are already 0/50 — feed Fac straight into Strength.
    nt.links.new(attr.outputs["Fac"], em.inputs["Strength"])
    if tint_attr:
        tint = nt.nodes.new("ShaderNodeAttribute")
        tint.attribute_name = tint_attr
        nt.links.new(tint.outputs["Color"], em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def build_scene(face_attr):
    reset_scene()
    scene = bpy.context.scene
    scene.render.engine = "SUPERLUXCORE"

    if face_attr:
        # Grid with faces on both sides of x=0; FACE attr "facehot" is 0
        # on faces with center x < 0 and 200 on the right — per-triangle
        # AOV gives an exact binary split.
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 0, 0))
        obj = bpy.context.active_object
        mesh = obj.data
        add_attr(mesh, "facehot", "FLOAT", "FACE",
                 [0.0 if p.center.x < 0 else 1.0 for p in mesh.polygons])
        mat = build_material("facehot")
    else:
        # Grid with a vertex column at x=0; POINT attr "hot" is 0 for
        # x<=0 and 200 for x>0 — interpolation makes the left half
        # exactly 0 and the right half ramp up to full emission.
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=4, y_subdivisions=4, size=2, location=(0, 0, 0))
        obj = bpy.context.active_object
        mesh = obj.data
        add_attr(mesh, "hot", "FLOAT", "POINT",
                 [0.0 if v.co.x <= 0 else 1.0 for v in mesh.vertices])
        add_attr(mesh, "tint", "FLOAT_VECTOR", "POINT",
                 [(0.0, 0.0, 1.0)] * len(mesh.vertices))
        mat = build_material("hot", tint_attr="tint")
    mesh.materials.append(mat)

    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, 0, 2.6)
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.superluxcore.config.engine = "PATH"
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_samples = True
    scene.superluxcore.halt.samples = 32
    return scene


def render(scene, device, path):
    scene.superluxcore.config.device = device
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return decode_png(path)


def sides(px, w, h, ch=0):
    cy = h // 2
    left = patch_mean(px, w, w // 3, cy, ch=ch)
    right = patch_mean(px, w, 2 * w // 3, cy, ch=ch)
    return left, right


def main():
    import superluxcore  # registers the engine

    # Scene A: vertex AOV (float) + extra color layer (vector)
    scene = build_scene(face_attr=False)
    w, h, px_cpu = render(scene, "CPU", "/tmp/e14_vert_cpu.png")
    scene = build_scene(face_attr=False)
    w, h, px_ocl = render(scene, "OCL", "/tmp/e14_vert_ocl.png")

    l_cpu, r_cpu = sides(px_cpu, w, h, ch=2)
    l_ocl, r_ocl = sides(px_ocl, w, h, ch=2)
    check("A.vertaov.cpu.left-dark", l_cpu < 5, f"left={l_cpu:.1f}")
    check("A.vertaov.cpu.right-lit", r_cpu > 120, f"right={r_cpu:.1f}")
    check("A.vertaov.ocl.left-dark", l_ocl < 5, f"left={l_ocl:.1f}")
    check("A.vertaov.ocl.right-lit", r_ocl > 120, f"right={r_ocl:.1f}")

    # tint=(0,0,1) on the lit side: blue dominates red
    b_r = patch_mean(px_cpu, w, 2 * w // 3, h // 2, ch=2)
    r_r = patch_mean(px_cpu, w, 2 * w // 3, h // 2, ch=0)
    b_o = patch_mean(px_ocl, w, 2 * w // 3, h // 2, ch=2)
    r_o = patch_mean(px_ocl, w, 2 * w // 3, h // 2, ch=0)
    check("A.vector-color.cpu", b_r > 120 and b_r > 1.5 * r_r,
          f"B={b_r:.1f} R={r_r:.1f}")
    check("A.vector-color.ocl", b_o > 120 and b_o > 1.5 * r_o,
          f"B={b_o:.1f} R={r_o:.1f}")

    # Scene B: FACE-domain float -> triangle AOV
    scene = build_scene(face_attr=True)
    w, h, px_cpu = render(scene, "CPU", "/tmp/e14_tri_cpu.png")
    scene = build_scene(face_attr=True)
    w, h, px_ocl = render(scene, "OCL", "/tmp/e14_tri_ocl.png")

    l_cpu, r_cpu = sides(px_cpu, w, h)
    l_ocl, r_ocl = sides(px_ocl, w, h)
    check("B.triaov.cpu.left-dark", l_cpu < 5, f"left={l_cpu:.1f}")
    check("B.triaov.cpu.right-lit", r_cpu > 120, f"right={r_cpu:.1f}")
    check("B.triaov.ocl.left-dark", l_ocl < 5, f"left={l_ocl:.1f}")
    check("B.triaov.ocl.right-lit", r_ocl > 120, f"right={r_ocl:.1f}")

    passed = sum(1 for _n, ok in RESULTS if ok)
    print(f"\n===== named attributes: {passed}/{len(RESULTS)} PASS =====")
    sys.exit(0 if passed == len(RESULTS) else 1)


main()
