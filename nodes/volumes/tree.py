import bpy
from nodeitems_utils import NodeCategory, NodeItem, NodeItemCustom
from ... import icons
from ..nodeitems import Separator
from ..base import SuperLuxCoreNodeTree


class SuperLuxCoreVolumeNodeTree(bpy.types.NodeTree, SuperLuxCoreNodeTree):
    bl_idname = "superluxcore_volume_nodes"
    bl_label = "SuperLuxCore Volume Nodes"
    bl_icon = icons.NTREE_VOLUME


class SuperLuxCoreNodeCategoryVolume(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == "superluxcore_volume_nodes"


# Here we define the menu structure the user sees when he
# presses Shift+A in the node editor to add a new node
superluxcore_node_categories_volume = [
    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_VOLUME", "Volume", items=[
        NodeItem("SuperLuxCoreNodeVolClear", label="Clear"),
        NodeItem("SuperLuxCoreNodeVolHomogeneous", label="Homogeneous"),
        NodeItem("SuperLuxCoreNodeVolHeterogeneous", label="Heterogeneous"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_TEXTURE", "Texture", items=[
        # Note: 2D textures make no sense for volumes
        NodeItem("SuperLuxCoreNodeTexBrick", label="Brick"),
        NodeItem("SuperLuxCoreNodeTexCheckerboard3D", label="3D Checkerboard"),
        NodeItem("SuperLuxCoreNodeTexfBM", label="fBM"),
        NodeItem("SuperLuxCoreNodeTexMarble", label="Marble"),
        NodeItem("SuperLuxCoreNodeTexWindy", label="Windy"),
        NodeItem("SuperLuxCoreNodeTexWrinkled", label="Wrinkled"),
        NodeItem("SuperLuxCoreNodeTexSmoke", label="Smoke Data"),
        NodeItem("SuperLuxCoreNodeTexOpenVDB", label="OpenVDB File"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_BLENDERTEXTURE", "Texture (Blender)", items=[
        NodeItem("SuperLuxCoreNodeTexBlenderBlend", label="Blend"),
        NodeItem("SuperLuxCoreNodeTexBlenderClouds", label="Clouds"),
        NodeItem("SuperLuxCoreNodeTexBlenderDistortedNoise", label="Distorted Noise"),
        NodeItem("SuperLuxCoreNodeTexBlenderMagic", label="Magic"),
        NodeItem("SuperLuxCoreNodeTexBlenderMarble", label="Marble"),
        NodeItem("SuperLuxCoreNodeTexBlenderMusgrave", label="Musgrave"),
        NodeItem("SuperLuxCoreNodeTexBlenderNoise", label="Fully Random Noise"),
        NodeItem("SuperLuxCoreNodeTexBlenderStucci", label="Stucci"),
        NodeItem("SuperLuxCoreNodeTexBlenderWood", label="Wood"),
        NodeItem("SuperLuxCoreNodeTexBlenderVoronoi", label="Voronoi"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_MATH", "Math", items=[
        # Note: 2D textures make no sense for volumes
        NodeItem("SuperLuxCoreNodeTexMath", label="Math"),
        NodeItem("SuperLuxCoreNodeTexColorMix", label="Color Math"),
        NodeItem("SuperLuxCoreNodeTexVectorMath", label="Vector Math"),
        NodeItem("SuperLuxCoreNodeTexDotProduct", label="Dot Product"),
        NodeItem("SuperLuxCoreNodeTexSplitFloat3", label="Split RGB"),
        NodeItem("SuperLuxCoreNodeTexMakeFloat3", label="Combine RGB"),
        NodeItem("SuperLuxCoreNodeTexRemap", label="Remap"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_UTILS", "Utils", items=[
        # Note: 2D textures make no sense for volumes
        NodeItem("SuperLuxCoreNodeTexBand", label="ColorRamp"),
        NodeItem("SuperLuxCoreNodeTexDistort", label="Distort"),
        NodeItem("SuperLuxCoreNodeTexHSV", label="HSV"),
        NodeItem("SuperLuxCoreNodeTexBrightContrast", label="Brightness/Contrast"),
        NodeItem("SuperLuxCoreNodeTexInvert", label="Invert"),
        NodeItem("SuperLuxCoreNodeTexColorAtDepth", label="Color at depth"),
        Separator(),
        NodeItem("SuperLuxCoreNodeTexConstfloat1", label="Constant Value"),
        NodeItem("SuperLuxCoreNodeTexConstfloat3", label="Constant Color"),
        NodeItem("SuperLuxCoreNodeTexIORPreset", label="IOR Preset"),
        Separator(),
        NodeItem("SuperLuxCoreNodeTexHitpointInfo", label="Hitpoint Info"),
        NodeItem("SuperLuxCoreNodeTexRandom", label="Random"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_MAPPING", "Mapping", items=[
        # Note: 2D mapping makes no sense for volumes
        NodeItem("SuperLuxCoreNodeTexMapping3D", label="3D Mapping"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_LIGHT", "Light", items=[
        NodeItem("SuperLuxCoreNodeTexLampSpectrum", label="Lamp Spectrum"),
        NodeItem("SuperLuxCoreNodeTexBlackbody", label="Blackbody Temperature"),
        NodeItem("SuperLuxCoreNodeTexIrregularData", label="Irregular Data"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_POINTER", "Pointer", items=[
        NodeItem("SuperLuxCoreNodeTreePointer", label="Pointer"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_OUTPUT", "Output", items=[
        NodeItem("SuperLuxCoreNodeVolOutput", label="Output"),
    ]),

    SuperLuxCoreNodeCategoryVolume("SUPERLUXCORE_VOLUME_LAYOUT", "Layout", items=[
        NodeItem("NodeFrame", label="Frame"),
        NodeItem("NodeReroute", label="Reroute"),
    ]),
]
