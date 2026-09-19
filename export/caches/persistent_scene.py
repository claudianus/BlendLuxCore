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
_KIND_REBUILD = 2

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
          bake_matrices, mb_sig, camera_sig, world_sig, vis_sig):
    _entries[key] = {
        "scene": luxcore_scene,
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
    }


def invalidate(key):
    _entries.pop(key, None)


def clear_all():
    _dirty.clear()
    _entries.clear()


def classify(dirty, entry, camera_obj=None):
    """
    Decide how the cached scene can be reused.

    Returns (mode, transform_deltas):
      mode "full"      — entry unusable, run first_run and rebuild it
      mode "reuse"     — dirty set empty/ignorable + membership intact
      mode "delta"     — reuse + per-object transform updates
    transform_deltas is a set of obj_keys needing transform updates
    (filled for "delta", empty otherwise).
    """
    if entry is None:
        return "full", set()

    camera_ptr = camera_obj.original.as_pointer() if camera_obj else None

    transform_keys = set()
    for ptr, (flags, kind) in dirty.items():
        if kind == _KIND_IGNORE:
            continue
        if kind != _KIND_OBJECT:
            return "full", set()
        if ptr == camera_ptr:
            # The render camera is re-exported every render, so its
            # updates never need a scene delta.
            continue
        key = str(ptr)
        exported = entry["objects"].get(key)
        if (
            exported is None
            or not hasattr(exported, "transform")
            or exported.duplicate_count > 0
            or key not in entry["delta_safe"]
        ):
            # Not in the exported map (newly added, instancer compound
            # key, unexportable type), a light (no transform path), an
            # instancer/pointcloud (its duplicate set can move too), or
            # a type whose transform cannot be updated in place.
            return "full", set()
        if flags & ~FLAG_TRANSFORM:
            return "full", set()
        transform_keys.add(key)

    return ("delta" if transform_keys else "reuse"), transform_keys
