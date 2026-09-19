# BlendLuxCore fork — feature documentation

Changes made to the Blender adapter since the fork, in the same evidence
format as the engine docs (`SuperLuxCore/doc/features/`). Each section states
what it does, why, references, the node/property mapping, and how it was
validated.

## Cycles node coverage — `export/cycles_node_reader.py`

**What/why.** Blender scenes use Cycles shader nodes; to render them LuxCore
must translate each node to an equivalent LuxCore texture/material. Coverage
grew from **36 to 71 of the ~101 Blender 5.2 shader nodes** by auto-routing
unmapped node trees through the Cycles reader (`Blender-first materials:
auto-route Blender node trees to the Cycles reader`).

**Added mappings** (commit `Cycles node reader: ...` and follow-ups):

| Node | LuxCore output | Notes |
|---|---|---|
| TexBrick | `brick` texture | |
| BsdfMetallic | metal material | |
| VectorCurve | curve-map texture | |
| VolumeCoefficients | volume params | |
| Volume Info | `densitygrid` texture | Density/Color/Flame/Temperature -> object grids |
| Displacement | `displacement` shape | output-socket mapping |
| Principled Hair BSDF | `hairmat` | Chiang model; see engine hair doc |
| Hair Info | `hitpointvertexaov` (strand-u / strand-random) | engine strand AOVs |
| Particle Info | `objectid` / `objectidnormalized` | per-instance random/id |
| White Noise | `whitenoise` | deterministic 3D-seed hash |
| Blackbody | `blackbody` texture | linked Temperature input |
| Map Range | `remap` | clamped (LuxCore remap always clamps) |
| Subsurface Scattering | Disney `subsurface` | approximation (no BSSRDF) |

**Validation:** exported SDL parses + renders; coverage measured at 71/101.
Remaining unmapped: IES, Sky/Environment, Gabor, PointDensity, VectorRotate,
VectorTransform, RayPortal — tracked on the roadmap.

**References:** Blender/Cycles node reference (Blender manual); the mapping
table above is the API surface.

## Volumes — `export/volume.py`

**What/why.** Blender VOLUME objects carry an OpenVDB file; they export to a
bounded box mesh with a heterogeneous LuxCore volume fed by `densitygrid`
textures. Fire emission is driven by the file's `flame`/`temperature`/`heat`
grid.

- Density, colour (albedo) and fire grids each become a `densitygrid` texture.
- Fire emission: the fire field is scaled to a Kelvin temperature and fed to a
  `blackbody` texture — a **true Planckian** colour, replacing an earlier
  hand-tuned `band` ramp (`blackbody: ... + Planckian fire emission`). The raw
  field also provides the HDR brightness mask.
- `B3: VOLUME and POINTCLOUD object export support` added the object types.
- **Volume Info node + material-driven volumes** (`VolumeInfo node export ->
  densitygrid...`): a `ShaderNodeVolumeInfo` in the material's *Volume* input
  reads the object's `density`/`color`/`flame`/`temperature` grid as a
  `densitygrid` texture (same world->[0,1]^3 active-voxel mapping as the
  auto-build). When such a material is present it **drives the volume
  coefficients** — tint / remap / custom emission on a `.vdb` — otherwise the
  object auto-builds exactly as before. Validation scene:
  `scenes/cornell/volumeinfo-test.scn` in the engine repo.

**Validation:** `scenes/cornell/fire-test.scn`, `bb-test.scn` and
`volumeinfo-test.scn` in the engine repo render correct Planckian fire
gradients on CPU and Metal/OpenCL.

## Object export — dupli / instances / point clouds

- MESH, CURVES (hair), VOLUME, POINTCLOUD and dupli-instance export;
  depsgraph Geometry-Nodes realized output is covered automatically.
- Blender 5.2 export bugs fixed (instancing visibility, hair curves, CURVE
  type, removed APIs).
- **Dupli/particle transform motion blur** (A5): when camera motion blur
  is enabled with object blur, dupli/particle instances export per-instance
  transform time series through `Scene.DuplicateObject`'s motion-multi
  overload — instances blur instead of rendering static. Opt-in is
  `enable_motion_blur` on the instanced object OR its instancer (emitter);
  either flag blurs all copies including the first instance. Matched
  across shutter steps by `(instancer, persistent_id)`; steps where an
  instance has no evaluated transform (particle born/died mid-shutter)
  reuse its center-frame matrix, and a dupli object whose ids collide
  falls back to static duplication. Transform interpolation only —
  vertex-level deformation blur is not supported by the engine.
- **Point-cloud motion blur** (A5 follow-up): POINTCLOUD objects with
  `enable_motion_blur` re-evaluate point positions/radii at every shutter
  step and export per-point transform time series — point 0 rides the
  base object's motion properties, the remaining points go through the
  same motion-multi duplication path as duplis. If the point count
  differs at any step (topology change) the whole cloud falls back to
  static, base object included.
  Per-stage export timings (export time breakdown + instance/object
  counts) are exposed in render stats.
- **Persistent scene reuse** (A6-II, `export/caches/persistent_scene.py`):
  final renders of the same scene + view layer reuse the previous
  `pyluxcore.Scene` instead of re-exporting every object. A
  `depsgraph_update_post` handler accumulates dirty datablock ids
  (keyed on `.original` pointers — `DepsgraphUpdate.id` is evaluated);
  an empty/ignorable dirty set reuses the scene wholesale, a
  transform-only update on a delta-safe object applies
  `Scene.UpdateObjectTransformation` (absolute for instanced exports,
  `new @ old.inverted()` for world-baked geometry), and anything else
  — geometry dirt, datablock dirt, membership changes, instancers,
  lights, volumes, object motion blur, camera/world signature changes —
  falls back to a full export and re-caches.
  Frame changes are covered separately: `frame_set()` leaves no
  depsgraph updates, so per-member transform snapshots plus animation
  classification decide between transform delta and rebuild, and
  animated materials mark the scene for an in-place material refresh.
  **Material deltas** (A6-III): a dirty `Material` datablock re-exports
  all member materials into the cached scene via `Scene.Parse`
  re-definition (a first-class engine operation, including light-source
  re-wiring). Shading echoes on objects/meshes/node trees only ever
  ride along with a real `Material` update — an echo without one
  (e.g. a world node tree) rebuilds — and material identity
  (`mat_sig`, renames) plus slot topology (`slot_sig`) signatures keep
  renames and binding edits on the rebuild path. A `shape_sig`
  signature replays the wrapper-shape chain (`define_shapes`/
  `_apply_cycles_displacement`) so material edits that add or remove
  `scene.shapes.*` wrappers (e.g. a displacement link) rebuild instead
  of going stale.
  **Mesh geometry deltas**: a dirty `Mesh` datablock or
  geometry-flagged object re-`DefineMesh`es its named shapes in place —
  the engine rewires every referencing scene object and triangle light
  itself. Eligibility is re-verified at apply time (MESH type,
  delta-safe, no wrapper shapes on the shared mesh, unchanged
  instancing decision, identical submesh set) and any failure falls
  back to a full export.
  Design + rationale: `doc/incremental_export_design.md`.
  Regression: `dev-tools/a6_persistent_scene_test.py` (headless;
  covers reuse, transform/material/geometry deltas, signature-driven
  rebuilds (visibility, camera, world, material rename, slot
  topology, shape stack), animated transforms and animated
  materials, with image-diff assertions).

## UX — Quick Setup + viewport stability

- Corona-style **Quick Setup**: a quality slider + denoise toggle; caustics
  auto-enabled when the scene has glass; progressive caustics refinement.
- Viewport: black-flash and UI-freeze fixes; engine-teardown hardening; an
  error-log file for fatal errors.
- Backend options: **Metal GPU** (Apple silicon) and a **spectral render**
  toggle.

## Platform compatibility

All adapter features are platform-neutral Python; the Metal backend option is
Apple-only and falls back to CPU/OpenCL elsewhere.
