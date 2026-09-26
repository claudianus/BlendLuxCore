# upstream_coexistence

Builds **BlendLuxCore Upstream** (`blendluxcore_up`): the stock upstream
BlendLuxCore addon repacked so it installs alongside SuperLuxCore
(`blendluxcore`) and both render engines appear in Blender's engine menu.

See `../../UPSTREAM_COEXISTENCE.md` for the isolation design (why the
upstream pyluxcore must never share a process with another pyluxcore build
and how the subprocess render path works).

## Build

```sh
./build.sh                 # produces dist/blendluxcore_up-2.11.1-macos_arm64.zip
./build.sh --install       # ... and installs into Blender 5.2 user extensions
./build.sh --keep-work     # keep intermediate staging (debugging)
```

Pinned inputs (top of `build.sh`):

- upstream `LuxCoreRender/BlendLuxCore` @ `5e204a7` (v2.11.1)
- PyPI `pyluxcore==2.11.2` `cp313` macOS arm64 wheel

## Pipeline

1. `transform_upstream.py` — tokenizer-based rename of every colliding
   identifier/string: package name, `pyluxcore`→`pyluxcore_upstream`,
   `LUXCORE`→`LUXCOREUP` engine id, `.luxcore`→`.luxcore_up` properties,
   `luxcore.*`→`luxcore_up.*` operators, node tree ids, all bpy-registered
   class names (`LuxCore*`→`LuxCoreUp*`, others→`*_UP`).
2. `post_patch.sh` — private pyluxcore loader with shell/worker split,
   subprocess render dispatch in `engine/base.py`, `render_worker.py`,
   label + manifest.
3. Wheel vendoring — `pyluxcore/`, `pyluxcoretools/`, dist-info into `bin/`;
   `libtbbmalloc_proxy` is stripped (process-wide malloc interposer that
   crashes Blender on macOS) and all binaries ad-hoc re-signed.

## Install

Blender → Preferences → Extensions → "Install from Disk" → the zip, or
`./build.sh --install`. Enable **both** `blendluxcore` and `blendluxcore_up`;
the render engine menu then shows `SuperLuxCore` and `LuxCoreRender Upstream`.

## Limitations (by design)

- Upstream renders run in isolated `--factory-startup` Blender workers:
  final render = one worker per F12; viewport render = one persistent
  worker streaming frames to the viewport (restart-per-change latency —
  good for performance/quality comparison, not interactive lookdev).
- Material preview renders are skipped for the upstream engine.
- Only the `Combined` pass is forwarded to the Render Result.
- A scene must exist as a .blend-copy during render (handled
  automatically; adds ~1 s overhead plus worker startup).

## Do not overwrite the installed extension

The installed `blendluxcore_up/` directory is a generated artifact —
never rsync/cp BlendLuxCore repo sources into it (breaks both engines:
duplicate `LUXCORE` engine id + node-category clashes). Rebuild instead.
`dev-tools/sync_dev_install.sh` guards this via manifest-id check;
each built package also carries `GENERATED_DO_NOT_OVERWRITE.txt`.
See `UPSTREAM_COEXISTENCE.md` → "Installed extension is a generated artifact".
