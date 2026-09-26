# Incremental scene export — A6 phase 2 design

Status: phase 1+2 implemented (persistent cache + dirty tracking +
transform deltas); per-object geometry deltas and instancer re-flush
still fall back to full export. Roadmap item A6-II — reuse the
exported SuperLuxCore scene across renders instead of rebuilding it from
scratch on every F12 / frame change.

## Problem and evidence

The A6 instrumentation (94d75b56, 2ecc73b8) measured the current
exporter: it is linear and already reasonable per-unit — ~0.9s Python +
~1.2s engine instancing for 500k duplis, ~0.5s for 1M hair roots. The
remaining waste is *re*-export: a second F12 on an unchanged scene pays
the full cost again, and an animation render pays it per frame even
though most objects are static between frames.

## Feasibility (verified against the APIs)

- `pysuperluxcore.RenderConfig(props, scene)` accepts an externally owned
  `Scene` (`superluxcore.h` RenderConfig::Create) — a module-level
  `pysuperluxcore.Scene` can outlive the RenderEngine that Blender destroys
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
   module-level `{"scene": pysuperluxcore.Scene, "fingerprints": {obj_key:
   fp}, "frame": int}` keyed by Blender scene pointer. Built by the
   normal `first_run` export — after `superluxcore_scene.Parse(props)` we
   keep the Scene instead of dropping it.
2. **Dirty tracking**: a `depsgraph_update_post` app handler appends
   `(id.as_pointer(), flags)` to a per-scene dirty set between renders.
   Frame changes are handled separately — *verified*: `frame_set()`
   produces no `depsgraph.updates` entries at all, so animated objects
   would otherwise reuse a stale scene silently. The entry stores the
   export frame; on a frame change `frame_change()` re-checks every
   member object directly (see Risks).
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
  `pysuperluxcore.Scene` safely outlives each RenderConfig/session.
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
- **Frame changes are invisible to dirty tracking** — *verified
  empirically*: `scene.frame_set(N)` produces no
  `depsgraph.updates` entries (the depsgraph just re-evaluates at the
  new time). Without a dedicated check, an animation render would
  reuse the frame-1 scene for every frame — the worst kind of stale
  output. The entry therefore stores `frame_current` plus a
  `matrix_world` snapshot for every member object (`bake` for
  delta-safe exports, `member_mats` for the rest). On a frame change
  `frame_change()` walks all members:
  - `_animation_kind()` inspects action + driver data paths (legacy
    and slotted actions): transform-only animation stays delta-safe,
    anything else forces a rebuild;
  - `_geometry_animated()` catches datablock/shape-key animation,
    physics/deformer modifiers, and NODES groups that read Scene
    Time — all rebuild;
  - a changed `matrix_world` on a delta-safe object becomes a
    transform delta; a changed matrix on anything else (lights,
    instancers) rebuilds;
  - `_material_animated()` marks the entry when any member material or
    its node tree carries animation/drivers, so a frame change also
    refreshes materials in place.
  Verified headless: keyframed cube rendered at frames 1/15/30
  produces correct per-frame output with `1 transform delta(s)` per
  frame instead of a re-export — and before this fix it rendered the
  frame-1 image three times.
- **Material deltas ride on `Scene.Parse` re-definition** — *verified
  empirically*: a material node edit dirties
  `Object(shading) + Mesh(shading) + Material(shading) +
  ShaderNodeTree(no flags)`. Material (like texture/volume)
  re-definition is a first-class engine operation — `ParseMaterials`
  rebuilds the named material in place including light-source
  re-wiring — so a dirty `Material` datablock triggers a re-export of
  every member material into the cached scene. Two safety rules keep
  this conservative:
  - *Echoes need a trigger*: `Mesh`/`NodeTree` datablocks and
    object-side `FLAG_SHADING` are classified as *echoes* — compatible
    with a material delta but unable to start one. A shading echo
    without an accompanying `Material` datablock update (world/light
    node trees look identical here, and superluxcore-mode worlds do not
    read the node tree at all) forces a full rebuild.
  - *Identity/topology signatures*: `mat_sig` (material pointer ->
    SuperLuxCore name) catches renames — a rename changes the
    `scene.materials.*` key objects reference — and `slot_sig`
    (per-object slot material/link layout) catches binding edits.
    Both force a rebuild; slot reassignment additionally flags
    geometry anyway.
- **Shape-stack signature guards material edits that change the
  wrapper chain** — adding a displacement link or a superluxcore shape
  node means the object needs a *new* `scene.shapes.*` wrapper a
  material delta cannot create (the cached scene has no such shape).
  `_shape_stack_sig()` replays the same `define_shapes`/
  `_apply_cycles_displacement` functions the export uses into a
  scratch `Properties`; the replayed `(shape names, prop string)`
  per member is compared at reuse and any mismatch rebuilds. This
  catches added/removed wrappers and value changes alike, and uses
  the export's own code path so it cannot drift from it.
- **Mesh geometry deltas ride on `Scene.DefineMesh`
  re-definition** — `DefineMesh` on an existing shape name replaces
  the mesh in place, updates every scene object's mesh reference,
  and rewires triangle lights for emissive materials. A dirty
  `Mesh` datablock resolves to its member objects via stored
  `geo_meta`; a geometry-flagged object becomes a candidate
  directly. Final eligibility is re-verified at apply time (any
  failure discards the scene and falls back to a full export):
  - the object must be a `MESH` type, delta-safe, and free of
    `dupli`/`duplicate` sub-objects;
  - **no wrapper shapes anywhere on the shared mesh** — wrapper
    shapes (displacement/pointiness/…) hold raw source-mesh pointers
    `UpdateMeshReferences` cannot rewire, so a replaced base mesh
    would leave them dangling;
  - the instancing decision (`can_share_mesh`/displacement/motion
    blur) must be unchanged, or the recomputed `mesh_key` and shape
    names would not line up;
  - the re-exported submesh set must be identical (a slot becoming
    (un)used shifts part names).
  World-baked meshes re-export with the current `matrix_world`, so
  their transform delta is subsumed; instanced exports keep the
  object transform and still take `UpdateObjectTransformation`.
- **Non-mesh geometry deltas use delete + re-export** — member
  objects whose dirty data cannot be patched by `DefineMesh` in place
  (hair curves, volumes, pointclouds, legacy curves, shifted submesh
  sets, wrapped meshes) are deleted from the cached scene and
  re-exported through the normal `_convert_obj` path into a scratch
  `Properties`, then `Scene.Parse` defines them fresh. The path also
  covers moved lights and non-delta-safe members on frame changes
  (an animated light is re-defined via `DeleteLight` + `Parse` —
  `frame_change()` routes it to the geometry-delta set instead of
  rebuilding). Dirty data datablocks resolve to members through
  `data_ptrs` ({obj_key: original data pointer}) rather than
  `geo_meta`, so every object-data type is covered; `geo_meta`
  remains the in-place `DefineMesh` eligibility record. Caveat:
  `DepsgraphObjectInstance` wrappers die with their iterator, so the
  re-export rebuilds a `SimpleNamespace` shim from snapshotted
  (object, show_self, matrix) values.

## Phasing

1. ~~Persistent-scene plumbing + fingerprint map + full-fallback
   safety~~ — done: `export/caches/persistent_scene.py` holds the
   Scene, exported-object map, membership set, bake matrices, and the
   camera/world/motion-blur signatures per (scene, view layer).
2. ~~depsgraph_update_post dirty set + per-object transform delta~~ —
   done for transform-only changes on delta-safe types (MESH-family:
   instanced exports get the absolute matrix, world-baked exports get
   `new @ old.inverted()` via `UpdateObjectTransformation`).
   Non-object datablock dirt, new/removed objects and instancers
   still take the full-export path; geometry and shading dirt gained
   dedicated delta paths in items 3–5, including lights via the
   delete + re-export path.
   Verified headless: repeated F12 reuses the scene (identical
   image), a moved object applies one transform delta (image shows
   the move), a bmesh edit updates the mesh in place.
3. ~~Material-only delta~~ — done: dirty `Material` datablocks re-
   export all member materials via `Scene.Parse` re-definition on the
   cached scene; shading echoes without a `Material` trigger, material
   renames (`mat_sig`) and slot topology edits (`slot_sig`) all fall
   back to rebuild. Driver/keyframed materials mark the entry
   material-dirty at frame changes. Verified headless (M1–M4 in
   `dev-tools/a6_persistent_scene_test.py`): color edit keeps the
   Scene and changes the image; rename/slot-swap rebuild; animated
   material refreshes across a frame change.
4. ~~Mesh geometry delta~~ — done: dirty `Mesh` datablocks and
   geometry-flagged objects re-`DefineMesh` their named shapes in
   place (object references and triangle lights rewire themselves).
   Eligibility is re-verified at apply time — MESH type, delta-safe,
   no wrappers on the shared mesh, unchanged instancing decision,
   identical submesh set — and any failure discards the scene for a
   full export. A `shape_sig` replay of the wrapper chain guards
   material edits that add/remove `scene.shapes.*` wrappers. Verified
   headless (R4, M5): a bmesh edit keeps the Scene and changes the
   image; adding a Displacement link rebuilds.
5. ~~CURVES/POINTCLOUD/VOLUME geometry deltas~~ — done via the
   delete + re-export fallback inside `_apply_geometry_deltas`:
   non-mesh object data (Curve/Curves/MetaBall/Volume/PointCloud)
   and in-place-ineligible members are deleted and re-exported
   through `_convert_obj` + `Scene.Parse`, with per-entry records
   (geo_meta, shape_sig, slot_sig, data_ptrs, bake) refreshed.
   `frame_change()` additionally routes geometry-animated members
   (shape keys, deform modifiers, Scene-Time GN) and moved
   non-delta-safe members (e.g. lights) to the same path, so
   animation frames now delta instead of rebuild. Verified headless
   (C1/F70/L80/L90/H1 in `dev-tools/a6_persistent_scene_test.py`):
   curve bevel edit, shape-keyed mesh across frames, keyframed light
   and a quick_fur hair-curves object all keep the Scene and change
   the image.
6. Instancer-set re-flush — done: a moved/dirty instancer (dupli
   emitter or particle system) re-flushes its sources' "src+dupli"
   scene objects instead of rebuilding. first_run records
   per-instancer source sets (instancer_srcs), sources with no
   standalone export (dupli_srcs), singular per-instance exports
   (instancer_singular) and ParticleSettings-to-instancer bindings
   (psys_map); _refresh_dupli_sets rescans
   depsgraph.object_instances, updates the base object's transform
   to the first instance matrix and re-DuplicateObjects the rest
   (typed array buffers, same as Duplis). A changed source set,
   emptied set, non-instancing base or an object_blur session falls
   back to rebuild. Dupli-source mesh edits re-DefineMesh the
   source's compound-key "_instance" mesh in place — the engine
   rewires the dupli base and every duplicate itself.
   depsgraph.objects in render mode omits instanced-only members,
   so all evaluated-object maps fill gaps through
   Object.evaluated_get (_eval_object_map). Verified headless
   (I1/I2 in dev-tools/a6_persistent_scene_test.py): moving a VERTS
   emitter re-flushes the dupli set on the same Scene, and a bmesh
   edit on the instanced source redefines its instanced mesh in
   place — both change the image.
7. Validation: repeated F12 timing, animation-sequence render timing,
   correctness diff (same outputs as full export) on the A6 benchmark
   scenes (500k duplis, 1M-strand hair, classroom). First numbers:
   `dev-tools/a6_benchmark.py` on a 1202-object ~2M-tri scene —
   reuse/transform/geometry deltas at ~20% of full-export time,
   material delta ~30% (camera/world/config re-export + signatures
   are the floor).
