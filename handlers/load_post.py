_needs_reload = "bpy" in locals()

import os
import tempfile
import bpy
from bpy.app.handlers import persistent

import pysuperluxcore
from .. import utils, operators
from ..utils import compatibility
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

def _init_persistent_cache_file_path(settings, suffix):
    if not settings.file_path:
        blend_name = utils.get_blendfile_name()
        if blend_name:
            pgi_path = "//" + blend_name + "." + suffix
        else:
            # Blend file was not saved yet
            pgi_path = os.path.join(tempfile.gettempdir(), "Untitled." + suffix)
        settings.file_path = pgi_path


def _init_SuperLuxCoreOnlineLibrary():
    user_preferences = utils.get_addon_preferences(bpy.context)
    ui_props = bpy.context.scene.superluxcoreOL.ui

    bpy.context.scene.superluxcoreOL.on_search = False
    bpy.context.scene.superluxcoreOL.search_category = ""

    ui_props.assetbar_on = False
    ui_props.turn_off = False
    ui_props.ToC_loaded = False
    bpy.context.scene.superluxcoreOL.model.thumbnails_loaded = False
    bpy.context.scene.superluxcoreOL.scene.thumbnails_loaded = False
    bpy.context.scene.superluxcoreOL.material.thumbnails_loaded = False

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
            scene.superluxcore.config.device = "CPU"

        # Use Blender output path for filesaver by default
        if not scene.superluxcore.config.filesaver_path:
            scene.superluxcore.config.filesaver_path = scene.render.filepath

        _init_persistent_cache_file_path(scene.superluxcore.config.photongi, "pgi")
        _init_persistent_cache_file_path(scene.superluxcore.config.envlight_cache, "env")
        _init_persistent_cache_file_path(scene.superluxcore.config.dls_cache, "dlsc")

        _init_SuperLuxCoreOnlineLibrary()

    # Run converters for backwards compatibility
    compatibility.run()

    # A loaded file can rewire datablock pointers entirely; persistent
    # render scenes and dirty maps from before the load are invalid.
    persistent_scene.clear_all()

    frame_change_pre.have_to_check_node_trees = False
    SuperLuxCoreErrorLog.clear()

    # After loading a .blend file, make it possible to execute the conversion operator again
    SUPERLUXCORE_OT_convert_to_v23.was_executed = False
