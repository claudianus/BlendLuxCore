import bpy
from bpy.props import PointerProperty, IntProperty
from . import (
    config, debug, denoiser, display, halt, lightgroups, devices, statistics, viewport,
)
from .legacy import LuxCoreLegacyBridge


#def init():
#    bpy.types.Scene.superluxcore = PointerProperty(type=SuperLuxCoreScene)


class SuperLuxCoreScene(LuxCoreLegacyBridge, bpy.types.PropertyGroup):
    config: PointerProperty(type=config.SuperLuxCoreConfig)
    denoiser: PointerProperty(type=denoiser.SuperLuxCoreDenoiser)
    halt: PointerProperty(type=halt.SuperLuxCoreHaltConditions)
    display: PointerProperty(type=display.SuperLuxCoreDisplaySettings)
    devices: PointerProperty(type=devices.SuperLuxCoreDeviceSettings)
    lightgroups: PointerProperty(type=lightgroups.SuperLuxCoreLightGroupSettings)
    viewport: PointerProperty(type=viewport.SuperLuxCoreViewportSettings)
    statistics: PointerProperty(type=statistics.SuperLuxCoreRenderStatsCollection)
    debug: PointerProperty(type=debug.SuperLuxCoreDebugSettings)

    @classmethod
    def register(cls):
        bpy.types.Scene.superluxcore = PointerProperty(
            name="SuperLuxCore Scene Settings",
            description="SuperLuxCore scene settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Scene.superluxcore
