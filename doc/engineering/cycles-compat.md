# Cycles compatibility findings

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

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

