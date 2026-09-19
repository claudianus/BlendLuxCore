# A6-II persistent-scene regression test (see doc/incremental_export_design.md).
#
# Exercises the final-render incremental export lifecycle end to end:
#   R1  first render            -> full export, scene cached
#   R2  unchanged re-render     -> cached pyluxcore.Scene reused as-is
#   R3  object moved            -> transform-only delta on the same Scene
#   R4  mesh edited via bmesh   -> full rebuild (new Scene object)
#   R5  unchanged re-render     -> new cache entry reused
#   R6  hide_render toggled     -> visibility signature mismatch, rebuild
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/a6_persistent_scene_test.py
#
# Requires the BlendLuxCore addon (with pyluxcore) installed. Exits 0 on
# PASS, 1 on FAIL. Rendered images land in $A6_TEST_OUT (default /tmp).

import importlib
import os
import sys

import bmesh
import bpy
import mathutils

OUT_DIR = os.environ.get("A6_TEST_OUT", "/tmp")

failures = []


def check(name, condition, detail=""):
    status = "ok" if condition else "FAIL"
    print(f"[A6-TEST] {status}: {name} {detail}")
    if not condition:
        failures.append(name)


def find_addon_key():
    return next(
        a.module
        for a in bpy.context.preferences.addons
        if "luxcore" in a.module.lower()
    )


def find_persistent_scene():
    return importlib.import_module(
        f"{find_addon_key()}.export.caches.persistent_scene"
    )


def render(tag):
    scene = bpy.context.scene
    scene.render.filepath = os.path.join(OUT_DIR, f"a6test_{tag}.png")
    bpy.ops.render.render(write_still=True)
    print(f"[A6-TEST] rendered {tag}")


def entry_of(persistent_scene):
    assert len(persistent_scene._entries) == 1, (
        f"expected exactly one cache entry, got {len(persistent_scene._entries)}"
    )
    return next(iter(persistent_scene._entries.values()))


def image_stats(path_a, path_b):
    """Mean abs diff + fraction of pixels differing > 0.15."""
    img_a = bpy.data.images.load(path_a)
    img_b = bpy.data.images.load(path_b)
    pa = img_a.pixels[:]
    pb = img_b.pixels[:]
    bpy.data.images.remove(img_a)
    bpy.data.images.remove(img_b)
    if len(pa) != len(pb):
        return float("inf"), 1.0
    n = len(pa) // 4
    total = 0.0
    changed = 0
    for i in range(0, len(pa), 4):
        d = max(abs(pa[i + c] - pb[i + c]) for c in range(3))
        total += d
        if d > 0.15:
            changed += 1
    return total / n, changed / n


# ---------- scene setup ----------
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)

scene = bpy.context.scene
scene.luxcore.config.engine = "PATH"
scene.luxcore.config.device = "OCL"
scene.luxcore.devices.use_native_cpu = False
scene.luxcore.halt.enable = True
scene.luxcore.halt.use_time = True
scene.luxcore.halt.time = 6
scene.luxcore.halt.use_samples = False
scene.render.engine = "LUXCORE"
scene.render.resolution_x = 320
scene.render.resolution_y = 240
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False

prefs = bpy.context.preferences.addons[find_addon_key()].preferences
if hasattr(prefs, "gpu_backend"):
    prefs.gpu_backend = "METAL"

bpy.ops.mesh.primitive_plane_add(size=20, location=(0, 0, -1))
plane = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0, 0, 0.2))
cube = bpy.context.object
cube.name = "Mover"

mat = bpy.data.materials.new("Ground")
mat.use_nodes = True
mat.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.7, 0.7, 0.7, 1)
plane.data.materials.append(mat)

mat2 = bpy.data.materials.new("Red")
mat2.use_nodes = True
mat2.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.8, 0.05, 0.05, 1)
cube.data.materials.append(mat2)

ld = bpy.data.lights.new("Sun", "SUN")
ld.energy = 4
lo = bpy.data.objects.new("Sun", ld)
scene.collection.objects.link(lo)
lo.rotation_euler = (0.6, 0.2, 0.8)

cd = bpy.data.cameras.new("Cam")
co = bpy.data.objects.new("Cam", cd)
scene.collection.objects.link(co)
co.location = (5, -5, 3.5)
co.rotation_euler = (
    mathutils.Vector((0, 0, 0.3)) - co.location
).to_track_quat("-Z", "Y").to_euler()
scene.camera = co

w = bpy.data.worlds.new("W")
scene.world = w
w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (
    0.03, 0.03, 0.05, 1,
)
w.node_tree.nodes["Background"].inputs[1].default_value = 0.4

persistent_scene = find_persistent_scene()
persistent_scene.clear_all()
mover_key = str(cube.original.as_pointer())

# ---------- R1: initial full export ----------
render("r1")
check("R1: cache entry stored", len(persistent_scene._entries) == 1)
entry = entry_of(persistent_scene)
scene_r1 = entry["scene"]
check(
    "R1: mover is delta-safe",
    mover_key in entry["delta_safe"],
)
check(
    "R1: mover bake matrix recorded",
    mover_key in entry["bake"],
)

# ---------- R2: unchanged re-render reuses the scene ----------
render("r2")
entry = entry_of(persistent_scene)
check(
    "R2: same pyluxcore.Scene reused",
    entry["scene"] is scene_r1,
)

# ---------- R3: transform-only update ----------
cube.location = (1.2, 0.5, 0.2)
bpy.context.view_layer.update()
render("r3")
entry = entry_of(persistent_scene)
check(
    "R3: same pyluxcore.Scene after transform delta",
    entry["scene"] is scene_r1,
)
bake = entry["bake"].get(mover_key)
check(
    "R3: bake matrix updated to new matrix_world",
    bake is not None
    and all(
        abs(a - b) < 1e-5
        for row_a, row_b in zip(bake, cube.matrix_world)
        for a, b in zip(row_a, row_b)
    ),
)

# ---------- R4: geometry edit -> full rebuild ----------
bm = bmesh.new()
bm.from_mesh(cube.data)
bmesh.ops.translate(
    bm, vec=mathutils.Vector((0, 0, 0.4)), verts=bm.verts[:4]
)
bm.to_mesh(cube.data)
bm.free()
cube.data.update()
bpy.context.view_layer.update()
render("r4")
entry = entry_of(persistent_scene)
scene_r4 = entry["scene"]
check(
    "R4: geometry edit rebuilt the cached scene",
    scene_r4 is not scene_r1,
)

# ---------- R5: reuse of the rebuilt entry ----------
render("r5")
entry = entry_of(persistent_scene)
check(
    "R5: rebuilt scene reused on unchanged re-render",
    entry["scene"] is scene_r4,
)

# ---------- R6: visibility toggle -> rebuild ----------
cube.hide_render = True
bpy.context.view_layer.update()
render("r6")
entry = entry_of(persistent_scene)
check(
    "R6: hide_render toggle rebuilt the scene",
    entry["scene"] is not scene_r4,
)

# ---------- image comparisons ----------
p = lambda tag: os.path.join(OUT_DIR, f"a6test_{tag}.png")
mean, frac = image_stats(p("r1"), p("r2"))
check(
    "R1~R2 images match (reuse keeps output)",
    mean < 0.02 and frac < 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r1"), p("r3"))
check(
    "R1!=R3 images differ (transform delta applied)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r4"), p("r5"))
check(
    "R4~R5 images match (rebuilt scene reused)",
    mean < 0.02 and frac < 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r5"), p("r6"))
check(
    "R5!=R6 images differ (cube hidden)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)

if failures:
    print(f"[A6-TEST] FAIL ({len(failures)}): {failures}")
    bpy.ops.wm.quit_blender()
    sys.exit(1)
print("[A6-TEST] PASS")
bpy.ops.wm.quit_blender()
