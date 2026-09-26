from platform import system
from os import environ

# Fix problem of OpenMP calling a trap about two libraries loading because blender's
# openmp lib uses @loader_path and zip does not preserve symbolic links (so can't
# spoof loader_path with symlinks)
if system() == "Darwin":
    environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


_needs_reload = "bpy" in locals()

import bpy

from .. import utils as blc_utils

from . import (
    lol,
    aovs,
    camera,
    debug,
    general,
    imagepipeline,
    ior_presets,
    keymaps,
    light,
    lightgroups,
    manual_compatibility,
    material,
    multi_image_import,
    node_editor,
    node_tree_presets,
    pointer_node,
    proxy,
    pysuperluxcoretools,
    render,
    render_settings_helper,
    texture,
    world,
)


if _needs_reload:
    import importlib
    modules = (
        aovs,
        camera,
        debug,
        general,
        imagepipeline,
        ior_presets,
        keymaps,
        light,
        lightgroups,
        manual_compatibility,
        material,
        multi_image_import,
        node_editor,
        node_tree_presets,
        pointer_node,
        proxy,
        pysuperluxcoretools,
        render,
        render_settings_helper,
        texture,
        world,
        lol,
    )
    for module in modules:
        importlib.reload(module)

classes = (
    aovs.SUPERLUXCORE_OT_add_lpe,
    aovs.SUPERLUXCORE_OT_remove_lpe,
    camera.SUPERLUXCORE_OT_camera_new_volume_node_tree,
    camera.SUPERLUXCORE_OT_camera_unlink_volume_node_tree,
    camera.SUPERLUXCORE_OT_camera_set_volume_node_tree,
    camera.SUPERLUXCORE_VOLUME_MT_camera_select_volume_node_tree,
    camera.SUPERLUXCORE_OT_camera_show_volume_node_tree,
    debug.SUPERLUXCORE_OT_toggle_debug_options,
    debug.SUPERLUXCORE_OT_debug_restart,
    general.SUPERLUXCORE_OT_use_cycles_settings,
    general.SUPERLUXCORE_OT_use_cycles_nodes_everywhere,
    general.SUPERLUXCORE_OT_errorlog_clear,
    general.SUPERLUXCORE_OT_switch_texture_context,
    general.SUPERLUXCORE_OT_switch_space_data_context,
    general.SUPERLUXCORE_OT_switch_to_camera_settings,
    general.SUPERLUXCORE_OT_set_suggested_clamping_value,
    general.SUPERLUXCORE_OT_set_quality_preset,
    general.SUPERLUXCORE_OT_update_opencl_devices,
    general.SUPERLUXCORE_OT_add_node,
    general.SUPERLUXCORE_OT_attach_sun_to_sky,
    general.SUPERLUXCORE_OT_copy_error_to_clipboard,
    general.SUPERLUXCORE_OT_open_website,
    general.SUPERLUXCORE_OT_open_website_popup,
    general.SUPERLUXCORE_OT_select_object,
    imagepipeline.SUPERLUXCORE_OT_select_crf,
    imagepipeline.SUPERLUXCORE_OT_set_raw_view_transform,
    ior_presets.SUPERLUXCORE_OT_ior_preset_names,
    ior_presets.SUPERLUXCORE_OT_ior_preset_values,
    light.SUPERLUXCORE_OT_light_new_volume_node_tree,
    light.SUPERLUXCORE_OT_light_unlink_volume_node_tree,
    light.SUPERLUXCORE_OT_light_set_volume_node_tree,
    light.SUPERLUXCORE_VOLUME_MT_light_select_volume_node_tree,
    light.SUPERLUXCORE_OT_light_show_volume_node_tree,
    lightgroups.SUPERLUXCORE_OT_add_lightgroup,
    lightgroups.SUPERLUXCORE_OT_remove_lightgroup,
    lightgroups.SUPERLUXCORE_OT_select_objects_in_lightgroup,
    lightgroups.SUPERLUXCORE_OT_create_lightgroup_nodes,
    manual_compatibility.SUPERLUXCORE_OT_convert_to_v23,
    material.SUPERLUXCORE_OT_material_new,
    material.SUPERLUXCORE_OT_material_unlink,
    material.SUPERLUXCORE_OT_material_copy,
    material.SUPERLUXCORE_OT_material_set,
    material.SUPERLUXCORE_MT_material_select,
    material.SUPERLUXCORE_OT_material_select,
    material.SUPERLUXCORE_OT_material_show_nodetree,
    material.SUPERLUXCORE_OT_mat_nodetree_new,
    material.SUPERLUXCORE_OT_set_mat_node_tree,
    material.SUPERLUXCORE_MATERIAL_MT_node_tree,
    multi_image_import.SUPERLUXCORE_OT_import_multiple_images,
    node_editor.SUPERLUXCORE_OT_node_editor_viewer,
    node_editor.SUPERLUXCORE_OT_mute_node,
    node_editor.SUPERLUXCORE_OT_node_editor_add_image,
    node_tree_presets.SUPERLUXCORE_OT_preset_material,
    node_tree_presets.SUPERLUXCORE_MATERIAL_MT_node_tree_preset,
    pointer_node.SUPERLUXCORE_OT_pointer_unlink_node_tree,
    pointer_node.SUPERLUXCORE_OT_pointer_set_node_tree,
    pointer_node.SUPERLUXCORE_MT_pointer_select_node_tree,
    pointer_node.SUPERLUXCORE_OT_pointer_show_node_tree,
    proxy.SUPERLUXCORE_OT_bake_lxm_proxy,
    pysuperluxcoretools.SUPERLUXCORE_OT_install_pyside,
    pysuperluxcoretools.SUPERLUXCORE_OT_start_pysuperluxcoretools,
    render.SUPERLUXCORE_OT_request_denoiser_refresh,
    render.SUPERLUXCORE_OT_request_display_refresh,
    render.SUPERLUXCORE_OT_toggle_pause,
    render.SUPERLUXCORE_OT_stop_render,
    render_settings_helper.SUPERLUXCORE_OT_render_settings_helper,
    texture.SUPERLUXCORE_OT_texture_show_nodetree,
    texture.SUPERLUXCORE_OT_tex_nodetree_new,
    texture.SUPERLUXCORE_OT_texture_unlink,
    texture.SUPERLUXCORE_OT_texture_set_node_tree,
    texture.SUPERLUXCORE_MT_texture_select_node_tree,
    world.SUPERLUXCORE_OT_world_new_volume_node_tree,
    world.SUPERLUXCORE_OT_world_unlink_volume_node_tree,
    world.SUPERLUXCORE_OT_world_set_volume_node_tree,
    world.SUPERLUXCORE_VOLUME_MT_world_select_volume_node_tree,
    world.SUPERLUXCORE_OT_world_show_volume_node_tree,
    world.SUPERLUXCORE_OT_world_set_ground_black,
    world.SUPERLUXCORE_OT_create_sun_hemi,
)

submodules = (lol, keymaps)

def register():
    blc_utils.register_module("Operators", classes, submodules)


def unregister():
    blc_utils.unregister_module("Operators", classes, submodules)
