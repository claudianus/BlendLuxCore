#!/bin/bash
# Post-transform patches for the upstream coexistence build.
# Usage: post_patch.sh <pkg_dir>
set -e
PKG="$1"

# 1) Private pyluxcore loader (replaces pip/wheel machinery entirely).
#    Shell mode: in the main Blender process a stub module is registered so the
#    native .so is never loaded next to another pyluxcore build (dyld
#    weak-symbol coalescing -> EXC_ARM_DA_ALIGN). Worker mode loads for real.
cat > "$PKG/luxloader/__init__.py" <<'PYEOF'
"""Private pyluxcore loader for the upstream coexistence build.

Loads the vendored upstream wheel package under the dedicated module name
'pyluxcore_upstream' so it can coexist with another BlendLuxCore-based
installation (e.g. SuperLuxCore) in the same Blender process, without
sharing sys.modules['pyluxcore'] or the shared extensions .local
site-packages.
"""

import importlib.util
import os
import sys
import types

from .. import utils

ROOT_FOLDER = utils.get_module_path()
BIN_FOLDER = ROOT_FOLDER / "bin"
PKG_FOLDER = BIN_FOLDER / "pyluxcore"

# Set only inside the isolated render worker process. Two pyluxcore builds in
# one process corrupt each other through dyld weak-symbol coalescing
# (EXC_ARM_DA_ALIGN in Scene::Preprocess), so the main Blender process never
# loads the native module: it registers a stub instead and renders via a
# --factory-startup subprocess where this addon is the only pyluxcore owner.
WORKER_ENV_VAR = "LUXCORE_UP_WORKER"


def in_worker_process():
    return os.environ.get(WORKER_ENV_VAR) == "1"


class _StubValue:
    """Universal no-op returned for any pyluxcore_upstream attribute."""

    def __call__(self, *args, **kwargs):
        return _StubValue()

    def __getattr__(self, name):
        return _StubValue()

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __getitem__(self, key):
        return _StubValue()

    def __bool__(self):
        return False

    def __int__(self):
        return 0

    def __float__(self):
        return 0.0

    def __str__(self):
        return ""

    def __eq__(self, other):
        return False

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return False

    __hash__ = object.__hash__


def _make_stub_module():
    module = types.ModuleType("pyluxcore_upstream")
    module.__version__ = "isolated"
    module.__file__ = str(PKG_FOLDER / "__init__.py")
    module.__getattr__ = lambda name: _StubValue()
    return module


def _pyluxcore_entries():
    return {
        k: sys.modules[k]
        for k in list(sys.modules)
        if k == "pyluxcore" or k.startswith("pyluxcore.")
    }


def ensure_pyluxcore():
    """Load bundled upstream pyluxcore as sys.modules['pyluxcore_upstream']."""
    if "pyluxcore_upstream" in sys.modules:
        return

    if not in_worker_process():
        sys.modules["pyluxcore_upstream"] = _make_stub_module()
        return

    pkg_init = PKG_FOLDER / "__init__.py"
    if not pkg_init.is_file():
        raise RuntimeError(
            "[BLC-upstream] bundled pyluxcore package not found in "
            + str(PKG_FOLDER)
        )

    # The spec name must be 'pyluxcore' so PyInit_pyluxcore resolves.
    spec = importlib.util.spec_from_file_location(
        "pyluxcore",
        str(pkg_init),
        submodule_search_locations=[str(PKG_FOLDER)],
    )
    module = importlib.util.module_from_spec(spec)

    saved = _pyluxcore_entries()
    # The package __init__ does 'from .pyluxcore import *'; the parent must be
    # registered under its real name during exec for the relative import, and
    # any pre-existing pyluxcore* entries (e.g. another addon's binary) must be
    # hidden so the import machinery actually loads our bundled submodule.
    for k in saved:
        sys.modules.pop(k, None)
    sys.modules["pyluxcore"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # Drop every pyluxcore* entry created while loading (single-phase
        # extension init registers itself in sys.modules), then restore the
        # entries another addon (e.g. SuperLuxCore) may own.
        for k in _pyluxcore_entries():
            sys.modules.pop(k, None)
        sys.modules.update(saved)

    sys.modules["pyluxcore_upstream"] = module
PYEOF

# 2) __init__.py: importlib.metadata has no dist for the private module
python3 - "$PKG/__init__.py" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
old = """    pyluxcore_upstream.Init(utils.log.LuxCoreUpLog.add)
    print(
        f"BlendLuxCore {utils.get_version_string()} registered "
        f"(with pyluxcore_upstream {version('pyluxcore_upstream')})"
    )"""
new = """    pyluxcore_upstream.Init(utils.log.LuxCoreUpLog.add)
    try:
        _plc_ver = version("pyluxcore_upstream")
    except Exception:
        _plc_ver = getattr(pyluxcore_upstream, "__version__", "bundled")
    print(
        f"BlendLuxCore Upstream {utils.get_version_string()} registered "
        f"(with pyluxcore {_plc_ver})"
    )"""
assert old in t, "register() block not found"
p.write_text(t.replace(old, new, 1))
PYEOF

# 3) Engine label + isolated subprocess render dispatch
python3 - "$PKG/engine/base.py" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()

# 3a) label
old = '    bl_idname = "LUXCOREUP"\n    bl_label = "LuxCoreRender"'
new = '    bl_idname = "LUXCOREUP"\n    bl_label = "LuxCoreRender Upstream"'
assert old in t, "engine label block not found"
t = t.replace(old, new, 1)

# 3b) imports
old = """from time import sleep
_needs_reload = "bpy" in locals()

import bpy
import pyluxcore_upstream
from . import final, preview, viewport
from .. import icons, utils, properties"""
new = """from time import sleep
_needs_reload = "bpy" in locals()

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bpy
import pyluxcore_upstream
from . import final, preview, viewport
from .. import icons, luxloader, remote_viewport, utils, properties"""
assert old in t, "imports block not found"
t = t.replace(old, new, 1)

# 3b2) __del__: also stop the viewport worker when the engine dies
old = """    def __del__(self):
        # Note: this method is also called when unregister() is called (for some reason I don't understand)
        try:
            if getattr(self, "session", None):"""
new = """    def __del__(self):
        # Note: this method is also called when unregister() is called (for some reason I don't understand)
        try:
            if not luxloader.in_worker_process():
                remote_viewport.shutdown()
            if getattr(self, "session", None):"""
assert old in t, "__del__ block not found"
t = t.replace(old, new, 1)

# 3c) render_final dispatch
old = """    def render_final(self, depsgraph):
        try:"""
new = """    def render_final(self, depsgraph):
        if not luxloader.in_worker_process():
            # The native upstream module must not share a process with another
            # pyluxcore build, so final renders run in an isolated subprocess.
            self._render_subprocess(depsgraph)
            return
        try:"""
assert old in t, "render_final block not found"
t = t.replace(old, new, 1)

# 3d) preview/viewport guards + _render_subprocess
old = """    def render_preview(self, depsgraph):
        try:
            preview.render(self, depsgraph)
        except Exception as error:
            import traceback
            traceback.print_exc()
            # Clean up
            del self.session
            self.session = None

    def view_update(self, context, depsgraph):
        viewport.view_update(self, context, depsgraph)

    def view_draw(self, context, depsgraph):
        try:"""
new = '''    def render_preview(self, depsgraph):
        if not luxloader.in_worker_process():
            # Material previews stay in-process and would need the native
            # module; skip them in shell mode (final render is isolated).
            return
        try:
            preview.render(self, depsgraph)
        except Exception as error:
            import traceback
            traceback.print_exc()
            # Clean up
            del self.session
            self.session = None

    def _render_subprocess(self, depsgraph):
        """Run the final render in an isolated Blender process.

        Two different pyluxcore builds must never share one process (dyld
        weak-symbol coalescing corrupts them -> EXC_ARM_DA_ALIGN), so the
        scene is saved to a temp .blend and rendered by a --factory-startup
        worker where only this addon's pyluxcore is loaded.
        """
        scene = bpy.context.scene
        scale = scene.render.resolution_percentage / 100
        width = int(scene.render.resolution_x * scale)
        height = int(scene.render.resolution_y * scale)

        tmpdir = Path(tempfile.mkdtemp(prefix="luxcore_up_"))
        blend_path = tmpdir / "scene.blend"
        exr_path = tmpdir / "render.exr"
        worker = Path(__file__).resolve().parent.parent / "render_worker.py"

        try:
            self.update_stats("LuxCore Upstream", "Saving scene copy...")
            bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), copy=True)

            env = os.environ.copy()
            env[luxloader.WORKER_ENV_VAR] = "1"
            cmd = [
                bpy.app.binary_path, "--factory-startup", "-b",
                str(blend_path), "--python", str(worker), "--", str(exr_path),
            ]
            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
            tail = []
            cancelled = False
            try:
                for line in proc.stdout:
                    tail.append(line.rstrip())
                    del tail[:-30]
                    stripped = line.strip()
                    if stripped.startswith("[LuxCore]") or stripped.startswith("[Engine"):
                        self.update_stats("LuxCore Upstream (isolated)", stripped[-100:])
                    if self.test_break():
                        cancelled = True
                        proc.kill()
                        break
                proc.wait()
            finally:
                if proc.poll() is None:
                    proc.kill()

            if cancelled:
                return
            if proc.returncode != 0 or not exr_path.exists():
                raise RuntimeError(
                    "Upstream render worker failed (see console):\\n"
                    + "\\n".join(tail)
                )

            img = bpy.data.images.load(str(exr_path))
            try:
                pixels = img.pixels[:]
                layer_name = bpy.context.view_layer.name
                self.add_pass("Combined", 4, "RGBA", layer=layer_name)
                result = self.begin_result(0, 0, width, height, layer=layer_name)
                result.layers[0].passes["Combined"].rect.foreach_set(pixels)
                self.end_result(result)
            finally:
                bpy.data.images.remove(img)
        except Exception as error:
            error_str = str(error)
            self.report({"ERROR"}, error_str)
            self.error_set(error_str)
            LuxCoreUpErrorLog.add_error(error_str)

    def view_update(self, context, depsgraph):
        if not luxloader.in_worker_process():
            # Native module absent in this process: the viewport render runs
            # in the isolated worker and streams frames back.
            remote_viewport.view_update(self, context, depsgraph)
            return
        viewport.view_update(self, context, depsgraph)

    def view_draw(self, context, depsgraph):
        if not luxloader.in_worker_process():
            remote_viewport.view_draw(self, context, depsgraph)
            return
        try:'''
assert old in t, "preview/viewport block not found"
t = t.replace(old, new, 1)

p.write_text(t)
PYEOF

# 4) Isolated render worker script
cat > "$PKG/render_worker.py" <<'PYEOF'
"""Isolated render worker for LuxCoreRender Upstream.

Invoked as:
    Blender --factory-startup -b scene.blend --python render_worker.py -- out.exr [halt_seconds]

LUXCORE_UP_WORKER=1 must be set so luxloader loads the real bundled
pyluxcore. --factory-startup ensures no other pyluxcore-owning addon is
enabled in this process.
"""

import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_exr = argv[0]
    halt_seconds = int(argv[1]) if len(argv) > 1 else 0

    bpy.ops.preferences.addon_enable(module="bl_ext.user_default.blendluxcore_up")

    scene = bpy.context.scene
    scene.render.engine = "LUXCOREUP"

    # Guarantee a halt condition so the isolated render can never run forever.
    halt = scene.luxcore_up.halt
    if not halt.is_enabled():
        halt.enable = True
        halt.use_time = True
        halt.time = halt_seconds or 300

    scene.render.filepath = out_exr
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"

    bpy.ops.render.render(write_still=True)
    print("UPSTREAM_WORKER_DONE")


main()
PYEOF

# 5) draw/viewport.py: private module can't be importlib.reload'ed
python3 - "$PKG/draw/viewport.py" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
old = "    importlib.reload(pyluxcore_upstream)"
new = """    try:
        importlib.reload(pyluxcore_upstream)
    except Exception:
        pass"""
assert old in t, "reload block not found"
p.write_text(t.replace(old, new, 1))
PYEOF

# 6) file names that were renamed inside code
mv "$PKG/preview_scene/LuxCore_preview.ply" "$PKG/preview_scene/LuxCoreUp_preview.ply"
mv "$PKG/properties/lol/LuxCoreOLScene.py" "$PKG/properties/lol/LuxCoreUpOLScene.py"

# 7) LOL thumbnail helper: addon lives under bl_ext.* now
python3 - "$PKG/scripts/LOL/render_thumbnail.py" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text()
old = "from BlendLuxCore.utils.compatibility import run"
new = """try:
    from BlendLuxCore.utils.compatibility import run
except ImportError:
    import importlib
    _pkg = next(
        m for m in sys.modules if m.endswith(".blendluxcore_up")
    )
    run = importlib.import_module(_pkg + ".utils.compatibility").run"""
assert old in t, "thumbnail import not found"
p.write_text(t.replace(old, new, 1))
PYEOF

# 8) manifest
cat > "$PKG/blender_manifest.toml" <<'TOMLEOF'
schema_version = "1.0.0"

id = "blendluxcore_up"
name = "BlendLuxCore Upstream"
tagline = "LuxCoreRender (upstream release)"
maintainer = "LuxCoreRender developers (coexistence repack)"
type = "add-on"

version = "2.11.1"

website = "https://luxcorerender.org"

tags = ["Render"]

blender_version_min = "4.5.0"

license = [
  "SPDX:GPL-3.0-or-later",
]

platforms = ["macos-arm64"]

wheels = []

[permissions]
network = "LuxCore Online Library access"
files = "File exchanges between renderer and denoiser"

[build]
paths_exclude_pattern = [
  "__pycache__/",
  "CMakeFiles/",
  ".*",
  "/out/*",
  "CMakeCache.txt",
  "cmake_install.cmake",
  "install_manifest.txt",
  "**/*.zip",
]
TOMLEOF

# 9) Overwrite-protection marker. This directory is a generated artifact;
#    other tooling/agents must never sync repo sources into it.
cat > "$PKG/GENERATED_DO_NOT_OVERWRITE.txt" <<'EOF'
THIS EXTENSION DIRECTORY IS A GENERATED BUILD ARTIFACT.

Do NOT copy, rsync, or deploy BlendLuxCore repository sources into it —
it must contain the transformed upstream package only. Overwriting it
with untransformed sources breaks both engines (duplicate LUXCORE
engine id, node-category clashes, pyluxcore module collision).

To update this extension, rebuild it:
    tools/upstream_coexistence/build.sh [--install]
See tools/upstream_coexistence/README.md and UPSTREAM_COEXISTENCE.md.

dev-tools/sync_dev_install.sh refuses to target this directory
(manifest id mismatch guard) — do not bypass that check.
EOF

# 10) Remote viewport: worker + main-process bridge (new modules)
cp "$(dirname "$0")/remote_viewport.py" "$PKG/remote_viewport.py"
cp "$(dirname "$0")/viewport_worker.py" "$PKG/viewport_worker.py"

# 11) Fake-context safety: the worker passes a duck-typed context whose
#     space_data is no real SpaceView3D, so visible_in_viewport_get() would
#     TypeError. Fall back to "visible" in that case.
python3 - "$PKG" <<'PYEOF'
import pathlib, sys
pkg = pathlib.Path(sys.argv[1])

helper = '''

def vp_visible(obj, context):
    try:
        return obj.visible_in_viewport_get(context.space_data)
    except Exception:
        return True
'''
utils = pkg / "utils/__init__.py"
t = utils.read_text()
assert "def vp_visible" not in t
utils.write_text(t + helper)

old = "not obj.visible_in_viewport_get(context.space_data)"
new = "not utils.vp_visible(obj, context)"
for rel in ("export/caches/__init__.py", "export/caches/object_cache.py"):
    p = pkg / rel
    t = p.read_text()
    assert old in t, f"visibility call not found in {rel}"
    p.write_text(t.replace(old, new))

# same fallback inside utils itself (is_instance_visible)
t = utils.read_text()
old = "if not viewport_vis_obj.visible_in_viewport_get(context.space_data):"
new = "if not vp_visible(viewport_vis_obj, context):"
assert old in t, "is_instance_visible call not found"
utils.write_text(t.replace(old, new))
PYEOF

echo "post-patch OK: $PKG"
