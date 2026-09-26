import bpy
from nodeitems_utils import NodeCategory, NodeItem, NodeItemCustom
from ... import icons
from ..nodeitems import Separator, NodeItemMultiImageImport
from ..base import SuperLuxCoreNodeTree


class SuperLuxCoreTextureNodeTree(bpy.types.NodeTree, SuperLuxCoreNodeTree):
    bl_idname = "superluxcore_texture_nodes"
    bl_label = "SuperLuxCore Texture Nodes"
    bl_icon = icons.NTREE_TEXTURE


class SuperLuxCoreNodeCategoryTexture(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == "superluxcore_texture_nodes"


# Here we define the menu structure the user sees when he
# presses Shift+A in the node editor to add a new node.
# In general it is a good idea to put often used nodes near the top.
superluxcore_node_categories_texture = [
    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_TEXTURE", "Texture", items=[
        NodeItemMultiImageImport(),
        NodeItem("SuperLuxCoreNodeTexImagemap", label="Image"),
        Separator(),
        # Procedurals
        NodeItem("SuperLuxCoreNodeTexBrick", label="Brick"),
        NodeItem("SuperLuxCoreNodeTexWireframe", label="Wireframe"),
        NodeItem("SuperLuxCoreNodeTexDots", label="Dots"),
        NodeItem("SuperLuxCoreNodeTexfBM", label="fBM"),
        NodeItem("SuperLuxCoreNodeTexCheckerboard2D", label="2D Checkerboard"),
        NodeItem("SuperLuxCoreNodeTexCheckerboard3D", label="3D Checkerboard"),
        NodeItem("SuperLuxCoreNodeTexMarble", label="Marble"),
        # NodeItem("SuperLuxCoreNodeTexWindy", label="Windy"),  # Same as FBM -> unnecessary
        NodeItem("SuperLuxCoreNodeTexWrinkled", label="Wrinkled"),
        Separator(),
        NodeItem("SuperLuxCoreNodeTexHitpoint", label="Vertex Color"),
        NodeItem("SuperLuxCoreNodeTexSmoke", label="Smoke Data"),
        NodeItem("SuperLuxCoreNodeTexOpenVDB", label="OpenVDB File"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_BLENDERTEXTURE", "Texture (Blender)", items=[
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

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_MATH", "Math", items=[
        NodeItem("SuperLuxCoreNodeTexMath", label="Math"),
        NodeItem("SuperLuxCoreNodeTexColorMix", label="Color Math"),
        NodeItem("SuperLuxCoreNodeTexVectorMath", label="Vector Math"),
        NodeItem("SuperLuxCoreNodeTexDotProduct", label="Dot Product"),
        NodeItem("SuperLuxCoreNodeTexSplitFloat3", label="Split RGB"),
        NodeItem("SuperLuxCoreNodeTexMakeFloat3", label="Combine RGB"),
        NodeItem("SuperLuxCoreNodeTexRemap", label="Remap"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_UTILS", "Utils", items=[
        NodeItem("SuperLuxCoreNodeTexBump", label="Bump"),
        NodeItem("SuperLuxCoreNodeTexBevel", label="Bevel"),
        # Possibly confusing, better deactivate (only needed in very rare cases anyway)
        # NodeItem("SuperLuxCoreNodeTexNormalmap", label="Normalmap"),
        NodeItem("SuperLuxCoreNodeTexBand", label="ColorRamp"),
        NodeItem("SuperLuxCoreNodeTexDistort", label="Distort"),
        NodeItem("SuperLuxCoreNodeTexHSV", label="HSV"),
        NodeItem("SuperLuxCoreNodeTexBrightContrast", label="Brightness/Contrast"),
        NodeItem("SuperLuxCoreNodeTexInvert", label="Invert"),
        Separator(),
        NodeItem("SuperLuxCoreNodeTexConstfloat1", label="Constant Value"),
        NodeItem("SuperLuxCoreNodeTexConstfloat3", label="Constant Color"),
        NodeItem("SuperLuxCoreNodeTexIORPreset", label="IOR Preset"),
        Separator(),
        NodeItem("SuperLuxCoreNodeTexHitpointInfo", label="Hitpoint Info"),
        NodeItem("SuperLuxCoreNodeTexPointiness", label="Pointiness"),
        NodeItem("SuperLuxCoreNodeTexObjectID", label="Object ID"),
        NodeItem("SuperLuxCoreNodeTexRandomPerIsland", label="Random Per Island"),
        NodeItem("SuperLuxCoreNodeTexTimeInfo", label="Time Info"),
        NodeItem("SuperLuxCoreNodeTexUV", label="UV Test"),
        NodeItem("SuperLuxCoreNodeTexRandom", label="Random"),
        NodeItem("SuperLuxCoreNodeTexBombing", label="Bombing"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_MAPPING", "Mapping", items=[
        NodeItem("SuperLuxCoreNodeTexMapping2D", label="2D Mapping"),
        NodeItem("SuperLuxCoreNodeTexMapping3D", label="3D Mapping"),
        NodeItem("SuperLuxCoreNodeTexTriplanar", label="Triplanar Mapping"),
        NodeItem("SuperLuxCoreNodeTexTriplanarBump", label="Triplanar Bump Mapping"),
        NodeItem("SuperLuxCoreNodeTexTriplanarNormalmap", label="Triplanar Normal Mapping"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_LIGHT", "Light", items=[
        NodeItem("SuperLuxCoreNodeTexLampSpectrum", label="Lamp Spectrum"),
        NodeItem("SuperLuxCoreNodeTexBlackbody", label="Blackbody Temperature"),
        NodeItem("SuperLuxCoreNodeTexIrregularData", label="Irregular Data"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_POINTER", "Pointer", items=[
        NodeItem("SuperLuxCoreNodeTreePointer", label="Pointer"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_OUTPUT", "Output", items=[
        NodeItem("SuperLuxCoreNodeTexOutput", label="Output"),
    ]),

    SuperLuxCoreNodeCategoryTexture("SUPERLUXCORE_TEXTURE_LAYOUT", "Layout", items=[
        NodeItem("NodeFrame", label="Frame"),
        NodeItem("NodeReroute", label="Reroute"),
    ]),
]
