import bpy
from nodeitems_utils import NodeCategory, NodeItem, NodeItemCustom
from ... import icons
from ...utils import node as utils_node
from ..nodeitems import Separator, NodeItemMultiImageImport
from ..base import SuperLuxCoreNodeTree


class SuperLuxCoreMaterialNodeTree(bpy.types.NodeTree, SuperLuxCoreNodeTree):
    bl_idname = "superluxcore_material_nodes"
    bl_label = "SuperLuxCore Material Nodes"
    bl_icon = icons.NTREE_MATERIAL

    @classmethod
    def get_from_context(cls, context):
        """
        Switches the displayed node tree when user selects object/material
        """
        obj = context.active_object

        if obj and obj.type not in {"LIGHT", "CAMERA"}:
            mat = obj.active_material

            if mat:
                node_tree = mat.superluxcore.node_tree

                if node_tree:
                    return node_tree, mat, mat

        return None, None, None

    # This block updates the preview, when socket links change
    def update(self):
        super().update()

        # Force viewport update of the corresponding material
        try:
            materials = bpy.data.materials
        except AttributeError:
            # bpy.data.materials may not be accessible in certain contexts
            # (e.g., during undo/redo operations or when data is restricted)
            materials = []

        for mat in materials:
            if (
                hasattr(
                    mat, "superluxcore"
                )  # workaround for https://projects.blender.org/blender/blender/issues/140488
                and mat.superluxcore.node_tree == self
            ):
                mat.diffuse_color = mat.diffuse_color
                break

        # Update opengl materials in case the node linked to the
        # output has changed
        try:
            utils_node.update_opengl_materials(None, bpy.context)
        except Exception:
            # Silently ignore errors in opengl material updates
            # This can fail in various contexts (e.g., during undo/redo)
            pass


class SuperLuxCoreNodeCategoryMaterial(NodeCategory):
    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == "superluxcore_material_nodes"


# Here we define the menu structure the user sees when he
# presses Shift+A in the node editor to add a new node.
# In general it is a good idea to put often used nodes near the top.
superluxcore_node_categories_material = [
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_MATERIAL",
        "Material",
        items=[
            NodeItem("SuperLuxCoreNodeMatDisney", label="Disney"),
            NodeItem("SuperLuxCoreNodeMatOpenPBR", label="OpenPBR"),
            NodeItem("SuperLuxCoreNodeMatMix", label="Mix"),
            NodeItem("SuperLuxCoreNodeMatMatte", label="Matte"),
            NodeItem(
                "SuperLuxCoreNodeMatMatteTranslucent", label="Matte Translucent"
            ),
            NodeItem("SuperLuxCoreNodeMatMetal", label="Metal"),
            NodeItem("SuperLuxCoreNodeMatMirror", label="Mirror"),
            NodeItem("SuperLuxCoreNodeMatDiffraction", label="Diffraction (CD)"),
            NodeItem("SuperLuxCoreNodeMatGlossy2", label="Glossy"),
            NodeItem(
                "SuperLuxCoreNodeMatGlossyTranslucent", label="Glossy Translucent"
            ),
            NodeItem("SuperLuxCoreNodeMatGlossyCoating", label="Glossy Coating"),
            NodeItem("SuperLuxCoreNodeMatGlass", label="Glass"),
            NodeItem("SuperLuxCoreNodeMatNull", label="Null (Transparent)"),
            NodeItem("SuperLuxCoreNodeMatHair", label="Hair"),
            NodeItem("SuperLuxCoreNodeMatCarpaint", label="Carpaint"),
            NodeItem("SuperLuxCoreNodeMatCloth", label="Cloth"),
            NodeItem("SuperLuxCoreNodeMatVelvet", label="Velvet"),
            NodeItem("SuperLuxCoreNodeMatTwoSided", label="Two Sided"),
            NodeItem(
                "SuperLuxCoreNodeMatFrontBackOpacity", label="Front/Back Opacity"
            ),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_VOLUME",
        "Volume",
        items=[
            NodeItem("SuperLuxCoreNodeVolClear", label="Clear"),
            NodeItem("SuperLuxCoreNodeVolHomogeneous", label="Homogeneous"),
            NodeItem("SuperLuxCoreNodeVolHeterogeneous", label="Heterogeneous"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_TEXTURE",
        "Texture",
        items=[
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
            NodeItem("SuperLuxCoreNodeTexFresnel", label="Fresnel"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_BLENDERTEXTURE",
        "Texture (Blender)",
        items=[
            NodeItem("SuperLuxCoreNodeTexBlenderBlend", label="Blend"),
            NodeItem("SuperLuxCoreNodeTexBlenderClouds", label="Clouds"),
            NodeItem(
                "SuperLuxCoreNodeTexBlenderDistortedNoise", label="Distorted Noise"
            ),
            NodeItem("SuperLuxCoreNodeTexBlenderMagic", label="Magic"),
            NodeItem("SuperLuxCoreNodeTexBlenderMarble", label="Marble"),
            NodeItem("SuperLuxCoreNodeTexBlenderMusgrave", label="Musgrave"),
            NodeItem("SuperLuxCoreNodeTexBlenderNoise", label="Fully Random Noise"),
            NodeItem("SuperLuxCoreNodeTexBlenderStucci", label="Stucci"),
            NodeItem("SuperLuxCoreNodeTexBlenderWood", label="Wood"),
            NodeItem("SuperLuxCoreNodeTexBlenderVoronoi", label="Voronoi"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_MATH",
        "Math",
        items=[
            NodeItem("SuperLuxCoreNodeTexMath", label="Math"),
            NodeItem("SuperLuxCoreNodeTexColorMix", label="Color Math"),
            NodeItem("SuperLuxCoreNodeTexVectorMath", label="Vector Math"),
            NodeItem("SuperLuxCoreNodeTexDotProduct", label="Dot Product"),
            NodeItem("SuperLuxCoreNodeTexSplitFloat3", label="Split RGB"),
            NodeItem("SuperLuxCoreNodeTexMakeFloat3", label="Combine RGB"),
            NodeItem("SuperLuxCoreNodeTexRemap", label="Remap"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_UTILS",
        "Utils",
        items=[
            NodeItem("SuperLuxCoreNodeTexBump", label="Bump"),
            # Possibly confusing, better deactivate (only needed in very rare cases anyway)
            # NodeItem("SuperLuxCoreNodeTexNormalmap", label="Normalmap"),
            NodeItem("SuperLuxCoreNodeTexBand", label="ColorRamp"),
            NodeItem("SuperLuxCoreNodeTexDistort", label="Distort"),
            NodeItem("SuperLuxCoreNodeTexHSV", label="HSV"),
            NodeItem(
                "SuperLuxCoreNodeTexBrightContrast", label="Brightness/Contrast"
            ),
            NodeItem("SuperLuxCoreNodeTexInvert", label="Invert"),
            Separator(),
            NodeItem("SuperLuxCoreNodeTexConstfloat1", label="Constant Value"),
            NodeItem("SuperLuxCoreNodeTexConstfloat3", label="Constant Color"),
            NodeItem("SuperLuxCoreNodeTexIORPreset", label="IOR Preset"),
            Separator(),
            NodeItem("SuperLuxCoreNodeTexHitpointInfo", label="Hitpoint Info"),
            NodeItem("SuperLuxCoreNodeTexPointiness", label="Pointiness"),
            NodeItem("SuperLuxCoreNodeTexObjectID", label="Object ID"),
            NodeItem(
                "SuperLuxCoreNodeTexRandomPerIsland", label="Random Per Island"
            ),
            NodeItem("SuperLuxCoreNodeTexTimeInfo", label="Time Info"),
            NodeItem("SuperLuxCoreNodeTexUV", label="UV Test"),
            NodeItem("SuperLuxCoreNodeTexRandom", label="Random"),
            NodeItem("SuperLuxCoreNodeTexBombing", label="Bombing"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_MAPPING",
        "Mapping",
        items=[
            NodeItem("SuperLuxCoreNodeTexMapping2D", label="2D Mapping"),
            NodeItem("SuperLuxCoreNodeTexMapping3D", label="3D Mapping"),
            NodeItem("SuperLuxCoreNodeTexTriplanar", label="Triplanar Mapping"),
            NodeItem(
                "SuperLuxCoreNodeTexTriplanarBump", label="Triplanar Bump Mapping"
            ),
            NodeItem(
                "SuperLuxCoreNodeTexTriplanarNormalmap",
                label="Triplanar Normal Mapping",
            ),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_LIGHT",
        "Light",
        items=[
            NodeItem("SuperLuxCoreNodeMatEmission", label="Light Emission"),
            Separator(),
            NodeItem("SuperLuxCoreNodeTexLampSpectrum", label="Lamp Spectrum"),
            NodeItem("SuperLuxCoreNodeTexBlackbody", label="Blackbody Temperature"),
            NodeItem("SuperLuxCoreNodeTexIrregularData", label="Irregular Data"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_SHAPE",
        "Shape Modifiers",
        items=[
            NodeItem("SuperLuxCoreNodeShapeSubdiv", label="Subdivision"),
            NodeItem(
                "SuperLuxCoreNodeShapeHeightDisplacement",
                label="Height Displacement",
            ),
            NodeItem(
                "SuperLuxCoreNodeShapeVectorDisplacement",
                label="Vector Displacement",
            ),
            NodeItem("SuperLuxCoreNodeShapeSimplify", label="Simplify"),
            NodeItem("SuperLuxCoreNodeShapeHarlequin", label="Harlequin"),
            NodeItem(
                "SuperLuxCoreNodeShapeMergeOnDistance",
                label="Merge on Distance",
            ),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_POINTER",
        "Pointer",
        items=[
            NodeItem("SuperLuxCoreNodeTreePointer", label="Pointer"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_OUTPUT",
        "Output",
        items=[
            NodeItem("SuperLuxCoreNodeMatOutput", label="Output"),
        ],
    ),
    SuperLuxCoreNodeCategoryMaterial(
        "SUPERLUXCORE_MATERIAL_LAYOUT",
        "Layout",
        items=[
            NodeItem("NodeFrame", label="Frame"),
            NodeItem("NodeReroute", label="Reroute"),
        ],
    ),
]
