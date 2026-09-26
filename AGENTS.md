# Agent notes

## Installed Blender extensions

- `extensions/user_default/blendluxcore` — this repo's add-on, synced via
  `dev-tools/sync_dev_install.sh` (default `EXT_ID=blendluxcore`).
- `extensions/user_default/blendluxcore_up` — **generated artifact** of
  `tools/upstream_coexistence/build.sh` (transformed upstream BlendLuxCore).
  NEVER deploy repo sources into it: both engines break (duplicate
  `LUXCORE` id, node-category clashes, pyluxcore collision). Update it only
  by rebuilding: `tools/upstream_coexistence/build.sh --install`.
  Details: `UPSTREAM_COEXISTENCE.md`.

## pyluxcore wheel install chain (verified 2026-09)

- The add-on's wheel manager runs at startup: it copies the wheel from
  `LuxCore/out/install/Release/wheel/*.whl` into
  `extensions/user_default/blendluxcore/wheels/`, then unpacks it into
  `extensions/.local/lib/python3.13/site-packages/pyluxcore`.
- It re-does this whenever `pyluxcore_installation_info.txt` is missing
  or mismatched — so a hand-copied .so in site-packages is silently
  replaced on the next Blender launch. Deploy via
  `dev-tools/sync_dev_install.sh` (updates both site-packages AND the
  cached wheel), never by copying the .so alone.
- `pip install --target site-packages` also works but pip may serve a
  cached stale build for a same-version wheel — use `--no-cache-dir`
  or just let sync_dev_install.sh handle it.
- macOS: any .so placed outside the pip flow needs
  `codesign --force --sign - <so>`.

## External-process render

- `scene.luxcore.config.external_process` serializes the scene+config to
  a .bcf (RenderConfig.Save) and renders it in a detached
  `python3 external_render_runner.py` process; render() returns
  immediately so Blender releases the depsgraph.
- The runner must poll `session.HasDone()` + `session.UpdateStats()`:
  halt conditions are evaluated inside `Film::RunTests()` which only
  runs during `UpdateFilm` — a bare `WaitForDone()` never returns.
- `opencl.devices.select` is stripped before serializing: the child
  enumerates devices itself and a mismatched-length selection aborts.

## Out-of-core spilling (scene.spill.*)

- `config.spill_geometry` + `spill_geometry_minmb` + `spill_images`
  map to `scene.spill.enable/.minbytes/.images` scene properties.
- Geometry buffers spill BEFORE the DataSet/BVH build (Embree binds
  the mapped addresses); image maps spill AFTER
  `imgMapCache.Preprocess` (resize policies + color conversion done).
- Spill files are unlinked right after mmap: the mapping stays valid,
  files self-clean on exit, empty `luxcore-geospill/<ts>-<ptr>/` dirs
  in TMPDIR are normal.
- File-backed pages are demand-paged and reclaimable — this is real
  out-of-core capacity, not free RAM: hot pages still occupy memory.
- `ImageMapStorageImpl::pixels` is a `shared_ptr<ImageMapPixel[]>`,
  not a vector — indexed access works, but no `begin()/emplace_back()`.
  Serialization uses `make_array` (raw elements) so mapped storage
  round-trips through .bcf.


## Standalone pyluxcore notes

- `pyluxcore.Scene(props)` single-Properties overload is the
  resize-policy ctor (empty scene) — build scenes via
  `pyluxcore.Scene()` then `scene.Parse(props)`.
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
