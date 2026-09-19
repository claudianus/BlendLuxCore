# Incremental scene export — A6 phase 2 design

Status: scoped. Roadmap item A6-II — reuse the exported LuxCore scene
across renders instead of rebuilding it from scratch on every F12 /
frame change.

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
- **RenderConfig ownership**: `RenderConfig` takes ownership of the
  Scene it is given — the cache must hand over a *fresh* `Scene` clone
  or re-fetch via `GetScene()` semantics; verify ownership transfer in
  pyluxcore before implementation (worst case: keep one Scene per
  render and rebuild the cache entry after each use).

## Phasing

1. Persistent-scene plumbing + fingerprint map + full-fallback safety
   (no perf change yet — just reuse without deltas, proving the
   lifecycle works).
2. depsgraph_update_post dirty set + per-object delta export for
   MESH objects (the common case).
3. Extend to CURVES/POINTCLOUD/VOLUME and instancer re-flush.
4. Validation: repeated F12 timing, animation-sequence render timing,
   correctness diff (same outputs as full export) on the A6 benchmark
   scenes (500k duplis, 1M-strand hair, classroom).
