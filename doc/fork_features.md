# SuperLuxCore fork — feature documentation

Changes made to the Blender adapter since the fork, in the same evidence
format as the engine docs (`SuperLuxCore/doc/features/`). Each section states
what it does, why, references, the node/property mapping, and how it was
validated.

## Cycles node coverage — `export/cycles_node_reader.py`

**What/why.** Blender scenes use Cycles shader nodes; to render them SuperLuxCore
must translate each node to an equivalent SuperLuxCore texture/material. Coverage
grew from 36 to **42 mapped + 32 approx of the 102 `ShaderNode*` types registered
by Blender 5.2.1** (remaining warn-tier nodes emit a warning + neutral fallback)
by auto-routing unmapped node trees through the Cycles reader. The authoritative,
type-by-type audit — including the denominator definition — is
`doc/cycles_node_coverage.md`.

**Added mappings** (commit `Cycles node reader: ...` and follow-ups):

| Node | SuperLuxCore output | Notes |
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
| Map Range | `remap` | clamped (SuperLuxCore remap always clamps) |
| Subsurface Scattering | Disney `subsurface` | approximation (no BSSRDF) |
| Attribute (generic/named) | `hitpointvertexaov` / `hitpointtriangleaov` / `hitpointcolor` | GN "Store Named Attribute" output and hand-authored `mesh.attributes`: scalar float/int/bool → vertex or triangle AOV, vector/float2 → extra color layer. Fac/Color/Vector outputs resolved; edge-domain and string attributes warn |

**Validation:** exported SDL parses + renders; coverage measured at
42 mapped + 32 approx + 8 warn-fallback of 102 registered node types
(`dev-tools/cycles_node_coverage_test.py` non-rendering audit +
`dev-tools/e23_cycles_compat_e2e_test.py` headless render).
Remaining unmapped: Sky/Environment (material context), PointDensity,
RayPortal — tracked on the roadmap. VectorRotate/VectorTransform are
mapped for constant transforms; texture-driven axes warn.

**References:** Blender/Cycles node reference (Blender manual); the mapping
table above is the API surface.

## Volumes — `export/volume.py`

**What/why.** Blender VOLUME objects carry an OpenVDB file; they export to a
bounded box mesh with a heterogeneous SuperLuxCore volume fed by `densitygrid`
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
  falls back to static duplication.
- **Deformation (vertex) motion blur** (E9): objects with
  `enable_motion_blur` get their evaluated mesh re-sampled at every
  shutter step during the same `frame_set` pass that collects transform
  motion, and the per-step loop-expanded vertex buffers are attached to
  the exported base shapes via `Scene.SetMeshVertexMotion` — the same
  `motion.N.time` schedule drives transform and deformation motion.
  Shape keys, armature, Geometry Nodes and other deforming modifiers
  are all covered because sampling uses `to_mesh()` on the evaluated
  object. Guard rails: an export-time topology signature (vertex count
  + loop vertex map) is re-checked per step, so a mid-shutter topology
  change falls back to static with a notice; meshes whose final shape
  is a wrapper (subdiv/displacement) are skipped since wrapper meshes
  cannot carry the base mesh's series; identical step buffers skip the
  call entirely.
  Regression: `dev-tools/e9_vertex_motion_e2e_test.py` renders an
  animated shape-key quad in Blender headless and asserts the blurred
  emissive footprint widens while a non-opted-in object stays sharp.
- **Strand (hair/curve) motion blur** (E9 Ph5b): hair-curves objects
  and particle hair with `enable_motion_blur` get their raw strand
  control points re-read on the evaluated object at every shutter step
  and passed to `Scene.SetStrandsVertexMotion`, which re-tessellates
  them through the recipe recorded at export time (the bindings store a
  raw→filtered source-index map, so step buffers use the *unfiltered*
  Blender layout). On Metal the series also drives native motion-curve
  primitives; CPU/OCL paths shade re-tessellated motion triangles.
  Particle hair with the motion-blur opt-in now exports strands
  unbaked (object transform on the SuperLuxCore object) so transform and
  deformation motion compose. Guard rails: a mid-shutter change in
  strand layout (curve counts / particle counts) or a shape wrapper
  falls back to static with a notice.
  Regression: `dev-tools/e9_strand_motion_e2e_test.py` renders a
  keyframed hair comb headless and asserts the blurred strands smear
  into a continuous band while non-opted-in strands stay sharp.
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
  `pysuperluxcore.Scene` instead of re-exporting every object. A
  `depsgraph_update_post` handler accumulates dirty datablock ids
  (keyed on `.original` pointers — `DepsgraphUpdate.id` is evaluated);
  an empty/ignorable dirty set reuses the scene wholesale, a
  transform-only update on a delta-safe object applies
  `Scene.UpdateObjectTransformation` (absolute for instanced exports,
  `new @ old.inverted()` for world-baked geometry); object-data dirt
  resolves to per-object geometry deltas (in-place `DefineMesh` for
  eligible meshes, delete + re-export otherwise), and anything else —
  datablock dirt, membership changes, instancer source-set changes,
  object motion blur on instancer deltas, camera/world signature
  changes — falls back to a full export and re-caches.
  Frame changes are covered separately: `frame_set()` leaves no
  depsgraph updates, so per-member transform snapshots plus animation
  classification decide between transform delta, geometry re-export
  (deforming meshes, moved lights) and rebuild, and animated materials
  mark the scene for an in-place material refresh.
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
  **Non-mesh geometry deltas** (delete + re-export): member objects
  whose dirty data cannot be patched in place — hair curves, volumes,
  pointclouds, legacy curves, shifted submesh sets, wrapped meshes —
  are deleted from the cached scene and re-exported through the normal
  conversion path (`Scene.Parse` re-definition); the same path carries
  moved lights and other non-delta-safe members on frame changes, so
  animation frames delta instead of rebuild. Dirty object-data
  datablocks resolve to members through a `data_ptrs` map.
  **Instancer-set refresh**: a moved or geometry-dirty dupli emitter /
  particle instancer re-flushes the `src+dupli` objects of every
  source it instances — the base object takes the first instance
  matrix via `UpdateObjectTransformation`, the `dupli` object is
  deleted and re-`DuplicateObject`ed with the remaining matrices and
  object ids. Moved dupli sources re-flush their parent instancers
  through a reverse `instancer_srcs` map, dirty `ParticleSettings`
  re-flush their emitters via `psys_map`, and a dupli-source mesh
  edit re-`DefineMesh`es the compound-key `_instance` mesh in place
  (the engine rewires the dupli base plus all duplicates). Sources
  that only exist as instanced exports — e.g. VERTS-dupli children —
  are resolved through `Object.evaluated_get` because render-mode
  `depsgraph.objects` omits them. Source-set changes (added/dropped
  sources), emptied dupli sets, per-instance ("singular") instancer
  exports and object motion blur fall back to a full rebuild.
  Design + rationale: `doc/incremental_export_design.md`.
  Regression: `dev-tools/a6_persistent_scene_test.py` (headless;
  covers reuse, transform/material/geometry deltas, curve/light/
  hair-curves re-export deltas, deforming-mesh frame deltas,
  instancer re-flush and dupli-source in-place redefines,
  signature-driven rebuilds (visibility, camera, world, material
  rename, slot topology, shape stack), animated transforms and
  animated materials, with image-diff assertions).
  Benchmark: `dev-tools/a6_benchmark.py` — on a 1202-object ~2M-tri
  scene, reuse/transform/geometry deltas run at ~20% and material
  deltas at ~30% of full-export time (measured on export-stage
  timings, not render wall time).

## UX — viewport stability

- **Automatic light strategy** (`light_strategy = "AUTO"`, opt-in):
  counts scene emitters — light objects, the world background, and
  emissive meshes weighted by polygon count — and picks ReSTIR DI above
  `AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD` (16) on engines that support it,
  log-power sampling otherwise. Explicit strategy choices always win.
- **Auto clamp** (`auto_clamping`, opt-in): once an unclamped render has
  produced a suggested clamp value, subsequent renders apply it
  automatically. Manual "Clamp Output" takes precedence.
- **Auto device selection** (`config.device = "AUTO"`, opt-in): uses
  the GPU(s) when an enabled device of the backend selected in the addon
  preferences exists, falls back to CPU otherwise. Enabled GPUs below
  4 GiB automatically run out-of-core so large scenes still fit, and get
  a capped wavefront task count (`opencl.task.count = 131072` vs the
  512K default) so the per-task buffers fit and leave headroom for the
  driver/compositor.
- **ReSTIR DI visibility weighting** (`restir_visibility_enable`): exposes
  SuperLuxCore's `lightstrategy.restir.visibility.enable` — candidates' shadow
  rays steer the reservoir target. Opt-in; honest description in the
  tooltip (it can reallocate noise into penumbra edges on mostly-visible
  scenes instead of reducing it).
- **ReSTIR GI** (`restir_gi_enable`): exposes SuperLuxCore's
  `path.restir.gi.enable` (first-bounce reservoir, temporal + gated
  spatial reuse). Runs on PATHCPU and the pathoclbase GPU engines
  (PATHOCL/TILEPATHOCL); the export is gated so RTPATHOCL/BIDIR* never
  see a silent no-op. Experimental, opt-in.
- **Convergence stat**: the render statistics panel shows the converged-
  pixel percentage whenever a convergence test runs — always on tiled
  engines, and on PATH*/PATHOCL when the noise-threshold halt condition is
  enabled (`batch.haltthreshold`/`batch.haltnoisethreshold`). "n/a" only
  when no convergence test is configured.
- Production defaults on new scenes: denoiser enabled, halt conditions
  enabled with a convergence stop (noise threshold) plus a 1024-spp cap.
- **OIDN component-decomposed denoising** (`denoiser.oidn_mode = "COMPONENTS"`,
  the default): denoises each radiance component (direct/indirect ×
  diffuse/glossy/specular) independently with shared albedo/normal guides
  and recombines exactly (residual passthrough preserves total energy).
  Indirect diffuse is filtered in illumination space (radiance ÷ albedo,
  multiplied back after — "albedo demodulation", the same trick production
  reconstruction filters use) so texture detail can not be blurred.
  Sub-options: Albedo Demodulation, Denoise Emission (off = crisp emitter
  silhouettes), Firefly Suppression (median+MAD isolated-spike clamp,
  0 = off). Engine side: `film.imagepipeline.N.mode = components`,
  `.demodulate`, `.emission.denoise`, `.firefly.sigma`; required film
  channels are requested automatically.
- Viewport: black-flash and UI-freeze fixes; engine-teardown hardening; an
  error-log file for fatal errors. Interactivity pass (P0-1): hold-last-frame
  after film resets (empty frames are never uploaded — the previous image
  stays up until new samples land, bounded to 0.5 s so black scenes still
  display), the last frame is also drawn while a restarted session boots,
  film readback runs on a worker thread (pysuperluxcore releases the GIL in
  `Film.GetOutputFloat`/`ApplyOIDN`, so the device-queue drain no longer
  stalls the UI), and "Interactive Denoise" runs OIDN periodically during
  rendering instead of only after pause (denoised frames own the display to
  avoid raw/denoised alternation; a fresh denoise still runs on pause).
- Backend options: **Metal GPU** (Apple silicon) and a **spectral render**
  toggle.

## Platform compatibility

All adapter features are platform-neutral Python; the Metal backend option is
Apple-only and falls back to CPU/OpenCL elsewhere.

## Development workflow

- `dev-tools/sync_dev_install.sh` — syncs a freshly built pysuperluxcore .so
  (with @rpath→@loader_path/.dylibs rewrites + runtime dylibs), updates the
  dev wheel the add-on installs from, points `blc_settings.json` at it
  (LOCAL wheel source — the wheel must live outside `wheels/` because
  luxloader backs that dir up before `pip download`), and rsyncs the
  add-on Python sources. Smoke-imports under Blender's bundled Python.
