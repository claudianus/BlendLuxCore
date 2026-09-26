import bpy
from idprop.types import IDPropertyArray, IDPropertyGroup

"""Bridge for .blend files authored with upstream BlendLuxCore.

Those files store every addon property group under the "luxcore" key on
each datablock (scene.luxcore.config, material.luxcore.node_tree, ...)
and node trees/nodes/sockets under LuxCore*/luxcore_* type names. The
SuperLuxCore rename made all of that data unreachable: unregistered
type names load as NodeTreeUndefined and the prop groups never bind.

Restoring the data without touching the file works like this:

1. Node/socket/tree alias classes are registered under the upstream
   LuxCore* bl_idnames (nodes/__init__.py), so stored node trees load
   as real types instead of NodeTreeUndefined.
2. The stored "luxcore" ID-property dictionaries are read directly -
   NOT through a registered RNA alias. A registered "luxcore" pointer
   prop would create a separate runtime storage whose contents only
   partially mirror the file data (verified: gain binds, turbidity
   doesn't) and could serialize ghost data on save. The raw
   IDPropertyGroup is the ground truth: scalars, enums (stored as
   ints), vectors and even resolved pointers (mat["luxcore"]
   ["node_tree"] binds to the real NodeTree) all read correctly.
3. Root groups inherit LuxCoreLegacyBridge: a property authored in the
   datablock's own "superluxcore" storage wins; otherwise the value
   stored under "luxcore" is returned, converted to the declared RNA
   type; otherwise the RNA default. Reads never write back - the
   user's data is left alone and any new edit lands in "superluxcore"
   storage where it then shadows the legacy value for that property.
"""


def _prop_def(pg_or_cls, name):
    try:
        return pg_or_cls.bl_rna.properties.get(name)
    except Exception:
        return None


def _convert_legacy_value(value, prop):
    """Convert a raw IDProperty value to the declared RNA prop type."""
    if prop is None:
        return value
    if prop.type == "POINTER":
        # Resolved datablock references arrive as real bpy objects;
        # unresolved/dangling ones are empty IDPropertyGroups -> None.
        return value if isinstance(value, bpy.types.ID) else None
    if prop.type == "ENUM":
        if isinstance(value, int) and not isinstance(value, bool):
            for item in prop.enum_items:
                if item.value == value:
                    return item.identifier
            return prop.default
        return value
    if prop.type == "BOOLEAN":
        return bool(value)
    if isinstance(value, IDPropertyArray):
        return tuple(value)
    return value


class LegacyGroupView:
    """Read-only typed view over a stored 'luxcore' IDPropertyGroup.

    Mirrors the RNA PropertyGroup interface: attribute access converts
    stored members through the declared prop definitions; nested prop
    groups return another view; is_property_set mirrors storage keys.
    """
    __slots__ = ("_data", "_cls")

    def __init__(self, data, cls):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_cls", cls)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        data = object.__getattribute__(self, "_data")
        cls = object.__getattribute__(self, "_cls")
        prop = _prop_def(cls, name)
        if prop is None:
            raise AttributeError(name)
        if prop.type == "COLLECTION":
            items = data.get(name)
            if items is None:
                return []
            item_cls = prop.fixed_type
            return [LegacyGroupView(v, item_cls) for v in items]
        value = data.get(name)
        if prop.type == "POINTER":
            fixed = getattr(prop, "fixed_type", None)
            if isinstance(value, IDPropertyGroup):
                if fixed is not None and issubclass(fixed, bpy.types.PropertyGroup):
                    return LegacyGroupView(value, fixed)
                return None  # unresolved datablock reference
            return value if isinstance(value, bpy.types.ID) else None
        if value is None:
            return _rna_default(prop)
        return _convert_legacy_value(value, prop)

    def is_property_set(self, name):
        return name in object.__getattribute__(self, "_data")

    @property
    def bl_rna(self):
        return object.__getattribute__(self, "_cls").bl_rna


def _group_has_authored_leaf(group):
    """True when a nested RNA group holds any assigned member.
    Materialization alone does not set leaf props, so is_property_set
    is reliable per leaf."""
    try:
        props = group.bl_rna.properties
    except Exception:
        return True
    for prop in props:
        name = prop.identifier
        if name in ("rna_type", "name"):
            continue
        try:
            if prop.type == "POINTER":
                value = getattr(group, name)
                if value is not None:
                    if isinstance(value, bpy.types.PropertyGroup):
                        if _group_has_authored_leaf(value):
                            return True
                    elif group.is_property_set(name):
                        return True
            elif prop.type == "COLLECTION":
                if len(getattr(group, name)):
                    return True
            elif group.is_property_set(name):
                return True
        except Exception:
            continue
    return False


def _rna_default(prop):
    try:
        return prop.default
    except Exception:
        return None


def _legacy_root(owner):
    """The datablock's stored upstream 'luxcore' dict, or None."""
    try:
        if owner is None or "luxcore" not in owner:
            return None
        return owner["luxcore"]
    except Exception:
        return None


# Property identifiers per bridged class, cached for the __getattribute__
# fast path.
_PROP_IDENTIFIERS = {}


class LuxCoreLegacyBridge:
    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name.startswith("_"):
            return value
        try:
            cls = type(self)
            props = _PROP_IDENTIFIERS.get(cls)
            if props is None:
                props = {p.identifier: p for p in cls.bl_rna.properties}
                _PROP_IDENTIFIERS[cls] = props
            prop = props.get(name)
            if prop is None:
                return value
            # Only the root group bridges; nested groups reached through
            # a bridged pointer resolve via LegacyGroupView instead.
            if self.path_from_id() != "superluxcore":
                return value
            owner = self.id_data
            # Whether the current RNA value is authored decides the
            # shadowing. Note: RNA group members never surface in
            # owner["superluxcore"] (that dict only exists for storage
            # without a registered prop), so is_property_set() is the
            # authored signal for leaf props.
            if prop.type == "POINTER":
                if value is not None:
                    fixed = getattr(prop, "fixed_type", None)
                    if fixed is not None and issubclass(
                            fixed, bpy.types.PropertyGroup):
                        if _group_has_authored_leaf(value):
                            return value
                        # Empty materialized group: fall through to
                        # the legacy value.
                    elif self.is_property_set(name):
                        return value  # an assigned datablock pointer
            elif prop.type == "COLLECTION":
                if len(value):
                    return value
            elif self.is_property_set(name):
                return value
            raw = _legacy_root(owner)
            if raw is None or name not in raw:
                return value
            member = raw[name]
            if prop.type == "POINTER":
                fixed = getattr(prop, "fixed_type", None)
                if isinstance(member, IDPropertyGroup):
                    if fixed is not None and issubclass(fixed, bpy.types.PropertyGroup):
                        return LegacyGroupView(member, fixed)
                    return None  # unresolved datablock reference
                return member if isinstance(member, bpy.types.ID) else None
            if prop.type == "COLLECTION":
                if isinstance(member, (IDPropertyArray, list)):
                    item_cls = prop.fixed_type
                    return [LegacyGroupView(v, item_cls) for v in member]
                return value
            return _convert_legacy_value(member, prop)
        except Exception:
            pass
        return value


def legacy_group(sl_props, cls=None):
    """Typed LegacyGroupView over the owner's stored 'luxcore' dict."""
    try:
        owner = sl_props.id_data
    except AttributeError:
        return None
    raw = _legacy_root(owner)
    if raw is None:
        return None
    return LegacyGroupView(raw, cls or type(sl_props))
