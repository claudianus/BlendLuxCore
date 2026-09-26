_needs_reload = "bpy" in locals()

import bpy
from ... import properties
from ... import utils

from . import (
    carpaint,
    cloth,
    disney,
    emission,
    frontbackopacity,
    glass,
    glossy2,
    glossycoating,
    glossytranslucent,
    hair,
    matte,
    mattetranslucent,
    metal,
    mirror,
    mix,
    null,
    openpbr,
    output,
    tree,
    twosided,
    velvet,
)
import nodeitems_utils
from . import tree
from .tree import superluxcore_node_categories_material

if _needs_reload:
    import importlib

    # Caveat: module order matters (due to PointerProperty and PropertyGroup)
    modules = (
        carpaint,
        cloth,
        disney,
        emission,
        frontbackopacity,
        glass,
        glossy2,
        glossycoating,
        glossytranslucent,
        hair,
        matte,
        mattetranslucent,
        metal,
        mirror,
        mix,
        null,
        output,
        tree,
        twosided,
        velvet,
        nodeitems_utils,
    )
    for module in modules:
        importlib.reload(module)

classes = (
    carpaint.SuperLuxCoreNodeMatCarpaint,
    carpaint.SuperLuxCoreSocketReflection,
    cloth.SuperLuxCoreSocketRepeatU,
    cloth.SuperLuxCoreSocketRepeatV,
    cloth.SuperLuxCoreNodeMatCloth,
    disney.SuperLuxCoreNodeMatDisney,
    frontbackopacity.SuperLuxCoreNodeMatFrontBackOpacity,
    glass.SuperLuxCoreSocketCauchyC,
    glass.SuperLuxCoreNodeMatGlass,
    glossy2.SuperLuxCoreNodeMatGlossy2,
    glossycoating.SuperLuxCoreNodeMatGlossyCoating,
    glossytranslucent.SuperLuxCoreNodeMatGlossyTranslucent,
    hair.SuperLuxCoreNodeMatHair,
    matte.SuperLuxCoreSocketSigma,
    matte.SuperLuxCoreNodeMatMatte,
    mattetranslucent.SuperLuxCoreNodeMatMatteTranslucent,
    metal.SuperLuxCoreNodeMatMetal,
    mirror.SuperLuxCoreNodeMatMirror,
    mix.SuperLuxCoreNodeMatMix,
    null.SuperLuxCoreNodeMatNull,
    openpbr.SuperLuxCoreNodeMatOpenPBR,
    output.SuperLuxCoreNodeMatOutput,
    tree.SuperLuxCoreMaterialNodeTree,
    twosided.SuperLuxCoreNodeMatTwoSided,
    velvet.SuperLuxCoreNodeMatVelvet,
    emission.SuperLuxCoreNodeMatEmission,
)


def register():
    nodeitems_utils.register_node_categories(
        "SUPERLUXCORE_MATERIAL_TREE", superluxcore_node_categories_material
    )

    utils.register_module("Materials", classes)


def unregister():
    utils.unregister_module("Materials", classes)
    nodeitems_utils.unregister_node_categories("SUPERLUXCORE_MATERIAL_TREE")
