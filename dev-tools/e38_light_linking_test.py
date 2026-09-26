# SPDX-License-Identifier: Apache-2.0
#
# E2E: Cycles light linking -> SuperLuxCore linkgroups.
#
# Covers three behaviors in one session:
#   1. Export plan: a light's receiver_collection resolves to a stable
#      named link group; members appear in obj_groups, non-members do
#      not (their linkAcceptMask stays 0, rejecting linked lights -
#      same as Cycles). Blocker collections warn (shadow linking
#      unsupported).
#   2. Render gate: with a black world, a light linked to cube A must
#      leave cube B and the ground dark (only bounce spill), while A
#      is lit.
#   3. Persistent-scene invalidation: removing the receiver membership
#      and re-rendering must produce a different image (signature
#      change), not a stale cached scene.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/e38_light_linking_test.py
#
# Exits 0 on PASS, 1 on FAIL.

import os
import sys
import importlib

import bpy
import numpy as np
from mathutils import Vector

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

OUT_DIR = os.environ.get("E38_TEST_OUT", "/tmp")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"[E38-TEST] {'ok' if ok else 'FAIL'}: {name} {detail}", flush=True)


def ensure_superluxcore():
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
        return "already-registered"
    except TypeError:
        bpy.ops.preferences.addon_enable(module="superluxcore")
        bpy.context.scene.render.engine = "SUPERLUXCORE"
        return "enabled"


def addon_module():
    return next(
        a.module for a in bpy.context.preferences.addons
        if "superluxcore" in a.module.lower()
    )


def reset_scene():
    scene = bpy.context.scene
    scene.world = None
    scene.camera = None
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.lights, bpy.data.cameras, bpy.data.worlds,
                 bpy.data.images, bpy.data.collections):
        for item in list(coll):
            if item.name in ("Render Result", "Collection"):
                continue
            try:
                coll.remove(item)
            except Exception:
                pass
    return scene


def make_cube(name, loc, size=1.4):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    obj = bpy.context.active_object
    obj.name = name
    mat = bpy.data.materials.new(name + "_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes["Principled BSDF"].inputs["Base Color"].default_value = (
        0.8, 0.8, 0.8, 1)
    obj.data.materials.append(mat)
    return obj


def build_linking_scene():
    """Light linked to LitCube only; DarkCube + ground are non-members."""
    scene = reset_scene()

    c1 = make_cube("LitCube", (-1.6, 0, 0))
    c2 = make_cube("DarkCube", (1.6, 0, 0))
    ground = make_cube("Ground", (0, 0, -1.2), size=1)
    ground.scale = (8, 8, 0.15)

    ld = bpy.data.lights.new("LinkedL_d", "POINT")
    ld.energy = 80
    ld.superluxcore.use_cycles_settings = True
    light = bpy.data.objects.new("LinkedL", ld)
    scene.collection.objects.link(light)
    light.location = (0, 0, 4)

    receivers = bpy.data.collections.new("RC")
    receivers.objects.link(c1)
    light.light_linking.receiver_collection = receivers

    # A blocker collection triggers the unsupported-feature warning.
    blockers = bpy.data.collections.new("BC")
    blockers.objects.link(c2)
    light.light_linking.blocker_collection = blockers

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0, -9, 3.5)
    cam.rotation_euler = (Vector((0, 0, 0)) - cam.location
                          ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.0

    return scene, c1, c2, light, receivers


def build_instance_link_scene():
    """Linked receiver inside an instanced collection.

    Two empties instance the same collection: one member is exported
    as the base object, the second goes through engine-side
    DuplicateObject - which must preserve the link masks.
    """
    scene = reset_scene()

    inst_coll = bpy.data.collections.new("IC")  # NOT linked to scene
    member = make_cube("InstMember", (0, 0, 0))
    # Move the member out of the scene collection into IC only.
    for c in list(member.users_collection):
        c.objects.unlink(member)
    inst_coll.objects.link(member)

    e1 = bpy.data.objects.new("InstE1", None)
    e1.instance_type = "COLLECTION"
    e1.instance_collection = inst_coll
    e1.location = (-1.6, 0, 0)
    scene.collection.objects.link(e1)
    e2 = bpy.data.objects.new("InstE2", None)
    e2.instance_type = "COLLECTION"
    e2.instance_collection = inst_coll
    e2.location = (1.6, 0, 0)
    scene.collection.objects.link(e2)

    ground = make_cube("Ground", (0, 0, -1.2), size=1)
    ground.scale = (8, 8, 0.15)

    ld = bpy.data.lights.new("LinkedL_d", "POINT")
    ld.energy = 80
    ld.superluxcore.use_cycles_settings = True
    light = bpy.data.objects.new("LinkedL", ld)
    scene.collection.objects.link(light)
    light.location = (0, 0, 4)

    receivers = bpy.data.collections.new("RC")
    receivers.objects.link(member)
    light.light_linking.receiver_collection = receivers

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0, -9, 3.5)
    cam.rotation_euler = (Vector((0, 0, 0)) - cam.location
                          ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.0

    return scene, member


def build_collection_flag_scene():
    """LayerCollection holdout/indirect_only + area light spread."""
    scene = reset_scene()

    normal_coll = scene.collection
    holdout_coll = bpy.data.collections.new("HC")
    indirect_coll = bpy.data.collections.new("IOC")
    scene.collection.children.link(holdout_coll)
    scene.collection.children.link(indirect_coll)

    nrm = make_cube("NormalCube", (-2.2, 0, 0))
    hold = make_cube("HoldCube", (0, 0, 0))
    ind = make_cube("IndirectCube", (2.2, 0, 0))
    for c in list(hold.users_collection):
        c.objects.unlink(hold)
    holdout_coll.objects.link(hold)
    for c in list(ind.users_collection):
        c.objects.unlink(ind)
    indirect_coll.objects.link(ind)

    vlc = scene.view_layers[0].layer_collection
    vlc.children["HC"].holdout = True
    vlc.children["IOC"].indirect_only = True

    ld = bpy.data.lights.new("AreaL_d", "AREA")
    ld.energy = 200
    ld.shape = "DISK"
    ld.size = 3.0
    ld.spread = 1.0  # directional limit - unsupported, must warn
    ld.superluxcore.use_cycles_settings = True
    light = bpy.data.objects.new("AreaL", ld)
    scene.collection.objects.link(light)
    light.location = (0, 0, 5)

    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = (0, -9, 2.5)
    cam.rotation_euler = (Vector((0, 0, 0)) - cam.location
                          ).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    world = bpy.data.worlds.new("w")
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (
        0.05, 0.08, 0.15, 1.0)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0

    # ALPHA AOV needed for the holdout alpha-hole check (off by default).
    scene.view_layers[0].superluxcore.aovs.alpha = True

    return scene, nrm


def render_to(path, res_x, res_y, samples):
    scene = bpy.context.scene
    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = res_x
    scene.render.resolution_y = res_y
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    if scene.camera:
        scene.camera.data.superluxcore.imagepipeline.tonemapper.enabled = False
    halt = scene.superluxcore.halt
    halt.enable = True
    halt.use_time = False
    halt.use_samples = True
    halt.samples = samples
    halt.use_noise_thresh = False
    # The auto-clamp suggestion from a previous render would confound the
    # linking comparison (see e23 for the same trap).
    path_cfg = scene.superluxcore.config.path
    path_cfg.use_clamping = False
    path_cfg.auto_clamping = False
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    arr = np.asarray(img.pixels[:], dtype=np.float64)
    bpy.data.images.remove(img)
    return arr.reshape(res_y, res_x, 4)[:, :, :3]


def main():
    print("[E38-TEST] addon:", ensure_superluxcore())
    mod = addon_module()
    compat = importlib.import_module(mod + ".export.cycles_compat")
    utils = importlib.import_module(mod + ".utils")

    scene, c1, c2, light, receivers = build_linking_scene()

    # --- 1. Export plan ----------------------------------------------------
    dg = bpy.context.evaluated_depsgraph_get()
    obj_groups, emitter_groups = compat.light_link_plan(scene, dg, set())

    group = emitter_groups.get(utils.make_key(light))
    check("emitter group assigned",
          group is not None and group.startswith("ll_"),
          f"group={group}")

    c1_groups = obj_groups.get(utils.make_key(c1))
    check("receiver member accepts emitter group",
          c1_groups is not None and group in c1_groups,
          f"c1={c1_groups}")

    # Non-members are absent from obj_groups: linkAcceptMask stays 0,
    # which rejects all group-masked lights (Cycles semantics).
    check("non-member absent from accept plan",
          utils.make_key(c2) not in obj_groups,
          f"c2={obj_groups.get(utils.make_key(c2))}")

    # --- 2. Render gate ----------------------------------------------------
    path_a = os.path.join(OUT_DIR, "e38_linked.exr")
    img_a = render_to(path_a, 640, 360, 96)
    h, w = img_a.shape[:2]
    # EXR pixels are bottom-up: the lit top faces form a band around
    # 60-75% height; left cube ~x 0.1-0.4, right cube ~x 0.6-0.9.
    band = (int(h * 0.58), int(h * 0.75))
    lit = img_a[band[0]:band[1], int(w * 0.1):int(w * 0.4)].mean()
    dark = img_a[band[0]:band[1], int(w * 0.6):int(w * 0.9)].mean()
    check("linked cube lit", lit > 0.005, f"lit={lit:.4f}")
    check("unlinked cube dark", dark < lit * 0.35,
          f"dark={dark:.4f} lit={lit:.4f}")

    # --- 3. Persistent-scene invalidation ----------------------------------
    receivers.objects.unlink(c1)
    path_b = os.path.join(OUT_DIR, "e38_unlinked.exr")
    img_b = render_to(path_b, 640, 360, 96)
    diff = np.abs(img_b - img_a).mean()
    check("scene invalidates on membership change", diff > 1e-4,
          f"mean|diff|={diff:.5f}")
    dark_b = img_b[band[0]:band[1], int(w * 0.6):int(w * 0.9)].mean()
    check("unlinked render lights former dark cube", dark_b > 0.005,
          f"dark_b={dark_b:.4f}")

    # --- blocker warning ----------------------------------------------------
    errlog = importlib.import_module(mod + ".utils.errorlog")
    warns = [w.message for w in errlog.SuperLuxCoreErrorLog.warnings]
    check("blocker linking warning logged",
          any("blocker" in m.lower() or "shadow" in m.lower()
              for m in warns),
          f"warnings={len(warns)}")

    # --- 4. Instanced linked receiver ---------------------------------------
    # The second instancer's copy is created by engine DuplicateObject -
    # regression for link masks being dropped on duplication.
    scene, member = build_instance_link_scene()
    dg = bpy.context.evaluated_depsgraph_get()
    obj_groups, _eg = compat.light_link_plan(scene, dg, set())
    check("instanced member in accept plan",
          utils.make_key(member) in obj_groups,
          f"plan={obj_groups.get(utils.make_key(member))}")

    path_i = os.path.join(OUT_DIR, "e38_instance.exr")
    img_i = render_to(path_i, 640, 360, 96)
    h, w = img_i.shape[:2]
    band = (int(h * 0.55), int(h * 0.75))
    inst_a = img_i[band[0]:band[1], int(w * 0.1):int(w * 0.4)].mean()
    inst_b = img_i[band[0]:band[1], int(w * 0.6):int(w * 0.9)].mean()
    check("base linked instance lit", inst_a > 0.005, f"a={inst_a:.4f}")
    check("dupli linked instance lit", inst_b > 0.005,
          f"b={inst_b:.4f}")

    # --- 5. View-layer collection flags -------------------------------------
    scene, nrm = build_collection_flag_scene()
    path_f = os.path.join(OUT_DIR, "e38_collflags.exr")
    img_f = render_to(path_f, 640, 360, 96)
    h, w = img_f.shape[:2]
    band = (int(h * 0.55), int(h * 0.8))
    x_nrm = slice(int(w * 0.08), int(w * 0.35))
    x_hold = slice(int(w * 0.38), int(w * 0.62))
    x_ind = slice(int(w * 0.65), int(w * 0.92))
    rgb_hold = img_f[band[0]:band[1], x_hold, :3].mean()
    rgb_ind = img_f[band[0]:band[1], x_ind, :3].mean()
    rgb_nrm = img_f[band[0]:band[1], x_nrm, :3].mean()
    bg_mean = (0.05 + 0.08 + 0.15) / 3
    check("holdout radiance cleared", rgb_hold < rgb_nrm * 0.5,
          f"rgb_hold={rgb_hold:.4f} rgb_nrm={rgb_nrm:.4f}")
    # Camera-invisible: the pixel shows the world background.
    check("indirect-only cube camera-invisible",
          abs(rgb_ind - bg_mean) < 0.02,
          f"rgb_ind={rgb_ind:.4f} bg={bg_mean:.4f}")

    # The alpha hole rides on RGBA_IMAGEPIPELINE, emitted with a
    # transparent film (the engine's ALPHA AOV is a separate pass,
    # not combined alpha).
    pipe = scene.camera.data.superluxcore.imagepipeline
    pipe.transparent_film = True
    path_t = os.path.join(OUT_DIR, "e38_collflags_t.exr")
    render_to(path_t, 640, 360, 96)
    img = bpy.data.images.load(path_t)
    rgba = np.asarray(img.pixels[:], dtype=np.float64).reshape(h, w, 4)
    bpy.data.images.remove(img)
    a_hold = rgba[band[0]:band[1], x_hold, 3].mean()
    # Cube pixels are opaque (1); the region mean mixes in the now-
    # transparent background, so assert on the max instead.
    a_nrm = rgba[band[0]:band[1], x_nrm, 3].max()
    check("collection holdout cuts alpha", a_hold < 0.1,
          f"a_hold={a_hold:.3f} a_nrm={a_nrm:.3f}")
    check("normal cube opaque", a_nrm > 0.9, f"a_nrm={a_nrm:.3f}")
    pipe.transparent_film = False

    warns = [w.message for w in errlog.SuperLuxCoreErrorLog.warnings]
    check("area light spread warning logged",
          any("spread" in m.lower() for m in warns),
          f"warnings={len(warns)}")

    failed = [n for n, ok in RESULTS if not ok]
    print(f"[E38-TEST] {len(RESULTS) - len(failed)}/{len(RESULTS)} "
          "checks passed")
    if failed:
        print("[E38-TEST] FAILURES:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
