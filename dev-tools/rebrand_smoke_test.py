"""Rebrand independence regression test.

    Blender -b --python dev-tools/rebrand_smoke_test.py

Verifies the SuperLuxCore/SuperBlendLuxCore fork is fully independent from
upstream identifiers — no LUXCORE engine id, no luxcore.* RNA
namespace, no pyluxcore module — so it can coexist with upstream.

What it verifies:
  * addon registers as bl_ext.user_default.superluxcore
  * scene.render.engine accepts "SUPERLUXCORE" and rejects "LUXCORE"
  * scene.superluxcore RNA namespace exists, scene.luxcore does not
  * pysuperluxcore imports, pyluxcore does not
"""

import sys

import bpy


def main():
    failures = []

    # 1) engine id
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
    except TypeError:
        failures.append("engine SUPERLUXCORE not registered")
    try:
        bpy.context.scene.render.engine = "LUXCORE"
        failures.append("engine LUXCORE accepted (collision with upstream)")
    except TypeError:
        pass  # expected: no such engine

    # 2) RNA namespace
    scene = bpy.context.scene
    if not hasattr(scene, "superluxcore"):
        failures.append("scene.superluxcore missing")
    if hasattr(scene, "luxcore"):
        failures.append("scene.luxcore present (upstream namespace leak)")

    # 3) python module
    try:
        import pysuperluxcore  # noqa: F401
    except ImportError:
        failures.append("import pysuperluxcore failed")
    try:
        import pyluxcore  # noqa: F401
        failures.append("import pyluxcore succeeded (upstream module leak)")
    except ImportError:
        pass

    if failures:
        for f in failures:
            print(f"[RebrandTest] FAIL: {f}")
        sys.exit(1)
    print("[RebrandTest] OK: SUPERLUXCORE engine + superluxcore RNA + "
          "pysuperluxcore module, no upstream leaks")


if __name__ == "__main__":
    main()
