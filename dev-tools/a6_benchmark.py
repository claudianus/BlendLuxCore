# A6 persistent-scene export benchmark (see doc/incremental_export_design.md).
#
# Builds a scene heavy enough to make export time meaningful
# (~1M-triangle ground mesh + hundreds of unique and linked mesh
# objects), then measures the export stage for each reuse mode:
#
#   B1  initial render            -> full export baseline
#   B2  unchanged re-render       -> cached scene reuse (~0 export)
#   B3  transform-only edit       -> UpdateObjectTransformation delta
#   B4  mesh edit (bmesh)         -> in-place DefineMesh delta
#   B5  material node edit        -> Scene.Parse material delta
#   B6  object added              -> full export (rebuild) reference
#
# Reports scene.superluxcore.statistics export times per stage plus the
# wall-clock render time. Render time is kept tiny (halt time) so the
# export share dominates the difference.
#
# Run headless:
#   blender --background --python dev-tools/a6_benchmark.py
#
# Tunables via env: A6_BENCH_OBJECTS (unique objects, default 200),
# A6_BENCH_LINKED (linked duplicates, default 200),
# A6_BENCH_GRID (ground grid resolution, default 600 -> ~1.4M tris).

import importlib
import os
import sys
from time import time

import bmesh
import bpy
import mathutils

N_UNIQUE = int(os.environ.get("A6_BENCH_OBJECTS", "200"))
N_LINKED = int(os.environ.get("A6_BENCH_LINKED", "200"))
GRID_RES = int(os.environ.get("A6_BENCH_GRID", "600"))

failures = []


def find_addon_key():
    return next(
        a.module
        for a in bpy.context.preferences.addons
        if "superluxcore" in a.module.lower()
    )


def export_stats(scene):
    stats = scene.superluxcore.statistics.get_active()
    return {
        "export": stats.export_time.value,
        "objects": stats.export_time_objects.value,
        "meshes": stats.export_time_meshes.value,
        "parse": stats.export_time_scene_parse.value,
    }


def render(scene, tag):
    scene.render.filepath = f"/tmp/a6bench_{tag}.png"
    t0 = time()
    bpy.ops.render.render(write_still=True)
    wall = time() - t0
    s = export_stats(scene)
    s["wall"] = wall
    print(
        f"[A6-BENCH] {tag}: export={s['export']:.3f}s "
        f"(objects={s['objects']:.3f}s meshes={s['meshes']:.3f}s "
        f"parse={s['parse']:.3f}s) wall={wall:.2f}s"
    )
    return s


# ---------- scene setup ----------
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)

scene = bpy.context.scene
scene.superluxcore.config.engine = "PATH"
scene.superluxcore.config.device = "OCL"
scene.superluxcore.devices.use_native_cpu = False
scene.superluxcore.halt.enable = True
scene.superluxcore.halt.use_time = True
scene.superluxcore.halt.time = 2
scene.superluxcore.halt.use_samples = False
scene.render.engine = "SUPERLUXCORE"
scene.render.resolution_x = 200
scene.render.resolution_y = 150
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"

prefs = bpy.context.preferences.addons[find_addon_key()].preferences
if hasattr(prefs, "gpu_backend"):
    prefs.gpu_backend = "METAL"

# Heavy ground: a subdivided grid, ~GRID_RES^2 * 2 triangles.
bm = bmesh.new()
bmesh.ops.create_grid(
    bm, x_segments=GRID_RES, y_segments=GRID_RES, size=15.0
)
ground_mesh = bpy.data.meshes.new("Ground")
bm.to_mesh(ground_mesh)
bm.free()
ground = bpy.data.objects.new("Ground", ground_mesh)
scene.collection.objects.link(ground)
ground.location = (0, 0, -1)

mat_ground = bpy.data.materials.new("GroundMat")
mat_ground.use_nodes = True
mat_ground.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.6, 0.6, 0.6, 1)
ground.data.materials.append(mat_ground)

# Unique cubes (own mesh each).
mat_red = bpy.data.materials.new("Red")
mat_red.use_nodes = True
mat_red.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.8, 0.05, 0.05, 1)

unique_objs = []
side = int(N_UNIQUE**0.5) + 1
for i in range(N_UNIQUE):
    me = bpy.data.meshes.new(f"U{i}")
    bmu = bmesh.new()
    bmesh.ops.create_cube(bmu, size=0.4)
    bmu.to_mesh(me)
    bmu.free()
    ob = bpy.data.objects.new(f"U{i}", me)
    scene.collection.objects.link(ob)
    ob.location = (
        (i % side) * 0.6 - 5,
        (i // side) * 0.6 - 5,
        0.1,
    )
    ob.data.materials.append(mat_red)
    unique_objs.append(ob)

# Linked duplicates of one mesh (can_share_mesh -> instancing).
shared_mesh = bpy.data.meshes.new("Shared")
bms = bmesh.new()
bmesh.ops.create_cone(bms, segments=12, radius1=0.25, depth=0.5)
bms.to_mesh(shared_mesh)
bms.free()
for i in range(N_LINKED):
    ob = bpy.data.objects.new(f"L{i}", shared_mesh)
    scene.collection.objects.link(ob)
    ob.location = (
        (i % side) * 0.6 - 5,
        5 + (i // side) * 0.6,
        0.2,
    )
    ob.data.materials.append(mat_ground)

# The mover: unique mesh, drives transform and geometry deltas.
mover = unique_objs[0]

ld = bpy.data.lights.new("Sun", "SUN")
ld.energy = 4
lo = bpy.data.objects.new("Sun", ld)
scene.collection.objects.link(lo)
lo.rotation_euler = (0.6, 0.2, 0.8)

cd = bpy.data.cameras.new("Cam")
co = bpy.data.objects.new("Cam", cd)
scene.collection.objects.link(co)
co.location = (14, -14, 10)
co.rotation_euler = (
    mathutils.Vector((0, 0, 0)) - co.location
).to_track_quat("-Z", "Y").to_euler()
scene.camera = co

w = bpy.data.worlds.new("W")
scene.world = w
w.node_tree.nodes["Background"].inputs[1].default_value = 0.3

persistent_scene = importlib.import_module(
    f"{find_addon_key()}.export.caches.persistent_scene"
)
persistent_scene.clear_all()

tri_count = sum(
    len(o.data.polygons) for o in scene.objects if o.type == "MESH"
)
print(
    f"[A6-BENCH] scene: {len(scene.objects)} objects, "
    f"{tri_count} base triangles"
)

# ---------- B1: full export baseline ----------
b1 = render(scene, "b1_full")
s_b1 = next(iter(persistent_scene._entries.values()))["scene"]

# ---------- B2: unchanged re-render ----------
b2 = render(scene, "b2_reuse")
s_b2 = next(iter(persistent_scene._entries.values()))["scene"]
assert s_b2 is s_b1, "B2: scene was not reused"

# ---------- B3: transform delta ----------
mover.location = (mover.location.x + 1.0, mover.location.y, 0.3)
bpy.context.view_layer.update()
b3 = render(scene, "b3_transform")
s_b3 = next(iter(persistent_scene._entries.values()))["scene"]
assert s_b3 is s_b1, "B3: scene was not reused"

# ---------- B4: geometry delta ----------
bm = bmesh.new()
bm.from_mesh(mover.data)
bmesh.ops.translate(
    bm, vec=mathutils.Vector((0, 0, 0.3)), verts=bm.verts[:4]
)
bm.to_mesh(mover.data)
bm.free()
mover.data.update()
bpy.context.view_layer.update()
b4 = render(scene, "b4_geometry")
s_b4 = next(iter(persistent_scene._entries.values()))["scene"]
assert s_b4 is s_b1, "B4: scene was not reused"

# ---------- B5: material delta ----------
mat_red.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.05, 0.05, 0.8, 1)
bpy.context.view_layer.update()
b5 = render(scene, "b5_material")
s_b5 = next(iter(persistent_scene._entries.values()))["scene"]
assert s_b5 is s_b1, "B5: scene was not reused"

# ---------- B6: object added -> full export ----------
new_me = bpy.data.meshes.new("New")
bmn = bmesh.new()
bmesh.ops.create_uvsphere(bmn, radius=0.5)
bmn.to_mesh(new_me)
bmn.free()
new_ob = bpy.data.objects.new("New", new_me)
scene.collection.objects.link(new_ob)
new_ob.location = (0, 0, 2)
new_ob.data.materials.append(mat_red)
bpy.context.view_layer.update()
b6 = render(scene, "b6_rebuild")
s_b6 = next(iter(persistent_scene._entries.values()))["scene"]
assert s_b6 is not s_b1, "B6: scene was not rebuilt"


def pct(full, delta):
    return 100.0 * delta / full if full > 0 else float("nan")


print("[A6-BENCH] ---- summary ----")
rows = [
    ("B1 full export", b1),
    ("B2 reuse (unchanged)", b2),
    ("B3 transform delta", b3),
    ("B4 geometry delta", b4),
    ("B5 material delta", b5),
    ("B6 full rebuild", b6),
]
print(f"{'stage':<24} {'export s':>9} {'objects':>8} "
      f"{'meshes':>8} {'parse':>8} {'wall':>7} {'%of full':>9}")
for name, s in rows:
    print(
        f"{name:<24} {s['export']:>9.3f} {s['objects']:>8.3f} "
        f"{s['meshes']:>8.3f} {s['parse']:>8.3f} {s['wall']:>7.2f} "
        f"{pct(b1['export'], s['export']):>8.1f}%"
    )

print("[A6-BENCH] DONE")
bpy.ops.wm.quit_blender()
sys.exit(0)
