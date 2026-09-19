# Incremental scene export — A6 phase 2 design

Status: phase 1+2 implemented (persistent cache + dirty tracking +
transform deltas); per-object geometry deltas and instancer re-flush
still fall back to full export. Roadmap item A6-II — reuse the
exported LuxCore scene across renders instead of rebuilding it from
scratch on every F12 / frame change.

## Problem and evidence

The A6 instrumentation (94d75b56, 2ecc73b8) measured the current
exporter: it is linear and already reasonable per-unit — ~0.9s Python +
~1.2s engine instancing for 500k duplis, ~0.5s for 1M hair roots. The
remaining waste is *re*-export: a second F12 on an unchanged scene pays
the full cost again, and an animation render pays it per frame even
though most objects are static between frames.

## Feasibility (verified against the APIs)

- `pyluxcore.RenderConfig(props, scene)` accepts an externally owned
  `Scene` (`luxcore.h` RenderConfig::Create) — a module-level
  `pyluxcore.Scene` can outlive the RenderEngine that Blender destroys
  after each final render.
- Scene edits are already supported live: `Parse(props)`,
  `UpdateObjectTransformation`, `UpdateObjectMaterial`,
  `DeleteObject(s)`, `RemoveUnused*` — the same primitives the viewport
  `BeginSceneEdit`/`EndSceneEdit` path uses today.
- Blender reports granular dirt: `depsgraph.updates` entries carry
  `.id`, `.is_updated_geometry`, `.is_updated_transform`,
  `.is_updated_shading` — exactly the per-object delta needed.
- `Scene::Save()` exists as a last-resort fallback for process
  restarts (serialize + reload); primary path keeps the Scene in
  memory.

## Design

1. **Persistent scene cache** (`export/caches/persistent_scene.py`):
   module-level `{"scene": pyluxcore.Scene, "fingerprints": {obj_key:
   fp}, "frame": int}` keyed by Blender scene pointer. Built by the
   normal `first_run` export — after `luxcore_scene.Parse(props)` we
   keep the Scene instead of dropping it.
2. **Dirty tracking**: a `depsgraph_update_post` app handler appends
   `(id.as_pointer(), flags)` to a per-scene dirty set between renders.
   Frame changes mark the scene "frame-dirty" (objects with drivers/
   animation need re-eval — the depsgraph reports them as updated
   anyway, so the handler path covers it).
3. **Delta export**: on render, for each dirty id: re-convert just that
   object through the existing `_convert_obj` path into a partial
   `Properties`, then `Parse` + `UpdateObjectTransformation`/
   `DeleteObject` for structural deltas (new/removed objects,
   dupli-count changes — dupli sets are re-flushed wholesale per
   instancer, reusing the A5 pending-duplicates machinery).
4. **Fallback**: any inconsistency (mesh-key change, particle count
   change, missing dirty info — e.g. scene loaded from file) marks the
   cache stale and falls back to a full `first_run`. Correctness
   beats cleverness: the cache is only allowed to *skip work*, never
   to skip correctness.

## What stays the same

- `first_run` remains the single source of truth for a full export —
  the incremental path reuses its per-object converters, not a fork.
- Viewport path untouched (already incremental via `view_update`).
- Motion blur / dupli / pointcloud machinery reused as-is; a dirty
  instancer re-flushes its whole instance set (same granularity the
  engine's `DuplicateObject` provides anyway).

## Risks / decisions

- **Identity**: `obj.as_pointer()` survives across renders in one
  session but not across file reload — reload clears the cache.
- **Depsgraph semantics**: `depsgraph.updates` is only populated
  inside depsgraph callbacks — must be collected in the handler, not
  at render time.
- **Dupli granularity**: engine-level `DuplicateObject` has no
  per-instance update op, so a dirty instancer re-exports its entire
  instance set — still a win vs full-scene export.
- **RenderConfig ownership**: *verified* — `RenderConfigImpl(props,
  scn)` stores a non-owning reference (`sceneRef`), so a Python-owned
  `pyluxcore.Scene` safely outlives each RenderConfig/session.
- **Stale-property risk**: `Scene.Parse` cannot delete properties —
  a toggled DoF, removed env light, or changed motion-blur step count
  would leave stale definitions in a reused scene. Reuse therefore
  requires matching camera spec (minus volatile position/motion keys),
  world property string, and motion-blur signature, all stored in the
  entry.
- **Evaluated-vs-original pointers**: `DepsgraphUpdate.id` is the
  *evaluated* datablock (`is_evaluated=True`) — its `as_pointer()`
  does not match the `make_key` object keys, which use
  `.original.as_pointer()`. Dirty records key on `.original`
  accordingly.
- **UpdateObjectTransformation semantics**: on instanced
  (`ExtInstanceTriangleMesh`) objects it replaces the transform
  (absolute); on world-baked meshes it applies the transform to the
  vertices, so baked objects take a *delta* `new @ old.inverted()`.
- **Instancer exclusion**: an instancer's transform moves its whole
  dupli set — detected at delta time via `instance_type != "NONE" or
  particle_systems` and forced to a full rebuild.

## Phasing

1. ~~Persistent-scene plumbing + fingerprint map + full-fallback
   safety~~ — done: `export/caches/persistent_scene.py` holds the
   Scene, exported-object map, membership set, bake matrices, and the
   camera/world/motion-blur signatures per (scene, view layer).
2. ~~depsgraph_update_post dirty set + per-object transform delta~~ —
   done for transform-only changes on delta-safe types (MESH-family:
   instanced exports get the absolute matrix, world-baked exports get
   `new @ old.inverted()` via `UpdateObjectTransformation`). Geometry/
   shading dirt, non-object datablock dirt, new/removed objects,
   instancers, lights and volumes still take the full-export path.
   Verified headless: repeated F12 reuses the scene (identical
   image), a moved object applies one transform delta (image shows
   the move), a bmesh edit triggers a full rebuild and re-cache.
3. Extend to CURVES/POINTCLOUD/VOLUME and instancer re-flush.
4. Validation: repeated F12 timing, animation-sequence render timing,
   correctness diff (same outputs as full export) on the A6 benchmark
   scenes (500k duplis, 1M-strand hair, classroom).
