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
