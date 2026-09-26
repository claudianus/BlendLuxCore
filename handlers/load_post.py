_needs_reload = "bpy" in locals()

import os
import bpy
from bpy.app.handlers import persistent

import pysuperluxcore
from .. import utils, operators
from . import frame_change_pre
from ..utils.errorlog import SuperLuxCoreErrorLog
from ..operators.manual_compatibility import SUPERLUXCORE_OT_convert_to_v23
from ..export.caches import persistent_scene

if _needs_reload:
    import importlib
    modules = (
        utils,
        operators,
        frame_change_pre,
    )
    for module in modules:
        importlib.reload(module)

def _setif_changed(obj, attr, value):
    """Assign only when the value differs, so loading a file does not
    dirty it with redundant bookkeeping writes."""
    try:
        if getattr(obj, attr) != value:
            setattr(obj, attr, value)
    except (AttributeError, TypeError):
        pass


def _init_SuperLuxCoreOnlineLibrary():
    user_preferences = utils.get_addon_preferences(bpy.context)
    ol = bpy.context.scene.superluxcoreOL
    ui_props = ol.ui

    _setif_changed(ol, "on_search", False)
    _setif_changed(ol, "search_category", "")
    _setif_changed(ui_props, "assetbar_on", False)
    _setif_changed(ui_props, "turn_off", False)
    _setif_changed(ui_props, "ToC_loaded", False)
    _setif_changed(ol.model, "thumbnails_loaded", False)
    _setif_changed(ol.scene, "thumbnails_loaded", False)
    _setif_changed(ol.material, "thumbnails_loaded", False)

    if not os.path.exists(user_preferences.global_dir):
        os.makedirs(user_preferences.global_dir)
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'model')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'model'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'model', 'preview')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'model', 'preview'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'model', 'preview', 'full')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'model', 'preview', 'full'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'material')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'material'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'material', 'preview')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'material', 'preview'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'material', 'preview', 'full')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'material', 'preview', 'full'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'scene')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'scene'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'scene', 'preview', 'full')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'scene', 'preview', 'full'))
    if not os.path.exists(os.path.join(user_preferences.global_dir, 'scene', 'preview')):
        os.makedirs(os.path.join(user_preferences.global_dir, 'scene', 'preview'))


@persistent
def handler(_):
    """ Note: the only argument Blender passes is always None """

    for scene in bpy.data.scenes:
        # Update OpenCL devices if .blend is opened on
        # a different computer than it was saved on
        scene.superluxcore.devices.update_devices_if_necessary()

        if pysuperluxcore.GetPlatformDesc().Get("compile.LUXRAYS_DISABLE_OPENCL").GetBool():
            # OpenCL not available, make sure we are using CPU device
            _setif_changed(scene.superluxcore.config, "device", "CPU")

        # filesaver_path and the persistent-cache paths are resolved
        # lazily at export (utils.get_persistent_cache_file_path /
        # export.config filesaver fallback) - writing them here would
        # modify the user's file just by opening it.

        _init_SuperLuxCoreOnlineLibrary()

    # Node-tree schema converters must NOT run on load: they remove and
    # rewrite user data silently ("데이터가 날아간다"). The translation
    # layer resolves legacy content at export/read time instead; the
    # manual SUPERLUXCORE_OT_convert_to_v23 operator still runs them on
    # explicit user request.

    # A loaded file can rewire datablock pointers entirely; persistent
    # render scenes and dirty maps from before the load are invalid.
    persistent_scene.clear_all()

    frame_change_pre.have_to_check_node_trees = False
    SuperLuxCoreErrorLog.clear()

    # After loading a .blend file, make it possible to execute the conversion operator again
    SUPERLUXCORE_OT_convert_to_v23.was_executed = False
