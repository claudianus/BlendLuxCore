_needs_reload = "bpy" in locals()
import bpy

from .. import export, draw, pysuperluxcore
from ..export.image import ImageExporter
import pysuperluxcore

if _needs_reload:
    import importlib
    modules = (
        export,
        draw,
        pysuperluxcore,
    )
    for module in modules:
        importlib.reload(module)


def handler():
    ImageExporter.cleanup()

    # Workaround for a bug in SuperLuxCore:
    # We have to uninstall the log handler to prevent a crash.
    # https://github.com/LuxCoreRender/SuperLuxCore/issues/29
    pysuperluxcore.SetLogHandler(None)
