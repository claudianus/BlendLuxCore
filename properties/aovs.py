import bpy
from bpy.props import PointerProperty, BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import PropertyGroup


class SuperLuxCoreLPE(PropertyGroup):
    """One Light Path Expression -> one HDR output (EXR layer LPE.<name>)"""
    name: StringProperty(name="Name", default="lpe",
                         description="Channel name - saved as EXR layer LPE.<name>")
    expression: StringProperty(name="Expression", default="",
                               description="Light path expression, e.g. C<RD>L (direct diffuse), "
                                           "C<RD><RD>+L (indirect), C<RS>.*L (specular chains), "
                                           "C.*L (everything). Grammar: | ( ) * + ? . <preds>")


# Attached to view layer
class SuperLuxCoreAOVSettings(PropertyGroup):
    # Basic Information
    rgb: BoolProperty(name="RGB", default=False,
                       description="Raw RGB values (HDR)")
    rgba: BoolProperty(name="RGBA", default=False,
                       description="Raw RGBA values (HDR)")
    alpha: BoolProperty(name="Alpha", default=False,
                       description="Alpha value [0..1]")
    depth: BoolProperty(name="Depth", default=False,
                       description="Distance from camera (Z-Pass)")
    albedo: BoolProperty(name="Albedo", default=False,
                       description="Unlit material/texture colors")

    # Material/Object Information
    material_id: BoolProperty(name="Material ID", default=False,
                       description="Material ID (1 value per material, use the ID Mask Node "
                                   "in compositing nodes to extract a mask)")
    material_id_color: BoolProperty(name="Material ID Color", default=False,
                       description="Material ID (1 color per material, anti-aliased)")
    object_id: BoolProperty(name="Object ID", default=False,
                       description="Object ID (1 value per object, use the ID Mask Node "
                                   "in compositing nodes to extract a mask)")
    cryptomatte_object: BoolProperty(name="Cryptomatte Object", default=False,
                       description="Cryptomatte matte by object name (EXR, use the Cryptomatte "
                                   "node in compositing)")
    cryptomatte_material: BoolProperty(name="Cryptomatte Material", default=False,
                       description="Cryptomatte matte by material name (EXR, use the Cryptomatte "
                                   "node in compositing)")

    # Light Information
    emission: BoolProperty(name="Emission", default=False,
                       description="Emission R, G, B")
    caustic: BoolProperty(name="Caustic", default=False,
                       description="Light that was refracted/reflected by glossy or specular materials. "
                                   "This AOV is only available when using Path + Light Tracing. "
                                   "Note that it only contains light traced caustics, not caustics "
                                   "computed by the PhotonGI caustics cache")

    # Direct Light Information
    direct_diffuse: BoolProperty(name="Combined", default=False,
                       description="Direct Diffuse R, G, B")
    direct_diffuse_reflect: BoolProperty(name="Reflect", default=False,
                       description="")
    direct_diffuse_transmit: BoolProperty(name="Transmit", default=False,
                       description="")
    direct_glossy: BoolProperty(name="Combined", default=False,
                       description="Direct Glossy R, G, B (e.g. glossy, metal materials)")
    direct_glossy_reflect: BoolProperty(name="Reflect", default=False,
                       description="")
    direct_glossy_transmit: BoolProperty(name="Transmit", default=False,
                       description="")

    # Indirect Light Information
    indirect_diffuse: BoolProperty(name="Combined", default=False,
                       description="Indirect diffuse R, G, B")
    indirect_diffuse_reflect: BoolProperty(name="Reflect", default=False,
                       description="")
    indirect_diffuse_transmit: BoolProperty(name="Transmit", default=False,
                       description="")
    indirect_glossy: BoolProperty(name="Combined", default=False,
                       description="Indirect glossy R, G, B (e.g. glossy, metal materials)")
    indirect_glossy_reflect: BoolProperty(name="Reflect", default=False,
                       description="")
    indirect_glossy_transmit: BoolProperty(name="Transmit", default=False,
                       description="")
    indirect_specular: BoolProperty(name="Combined", default=False,
                       description="Indirect specular R, G, B (e.g. glass, mirror materials)")
    indirect_specular_reflect: BoolProperty(name="Reflect", default=False,
                       description="")
    indirect_specular_transmit: BoolProperty(name="Transmit", default=False,
                       description="")

    # Geometry Information
    position: BoolProperty(name="Position", default=False,
                       description="World X, Y, Z")
    shading_normal: BoolProperty(name="Shading Normal", default=False,
                       description="Normal vector X, Y, Z with mesh smoothing")
    avg_shading_normal: BoolProperty(name="Avg. Shading Normal", default=False,
                       description="Same as shading normal, but with anti-aliasing")
    geometry_normal: BoolProperty(name="Geometry Normal", default=False,
                       description="Normal vector X, Y, Z without mesh smoothing")
    uv: BoolProperty(name="UV", default=False,
                       description="Texture coordinates U, V")

    # Shadow Information
    direct_shadow_mask: BoolProperty(name="Direct", default=False,
                       description="Mask containing shadows by direct light")
    indirect_shadow_mask: BoolProperty(name="Indirect", default=False,
                       description="Mask containing shadows by indirect light")

    # Render Information
    raycount: BoolProperty(name="Raycount", default=False,
                       description="Ray count per pixel (normalized so the values range from 0 to 1)")
    samplecount: BoolProperty(name="Samplecount", default=False,
                       description="Samples per pixel (normalized so the values range from 0 to 1)")
    convergence: BoolProperty(name="Convergence", default=False,
                       description="The convergence per pixel. The lower the value, the more converged "
                                   "(noise-free) the pixel is. If the convergence halt condition is "
                                   "enabled, the render is stopped once all pixels fall below the "
                                   "convergence threshold")
    noise: BoolProperty(name="Noise", default=False,
                       description="The noise amount per pixel. High values mean more noise, low values less noise")
    irradiance: BoolProperty(name="Irradiance", default=False,
                       description="Surface irradiance")
    variance: BoolProperty(name="Variance", default=False,
                       description="Per-pixel radiance variance estimate (HDR). Input for external "
                                   "denoisers (e.g. Arnold noice) and temporal accumulation confidence")
    motion_vector: BoolProperty(name="Motion Vector", default=False,
                       description="Screen-space velocity of the first visible surface in pixels/frame "
                                   "(HDR: x,y = velocity, z = valid flag, w = object-motion flag). "
                                   "Required by temporal denoisers; needs PATH engine")

    # Light Path Expressions (PATH engine only; one HDR output each,
    # EXR layer name LPE.<name>)
    lpe_list: CollectionProperty(type=SuperLuxCoreLPE)
    lpe_index: IntProperty()
