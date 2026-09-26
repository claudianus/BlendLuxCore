# A6-II persistent-scene regression test (see doc/incremental_export_design.md).
#
# Exercises the final-render incremental export lifecycle end to end:
#   R1  first render            -> full export, scene cached
#   R2  unchanged re-render     -> cached pysuperluxcore.Scene reused as-is
#   R3  object moved            -> transform-only delta on the same Scene
#   R4  mesh edited via bmesh   -> in-place mesh delta (same Scene)
#   R5  unchanged re-render     -> new cache entry reused
#   R6  hide_render toggled     -> visibility signature mismatch, rebuild
#   R7  camera moved            -> same Scene reused (camera re-exported)
#   R8  DoF toggled             -> camera signature mismatch, rebuild
#   R9  world color changed     -> world signature mismatch, rebuild
#   F15/F30 frame_set()         -> keyframed transform arrives via
#                                  delta (depsgraph reports nothing
#                                  on frame changes — see frame_change)
#   M1  material node edit      -> in-place material delta (same Scene)
#   M2  material renamed        -> rebuild (SuperLuxCore name changes)
#   M3  slot reassigned         -> rebuild (geometry flags)
#   M4  driver-animated color   -> frame change keeps Scene, refreshes
#   M5  displacement node added -> shape signature mismatch, rebuild
#   C1  curve data edit         -> in-place mesh delta (self-instanced)
#   F70 shape-key frame change  -> geometry delta (deforming mesh)
#   F80/F90 keyframed light     -> delete + re-export delta (light)
#   H1  hair-curves data edit   -> re-export delta (DefineStrands)
#   I1  instancer moved         -> dupli set re-flush (same Scene)
#   I2  dupli-source mesh edit  -> in-place "_instance" mesh redefine
#   P1  particle settings edit  -> psys_map re-flush (same Scene)
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/a6_persistent_scene_test.py
#
# Requires the SuperLuxCore addon (with pysuperluxcore) installed. Exits 0 on
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
        if "superluxcore" in a.module.lower()
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
scene.superluxcore.config.engine = "PATH"
scene.superluxcore.config.device = "OCL"
scene.superluxcore.devices.use_native_cpu = False
scene.superluxcore.halt.enable = True
scene.superluxcore.halt.use_time = True
scene.superluxcore.halt.time = 6
scene.superluxcore.halt.use_samples = False
scene.render.engine = "SUPERLUXCORE"
scene.render.resolution_x = 320
scene.render.resolution_y = 240
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = False
scene.frame_start = 1
scene.frame_end = 30

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

# Curve object — non-MESH member, so a data edit must take the
# delete + re-export geometry delta path (no in-place DefineMesh).
bpy.ops.curve.primitive_bezier_circle_add(
    radius=0.9,
    location=(-0.6, -0.2, 1.2),
)
curve_obj = bpy.context.object
curve_obj.data.dimensions = "2D"
curve_obj.data.fill_mode = "BOTH"

# Shape-keyed cube — geometry animation between f60/f70 exercises the
# frame_change -> geometry delta path (deforming mesh re-export).
bpy.ops.mesh.primitive_cube_add(size=0.8, location=(2.2, -1.5, -0.6))
skcube = bpy.context.object
skcube.shape_key_add(name="Basis")
sk = skcube.shape_key_add(name="Key1")
for i, v in enumerate(sk.data):
    v.co.z += 0.9 if i % 2 else -0.2
sk.value = 0.0
sk.keyframe_insert("value", frame=60)
sk.value = 1.0
sk.keyframe_insert("value", frame=70)
sk.value = 0.0

# Keyframed sun — a non-delta-safe member moving between frames takes
# the delete + re-export path (light re-definition via Parse).
lo.keyframe_insert("rotation_euler", frame=80)
lo.rotation_euler = (0.9, 0.1, 0.5)
lo.keyframe_insert("rotation_euler", frame=90)
lo.rotation_euler = (0.6, 0.2, 0.8)

# Hair-curves object — its Curves data block resolves through
# data_ptrs and re-exports via DefineStrands in place.
bpy.context.view_layer.objects.active = skcube
skcube.select_set(True)
try:
    bpy.ops.object.quick_fur()
    hair_obj = bpy.context.object
    hair_obj.hide_render = False
except Exception as e:
    print(f"[A6-TEST] quick_fur unavailable, hair stage skipped: {e}")
    hair_obj = None
bpy.ops.object.select_all(action="DESELECT")

# VERTS-dupli instancer — moving the emitter re-flushes the source's
# dupli set (delete + re-DuplicateObject) instead of rebuilding. The
# cluster floats above the ground plane so its instances stay clearly
# visible against the backdrop.
bpy.ops.mesh.primitive_ico_sphere_add(
    radius=0.35, location=(0.3, 0.8, 0.65)
)
dupli_src = bpy.context.object
bpy.ops.mesh.primitive_grid_add(
    x_subdivisions=4,
    y_subdivisions=4,
    size=1.8,
    location=(0.3, 0.8, 0.8),
)
emitter = bpy.context.object
emitter.instance_type = "VERTS"
dupli_src.parent = emitter

# Particle instancer — a ParticleSettings edit re-flushes the emitter's
# dupli set via psys_map (settings ptr -> instancer key). Static
# particles (physics NO, lifetime spanning the whole test) keep the
# image stable across the frame changes above.
bpy.ops.mesh.primitive_ico_sphere_add(
    radius=0.12, location=(0, 0, 5)
)
psys_src = bpy.context.object
psys_src.hide_render = True
bpy.ops.mesh.primitive_plane_add(
    size=1.2, location=(-2.0, -1.5, -0.55)
)
psys_emitter = bpy.context.object
bpy.ops.object.select_all(action="DESELECT")
psys_emitter.select_set(True)
bpy.context.view_layer.objects.active = psys_emitter
bpy.ops.object.particle_system_add()
psys_settings = psys_emitter.particle_systems[-1].settings
psys_settings.count = 20
psys_settings.frame_start = 1
psys_settings.frame_end = 1
psys_settings.lifetime = 500
psys_settings.physics_type = "NO"
psys_settings.render_type = "OBJECT"
psys_settings.instance_object = psys_src
bpy.ops.object.select_all(action="DESELECT")

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
    "R2: same pysuperluxcore.Scene reused",
    entry["scene"] is scene_r1,
)

# ---------- R3: transform-only update ----------
cube.location = (1.2, 0.5, 0.2)
bpy.context.view_layer.update()
render("r3")
entry = entry_of(persistent_scene)
check(
    "R3: same pysuperluxcore.Scene after transform delta",
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

# ---------- R4: geometry edit -> in-place mesh delta -----------------
# A bmesh edit dirties the Mesh datablock (+object geometry flag):
# DefineMesh replaces the named shapes in place, so the same Scene
# survives with updated geometry.
bm = bmesh.new()
bm.from_mesh(cube.data)
bmesh.ops.translate(
    bm, vec=mathutils.Vector((0, 0, 0.9)), verts=bm.verts[:4]
)
bm.to_mesh(cube.data)
bm.free()
cube.data.update()
bpy.context.view_layer.update()
render("r4")
entry = entry_of(persistent_scene)
scene_r4 = entry["scene"]
check(
    "R4: geometry edit applied in-place mesh delta",
    scene_r4 is scene_r1,
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
scene_r6 = entry["scene"]
check(
    "R6: hide_render toggle rebuilt the scene",
    scene_r6 is not scene_r4,
)

# ---------- R7: camera move reuses the scene ----------
# (camera is re-exported every render; only its datablock props are
# part of the reuse signature, not the volatile transform)
co.location = (-6, -2, 5)
co.rotation_euler = (
    mathutils.Vector((0, 0, 0.3)) - co.location
).to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()
render("r7")
entry = entry_of(persistent_scene)
scene_r7 = entry["scene"]
check(
    "R7: camera move keeps the cached scene",
    scene_r7 is scene_r6,
)

# ---------- R8: DoF toggle -> camera signature rebuild ----------
cd.dof.use_dof = True
cd.dof.focus_object = plane
cd.dof.aperture_fstop = 1.4
bpy.context.view_layer.update()
render("r8")
entry = entry_of(persistent_scene)
scene_r8 = entry["scene"]
check(
    "R8: DoF toggle rebuilt the scene (stale props unsafe)",
    scene_r8 is not scene_r7,
)

# ---------- R9: world change -> world signature rebuild ----------
w.node_tree.nodes["Background"].inputs[0].default_value = (
    0.05, 0.02, 0.02, 1,
)
bpy.context.view_layer.update()
render("r9")
entry = entry_of(persistent_scene)
scene_r9 = entry["scene"]
check(
    "R9: world change rebuilt the scene",
    scene_r9 is not scene_r8,
)

# ---------- F15/F30: frame_set moves animated transform via delta ------
# depsgraph reports no updates on frame changes; frame_change() must
# catch the moved keyframed object and patch it as a transform delta.
cube.hide_render = False
scene.frame_set(1)
cube.keyframe_insert("location", frame=1)
cube.location = (1.5, 0.5, 0.6)
cube.keyframe_insert("location", frame=30)

scene.frame_set(15)
bpy.context.view_layer.update()
render("f15")
entry = entry_of(persistent_scene)
scene_f15 = entry["scene"]
check(
    "F15: unhide + frame change rebuilt entry",
    scene_f15 is not scene_r9,
)

scene.frame_set(30)
bpy.context.view_layer.update()
render("f30")
entry = entry_of(persistent_scene)
check(
    "F30: frame change applied transform delta on same scene",
    entry["scene"] is scene_f15,
)
bake = entry["bake"].get(mover_key)
check(
    "F30: bake matrix tracks animated position",
    bake is not None
    and all(
        abs(a - b) < 1e-5
        for row_a, row_b in zip(bake, cube.matrix_world)
        for a, b in zip(row_a, row_b)
    ),
)

# ---------- M1: material node edit -> in-place material delta --------
# Material datablock + Mesh/NodeTree shading echoes -> all member
# materials are re-exported into the same cached scene (Parse
# re-definition), while identity/slot signatures stay intact.
mat2.node_tree.nodes["Principled BSDF"].inputs[
    "Base Color"
].default_value = (0.05, 0.05, 0.8, 1)
bpy.context.view_layer.update()
render("m1")
entry = entry_of(persistent_scene)
check(
    "M1: material edit reused scene via material delta",
    entry["scene"] is scene_f15,
)

# ---------- M2: material rename -> rebuild (SuperLuxCore name changes) -----
mat2.name = "Renamed"
bpy.context.view_layer.update()
render("m2")
entry = entry_of(persistent_scene)
scene_m2 = entry["scene"]
check(
    "M2: material rename rebuilt the scene",
    scene_m2 is not scene_f15,
)

# ---------- M3: slot reassignment -> rebuild ---------------------------
# Slot swaps flag geometry on object+mesh, so they can never reach the
# material-delta path.
cube.data.materials[0] = mat
bpy.context.view_layer.update()
render("m3")
entry = entry_of(persistent_scene)
scene_m3 = entry["scene"]
check(
    "M3: slot reassignment rebuilt the scene",
    scene_m3 is not scene_m2,
)

# ---------- M4: animated material across a frame change ---------------
# A driver on the Ground material's node tree must mark the entry
# material-dirty at frame_change(): same scene, refreshed materials.
# The cube's transform animation already finished at f30, so the f50
# image change is purely shading.
sock = mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"]
fc = sock.driver_add("default_value", 0)
fc.driver.expression = "frame / 50"
scene.frame_set(50)
bpy.context.view_layer.update()
render("f50")
entry = entry_of(persistent_scene)
check(
    "M4: animated material kept scene on frame change",
    entry["scene"] is scene_m3,
)

# ---------- M5: displacement added -> shape signature rebuild ---------
# A Displacement output link on the *assigned* material means the
# object's shape needs a wrapper shape (scene.shapes.*_disp*) that a
# material delta cannot create — the replayed shape signature must
# catch it and rebuild. (mat is assigned to both cube and plane; the
# renamed mat2 is unreferenced since M3, so it must be mat here.)
out_node = mat.node_tree.nodes["Material Output"]
disp_node = mat.node_tree.nodes.new("ShaderNodeDisplacement")
disp_node.inputs["Scale"].default_value = 0.3
noise_node = mat.node_tree.nodes.new("ShaderNodeTexNoise")
mat.node_tree.links.new(
    noise_node.outputs["Fac"], disp_node.inputs["Height"]
)
mat.node_tree.links.new(
    disp_node.outputs["Displacement"],
    out_node.inputs["Displacement"],
)
bpy.context.view_layer.update()
render("m5")
entry = entry_of(persistent_scene)
scene_m5 = entry["scene"]
check(
    "M5: displacement node rebuilt the scene",
    scene_m5 is not scene_m3,
)

# ---------- C1: curve data edit -> geometry delta -------------------
# A Curve datablock edit resolves through data_ptrs to the member
# object. The curve is also a dupli source (Blender 5.2 reports it
# instanced under itself), so its instanced and standalone meshes are
# both re-defined in place via DefineMesh — same Scene kept.
curve_key = str(curve_obj.original.as_pointer())
old_curve_exported = entry["objects"].get(curve_key)
check(
    "C1: curve object was exported",
    old_curve_exported is not None,
)
for spline in curve_obj.data.splines:
    for bp in spline.bezier_points:
        bp.co.x *= 1.6
        bp.co.y *= 1.6
bpy.context.view_layer.update()
render("c1")
entry = entry_of(persistent_scene)
scene_c1 = entry["scene"]
check(
    "C1: curve edit kept scene via delta",
    scene_c1 is scene_m5,
)
check(
    "C1: curve export still registered",
    entry["objects"].get(curve_key) is not None,
)

# ---------- F60/F70: deforming mesh frame delta -----------------------
# The shape-keyed cube is geometry-animated: frame_change() routes it
# to the geometry delta (in-place mesh re-export at the new frame)
# instead of a full rebuild. (The in-place path keeps the same
# ExportedObject — the image comparison below proves the new shape.)
scene.frame_set(70)
bpy.context.view_layer.update()
render("f70")
entry = entry_of(persistent_scene)
scene_f70 = entry["scene"]
check(
    "F70: deforming mesh kept scene via geometry delta",
    scene_f70 is scene_c1,
)

# ---------- L80/L90: animated light frame delta -----------------------
# The keyframed sun is not transform-delta-safe (lights are defined by
# props, not object transforms): frame_change() re-exports it via
# delete + re-add instead of rebuilding.
lo_key = str(lo.original.as_pointer())
old_lo_exported = entry["objects"].get(lo_key)
scene.frame_set(80)
bpy.context.view_layer.update()
render("f80")
entry = entry_of(persistent_scene)
check(
    "L80: frame change reused scene (light at rest pose)",
    entry["scene"] is scene_f70,
)
scene.frame_set(90)
bpy.context.view_layer.update()
render("f90")
entry = entry_of(persistent_scene)
scene_f90 = entry["scene"]
check(
    "L90: animated light kept scene via re-export delta",
    scene_f90 is scene_f70,
)
check(
    "L90: light re-exported (new ExportedLight)",
    entry["objects"].get(lo_key) is not None
    and entry["objects"][lo_key] is not old_lo_exported,
)

# ---------- H1: hair-curves data edit -> re-export delta --------------
if hair_obj is not None:
    hair_key = str(hair_obj.original.as_pointer())
    old_hair_exported = entry["objects"].get(hair_key)
    check(
        "H1: hair-curves object was exported",
        old_hair_exported is not None,
    )
    hair_obj.data.update_tag()
    bpy.context.view_layer.update()
    render("h1")
    entry = entry_of(persistent_scene)
    scene_h1 = entry["scene"]
    check(
        "H1: curves data edit kept scene via re-export delta",
        scene_h1 is scene_f90,
    )
    check(
        "H1: hair-curves object re-exported",
        entry["objects"].get(hair_key) is not None
        and entry["objects"][hair_key] is not old_hair_exported,
    )
    scene_last = scene_h1
    tag_last = "h1"
else:
    scene_last = scene_f90
    tag_last = "f90"

# ---------- I1: instancer moved -> dupli set re-flush -----------------
# Moving the VERTS emitter shifts every instance matrix: the source's
# "dupli" scene object is deleted + re-DuplicateObject'ed (dupli
# re-flush) while the emitter's own transform takes the normal delta.
emitter.location = (0.8, -0.3, 1.3)
bpy.context.view_layer.update()
render("i1")
entry = entry_of(persistent_scene)
scene_i1 = entry["scene"]
check(
    "I1: instancer move kept scene via dupli re-flush",
    scene_i1 is scene_last,
)

# ---------- I2: dupli-source mesh edit -> in-place redefine ----------
# Editing the instanced mesh redefines the source's "_instance" mesh in
# place — the dupli set picks it up without a rebuild (DefineMesh
# rewires the dupli base and all duplicates).
bm = bmesh.new()
bm.from_mesh(dupli_src.data)
bmesh.ops.scale(bm, vec=(3.0, 3.0, 3.0), verts=bm.verts)
bm.to_mesh(dupli_src.data)
bm.free()
bpy.context.view_layer.update()
render("i2")
entry = entry_of(persistent_scene)
scene_i2 = entry["scene"]
check(
    "I2: dupli-source mesh edit kept scene",
    scene_i2 is scene_i1,
)

# ---------- P1: particle settings -> instancer re-flush -------------
# A ParticleSettings datablock edit resolves through psys_map to the
# emitter: the emitter takes a geometry delta and its dupli set is
# re-flushed with the new particle count — same Scene kept.
scene.frame_set(1)
psys_settings.count = 45
bpy.context.view_layer.update()
render("p1")
entry = entry_of(persistent_scene)
scene_p1 = entry["scene"]
check(
    "P1: particle settings edit kept scene",
    scene_p1 is scene_i2,
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
mean, frac = image_stats(p("r3"), p("r4"))
check(
    "R3!=R4 images differ (geometry delta applied)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r4"), p("r5"))
check(
    "R4~R5 images match (delta'd scene reused)",
    mean < 0.02 and frac < 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r5"), p("r6"))
check(
    "R5!=R6 images differ (cube hidden)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("r6"), p("r7"))
check(
    "R6!=R7 images differ (camera moved)",
    # Cube is hidden here, so the frame is a near-featureless plane:
    # a large camera move shifts shading subtly (noise floor ~0.002).
    mean > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("f15"), p("f30"))
check(
    "F15!=F30 images differ (animated cube moved)",
    # The curve disk occludes part of the mover's path and the
    # instancer cluster adds static pixels, so the signal sits below
    # the other stages' noise level.
    mean > 0.012 and frac > 0.02,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("f30"), p("m1"))
check(
    "F30!=M1 images differ (material delta applied)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("f30"), p("f50"))
check(
    "F30!=F50 images differ (animated material applied)",
    mean > 0.02 and frac > 0.05,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("m5"), p("c1"))
check(
    "M5!=C1 images differ (curve re-export applied)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("c1"), p("f70"))
check(
    "C1!=F70 images differ (deformed mesh delta applied)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("f80"), p("f90"))
check(
    "F80!=F90 images differ (animated light re-exported)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p(tag_last), p("i1"))
check(
    "last!=I1 images differ (dupli set re-flushed)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("i1"), p("i2"))
check(
    "I1!=I2 images differ (instanced mesh redefined)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)
mean, frac = image_stats(p("i2"), p("p1"))
check(
    "I2!=P1 images differ (particle count re-flushed)",
    mean > 0.005 and frac > 0.005,
    f"mean={mean:.4f} changed={frac:.3f}",
)

if failures:
    print(f"[A6-TEST] FAIL ({len(failures)}): {failures}")
    bpy.ops.wm.quit_blender()
    sys.exit(1)
print("[A6-TEST] PASS")
bpy.ops.wm.quit_blender()
