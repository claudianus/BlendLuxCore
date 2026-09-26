# Agent notes

## Legacy upstream file compatibility (verified 2026-09)

- Upstream BlendLuxCore files store every prop group under the
  `"luxcore"` ID key (`world["luxcore"]["gain"]`, `mat["luxcore"]
  ["node_tree"]`, `scene["luxcore"]["config"]`) and node types under
  `LuxCore*`/`luxcore_*` names. Registered PointerProperty group
  members NEVER surface in `id["<prop>"]` — RNA groups live in their
  own storage; `id["..."]` only contains unregistered/ID-property data.
  An RNA `luxcore` alias prop therefore reads defaults, NOT the file's
  stored dict — `properties/legacy.py` reads the raw
  `IDPropertyGroup` instead (enums come back as ints, converted via
  `prop.enum_items` value matching; resolved pointers like
  `["node_tree"]` arrive as real datablocks, dangling ones as empty
  groups → None).
- `LuxCoreLegacyBridge.__getattribute__` on each root group resolves
  prop reads: authored `superluxcore` value (is_property_set, pointers
  need non-empty group via _group_has_authored_leaf) > stored
  `luxcore` value > RNA default. Reads never write.
- `utils.misc.use_cycles_compat` / `material_use_cycles_nodes` decide
  light/world/material interpretation purely from stored data: legacy
  `luxcore` or authored `superluxcore` -> native path; nothing stored
  -> Cycles translation fallback. A `use_cycles_settings` member stored
  by old files is still honored (it persists as an unregistered ID-prop
  member).
- Load handlers must not write to datablocks: `compatibility.run()`
  (node/socket rewriting) no longer runs on load — it stays available
  through `SUPERLUXCORE_OT_convert_to_v23`. Cache paths
  (photongi/envlight/dlsc) and `filesaver_path` resolve lazily at
  export; LOL UI resets go through `_setif_changed`.
- Node aliases: `nodes/__init__.py` registers one subclass per
  SuperLuxCore node/socket/tree class under the upstream bl_idname;
  `utils.node` maps both name families (`legacy_idname`,
  `canonical_idname`, TREE_TYPES includes both).
- Regression: `dev-tools/e46_legacy_bridge_test.py` (HALL_BENCH),
  `dev-tools/e45_cycles_light_defaults_test.py` (Cycles fallback).

## Installed Blender extensions

- `extensions/user_default/superluxcore` — this repo's add-on, synced via
  `dev-tools/sync_dev_install.sh` (default `EXT_ID=superluxcore`).

## pysuperluxcore wheel install chain (verified 2026-09)

- The add-on's wheel manager runs at startup: it copies the wheel from
  `SuperLuxCore/out/install/Release/wheel/*.whl` into
  `extensions/user_default/superluxcore/wheels/`, then unpacks it into
  `extensions/.local/lib/python3.13/site-packages/pysuperluxcore`.
- It re-does this whenever `pysuperluxcore_installation_info.txt` is missing
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

- `scene.superluxcore.config.external_process` serializes the scene+config to
  a .bcf (RenderConfig.Save) and renders it in a detached
  `python3 external_render_runner.py` process; render() returns
  immediately so Blender releases the depsgraph.
- The runner must poll `session.HasDone()` + `session.UpdateStats()`:
  halt conditions are evaluated inside `Film::RunTests()` which only
  runs during `UpdateFilm` — a bare `WaitForDone()` never returns.
- `opencl.devices.select` is stripped before serializing: the child
  enumerates devices itself and a mismatched-length selection aborts.

## Stop conditions (halt)

- `utils.get_halt_conditions(scene)` returns the halt of the view layer
  named by `utils.view_layer.State.active_view_layer` ONLY when that
  layer's `halt.enable` is set; outside an export (state `""`) it always
  returns the scene halt. Layer overrides are opt-in:
  `SuperLuxCoreViewLayerHaltConditions` (halt.py) subclasses the shared
  group with `enable=False` — the shared class's `enable=True` default
  once made every layer silently override global stop conditions.
- `batch.halttime` is SAMPLING time: the engine restarts the film clock
  after kernel compilation/thread (re)start (`RestartSampleClock()` in
  RenderEngine::Start/EndSceneEdit), so a first-time ~100 s Metal
  compile does not consume the user's time limit.

## Out-of-core spilling (scene.spill.*)

- `config.spill_geometry` + `spill_geometry_minmb` + `spill_images`
  map to `scene.spill.enable/.minbytes/.images` scene properties.
- Geometry buffers spill BEFORE the DataSet/BVH build (Embree binds
  the mapped addresses); image maps spill AFTER
  `imgMapCache.Preprocess` (resize policies + color conversion done).
- Spill files are unlinked right after mmap: the mapping stays valid,
  files self-clean on exit, empty `superluxcore-geospill/<ts>-<ptr>/` dirs
  in TMPDIR are normal.
- File-backed pages are demand-paged and reclaimable — this is real
  out-of-core capacity, not free RAM: hot pages still occupy memory.
- `ImageMapStorageImpl::pixels` is a `shared_ptr<ImageMapPixel[]>`,
  not a vector — indexed access works, but no `begin()/emplace_back()`.
  Serialization uses `make_array` (raw elements) so mapped storage
  round-trips through .bcf.


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

## Phantom mesh lights (Cycles emission compat)

- Blender 5.x defaults Principled to Emission Strength=1.0 + black
  Emission Color. Emitting `scale(strength, color)` for that makes a
  NON-constant texture — SuperLuxCore nulls only literal constant
  zero/black emissions (`parsematerials.cpp`), so the material stayed
  `IsLightSource()` and every triangle became a mesh light (546,123
  fake lights on the ASiO scene: multi-GB task buffers + light BVH).
- Fix: `cycles_node_reader` folds/skips provably-zero emission
  (`_is_zero`, `_tex_binary` constant folding) in the Principled,
  standalone-Emission and AddShader paths — emits constant `0.0`
  instead. Linked/textured emission is untouched.
- Regression: `dev-tools/e44_black_emission_test.py` (7 cases:
  black/zero/real emission on Principled, Emission node, Add Shader).

## Blender 5.2 RNA/API gotchas

- `CurveMap` lost `.evaluate()` — call
  `curve_mapping.evaluate(curve_map, position)` (helper
  `_evaluate_curve` keeps both signatures).
- RNA writes ("Writing to ID classes in this context is not allowed")
  inside render/viewport callbacks: `utils_compatibility.run()` is
  wrapped in try/RuntimeError during export (load_post already ran the
  same upgrades); `find_suggested_clamp_value` swallows the
  RuntimeError; `config._enabled_gpu_devices` falls back to reading
  `GetOpenCLDeviceDescs()` when the device collection can't be
  lazily populated.
- `get_current_view_layer()` returns None outside the final-render
  path (`State.active_view_layer` unset — direct export calls, tests);
  `aovs.convert` falls back to `view_layers[0]` instead of dropping
  all film outputs.
- Empty `config.convert()` result (export exception) is checked with
  `str(config_props) == ""` in BOTH `export_scene` (raises) and
  `get_viewport_changes` (skips the config-cache diff) — never feed an
  empty config to the session worker: `renderengine.type` would be
  undefined downstream. All `config_props.Get("renderengine.type")`
  sites use an explicit fallback.

## .lxm mesh proxy

- `scene.objects.X.ply = file.lxm` loads a raw-section mesh proxy:
  `ExtTriangleMesh::LoadProxy` mmaps the file MAP_PRIVATE and adopts
  each 64-byte-aligned section in place — no PLY parse, no heap copy,
  pages evictable (out-of-core by construction). Convert with
  `scene.SaveMesh(meshName, "x.lxm")`.
- Format: 128-byte header (magic "LXM1", version, counts, layer
  masks) + aligned raw sections: verts, tris, normals?, uv/col/alpha/
  vertAOV/triAOV layers. Same-build portability only (raw POD dump);
  load-time validation covers truncation, bad magic, and crafted
  element counts. Windows read-only path maps via FILE_MAP_READ
  fallback (MapFileCopyOnWrite is implemented — see below).
- Regression: `dev-tools/lxm_proxy_test.py` (byte-exact round-trip +
  720p render compare + error paths).

## Image map decode peak (resize policies)

- `scene.images.resizepolicy` FIXED/MINMEM now probe size via
  `ImageMap::GetSize()` (header only) and construct the ImageMap
  directly at the target resolution. `ImageMap::Init()` then either
  picks the smallest covering mip level (.tx) or, for non-mipped
  files, streams decode+downscale through a lazy tile-cached
  `ImageBuf` + `ImageBufAlgo::resize` — the full-resolution pixels
  never materialize in heap. Measured: 8192x8192 PNG → persistent
  MALLOC_LARGE 195MB -> 3MB.
- `ImageMap::Resize()` (post-hoc path) still holds source+dest
  buffers; only used for upscale (FIXED scale>1) now.
- Instrumentation (MINMEM) may still decide UINT_MAX = "keep
  original" and reload at full res — that reload is the classic
  full-decode path by design.
- macOS note: `ps rss`/`ru_maxrss` lag/miss allocator-cached regions;
  use `vmmap -summary` MALLOC_LARGE for real heap attribution.
- Windows: `MapFileCopyOnWrite` now implemented
  (CreateFileMapping/PAGE_WRITECOPY + FILE_MAP_COPY); read-only files
  fall back to FILE_MAP_READ. `SpillToFile` uses
  FILE_FLAG_DELETE_ON_CLOSE as the unlink-after-mmap equivalent.

## Test

- `.lxm` sections are stored spatially ordered (header flag bit1):
  triangles Morton-sorted by centroid, vertices first-use-renumbered,
  unreferenced vertices dropped — page-local reads under memory
  pressure. The loader is order-agnostic; tests verify geometry as
  multisets / via implied permutations, not raw byte order.
- `dev-tools/imagemap_stream_test.py` — standalone pysuperluxcore test:
  8192x8192 non-mipped PNG, NONE vs FIXED-256 vs MINMEM; checks the
  "streaming resize" path fires and renders correctly at 1280x720.

## Mesh proxies (.lxm)

- `obj.superluxcore.proxy_filepath` (Object Properties > Mesh Proxy) emits
  `scene.objects.X.ply` instead of converting the mesh — SuperLuxCore mmaps
  the .lxm copy-on-write. `superluxcore.bake_lxm_proxy` bakes evaluated
  geometry. File key = path+mtime+size, so re-bakes re-export.
- `config.proxy_auto` + `proxy_auto_mintris` (Render Properties >
  SuperLuxCore Tools > Automatic Mesh Proxy): heavy static meshes are baked
  per material slot to `tempfile.mkdtemp(superluxcore_autoproxy_*)` at
  export. Dedup/staleness signature = data name + vert/poly/tris +
  modifier names + 64 sampled vertex coords (count-preserving edits
  detected). Displacement and deform-motion-blur objects are excluded.
- Persistent-scene delta: proxied objects are `has_shape_wrapper=True`
  (re-export, never in-place DefineMesh) and `_mesh_inplace_safe`
  vetoes proxy-eligible objects so the .ply ref stays authoritative.
- proxy_paths is a {slot: path} dict — multi-material objects emit
  one .lxm per material slot.

## .lxm proxies + auto-proxy

- Manual: `obj.superluxcore.proxy_filepath` (Object > Mesh Proxy) or
  `superluxcore.bake_lxm_proxy`. Proxy objects skip mesh conversion entirely
  — only `scene.objects.X.ply = <path>` is emitted; SuperLuxCore maps the
  file. Single material only, no displacement/motion blur.
- Auto: `config.proxy_auto` + `proxy_auto_mintris` bakes heavy
  evaluated meshes to `tempfile.gettempdir()/superluxcore_autoproxy/*.lxm`
  (module-level `_auto_proxies` dict survives cache rebuilds; stale
  `ap_*` files >24h swept once per process). Signature = data name +
  counts + modifier types + 64-vertex position sample hash.
- External file changes: `geo_meta` records (path, mtime_ns, size) —
  persistent-scene reuse stats proxy files; `handlers/proxy_watch.py`
  timer (2s) marks objects updated on change for viewport live reload.
- bool scene props via SetFromString: use `1` not `true`, or typed
  `pysuperluxcore.Property(name, True)` — "true" string fails bool parse.
- World > HDRI > `cdfdim` caps env importance CDF (block-summed,
  unbiased; default 4096, 0=unlimited).


## Cycles light/world auto-resolution (use_cycles_settings)

- `light.superluxcore.use_cycles_settings` / `world.superluxcore.…` now
  default **True** — a .blend authored for Cycles stores no SuperLuxCore
  prop values, so `utils.misc.resolve_use_cycles_settings` routes it
  through the Cycles converter automatically (the ASiO Sun at
  energy=1000 exported through the native path used the `sun_sky_gain`
  default 2e-5 — ~500x too dark vs Cycles).
- Resolution order: explicitly stored flag wins; if the flag was never
  set, native-only prop writes on the datablock pin it to the native
  path, otherwise Cycles. Shared props (`importance`, `link_groups`,
  `lightgroup`) and `rna_type`/`name` never pin native.
- `compatibility.run()` writes the resolved value into the flag at
  load_post so the UI checkbox matches the export mode on old files.
- Cycles sun → `distant`/`sharpdistant` with
  `gain = energy / (2π(1-cos θ))`; Cycles world with unlinked Surface
  emits nothing. Regression: `dev-tools/e45_cycles_light_defaults_test.py`.

## Deferred RNA writes from the render callback

- RNA writes throw RuntimeError inside `bpy.ops.render.render`
  (`Writing to ID classes in this context`) but are legal in
  `render_complete` handlers. `utils.render.find_suggested_clamp_value`
  stashes the value in `_pending_suggested_clamp` on RuntimeError;
  `handlers/render_complete.py` flushes it (render_complete fires before
  `bpy.ops.render.render` returns, so post-render reads see the value).
- `engine/final.py`: the clamp-suggestion check must run BEFORE the
  `HasDone()` break — a fast render can hit the halt condition in the
  first stats update and would otherwise never record the suggestion.
