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

# Registered alias classes that let node trees saved by upstream
# BlendLuxCore (LuxCore*/luxcore_* type names) load as real SuperLuxCore
# types instead of NodeTreeUndefined placeholders.
_legacy_alias_classes = []


def _register_legacy_idname_aliases():
    """Subclass every SuperLuxCore node/socket/tree class under its
    upstream LuxCore* bl_idname so legacy .blend files keep working.
    Aliases inherit all behavior; they only differ in type name."""
    from ..utils.node import legacy_idname

    def walk(cls):
        yield cls
        for sub in cls.__subclasses__():
            yield from walk(sub)

    for base in (bpy.types.Node, bpy.types.NodeSocket, bpy.types.NodeTree):
        for cls in walk(base):
            # Nodes and sockets derive bl_idname from the class name
            idname = getattr(cls, "bl_idname", None) or cls.__name__
            if not isinstance(idname, str):
                continue
            legacy = legacy_idname(idname)
            if legacy == idname:
                continue
            # Name the class after the legacy idname so name-derived
            # type lookup finds it even if bl_idname were ignored.
            alias = type(
                legacy, (cls,),
                {"bl_idname": legacy, "__module__": cls.__module__},
            )
            try:
                bpy.utils.register_class(alias)
                _legacy_alias_classes.append(alias)
            except ValueError:
                # e.g. already registered from a previous addon load
                pass


def _unregister_legacy_idname_aliases():
    for cls in reversed(_legacy_alias_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _legacy_alias_classes.clear()


def register():
    utils.register_module("Nodes", classes, submodules)
    _register_legacy_idname_aliases()


def unregister():
    _unregister_legacy_idname_aliases()
    utils.unregister_module("Nodes", classes, submodules)
