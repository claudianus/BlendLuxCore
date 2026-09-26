import bpy
from bpy.props import PointerProperty
from . import aovs, halt

class SuperLuxCoreViewLayer(bpy.types.PropertyGroup):
    aovs: PointerProperty(type=aovs.SuperLuxCoreAOVSettings)
    halt: PointerProperty(type=halt.SuperLuxCoreViewLayerHaltConditions)

    @classmethod
    def register(cls):
        bpy.types.ViewLayer.superluxcore = PointerProperty(
            name="SuperLuxCore ViewLayer Settings",
            description="SuperLuxCore ViewLayer settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.ViewLayer.superluxcore
