# Persistent LuxCore scene cache for incremental final-render export
# (A6-II, see doc/incremental_export_design.md).
#
# A rendered pyluxcore.Scene normally dies with its RenderSession. Since
# RenderConfig only *references* the Scene (non-owning constructor), a
# module-level cache can keep it alive across final renders of the same
# Blender scene + view layer. The next render then reuses it instead of
# paying the full first_run export again.
#
# Delta correctness model (conservative — reuse only skips work, never
# correctness):
#
#   * depsgraph_update_post accumulates dirty datablock ids + flags per
#     original scene between renders.
#   * An empty/ignorable dirty set plus an unchanged object-membership
#     set means "nothing visible changed" -> reuse the scene wholesale
#     (camera/world/config are still re-exported every render).
#   * An exported object flagged transform-only gets a cheap
#     UpdateObjectTransformation: instanced exports take the absolute
#     matrix; world-baked exports take new @ old.inverted().
#   * Anything else (geometry/shading updates, added/removed objects,
#     instancer updates, non-object datablocks that affect the render)
#     falls back to a full first_run and rebuilds the cache entry.

import bpy

FLAG_GEOMETRY = 1
FLAG_TRANSFORM = 2
FLAG_SHADING = 4

_KIND_OBJECT = 0
_KIND_IGNORE = 1
_KIND_MATERIAL_ECHO = 2
_KIND_MATERIAL = 3
_KIND_GEOMETRY = 4
_KIND_REBUILD = 5

# Datablock types whose updates do not require a scene rebuild: either
# they are re-exported every render anyway (World, Camera data, Scene
# frame state) or they cannot appear in a render (UI/asset noise).
# Objects are handled separately via the exported-object map.
# Names are resolved dynamically because not every RNA type exists in
# every Blender version.
_IGNORABLE_TYPE_NAMES = (
    "Scene",
    "World",
    "Camera",
    "Collection",
    "Action",
    "Speaker",
    "WorkSpace",
    "Screen",
    "WindowManager",
    "Brush",
    "Palette",
    "PaintCurve",
    "Library",
    "MovieClip",
    "Sound",
    "Text",
    "Mask",
    "FreestyleLineStyle",
)
_IGNORABLE_TYPES = tuple(
    t
    for t in (
        getattr(bpy.types, name, None) for name in _IGNORABLE_TYPE_NAMES
    )
    if t is not None
)

# Object-data datablock types (besides Mesh, which is handled in the
# Mesh/NodeTree branch above) whose geometry updates can be resolved to
# member objects via data_ptrs: hair curves, legacy curves, metaballs,
# volumes and pointclouds. Mesh-bearing types may take the in-place
# DefineMesh delta; the rest are re-exported via delete + re-add.
_GEO_DATA_TYPES = tuple(
    t
    for t in (
        getattr(bpy.types, name, None)
        for name in (
            "Curve",
            "Curves",
            "MetaBall",
            "Volume",
            "PointCloud",
        )
    )
    if t is not None
)

# {original scene pointer: {id pointer: [flags, kind]}}
_dirty = {}

# {(scene pointer, view layer name): cache entry dict}
_entries = {}


def on_depsgraph_update(scene, depsgraph):
    """Accumulate dirty ids. Called from handlers.depsgraph_update_post."""
    if depsgraph is None:
        return
    updates = _dirty.setdefault(scene.as_pointer(), {})
    for update in depsgraph.updates:
        try:
            id_block = update.id
        except Exception:
            continue
        if id_block is None:
            continue
        flags = 0
        if update.is_updated_geometry:
            flags |= FLAG_GEOMETRY
        if update.is_updated_transform:
            flags |= FLAG_TRANSFORM
        if update.is_updated_shading:
            flags |= FLAG_SHADING
        if isinstance(id_block, bpy.types.Object):
            kind = _KIND_OBJECT
        elif isinstance(id_block, _IGNORABLE_TYPES):
            kind = _KIND_IGNORE
        elif isinstance(id_block, bpy.types.Material):
            kind = _KIND_MATERIAL
        elif isinstance(
            id_block, (bpy.types.Mesh, bpy.types.NodeTree)
        ):
            if not flags & ~FLAG_SHADING:
                # Mesh/NodeTree datablocks ride along with material
                # edits (shading-only flags). They are only an *echo*:
                # a world or light node tree looks identical here, so
                # the echo is compatible with a material delta but may
                # never trigger one — classify() rebuilds when no
                # Material datablock accompanies it.
                kind = _KIND_MATERIAL_ECHO
            elif isinstance(id_block, bpy.types.Mesh):
                # A Mesh datablock with geometry flags: classify()
                # resolves it to member objects via geo_meta (no member
                # uses it -> hard rebuild).
                kind = _KIND_GEOMETRY
            else:
                kind = _KIND_REBUILD
        elif isinstance(id_block, _GEO_DATA_TYPES):
            if not flags & ~FLAG_SHADING:
                # Shading-only flag: same material-edit echo as Mesh.
                kind = _KIND_MATERIAL_ECHO
            else:
                # Non-mesh object data (curves, volume, pointcloud):
                # resolve to member objects like Mesh; objects without
                # mesh-delta eligibility take the delete + re-export
                # path instead of a full rebuild.
                kind = _KIND_GEOMETRY
        else:
            kind = _KIND_REBUILD
        # DepsgraphUpdate.id is the *evaluated* datablock: the original
        # pointer is what exported_objects/membership keys use.
        original = getattr(id_block, "original", None)
        ptr = (
            original.as_pointer()
            if original is not None
            else id_block.as_pointer()
        )
        # Merge with the most severe classification seen so far
        # (OBJECT < IGNORE < REBUILD in numeric order, but the default
        # for a first-time id must be OBJECT — starting at IGNORE would
        # swallow OBJECT updates because IGNORE > OBJECT numerically).
        old_flags, old_kind = updates.get(ptr, (0, _KIND_OBJECT))
        updates[ptr] = (old_flags | flags, max(old_kind, kind))


def take_dirty(scene_ptr):
    """Return and clear the accumulated dirty map for a scene."""
    return _dirty.pop(scene_ptr, {})


def get(key):
    return _entries.get(key)


def store(key, luxcore_scene, exported_objects, member_keys,
          bake_matrices, member_mats, mb_sig, camera_sig, world_sig,
          vis_sig, frame, mat_sig, slot_sig, geo_meta, shape_sig,
          data_ptrs):
    _entries[key] = {
        "scene": luxcore_scene,
        # frame at export time: frame_set() moves animated objects
        # without leaving depsgraph updates, so a changed frame forces
        # the per-member frame_change() re-check below
        "frame": frame,
        # matrix_world of member objects not covered by bake_matrices
        # (lights, non-mesh types): frame_change() compares against it
        # to spot transforms it cannot apply as deltas
        "member_mats": member_mats,
        # {obj_key: ExportedObject} for lux object names / delete()
        "objects": dict(exported_objects),
        # base-object key set at export time (add/remove detection)
        "members": member_keys,
        # per-object visibility flags at export time — toggles that
        # change visibility without necessarily dirtying the depsgraph
        # (hide_render etc.) are caught by comparing this snapshot
        "vis": vis_sig,
        # {obj_key: matrix_world} at export time (baked-transform delta)
        "bake": {k: m for k, (m, _safe) in bake_matrices.items()},
        # obj_keys whose transform can be updated in place
        "delta_safe": {k for k, (_m, safe) in bake_matrices.items() if safe},
        # (motion_blur_enabled, steps): Parse cannot remove stale
        # motion.N properties, so reuse requires an identical signature
        "mb_sig": mb_sig,
        # camera spec (minus volatile position/motion keys) and world
        # property string — same reason: Parse cannot delete stale keys
        "camera_sig": camera_sig,
        "world_sig": world_sig,
        # {material ptr: luxcore name} — a rename changes the LuxCore
        # material name, which objects reference, so it must rebuild;
        # {obj_key: ((mat ptr, slot link), ...)} — slot/link edits are
        # object-side definitions a material delta cannot reach
        "mat_sig": mat_sig,
        "slot_sig": slot_sig,
        # {obj_key: (mesh src ptr, mesh_key, use_instancing,
        # base shape names, has wrapper shapes)} — geometry-delta
        # eligibility: DefineMesh replaces a named mesh in place and
        # rewires every object referencing it (incl. triangle lights),
        # but wrapper shapes hold raw source-mesh pointers it cannot
        # fix, so they are excluded here and re-checked at apply time
        "geo_meta": geo_meta,
        # {obj_key: (expected shape chain, wrapper prop string)} —
        # replayed at reuse because material edits can add/remove
        # wrapper shapes (displacement, pointiness...) that neither a
        # material delta nor slot signatures can see
        "shape_sig": shape_sig,
        # {obj_key: original data pointer} for every member with data —
        # dirty object-data datablocks (Mesh, Curves, Volume, ...) are
        # resolved to their member objects through this map
        "data_ptrs": data_ptrs,
    }


def invalidate(key):
    _entries.pop(key, None)


def clear_all():
    _dirty.clear()
    _entries.clear()


def classify(dirty, entry, camera_obj=None):
    """
    Decide how the cached scene can be reused.

    Returns (mode, transform_deltas, material_dirty, geometry_keys):
      mode "full"      — entry unusable, run first_run and rebuild it
      mode "reuse"     — dirty set empty/ignorable + membership intact
      mode "delta"     — reuse + per-object deltas
    transform_deltas is a set of obj_keys needing transform updates,
    geometry_keys a set of obj_keys whose mesh can be re-DefineMesh'ed
    in place (final eligibility — wrappers, shared meshes, submesh
    count — is re-verified at apply time, which falls back to "full"
    on any mismatch); material_dirty asks the exporter to re-export
    every member material in place (material re-definition is
    supported by Scene.Parse).
    """
    if entry is None:
        return "full", set(), False, set()

    camera_ptr = camera_obj.original.as_pointer() if camera_obj else None

    transform_keys = set()
    geometry_keys = set()
    material_dirty = False
    material_echo = False
    for ptr, (flags, kind) in dirty.items():
        if kind == _KIND_IGNORE:
            continue
        if kind == _KIND_MATERIAL:
            # A material content edit: refresh all member materials
            # via Parse re-definition.
            material_dirty = True
            continue
        if kind == _KIND_MATERIAL_ECHO:
            # Mesh/NodeTree shading echo — only safe alongside a real
            # Material update (checked after the loop); world/light
            # node trees produce the same shape and must rebuild.
            material_echo = True
            continue
        if kind == _KIND_GEOMETRY:
            # An object-data datablock changed geometry: resolve to the
            # member objects that source it. Mesh members may take the
            # in-place DefineMesh delta; everything else is re-exported
            # via delete + re-add at apply time. No user -> the change
            # cannot be attributed -> hard rebuild.
            users = {
                key
                for key, dptr in entry["data_ptrs"].items()
                if dptr == ptr
            }
            if not users:
                return "full", set(), False, set()
            geometry_keys |= users
            continue
        if kind != _KIND_OBJECT:
            return "full", set(), False, set()
        if ptr == camera_ptr:
            # The render camera is re-exported every render, so its
            # updates never need a scene delta.
            continue
        key = str(ptr)
        exported = entry["objects"].get(key)
        if flags & ~(FLAG_TRANSFORM | FLAG_SHADING):
            # Geometry on an object: a candidate for an in-place mesh
            # re-definition (verified at apply time). The camera is
            # exempted above; slot reassignment lands here too and is
            # filtered out by the slot_sig check upstream.
            geometry_keys.add(key)
            continue
        if flags & FLAG_SHADING:
            # Object-side shading echo — same trigger rule as
            # Mesh/NodeTree echoes: a material delta only runs when a
            # Material datablock was also dirtied, because object-side
            # shading changes it cannot cover must stay a rebuild.
            material_echo = True
        if flags & FLAG_TRANSFORM:
            if (
                exported is None
                or not hasattr(exported, "transform")
                or exported.duplicate_count > 0
                or key not in entry["delta_safe"]
            ):
                # Not in the exported map (newly added, instancer
                # compound key, unexportable type), a light (no
                # transform path), an instancer/pointcloud (its
                # duplicate set can move too), or a type whose
                # transform cannot be updated in place.
                return "full", set(), False, set()
            transform_keys.add(key)

    if material_echo and not material_dirty:
        # Shading echoes (object/mesh/node-tree) with no Material
        # datablock update: the change is something a material
        # re-export cannot cover (world/light node tree, object-side
        # shading props) — rebuild conservatively.
        return "full", set(), False, set()

    return (
        "delta"
        if transform_keys or material_dirty or geometry_keys
        else "reuse"
    ), transform_keys, material_dirty, geometry_keys


# ------------------------------------------------------------------
# Frame-change handling
#
# depsgraph.updates does NOT report changes driven by frame_set() —
# the depsgraph simply re-evaluates at the new time and every animated
# value moves without a dirty flag. Between animation frames the dirty
# set therefore comes back empty even though objects moved, which is
# exactly the reuse case that must not be trusted. On a frame change
# every member object is re-checked directly instead.

# Object channels that only affect the transform and can therefore be
# applied through a transform delta.
_TRANSFORM_DATA_PATHS = frozenset(
    {
        "location",
        "scale",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "delta_location",
        "delta_scale",
        "delta_rotation_euler",
        "delta_rotation_quaternion",
        "delta_rotation_axis_angle",
    }
)

# Modifier types whose output geometry can change over time even when
# no datablock carries animation (physics sims, deformers, animated
# displacement/wrapping). A NODES modifier is only suspicious when its
# node group actually reads Scene Time.
_GEOMETRY_ANIMATED_MODIFIERS = frozenset(
    {
        "ARMATURE",
        "CAST",
        "CLOTH",
        "CURVE",
        "DISPLACE",
        "DYNAMIC_PAINT",
        "FLUID",
        "HOOK",
        "LATTICE",
        "MESH_DEFORM",
        "OCEAN",
        "PARTICLE_SYSTEM",
        "SHRINKWRAP",
        "SIMPLE_DEFORM",
        "SOFT_BODY",
        "SURFACE_DEFORM",
        "WAVE",
    }
)


def _action_data_paths(action):
    paths = [fc.data_path for fc in getattr(action, "fcurves", ())]
    # Slotted actions (Blender 4.4+): layers -> strips -> channelbags
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                paths.extend(fc.data_path for fc in bag.fcurves)
    return paths


def _animation_kind(obj):
    """
    Classify the original object's own animation:
      "none"      — no animation channels on the object
      "transform" — only object transform channels are animated
      "other"     — anything else (shape/misc channels, unreadable
                    actions): cannot be trusted for a transform delta
    """
    ad = getattr(obj, "animation_data", None)
    if ad is None:
        return "none"
    paths = [fc.data_path for fc in ad.drivers]
    if ad.action is not None:
        paths.extend(_action_data_paths(ad.action))
    if not paths:
        # animation_data exists but exposes no readable channels —
        # cannot prove it is transform-only, so stay conservative.
        return "other"
    base = {p.split("[", 1)[0] for p in paths}
    return "transform" if base <= _TRANSFORM_DATA_PATHS else "other"


def _nodes_uses_scene_time(node_group):
    stack, seen = [node_group], set()
    while stack:
        group = stack.pop()
        if group is None or id(group) in seen:
            continue
        seen.add(id(group))
        for node in group.nodes:
            if "SceneTime" in node.bl_idname:
                return True
            child = getattr(node, "node_tree", None)
            if child is not None:
                stack.append(child)
    return False


def _geometry_animated(obj):
    """Can the evaluated geometry change between frames by itself?"""
    data = getattr(obj, "data", None)
    if getattr(data, "animation_data", None) is not None:
        return True
    if (
        getattr(getattr(data, "shape_keys", None), "animation_data", None)
        is not None
    ):
        return True
    for mod in obj.modifiers:
        if not mod.show_render:
            continue
        if mod.type in _GEOMETRY_ANIMATED_MODIFIERS:
            return True
        if mod.type == "NODES" and _nodes_uses_scene_time(
            getattr(mod, "node_group", None)
        ):
            return True
    return False


def _material_animated(obj):
    """Could any of the object's slot materials change across frames?"""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        if getattr(mat, "animation_data", None) is not None:
            return True
        for tree in (
            getattr(mat, "node_tree", None),
            getattr(getattr(mat, "luxcore", None), "node_tree", None),
        ):
            if getattr(tree, "animation_data", None) is not None:
                return True
    return False


def frame_change(entry, eval_by_key, camera_key):
    """
    Classify a reuse candidate after scene.frame_current changed.

    Returns (rebuild_needed, transform_keys, geometry_keys,
    material_dirty): transform_keys holds the delta-safe member objects
    whose matrix_world differs from the stored export-time matrix
    (animated or silently moved); geometry_keys holds objects whose
    *geometry* is animated — they are re-exported at the current frame
    (in-place mesh replace or delete + re-add); material_dirty asks for
    a member-material refresh when a material is animated. Anything
    animated beyond that — non-transform/non-geometry animation, or a
    transform change on an object that cannot be patched or re-exported
    in place — forces a full rebuild.
    """
    transform_keys = set()
    geometry_keys = set()
    material_dirty = False
    for key, eval_obj in eval_by_key.items():
        if key == camera_key or key not in entry["members"]:
            continue
        original = getattr(eval_obj, "original", None) or eval_obj
        if _animation_kind(original) == "other":
            return True, set(), set(), False
        if _geometry_animated(original):
            # Deforming mesh / hair / point data at a new frame: the
            # object is re-exported below, which also picks up any
            # transform it gained.
            geometry_keys.add(key)
            continue
        if _material_animated(original):
            material_dirty = True
        base = entry["bake"].get(key) or entry["member_mats"].get(key)
        if base is not None and eval_obj.matrix_world != base:
            if key not in entry["delta_safe"]:
                # An unpatchable object moved between frames: a member
                # with an exported entry can be re-exported in place
                # (delete + re-add picks up its new transform too) —
                # except instancers, whose transform also moves their
                # dupli set, which the re-export cannot reach.
                if (
                    key in entry["objects"]
                    and eval_obj.instance_type == "NONE"
                    and not eval_obj.particle_systems
                ):
                    geometry_keys.add(key)
                    continue
                return True, set(), set(), False
            transform_keys.add(key)
    return False, transform_keys, geometry_keys, material_dirty
