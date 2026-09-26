_needs_reload = "bpy" in locals()

import bpy

from .. import utils

from . import (
    addon_preferences,
    blender_hair_curves,
    blender_object,
    camera,
    image_tools,
    light,
    material,
    node_editor,
    output,
    particle,
    physics,
    scene_lightgroups,
    scene_units,
    texture,
    view_layer,
    view_layer_aovs,
    volume,
    world,
    lol,
    render,
)


if _needs_reload:
    import importlib

    addon_preferences = importlib.reload(addon_preferences)
    blender_hair_curves = importlib.reload(blender_hair_curves)
    blender_object = importlib.reload(blender_object)
    camera = importlib.reload(camera)
    image_tools = importlib.reload(image_tools)
    light = importlib.reload(light)
    material = importlib.reload(material)
    node_editor = importlib.reload(node_editor)
    output = importlib.reload(output)
    particle = importlib.reload(particle)
    physics = importlib.reload(physics)
    scene_lightgroups = importlib.reload(scene_lightgroups)
    scene_units = importlib.reload(scene_units)
    texture = importlib.reload(texture)
    view_layer = importlib.reload(view_layer)
    view_layer_aovs = importlib.reload(view_layer_aovs)
    volume = importlib.reload(volume)
    world = importlib.reload(world)
    lol = importlib.reload(lol)
    render = importlib.reload(render)

classes = (
    addon_preferences.SuperLuxCoreAddonPreferences,
    blender_object.SUPERLUXCORE_OBJECT_PT_object,
    blender_hair_curves.SUPERLUXCORE_DATA_PT_curve_hair,
    camera.SUPERLUXCORE_CAMERA_PT_presets,
    camera.SUPERLUXCORE_SAFE_AREAS_PT_presets,
    camera.SUPERLUXCORE_CAMERA_PT_lens,
    camera.SUPERLUXCORE_CAMERA_PT_clipping_plane,
    camera.SUPERLUXCORE_CAMERA_PT_depth_of_field,
    camera.SUPERLUXCORE_CAMERA_PT_bokeh,
    camera.SUPERLUXCORE_CAMERA_PT_motion_blur,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_tonemapper,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_bloom,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_mist,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_vignetting,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_color_aberration,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_background_image,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_white_balance,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_camera_response_function,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_color_LUT,
    camera.SUPERLUXCORE_CAMERA_PT_image_pipeline_contour_lines,
    camera.SUPERLUXCORE_CAMERA_PT_volume,
    image_tools.SUPERLUXCORE_IMAGE_PT_display,
    image_tools.SUPERLUXCORE_IMAGE_PT_denoiser,
    image_tools.SUPERLUXCORE_IMAGE_PT_statistics,
    light.SUPERLUXCORE_LIGHT_PT_context_light,
    light.SUPERLUXCORE_LIGHT_PT_volume,
    light.SUPERLUXCORE_LIGHT_PT_performance,
    light.SUPERLUXCORE_LIGHT_PT_visibility,
    light.SUPERLUXCORE_LIGHT_PT_spot,
    light.SUPERLUXCORE_LIGHT_PT_ies_light,
    light.SUPERLUXCORE_LIGHT_PT_nodes,
    light.SUPERLUXCORE_LIGHT_PT_cycles_nodes,
    material.SUPERLUXCORE_PT_context_material,
    material.SUPERLUXCORE_PT_material_presets,
    material.SUPERLUXCORE_PT_material_preview,
    material.SUPERLUXCORE_PT_material_settings,
    particle.SUPERLUXCORE_HAIR_PT_hair,
    particle.SUPERLUXCORE_PARTICLE_PT_textures,
    scene_lightgroups.SUPERLUXCORE_SCENE_PT_lightgroups,
    scene_units.SUPERLUXCORE_PT_unit_advanced,
    view_layer.SUPERLUXCORE_VIEWLAYER_PT_layer,
    view_layer.SUPERLUXCORE_VIEWLAYER_PT_override,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_basic,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_material_object,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_light,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_shadow,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_geometry,
    view_layer_aovs.SUPERLUXCORE_RENDERLAYER_PT_aovs_render,
    world.SUPERLUXCORE_PT_context_world,
    world.SUPERLUXCORE_WORLD_PT_sky2,
    world.SUPERLUXCORE_WORLD_PT_infinite,
    world.SUPERLUXCORE_WORLD_PT_volume,
    world.SUPERLUXCORE_WORLD_PT_performance,
    world.SUPERLUXCORE_WORLD_PT_visibility,
)

submodules = (
    lol,
    render,
    blender_object,
    camera,
    light,
    material,
    node_editor,
    output,
    particle,
    physics,
    texture,
    volume,
    world
)


def register():
    utils.register_module("UI", classes, submodules)


def unregister():
    utils.unregister_module("UI", classes, submodules)
