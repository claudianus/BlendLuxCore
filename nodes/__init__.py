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


_BPY_NODE_BASES = (bpy.types.Node, bpy.types.NodeSocket, bpy.types.NodeTree)
# Never carried over when flattening a registered base's class dict.
_ALIAS_ATTR_SKIP = {"__dict__", "__weakref__", "__module__", "__doc__",
                    "bl_rna", "bl_idname"}


def _rebind_super_cells(cls):
    """Retarget zero-arg super() in methods copied onto an alias class.
    super() captures the DEFINING class in a __class__ closure cell; left
    pointing at the canonical class it raises TypeError on alias
    instances (alias is not a subclass). Rebinding to the alias makes
    super() resolve to the alias's own bases, which mirror the
    canonical class's tail MRO."""
    import types as _types

    def cell_for(value):
        return (lambda: value).__closure__[0]

    for name, member in list(vars(cls).items()):
        wrapper = None
        fn = None
        if isinstance(member, (classmethod, staticmethod)):
            fn, wrapper = member.__func__, type(member)
        elif isinstance(member, _types.FunctionType):
            fn, wrapper = member, lambda f: f
        if fn is None or not fn.__closure__:
            continue
        freevars = fn.__code__.co_freevars
        if "__class__" not in freevars:
            continue
        cells = list(fn.__closure__)
        cells[freevars.index("__class__")] = cell_for(cls)
        new_fn = _types.FunctionType(fn.__code__, fn.__globals__,
                                     fn.__name__, fn.__defaults__,
                                     tuple(cells))
        new_fn.__kwdefaults__ = fn.__kwdefaults__
        setattr(cls, name, wrapper(new_fn))


def _register_legacy_idname_aliases():
    """Give every SuperLuxCore node/socket/tree class an alias under its
    upstream LuxCore* bl_idname so legacy .blend files keep working.
    Aliases carry the same behavior; they only differ in type name.

    IMPORTANT: the alias must NOT subclass the real class. Registering a
    subclass of an already-registered node type under a different
    bl_idname orphans the base's RNA python binding in Blender 5.x —
    nodes.new()/the Add menu then fail for the base type. Instead the
    alias inlines the registered base's python surface (props/methods)
    and keeps only the mixins + the plain bpy base."""
    from ..utils.node import legacy_idname

    seen = set()

    def walk(cls):
        if cls in seen:
            return
        seen.add(cls)
        yield cls
        for sub in cls.__subclasses__():
            yield from walk(sub)

    def absorb(base, bases, attrs):
        if base in _BPY_NODE_BASES:
            bases.append(base)
        elif issubclass(base, _BPY_NODE_BASES):
            # Registered bpy class (e.g. SuperLuxCoreNodeMaterial):
            # flatten — copy the attrs it defines itself, then absorb
            # its own bases (deeper bases keep lower precedence).
            for k, v in vars(base).items():
                if k not in _ALIAS_ATTR_SKIP:
                    attrs.setdefault(k, v)
            for bb in base.__bases__:
                absorb(bb, bases, attrs)
        elif base is not object:
            bases.append(base)

    for kind in _BPY_NODE_BASES:
        for cls in walk(kind):
            # Nodes and sockets derive bl_idname from the class name
            idname = getattr(cls, "bl_idname", None) or cls.__name__
            if not isinstance(idname, str):
                continue
            legacy = legacy_idname(idname)
            if legacy == idname:
                continue
            bases, attrs = [], {}
            for base in cls.__bases__:
                absorb(base, bases, attrs)
            for k, v in vars(cls).items():
                if k not in _ALIAS_ATTR_SKIP:
                    attrs[k] = v
            attrs["bl_idname"] = legacy
            attrs["__module__"] = cls.__module__
            if not any(b in _BPY_NODE_BASES for b in bases):
                bases.append(kind)
            # Name the class after the legacy idname so name-derived
            # type lookup finds it even if bl_idname were ignored.
            alias = type(legacy, tuple(dict.fromkeys(bases)), attrs)
            _rebind_super_cells(alias)
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
