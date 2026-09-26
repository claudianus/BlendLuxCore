# Diffraction material node

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

## Diffraction material node

`nodes/materials/diffraction.py` — exports the engine's `diffraction`
type (SuperLuxCore). Props: kr, spacing (nm — UI shows live lines/mm),
roughness, fillfactor, orientation (u|v|radialuv|radial), center /
centeru/centerv, blaze (scene prop is DEGREES — node stores radians via
subtype ANGLE, export converts with math.degrees), orders. Spectral-mode
hint when `config.spectral_enable` is off.

