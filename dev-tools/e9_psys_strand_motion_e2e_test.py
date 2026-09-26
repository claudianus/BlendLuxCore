# E9 Phase-5b end-to-end regression: SuperLuxCore particle-hair strand
# motion blur export (SuperLuxCore dev-tools/deformation-motion-blur-design.md).
#
# Scene: a subdivided emitter plane with HAIR particles (render_type
# PATH). A shape key slides the emitter's verts +x across the shutter
# interval, so the strand roots ride the deforming surface — genuine
# strand deformation motion, not object transform motion. The object
# opts into motion blur; the exporter must re-read co_hair at every
# shutter step and attach a strand motion series via
# Scene.SetStrandsVertexMotion.
#
# Assertions:
#   * A/B: the blurred render covers a much wider screen-space span than
#     the static render (strand motion actually reached the accelerator).
#   * A non-opted-in emitter stays sharp.
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/e9_psys_strand_motion_e2e_test.py

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
    scene.render.filepath = os.path.join(OUT_DIR, f"e9psys_{tag}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[E9-TEST] rendered {tag}")


def red_span(path):
    """(count, min_col, max_col) of emissive-red pixels. Loose mask —
    motion-blurred strands spread energy over the sweep."""
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

# Emitter: 2x2 plane at z=0 subdivided into a grid; hair strands point
# up from the surface (+z normals). A shape key sweeps the whole plane
# +2.0x between frame 1 and 20, carrying the strand roots with it.
mesh = bpy.data.meshes.new("Emitter")
verts, faces = [], []
N = 5
for iy in range(N):
    for ix in range(N):
        verts.append((-1.0 + 2.0 * ix / (N - 1), -1.0 + 2.0 * iy / (N - 1), 0.0))
for iy in range(N - 1):
    for ix in range(N - 1):
        a = iy * N + ix
        faces.append((a, a + 1, a + 1 + N, a + N))
mesh.from_pydata(verts, [], faces)
mesh.update()
emitter = bpy.data.objects.new("Emitter", mesh)
scene.collection.objects.link(emitter)

basis = emitter.shape_key_add(name="Basis")
key = emitter.shape_key_add(name="Sweep")
for i in range(len(verts)):
    key.data[i].co.x += 2.0
key.value = 0.0
key.keyframe_insert("value", frame=1)
key.value = 1.0
key.keyframe_insert("value", frame=20)
scene.frame_set(10)

# Particle hair on the emitter
bpy.context.view_layer.objects.active = emitter
emitter.select_set(True)
bpy.ops.object.particle_system_add()
psys = emitter.particle_systems[-1]
settings = psys.settings
settings.type = "HAIR"
settings.count = 25
settings.hair_length = 0.8
settings.render_type = "PATH"
settings.render_step = 2  # 2**2+1 = 5 points per strand
settings.superluxcore.hair.hair_size = 0.04
settings.superluxcore.hair.tesseltype = "ribbon"
emitter.select_set(False)

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
cam.location = (0, 0, 4.5)
scene.camera = cam

mb = cam_data.superluxcore.motion_blur
mb.enable = True
mb.object_blur = True
mb.camera_blur = False
mb.shutter = 8.0
mb.steps = 3

# ---------- A: motion blur on, object opted in ----------
emitter.superluxcore.enable_motion_blur = True
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
emitter.superluxcore.enable_motion_blur = False
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
