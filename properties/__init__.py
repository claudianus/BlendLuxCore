_needs_reload = "bpy" in locals()

import bpy
from .. import utils
from . import (
    aovs,
    image_user,
    hair,
    blender_object,
    imagepipeline,
    camera,
    config,
    debug,
    denoiser,
    devices,
    display,
    halt,
    ies,
    light,
    lightgroups,
    material,
    viewport,
    statistics,
    scene,
    view_layer,
    world,
    lol,
)

# TODO
from .ies import SuperLuxCoreIESProps

if _needs_reload:
    import importlib

    # Caveat: module order matters (due to PointerProperty and PropertyGroup)
    modules = (
        aovs,
        image_user,
        hair,
        blender_object,
        imagepipeline,
        camera,
        config,
        debug,
        denoiser,
        devices,
        display,
        halt,
        ies,
        light,
        lightgroups,
        material,
        viewport,
        statistics,
        scene,
        view_layer,
        world,
        lol,
    )
    for module in modules:
        importlib.reload(module)


# Warning: order matters, for correct loading and reloading
classes = (
    aovs.SuperLuxCoreAOVSettings,
    image_user.SuperLuxCoreImageUser,
    hair.SuperLuxCoreHair,
    blender_object.SuperLuxCoreObjectProps,
    imagepipeline.SuperLuxCoreImagepipelineTonemapper,
    imagepipeline.SuperLuxCoreImagepipelineBloom,
    imagepipeline.SuperLuxCoreImagepipelineMist,
    imagepipeline.SuperLuxCoreImagepipelineVignetting,
    imagepipeline.SuperLuxCoreImagepipelineColorAberration,
    imagepipeline.SuperLuxCoreImagepipelineBackgroundImage,
    imagepipeline.SuperLuxCoreImagepipelineWhiteBalance,
    imagepipeline.SuperLuxCoreImagepipelineCameraResponseFunc,
    imagepipeline.SuperLuxCoreImagepipelineColorLUT,
    imagepipeline.SuperLuxCoreImagepipelineContourLines,
    imagepipeline.SuperLuxCoreImagepipeline,
    camera.SuperLuxCoreMotionBlur,
    camera.SuperLuxCoreBokeh,
    camera.SuperLuxCoreCameraProps,
    config.SuperLuxCoreConfigPath,
    config.SuperLuxCoreConfigTile,
    config.SuperLuxCoreConfigDLSCache,
    config.SuperLuxCoreConfigPhotonGI,
    config.SuperLuxCoreConfigEnvLightCache,
    config.SuperLuxCoreConfigNoiseEstimation,
    config.SuperLuxCoreConfigImageResizePolicy,
    config.SuperLuxCoreConfigSimple,
    config.SuperLuxCoreConfig,
    debug.SuperLuxCoreDebugSettings,
    denoiser.SuperLuxCoreDenoiser,
    devices.SuperLuxCoreOpenCLDevice,
    devices.SuperLuxCoreDeviceSettings,
    display.SuperLuxCoreDisplaySettings,
    hair.SuperLuxCoreParticlesProps,
    halt.SuperLuxCoreHaltConditions,
    ies.SuperLuxCoreIESProps,
    light.SuperLuxCoreLightProps,
    lightgroups.SuperLuxCoreLightGroup,
    lightgroups.SuperLuxCoreLightGroupSettings,
    material.SuperLuxCoreMaterialPreviewProps,
    material.SuperLuxCoreMaterialProps,
    viewport.SuperLuxCoreViewportSettings,
    statistics.SuperLuxCoreRenderStatsCollection,
    scene.SuperLuxCoreScene,
    view_layer.SuperLuxCoreViewLayer,
    world.SuperLuxCoreWorldProps,
)

submodules = (lol,)


def register():
    utils.register_module("Properties", classes, submodules)


def unregister():
    utils.unregister_module("Properties", classes, submodules)
