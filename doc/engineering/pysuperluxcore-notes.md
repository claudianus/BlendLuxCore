# Standalone pysuperluxcore API notes

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

## Standalone pysuperluxcore notes

- `pysuperluxcore.Scene(props)` single-Properties overload is the
  resize-policy ctor (empty scene) — build scenes via
  `pysuperluxcore.Scene()` then `scene.Parse(props)`.
- `session.Parse(props)` handles FILM properties only; scene edits go
  through `scene.UpdateObjectTransformation()` etc. between
  `session.BeginSceneEdit()/EndSceneEdit()`.
- PATHOCL + dual GPU aliases: on Apple Silicon, leaving
  `opencl.devices.select` empty picks BOTH OPENCL_GPU and METAL_GPU
  (same physical GPU) and crashes inside AGX OpenCL-over-Metal encode
  (pre-existing, unrelated to spilling — reproduces with spill off).
  Select a single device, e.g. `opencl.devices.select = "01"`.
- Live geometry edits re-upload from spilled staging transparently:
  CompileGeometry rebuilds the SpillableArrays (mutating ops pull them
  back to heap), the upload then re-spills — verified by a second
  "Host staging spilled" log line after EndSceneEdit.
- `RenderConfig.GetProperties()` returns a CLONED Properties (owned).
  The native method returns a `const unique_ptr&` which py::smart_holder
  cannot materialize on the non-owning wrapper from
  `RenderSession.GetRenderConfig()` — it threw
  "Non-owning holder (load_as_shared_ptr)". `GetRenderConfig` also has
  `py::keep_alive<0,1>` so the borrowed config keeps the session alive.
  Scalar reads can still use `config.GetProperty(name)` (returns by copy,
  always safe). `SessionWorker` keeps `worker.scene` = the exported
  scene rather than re-fetching via `GetRenderConfig().GetScene()`.

