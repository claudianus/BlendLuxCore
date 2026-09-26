import bpy
from bpy.props import PointerProperty, BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup
from .hair import LuxCoreHair

DESC_VISIBLE_TO_CAM = (
    "If disabled, the object will not be visible to camera rays. "
    "Note that it will still be visible in indirect light, shadows and reflections"
)
DESC_MOTION_BLUR = "Export this object as instance if object motion blur is enabled in camera settings"
DESC_OBJECT_ID = (
    "ID for Object ID AOV. If -1 is set, the object name is hashed to a number and used as ID. "
    "The ID can be accessed from the Object ID node in material node trees. "
    "Note that the random IDs of LuxCore can be greater than 32767 "
    "(the ID Mask node in the compositor can't handle those numbers)"
)
DESC_EXCLUDE_FROM_RENDER = (
    "The object will be excluded from render. "
    "Useful if you need objects to render for other engines, but not for LuxCore"
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


class LuxCoreObjectProps(PropertyGroup):
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
    hair: PointerProperty(
        name="LuxCore Hair Curve Settings",
        description="LuxCore hair curve settings",
        type=LuxCoreHair,
    )

    @classmethod
    def register(cls):
        bpy.types.Object.luxcore = PointerProperty(
            name="LuxCore Object Settings",
            description="LuxCore object settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Object.luxcore
