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
