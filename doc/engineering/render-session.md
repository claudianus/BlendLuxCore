# Render session lifecycle: halt, callbacks, viewport restart

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

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

## Viewport config restart MUST include imagepipeline+halt props

- `config_cache.props` holds only `config.convert()` output —
  `film.imagepipelines.*` and `batch.halt*` are merged into the session
  config AFTER `config_cache.diff()` (`export_scene`), so they are NOT
  in the cache. A viewport config change restarts the session via
  `worker.submit_config` -> `SessionWorker._do_config` ->
  `RenderConfig(props, reused_scene)`.
- With no `film.imagepipelines.*` props the new film gets ONE EMPTY
  pipeline: `Film::GetOutput(RGB(A)_IMAGEPIPELINE)` then returns the
  merged raw HDR radiance (no tonemap) -> blown-out white viewport; on a
  film without the ALPHA channel (RGBA read on RGB film) GetOutput
  early-returns WITHOUT writing the buffer. Always submit
  `exporter.get_restart_config_props()` (config + imagepipeline + halt
  caches merged) — never `config_cache.props` alone.
- `draw/viewport.py::_fetch_pixels` uses `np.zeros` (not `np.empty`):
  an early-returned GetOutputFloat leaves zeros -> held as "empty film"
  instead of uploading uninitialized garbage (which `np.any(data>0)`
  accepts as content -> white). Same reason the FrameBuffer ctor
  zero-inits its gpu buffer.

## Temporary property mutation during export (post-restore trap)

Any code that temporarily mutates a scene/config property for export
and restores it afterwards (snapshot/restore pattern — e.g. the
Quick-Setup auto-config that was removed during the rebrand) makes the
mutated value invisible to every converter that runs AFTER the
restore. `export/halt.py`, image-pipeline and object-cache converters
all run post-restore, so the temporary values were silently dropped.

Rule: export-time consumers must read `wants_*`-style predicates or a
direct mapping, never rely on mutated property state surviving to
their call site.

