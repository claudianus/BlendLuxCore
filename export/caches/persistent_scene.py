# Persistent SuperLuxCore scene cache for incremental final-render export
# (A6-II, see doc/incremental_export_design.md).
#
# A rendered pysuperluxcore.Scene normally dies with its RenderSession. Since
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
_KIND_PARTICLES = 5
_KIND_REBUILD = 6

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
        elif isinstance(
            id_block, getattr(bpy.types, "ParticleSettings", ())
        ):
            # A ParticleSettings datablock dirties every instancer
            # using it — classify() resolves it to member instancers
            # via psys_map and refreshes their dupli sets.
            kind = _KIND_PARTICLES
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


def store(key, superluxcore_scene, exported_objects, member_keys,
          bake_matrices, member_mats, mb_sig, camera_sig, world_sig,
          vis_sig, frame, mat_sig, slot_sig, geo_meta, shape_sig,
          data_ptrs, instancers, dupli_srcs, psys_map,
          instancer_srcs, instancer_singular):
    _entries[key] = {
        "scene": superluxcore_scene,
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
        # {material ptr: superluxcore name} — a rename changes the SuperLuxCore
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
        # {obj_key} of member instancers (dupli/particle emitters):
        # a dirty/moved instancer invalidates the dupli sets of every
        # source it instances, refreshed via _refresh_dupli_sets
        "instancers": instancers,
        # {obj_key: (ExportedObject, compound obj_key) | (None, None)}
        # for objects used as dupli sources — their "src+dupli" scene
        # objects are rebuilt when an instancer's set changes; a None
        # entry marks a source that was instanced but not exportable
        "dupli_srcs": dupli_srcs,
        # {ParticleSettings original ptr: instancer obj_key}
        "psys_map": psys_map,
        # {instancer obj_key: set(source original ptr)} — the source
        # set an instancer emitted at export time; a changed set (new
        # or dropped sources) cannot be delta-refreshed
        "instancer_srcs": instancer_srcs,
        # {instancer obj_key} whose instances were exported one-by-one
        # (non-mesh sources, viewport live-transform particles) instead
        # of as a dupli set — dirty instancers of this kind rebuild
        "instancer_singular": instancer_singular,
    }


def invalidate(key):
    _entries.pop(key, None)


def clear_all():
    _dirty.clear()
    _entries.clear()


def classify(dirty, entry, camera_obj=None):
    """
    Decide how the cached scene can be reused.

    Returns (mode, transform_deltas, material_dirty, geometry_keys,
    instancer_keys):
      mode "full"      — entry unusable, run first_run and rebuild it
      mode "reuse"     — dirty set empty/ignorable + membership intact
      mode "delta"     — reuse + per-object deltas
    transform_deltas is a set of obj_keys needing transform updates,
    geometry_keys a set of obj_keys whose mesh can be re-DefineMesh'ed
    in place (final eligibility — wrappers, shared meshes, submesh
    count — is re-verified at apply time, which falls back to "full"
    on any mismatch); instancer_keys a set of dirty/moved instancers
    whose dupli sets must be re-flushed; material_dirty asks the
    exporter to re-export every member material in place (material
    re-definition is supported by Scene.Parse).
    """
    if entry is None:
        return "full", set(), False, set(), set()

    camera_ptr = camera_obj.original.as_pointer() if camera_obj else None

    transform_keys = set()
    geometry_keys = set()
    instancer_keys = set()
    material_dirty = False
    material_echo = False
    moved_ptrs = set()
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
        if kind == _KIND_PARTICLES:
            # A ParticleSettings datablock changed: every instancer
            # using it must re-flush its dupli set (particle counts,
            # emission, dupli source bindings can all shift).
            users = {
                okey
                for sptr, okey in entry["psys_map"].items()
                if sptr == ptr
            }
            if not users or (
                users & entry["instancer_singular"]
            ):
                return "full", set(), False, set(), set()
            instancer_keys |= users
            # Hair emitted as PATH strands lives on the emitter's own
            # exported object, not in a dupli set — a settings change
            # re-exports the emitter so its hair parts regenerate.
            geometry_keys |= {
                k for k in users if k in entry["objects"]
            }
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
                return "full", set(), False, set(), set()
            geometry_keys |= users
            continue
        if kind != _KIND_OBJECT:
            return "full", set(), False, set(), set()
        if ptr == camera_ptr:
            # The render camera is re-exported every render, so its
            # updates never need a scene delta.
            continue
        key = str(ptr)
        exported = entry["objects"].get(key)
        is_instancer = key in entry["instancers"]
        if flags & ~(FLAG_TRANSFORM | FLAG_SHADING):
            # Geometry on an object: a candidate for an in-place mesh
            # re-definition (verified at apply time). The camera is
            # exempted above; slot reassignment lands here too and is
            # filtered out by the slot_sig check upstream. An
            # instancer's geometry dirt may also change which/how many
            # duplis it spawns.
            if is_instancer:
                if key in entry["instancer_singular"]:
                    return "full", set(), False, set(), set()
                instancer_keys.add(key)
                if key not in entry["objects"]:
                    # A show_self=False instancer exports nothing of
                    # its own — the dupli re-flush already picks up the
                    # new spawn geometry.
                    continue
            geometry_keys.add(key)
            continue
        if flags & FLAG_SHADING:
            # Object-side shading echo — same trigger rule as
            # Mesh/NodeTree echoes: a material delta only runs when a
            # Material datablock was also dirtied, because object-side
            # shading changes it cannot cover must stay a rebuild.
            material_echo = True
        if flags & FLAG_TRANSFORM:
            moved_ptrs.add(ptr)
            if is_instancer:
                # The instancer's dupli set must be re-flushed; its own
                # object (if it has one — a show_self=False instancer
                # exports nothing) is transform-patched or re-exported.
                if key in entry["instancer_singular"]:
                    return "full", set(), False, set(), set()
                instancer_keys.add(key)
                if exported is None:
                    continue
                if key in entry["delta_safe"]:
                    transform_keys.add(key)
                else:
                    geometry_keys.add(key)
                continue
            if (
                exported is None
                or not hasattr(exported, "transform")
                or exported.duplicate_count > 0
                or key not in entry["delta_safe"]
            ):
                # Not in the exported map (newly added, instancer
                # compound key, unexportable type), or a type whose
                # transform cannot be updated in place: members with an
                # exported entry take the delete + re-export path.
                if key in entry["objects"] and key in entry["members"]:
                    geometry_keys.add(key)
                    continue
                if key in entry["dupli_srcs"]:
                    # A dupli source with no standalone export (e.g. a
                    # vert-dupli child Blender does not emit as its own
                    # scene object): its move is carried entirely by
                    # the parent instancer's dupli re-flush below.
                    continue
                return "full", set(), False, set(), set()
            transform_keys.add(key)

    if material_echo and not material_dirty:
        # Shading echoes (object/mesh/node-tree) with no Material
        # datablock update: the change is something a material
        # re-export cannot cover (world/light node tree, object-side
        # shading props) — rebuild conservatively.
        return "full", set(), False, set(), set()

    if moved_ptrs:
        # A moved dupli source (or a moved instancer that is itself
        # instanced) shifts every instance spawned from it — re-flush
        # the dupli sets of all instancers emitting it.
        for pkey, srcs in entry["instancer_srcs"].items():
            if not srcs.isdisjoint(moved_ptrs):
                if pkey in entry["instancer_singular"]:
                    return "full", set(), False, set(), set()
                instancer_keys.add(pkey)

    return (
        "delta"
        if (
            transform_keys
            or material_dirty
            or geometry_keys
            or instancer_keys
        )
        else "reuse"
    ), transform_keys, material_dirty, geometry_keys, instancer_keys


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
            getattr(getattr(mat, "superluxcore", None), "node_tree", None),
        ):
            if getattr(tree, "animation_data", None) is not None:
                return True
    return False


def frame_change(entry, eval_by_key, camera_key):
    """
    Classify a reuse candidate after scene.frame_current changed.

    Returns (rebuild_needed, transform_keys, geometry_keys,
    instancer_keys, material_dirty): transform_keys holds the
    delta-safe member objects whose matrix_world differs from the
    stored export-time matrix (animated or silently moved);
    geometry_keys holds objects whose *geometry* is animated — they are
    re-exported at the current frame (in-place mesh replace or delete +
    re-add); instancer_keys holds moved/animated instancers whose dupli
    sets must be re-flushed; material_dirty asks for a member-material
    refresh when a material is animated. Anything animated beyond that
    — non-transform/non-geometry animation, or a transform change on an
    object that cannot be patched or re-exported in place — forces a
    full rebuild.
    """
    transform_keys = set()
    geometry_keys = set()
    instancer_keys = set()
    material_dirty = False
    for key, eval_obj in eval_by_key.items():
        if key == camera_key or key not in entry["members"]:
            continue
        original = getattr(eval_obj, "original", None) or eval_obj
        if _animation_kind(original) == "other":
            return True, set(), set(), set(), False
        is_instancer = key in entry["instancers"]
        if is_instancer and key in entry["instancer_singular"]:
            # The instancer has per-instance exported objects — a dupli
            # re-flush cannot reach them.
            return True, set(), set(), set(), False
        if _geometry_animated(original):
            # Deforming mesh / hair / point data at a new frame: the
            # object is re-exported below, which also picks up any
            # transform it gained. For an instancer the deformed emitter
            # may also respawn its duplis.
            geometry_keys.add(key)
            if is_instancer:
                instancer_keys.add(key)
            continue
        if _material_animated(original):
            material_dirty = True
        base = entry["bake"].get(key) or entry["member_mats"].get(key)
        if base is not None and eval_obj.matrix_world != base:
            if key in entry["delta_safe"]:
                transform_keys.add(key)
            elif key in entry["objects"]:
                # Unpatchable member (light, volume, ...): delete +
                # re-add picks up the new transform too.
                geometry_keys.add(key)
            elif is_instancer or key in entry["dupli_srcs"]:
                # An instancer with no exported object (show_self off)
                # or a dupli source with no standalone export: its move
                # still shifts the whole dupli set, re-flushed below.
                pass
            else:
                return True, set(), set(), set(), False
            if is_instancer:
                instancer_keys.add(key)
            # A moved dupli source shifts every instance spawned from
            # it — re-flush all instancers emitting this member.
            src_ptr = int(key)
            for pkey, srcs in entry["instancer_srcs"].items():
                if src_ptr in srcs:
                    if pkey in entry["instancer_singular"]:
                        return True, set(), set(), set(), False
                    instancer_keys.add(pkey)
    return (
        False,
        transform_keys,
        geometry_keys,
        instancer_keys,
        material_dirty,
    )
