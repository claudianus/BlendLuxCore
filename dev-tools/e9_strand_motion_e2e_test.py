# E9 Phase-5b end-to-end regression: SuperLuxCore hair-curves strand
# motion blur export (SuperLuxCore dev-tools/deformation-motion-blur-design.md).
#
# Scene: a "comb" of hair strands on a CURVES object whose control
# points are keyframed to sweep +x across the shutter interval. The
# object opts into motion blur; the exporter must re-read the evaluated
# curves at every shutter step and attach a strand motion series via
# Scene.SetStrandsVertexMotion.
#
# Assertions:
#   * A/B: the blurred render covers a much wider screen-space span than
#     the static render (strand motion actually reached the accelerator).
#   * The smear extends toward +x.
#   * A non-opted-in strand object stays sharp (no false motion).
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/e9_strand_motion_e2e_test.py
#
# Requires the SuperLuxCore addon with a pysuperluxcore build exposing
# Scene.SetStrandsVertexMotion. Exits 0 on PASS, 1 on FAIL. Rendered
# images land in $E9_TEST_OUT (default /tmp).

import os
import sys

import bpy

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
    scene.render.filepath = os.path.join(OUT_DIR, f"e9strand_{tag}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[E9-TEST] rendered {tag}")


def red_span(path):
    """(count, min_col, max_col) of emissive-red pixels. The mask is
    deliberately loose (r > 0.15 and dominant) because motion-blurred
    strands spread their energy over the sweep and sit below the
    saturated threshold at intermediate positions."""
    img = bpy.data.images.load(path)
    w, h = img.size
    px = img.pixels[:]
    bpy.data.images.remove(img)
    colcount = [0] * w
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a > 0.5 and r > 0.15 and r > 1.15 * g and r > 1.15 * b:
            colcount[(i // 4) % w] += 1
    cols = [c for c, n in enumerate(colcount) if n >= 2]
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

# Hair comb: 12 vertical strands, 6 control points each, spread over
# x in [-0.8, 0.8]. Every point sweeps +2.0 x between frame 1 and 20,
# so at frame 10 the strands sit ~+1.0 right of the frame-1 pose.
STRANDS, PPS = 12, 6
curves = bpy.data.hair_curves.new("MotionHair")
curves.add_curves([PPS] * STRANDS)
for si, c in enumerate(curves.curves):
    x = -0.8 + 1.6 * si / (STRANDS - 1)
    for i, p in enumerate(c.points):
        p.position = (x, -0.5 + i * 0.2, 0.0)
        p.radius = 1.0

hair_obj = bpy.data.objects.new("MotionHair", curves)
scene.collection.objects.link(hair_obj)
hair_obj.superluxcore.hair.hair_size = 0.05
hair_obj.superluxcore.hair.tesseltype = "ribbon"

# Keyframe every point's position: 0.0 offset at frame 1, +2.0x at
# frame 20. keyframe_insert on point.position animates the FCurves
# datablock directly.
for c in curves.curves:
    for p in c.points:
        base_x = p.position.x
        p.keyframe_insert("position", frame=1)
        p.position = (base_x + 2.0, p.position.y, p.position.z)
        p.keyframe_insert("position", frame=20)
        p.position = (base_x, p.position.y, p.position.z)
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
curves.materials.append(mat)

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
hair_obj.superluxcore.enable_motion_blur = True
render("blur")
count_b, lo_b, hi_b = red_span(scene.render.filepath)
check("blur render produced emissive pixels", count_b > 30, f"count={count_b}")

# ---------- B: motion blur off -> static pose ----------
mb.enable = False
render("static")
count_s, lo_s, hi_s = red_span(scene.render.filepath)
check("static render produced emissive pixels", count_s > 30, f"count={count_s}")

span_b = hi_b - lo_b
span_s = hi_s - lo_s
check(
    "blurred span wider than static",
    span_b > span_s + 10,
    f"blur={span_b}px static={span_s}px",
)
check(
    "smear extends toward +x (screen-left in this camera setup)",
    lo_b < lo_s - 10,
    f"blur=({lo_b},{hi_b}) static=({lo_s},{hi_s})",
)

# ---------- C: blur on but object NOT opted in -> stays sharp ----------
mb.enable = True
hair_obj.superluxcore.enable_motion_blur = False
render("noopt")
count_n, lo_n, hi_n = red_span(scene.render.filepath)
span_n = hi_n - lo_n
check(
    "non-opted-in strands stay sharp",
    span_n <= span_s + 5,
    f"noopt={span_n}px static={span_s}px",
)

print(f"[E9-TEST] {len(failures)} failures")
sys.exit(1 if failures else 0)
