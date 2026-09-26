# Deployment: installed extension, wheel chain, external render

> Engineering note for SuperBlendLuxCore — extracted from AGENTS.md.
> Feature/user-facing docs live in `doc/features/` (SuperLuxCore) or `doc/` (SuperBlendLuxCore).

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

