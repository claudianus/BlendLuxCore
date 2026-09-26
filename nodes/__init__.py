_needs_reload = "bpy" in locals()

import bpy

from .. import icons
from .. import utils
from . import base, sockets, materials, shapes, textures, volumes
from .base import (
    TREE_TYPES,
    TREE_ICONS,
    NOISE_BASIS_ITEMS,
    NOISE_TYPE_ITEMS,
    MIN_NOISE_SIZE,
    COLORDEPTH_DESC,
)

if _needs_reload:
    import importlib

    modules = (base, sockets, materials, shapes, textures, volumes)
    for module in modules:
        importlib.reload(module)


classes = (
    base.SuperLuxCoreNodeTreePointer,
    sockets.SuperLuxCoreSocketMaterial,
    sockets.SuperLuxCoreSocketVolume,
    sockets.SuperLuxCoreSocketFresnel,
    sockets.SuperLuxCoreSocketMatEmission,
    sockets.SuperLuxCoreSocketBump,
    sockets.SuperLuxCoreSocketColor,
    sockets.SuperLuxCoreSocketFloatUnbounded,
    sockets.SuperLuxCoreSocketFloatPositive,
    sockets.SuperLuxCoreSocketFloat0to1,
    sockets.SuperLuxCoreSocketFloat0to2,
    sockets.SuperLuxCoreSocketBumpHeight,
    sockets.SuperLuxCoreSocketFloatDisneySheen,
    sockets.SuperLuxCoreSocketVector,
    sockets.SuperLuxCoreSocketRoughness,
    sockets.SuperLuxCoreSocketIOR,
    sockets.SuperLuxCoreSocketFilmThickness,
    sockets.SuperLuxCoreSocketFilmIOR,
    sockets.SuperLuxCoreSocketVolumeAsymmetry,
    sockets.SuperLuxCoreSocketMapping2D,
    sockets.SuperLuxCoreSocketMapping3D,
    sockets.SuperLuxCoreSocketShape,
)

submodules = (materials, shapes, textures, volumes)


def register():
    utils.register_module("Nodes", classes, submodules)


def unregister():
    utils.unregister_module("Nodes", classes, submodules)
