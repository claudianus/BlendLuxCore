import bpy
from bpy.props import PointerProperty, BoolProperty, FloatProperty, IntProperty, StringProperty, EnumProperty
from bpy.types import PropertyGroup
from .hair import SuperLuxCoreHair
from .legacy import LuxCoreLegacyBridge

DESC_VISIBLE_TO_CAM = (
    "If disabled, the object will not be visible to camera rays. "
    "Note that it will still be visible in indirect light, shadows and reflections"
)
DESC_MOTION_BLUR = "Export this object as instance if object motion blur is enabled in camera settings"
DESC_OBJECT_ID = (
    "ID for Object ID AOV. If -1 is set, the object name is hashed to a number and used as ID. "
    "The ID can be accessed from the Object ID node in material node trees. "
    "Note that the random IDs of SuperLuxCore can be greater than 32767 "
    "(the ID Mask node in the compositor can't handle those numbers)"
)
DESC_EXCLUDE_FROM_RENDER = (
    "The object will be excluded from render. "
    "Useful if you need objects to render for other engines, but not for SuperLuxCore"
)
DESC_MESH_PROXY = (
    "Render from an .lxm proxy file instead of this object's mesh data: the "
    "geometry stays on disk and is memory-mapped at render time (no parse, no "
    "heap copy, pages evictable under memory pressure — for heavy static "
    "assets). Bake one with the 'Bake .lxm Proxy' button. Limits: a proxy uses "
    "the object's first material slot only, and shape wrappers (displacement, "
    "pointiness), motion blur and live mesh edits do not apply"
)
DESC_LIGHT_PORTAL = (
    "Use this mesh's quad faces as light portals: aperture guides that tell the path "
    "tracer where light enters the space (windows, doorways, slits). The object itself "
    "is NOT rendered - it is a sampling aid only, so it can sit inside the opening "
    "without blocking light. Requires Path Guiding enabled (the learned field decides "
    "how much each bounce trusts the portal). CPU engines only"
)
DESC_LINK_GROUPS = (
    "Comma-separated light link group names (e.g. 'key,fill'). A light with at "
    "least one matching group illuminates this object; lights without groups "
    "always illuminate everything. Direct illumination only - indirect bounces "
    "are not filtered. Leave empty to accept all lights"
)
DESC_LINK_MODE = (
    "Include: the object is lit by lights sharing a listed group. "
    "Exclude: the object is lit by everything EXCEPT lights sharing a listed group"
)


class SuperLuxCoreObjectProps(LuxCoreLegacyBridge, PropertyGroup):
    visible_to_camera: BoolProperty(
        name="Visible to Camera", default=True, description=DESC_VISIBLE_TO_CAM
    )
    exclude_from_render: BoolProperty(
        name="Exclude from Render",
        default=False,
        description=DESC_EXCLUDE_FROM_RENDER,
    )
    enable_motion_blur: BoolProperty(
        name="Motion Blur", default=True, description=DESC_MOTION_BLUR
    )
    is_light_portal: BoolProperty(
        name="Light Portal", default=False, description=DESC_LIGHT_PORTAL
    )
    id: IntProperty(
        name="Object ID",
        default=-1,
        min=-1,
        soft_max=32767,
        description=DESC_OBJECT_ID,
    )
    proxy_filepath: StringProperty(
        name=".lxm Proxy File",
        default="",
        subtype="FILE_PATH",
        description=DESC_MESH_PROXY,
    )
    link_groups: StringProperty(
        name="Light Link Groups",
        default="",
        description=DESC_LINK_GROUPS,
    )
    link_mode: EnumProperty(
        name="Link Mode",
        items=[
            ("include", "Include", "Lit by lights sharing a listed group"),
            ("exclude", "Exclude", "Lit by everything except lights sharing a listed group"),
        ],
        default="include",
        description=DESC_LINK_MODE,
    )
    hair: PointerProperty(
        name="SuperLuxCore Hair Curve Settings",
        description="SuperLuxCore hair curve settings",
        type=SuperLuxCoreHair,
    )

    @classmethod
    def register(cls):
        bpy.types.Object.superluxcore = PointerProperty(
            name="SuperLuxCore Object Settings",
            description="SuperLuxCore object settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Object.superluxcore
