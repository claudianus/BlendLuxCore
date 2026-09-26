import bpy
from bpy.types import PropertyGroup
from bpy.props import PointerProperty, FloatProperty, BoolProperty, EnumProperty
from ..utils import node as utils_node
# from ..operators.material import show_nodetree
from ..utils.node import show_nodetree
from .legacy import LuxCoreLegacyBridge


class SuperLuxCoreMaterialPreviewProps(PropertyGroup):
    def update_preview(self, context):
        material = self.id_data
        # A trick to force a material preview refresh (update_tag() does not work)
        material.preview_render_type = material.preview_render_type

    zoom: FloatProperty(
        name="Zoom",
        default=1,
        min=1,
        soft_max=3,
        max=10,
        description="Zoom of the preview camera",
        update=update_preview,
    )


class SuperLuxCoreMaterialProps(LuxCoreLegacyBridge, PropertyGroup):
    def update_auto_vp_color(self, context):
        if self.auto_vp_color:
            utils_node.update_opengl_materials(None, context)

    auto_vp_color: BoolProperty(
        name="Automatic Viewport Color",
        default=True,
        update=update_auto_vp_color,
        description="Automatically choose a viewport color "
        "from the first nodes in the node tree",
    )
    node_tree: PointerProperty(name="Node Tree", type=bpy.types.NodeTree)
    preview: PointerProperty(type=SuperLuxCoreMaterialPreviewProps)

    principled_target: EnumProperty(
        name="Principled Target",
        items=(
            ("openpbr", "OpenPBR",
             "Map Principled BSDF onto the OpenPBR material (ASWF standard, "
             "native coat/fuzz/thin-film/dispersion lobes)"),
            ("disney", "Disney (legacy)",
             "Map Principled BSDF onto the legacy Disney material"),
        ),
        default="openpbr",
        description="Material used when converting Blender's Principled BSDF "
        "nodes (Cycles node reader and Blender-first materials)",
    )

    @classmethod
    def register(cls):
        bpy.types.Material.superluxcore = PointerProperty(
            name="SuperLuxCore Material Settings",
            description="SuperLuxCore material settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Material.superluxcore
