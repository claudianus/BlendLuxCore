# E9 Phase-5 end-to-end regression: SuperLuxCore mesh deformation motion
# blur export (see SuperLuxCore dev-tools/deformation-motion-blur-design.md).
#
# Scene: a camera-facing emissive quad driven by a shape key whose value
# animates across the shutter interval. The object opts into motion blur;
# the exporter must sample the evaluated mesh at every shutter step and
# attach a vertex-motion series via Scene.SetMeshVertexMotion.
#
# Assertions:
#   * A/B: the blurred render covers a much wider screen-space span than
#     the static render (vertex motion actually reached the accelerator).
#   * The smear direction matches the shape-key displacement (+x).
#   * A non-opted-in deforming object stays sharp (no false vertex
#     motion).
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/e9_vertex_motion_e2e_test.py
#
# Requires the SuperLuxCore addon with a pysuperluxcore build that exposes
# Scene.SetMeshVertexMotion. Exits 0 on PASS, 1 on FAIL. Rendered images
# land in $E9_TEST_OUT (default /tmp).

import math
import os
import sys

import bpy
import mathutils

OUT_DIR = os.environ.get("E9_TEST_OUT", "/tmp")
failures = []


def check(name, condition, detail=""):
    status = "ok" if condition else "FAIL"
    print(f"[E9-TEST] {status}: {name} {detail}")
    if not condition:
        failures.append(name)


def find_addon_key():
    return next(
        a.module
        for a in bpy.context.preferences.addons
        if "superluxcore" in a.module.lower()
    )


def render(tag):
    scene = bpy.context.scene
    scene.render.filepath = os.path.join(OUT_DIR, f"e9vm_{tag}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[E9-TEST] rendered {tag}")


def red_span(path):
    """(count, min_col, max_col) of emissive-red pixels. A column only
    counts when it holds at least 3 saturated opaque red pixels, so
    sparse firefly/background noise cannot stretch the span."""
    img = bpy.data.images.load(path)
    w, h = img.size
    px = img.pixels[:]
    bpy.data.images.remove(img)
    colcount = [0] * w
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a > 0.9 and r > 0.35 and r > 1.3 * g and r > 1.3 * b:
            colcount[(i // 4) % w] += 1
    cols = [c for c, n in enumerate(colcount) if n >= 3]
    total = sum(colcount)
    return (total, min(cols), max(cols)) if cols else (0, 0, 0)


# ---------- scene ----------
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
for me in list(bpy.data.meshes):
    bpy.data.meshes.remove(me)

scene = bpy.context.scene
scene.superluxcore.config.engine = "PATH"
scene.superluxcore.config.device = "OCL"
scene.superluxcore.devices.use_native_cpu = False
scene.superluxcore.halt.enable = True
scene.superluxcore.halt.use_samples = True
scene.superluxcore.halt.samples = 64
scene.render.engine = "SUPERLUXCORE"
scene.render.resolution_x = 320
scene.render.resolution_y = 240
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = True
scene.frame_start = 1
scene.frame_end = 20
scene.frame_set(10)

prefs = bpy.context.preferences.addons[find_addon_key()].preferences
if hasattr(prefs, "gpu_backend"):
    prefs.gpu_backend = "METAL"

# Deforming quad: shape key slides all verts +1.5 x at value=1, animated
# 0 @ frame 1 -> 1 @ frame 20. At frame 10 the pose is ~+0.75.
mesh = bpy.data.meshes.new("DeformQuad")
verts = [(-0.4, -0.4, 0.0), (0.4, -0.4, 0.0), (0.4, 0.4, 0.0), (-0.4, 0.4, 0.0)]
mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
mesh.update()
quad = bpy.data.objects.new("DeformQuad", mesh)
scene.collection.objects.link(quad)

basis = quad.shape_key_add(name="Basis")
key = quad.shape_key_add(name="Sweep")
for i in range(4):
    key.data[i].co.x += 1.5
key.value = 0.0
key.keyframe_insert("value", frame=1)
key.value = 1.0
key.keyframe_insert("value", frame=20)
scene.frame_set(10)

mat = bpy.data.materials.new("Emit")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
em = nt.nodes.new("ShaderNodeEmission")
em.inputs["Color"].default_value = (1.0, 0.0, 0.0, 1.0)
em.inputs["Strength"].default_value = 5.0
out = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
mesh.materials.append(mat)

cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
cam.location = (0, 0, 4)
scene.camera = cam

mb = cam_data.superluxcore.motion_blur
mb.enable = True
mb.object_blur = True
mb.camera_blur = False
mb.shutter = 8.0
mb.steps = 3

# ---------- A: motion blur on, object opted in ----------
quad.superluxcore.enable_motion_blur = True
render("blur")
count_b, lo_b, hi_b = red_span(scene.render.filepath)
check("blur render produced emissive pixels", count_b > 50, f"count={count_b}")

# ---------- B: motion blur off -> static pose ----------
mb.enable = False
render("static")
count_s, lo_s, hi_s = red_span(scene.render.filepath)
check("static render produced emissive pixels", count_s > 50, f"count={count_s}")

# The +1.5x sweep at 4m over ~40% of the frame width must widen the
# emissive footprint far beyond interpolation noise.
span_b = hi_b - lo_b
span_s = hi_s - lo_s
check(
    "blurred span much wider than static",
    span_b > span_s * 1.4 and span_b > 40,
    f"blur={span_b}px static={span_s}px",
)
check(
    "smear extends toward +x (screen-right in this setup) or left",
    hi_b > hi_s or lo_b < lo_s,
    f"blur=({lo_b},{hi_b}) static=({lo_s},{hi_s})",
)

# ---------- C: blur on but object NOT opted in -> stays sharp ----------
mb.enable = True
quad.superluxcore.enable_motion_blur = False
render("noopt")
count_n, lo_n, hi_n = red_span(scene.render.filepath)
span_n = hi_n - lo_n
check(
    "non-opted-in object stays sharp",
    span_n < span_b * 0.7,
    f"noopt={span_n}px blur={span_b}px",
)

print(f"[E9-TEST] {len(failures)} failures")
sys.exit(1 if failures else 0)
