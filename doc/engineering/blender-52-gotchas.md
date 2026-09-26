# Blender 5.2 RNA/API gotchas

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

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

