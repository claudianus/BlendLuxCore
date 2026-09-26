from .. import utils
from . import (
    band, bevel, blackbody, blenderblend, blenderclouds, blenderdistortednoise, blendermagic,
    blendermarble, blendermusgrave, blendernoise, blenderstucci, blendervoronoi,
    blenderwood, bombing, brick, brightcontrast, bump, checkerboard2d, checkerboard3d,
    coloratdepth, colormix, constfloat1, constfloat3, distort, dotproduct, dots, fbm,
    fresnel, hitpoint, hitpointinfo, hsv, imagemap, invert, iorpreset, irregulardata,
    lampspectrum, makefloat3, mapping2d, mapping3d, marble, math, normalmap, objectid,
    openVDB, output, pointiness, random, random_per_island, remap, smoke, splitfloat3,
    timeinfo, tree, triplanar, triplanar_bump, triplanar_normalmap, uv, vectormath,
    windy, wireframe, wrinkled,
)
import nodeitems_utils
from .tree import superluxcore_node_categories_texture

classes = (
    band.ColorRampItem,
    band.SuperLuxCoreNodeTexBand,
    bevel.SuperLuxCoreNodeTexBevel,
    blackbody.SuperLuxCoreNodeTexBlackbody,
    blenderblend.SuperLuxCoreNodeTexBlenderBlend,
    blenderclouds.SuperLuxCoreNodeTexBlenderClouds,
    blenderdistortednoise.SuperLuxCoreNodeTexBlenderDistortedNoise,
    blendermagic.SuperLuxCoreNodeTexBlenderMagic,
    blendermarble.SuperLuxCoreNodeTexBlenderMarble,
    blendermusgrave.SuperLuxCoreNodeTexBlenderMusgrave,
    blendernoise.SuperLuxCoreNodeTexBlenderNoise,
    blenderstucci.SuperLuxCoreNodeTexBlenderStucci,
    blendervoronoi.SuperLuxCoreNodeTexBlenderVoronoi,
    blenderwood.SuperLuxCoreNodeTexBlenderWood,
    bombing.SuperLuxCoreNodeTexBombing,
    brick.SuperLuxCoreNodeTexBrick,
    brightcontrast.SuperLuxCoreNodeTexBrightContrast,
    bump.SuperLuxCoreNodeTexBump,
    checkerboard2d.SuperLuxCoreNodeTexCheckerboard2D,
    checkerboard3d.SuperLuxCoreNodeTexCheckerboard3D,
    coloratdepth.SuperLuxCoreNodeTexColorAtDepth,
    colormix.SuperLuxCoreNodeTexColorMix,
    constfloat1.SuperLuxCoreNodeTexConstfloat1,
    constfloat3.SuperLuxCoreNodeTexConstfloat3,
    distort.SuperLuxCoreNodeTexDistort,
    dotproduct.SuperLuxCoreNodeTexDotProduct,
    dots.SuperLuxCoreNodeTexDots,
    fbm.SuperLuxCoreNodeTexfBM,
    fresnel.SuperLuxCoreNodeTexFresnel,
    hitpoint.SuperLuxCoreNodeTexHitpoint,
    hitpointinfo.SuperLuxCoreNodeTexHitpointInfo,
    hsv.SuperLuxCoreNodeTexHSV,
    imagemap.SuperLuxCoreNodeTexImagemap,
    invert.SuperLuxCoreNodeTexInvert,
    iorpreset.SuperLuxCoreNodeTexIORPreset,
    irregulardata.SuperLuxCoreNodeTexIrregularData,
    lampspectrum.SuperLuxCoreNodeTexLampSpectrum,
    makefloat3.SuperLuxCoreNodeTexMakeFloat3,
    mapping2d.SuperLuxCoreNodeTexMapping2D,
    mapping3d.SuperLuxCoreNodeTexMapping3D,
    marble.SuperLuxCoreNodeTexMarble,
    math.SuperLuxCoreNodeTexMath,
    normalmap.SuperLuxCoreNodeTexNormalmap,
    objectid.SuperLuxCoreNodeTexObjectID,
    openVDB.SuperLuxCoreNodeTexOpenVDB,
    output.SuperLuxCoreNodeTexOutput,
    pointiness.SuperLuxCoreNodeTexPointiness,
    random.SuperLuxCoreNodeTexRandom,
    random_per_island.SuperLuxCoreNodeTexRandomPerIsland,
    remap.SuperLuxCoreNodeTexRemap,
    smoke.SuperLuxCoreNodeTexSmoke,
    splitfloat3.SuperLuxCoreNodeTexSplitFloat3,
    timeinfo.SuperLuxCoreNodeTexTimeInfo,
    tree.SuperLuxCoreTextureNodeTree,
    triplanar.SuperLuxCoreNodeTexTriplanar,
    triplanar_bump.SuperLuxCoreNodeTexTriplanarBump,
    triplanar_normalmap.SuperLuxCoreNodeTexTriplanarNormalmap,
    uv.SuperLuxCoreNodeTexUV,
    vectormath.SuperLuxCoreNodeTexVectorMath,
    windy.SuperLuxCoreNodeTexWindy,
    wireframe.SuperLuxCoreNodeTexWireframe,
    wrinkled.SuperLuxCoreNodeTexWrinkled,
)


def register():
    nodeitems_utils.register_node_categories(
        "SUPERLUXCORE_TEXTURE_TREE", superluxcore_node_categories_texture
    )

    utils.register_module("Textures", classes)

def unregister():
    utils.unregister_module("Textures", classes)

    nodeitems_utils.unregister_node_categories("SUPERLUXCORE_TEXTURE_TREE")
