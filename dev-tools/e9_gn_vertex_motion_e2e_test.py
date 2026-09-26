# E9 Phase-6 end-to-end regression: GN-driven deformation motion blur.
#
# Scene: an emissive cube with a Geometry Nodes modifier that slides
# all points +x proportional to Scene Time — a deterministic
# deforming-modifier case for the vertex-motion export path (the
# exporter samples to_mesh() on the evaluated object per shutter step).
#
# Assertions mirror e9_vertex_motion_e2e_test.py: blurred footprint
# widens vs static, non-opted-in object stays sharp.
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/e9_gn_vertex_motion_e2e_test.py

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
    scene.render.filepath = os.path.join(OUT_DIR, f"e9gn_{tag}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[E9-TEST] rendered {tag}")


def red_span(path):
    """(count, min_col, max_col) of emissive-red pixels."""
    img = bpy.data.images.load(path)
    w, h = img.size
    px = img.pixels[:]
    bpy.data.images.remove(img)
    colcount = [0] * w
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a > 0.5 and r > 0.15 and r > 1.15 * g and r > 1.15 * b:
            colcount[(i // 4) % w] += 1
    cols = [c for c, n in enumerate(colcount) if n >= 3]
    total = sum(colcount)
    return (total, min(cols), max(cols)) if cols else (0, 0, 0)


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
scene.render.fps = 24
scene.frame_set(10)

prefs = bpy.context.preferences.addons[find_addon_key()].preferences
if hasattr(prefs, "gpu_backend"):
    prefs.gpu_backend = "METAL"

# Deforming quad via GN: Set Position offset = (frame - 10) * 0.35 in
# +x. At frame 10 the quad sits centered; the shutter steps (frames
# 6/10/14) land at -1.4/0/+1.4 — a clear sweep staying in frame.
mesh = bpy.data.meshes.new("GNQuad")
verts = [(-0.4, -0.4, 0.0), (0.4, -0.4, 0.0), (0.4, 0.4, 0.0), (-0.4, 0.4, 0.0)]
mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
mesh.update()
quad = bpy.data.objects.new("GNQuad", mesh)
scene.collection.objects.link(quad)

ng = bpy.data.node_groups.new("GNMotion", "GeometryNodeTree")
ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
n_in = ng.nodes.new("NodeGroupInput")
n_out = ng.nodes.new("NodeGroupOutput")
n_time = ng.nodes.new("GeometryNodeInputSceneTime")
n_sub = ng.nodes.new("ShaderNodeMath")
n_sub.operation = "SUBTRACT"
n_sub.inputs[1].default_value = 10.0
n_mul = ng.nodes.new("ShaderNodeMath")
n_mul.operation = "MULTIPLY"
n_mul.inputs[1].default_value = 0.35
n_comb = ng.nodes.new("ShaderNodeCombineXYZ")
n_set = ng.nodes.new("GeometryNodeSetPosition")
ng.links.new(n_time.outputs["Frame"], n_sub.inputs[0])
ng.links.new(n_sub.outputs[0], n_mul.inputs[0])
ng.links.new(n_mul.outputs[0], n_comb.inputs["X"])
ng.links.new(n_in.outputs["Geometry"], n_set.inputs["Geometry"])
ng.links.new(n_comb.outputs["Vector"], n_set.inputs["Offset"])
ng.links.new(n_set.outputs["Geometry"], n_out.inputs["Geometry"])
mod = quad.modifiers.new("GNMotion", "NODES")
mod.node_group = ng

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

span_b = hi_b - lo_b
span_s = hi_s - lo_s
check(
    "blurred span much wider than static",
    span_b > span_s * 1.4 and span_b > 40,
    f"blur={span_b}px static={span_s}px",
)

# ---------- C: blur on but object NOT opted in -> stays sharp ----------
mb.enable = True
quad.superluxcore.enable_motion_blur = False
render("noopt")
count_n, lo_n, hi_n = red_span(scene.render.filepath)
span_n = hi_n - lo_n
check(
    "non-opted-in object stays sharp",
    span_n <= span_s + 10,
    f"noopt={span_n}px static={span_s}px",
)

print(f"[E9-TEST] {len(failures)} failures")
sys.exit(1 if failures else 0)
