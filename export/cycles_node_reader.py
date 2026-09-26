import bpy
import pyluxcore
from .. import utils
from ..utils import node as utils_node
from ..utils.errorlog import LuxCoreErrorLog
from . import named_attributes
from .image import ImageExporter
import math
from math import degrees, log
from mathutils import Euler, Matrix, Vector

ERROR_VALUE = 0
MISSING_IMAGE_COLOR = [1, 0, 1]
# Neutral fallbacks for unsupported outputs; never silently return black

# Node types with no LuxCore equivalent — the generic fallback path uses
# these to emit a specific reason instead of a bare "unsupported" warning.
_UNSUPPORTED_NODE_NOTES = {
    "ShaderNodeScript": "OSL scripts cannot be executed by LuxCore",
    "ShaderNodeShaderToRGB": "shader-to-color requires an Eevee-style raster pass",
    "ShaderNodeOutputAOV": "custom AOVs are written via LuxCore film outputs, not material nodes",
    "ShaderNodeOutputLineStyle": "Freestyle line-style output has no LuxCore equivalent",
    "ShaderNodeLightFalloff": "light falloff is configured on LuxCore light definitions",
    "ShaderNodeCameraData": "view vector/depth is not available to LuxCore textures",
    "ShaderNodeRaycast": "scene raycast queries are not available to LuxCore textures",
    "ShaderNodeRadialTiling": "no polar/radial tiling texture in LuxCore",
    "ShaderNodeTexIES": "IES profiles live on LuxCore light definitions, not material textures",
    "ShaderNodeTexSky": "sky models exist as LuxCore lights (sky2/sun), not material textures",
    "ShaderNodeSqueeze": "Freestyle squeeze value has no shading meaning",
    "ShaderNodeUVAlongStroke": "Freestyle stroke UVs have no shading meaning",
    "ShaderNodeBackground": "Background is a world-shader node; use Emission in materials",
}
FALLBACK_COLOR = [0.5, 0.5, 0.5]
FALLBACK_FLOAT = 0.5
FALLBACK_VECTOR = [0.0, 0.0, 0.0]

# ShaderNodeLightPath output socket -> LuxCore "rayinfo" texture channel.
# The engine fills HitPoint with the context of the incoming ray during
# Scene::Intersect() (ray flags, generating BSDF event, path depth
# counters, segment length). See LuxCore slg/textures/hitpoint/rayinfo.h.
_LIGHT_PATH_CHANNELS = {
    "Is Camera Ray": "iscameraray",
    "Is Shadow Ray": "isshadowray",
    "Is Diffuse Ray": "isdiffuseray",
    "Is Glossy Ray": "isglossyray",
    "Is Singular Ray": "issingularray",
    "Is Reflection Ray": "isreflectionray",
    "Is Transmission Ray": "istransmissionray",
    # Blender 4.x output; also 1 for hits inside volumes
    "Is Volume Scatter Ray": "isvolumescatterray",
    "Ray Length": "raylength",
    "Ray Depth": "raydepth",
    "Diffuse Depth": "diffusedepth",
    "Glossy Depth": "glossydepth",
    # Cycles counts transparent BSDF crossings; LuxCore increments the
    # path transparentDepth while stepping through pass-through materials
    "Transparent Depth": "transparentdepth",
    "Transmission Depth": "transmissiondepth",
}

math_operation_map = {
    "MULTIPLY": "scale",
    "GREATER_THAN": "greaterthan",
    "LESS_THAN": "lessthan",
}


def convert(material, props, luxcore_name, obj_name=""):
    # print("Converting Cycles node tree of material", material.name_full)
    output = material.node_tree.get_output_node("CYCLES")
    if output is None:
        return black(luxcore_name)

    link = utils_node.get_link(output.inputs["Surface"])
    volume_link = utils_node.get_link(output.inputs["Volume"]) if "Volume" in output.inputs else None

    # Note: the Displacement output is not handled here — it is a mesh-level
    # effect exported by the object cache as a LuxCore "displacement" shape
    # (see get_displacement_link / export_displacement below).

    if link is None and volume_link is None:
        return black(luxcore_name)

    if link is not None:
        result = _node(link.from_node, link.from_socket, props, material, luxcore_name, obj_name)
        if result == ERROR_VALUE:
            return black(luxcore_name)

        assert result == luxcore_name
    else:
        # Volume-only material: an invisible surface carrying the interior volume
        props.Set(utils.luxutils.create_props("scene.materials." + luxcore_name + ".", {
            "type": "null",
        }))

    if volume_link is not None:
        volume_defs = _volume(volume_link.from_node, volume_link.from_socket,
                              props, material, luxcore_name, obj_name)
        if volume_defs is not None:
            volume_name = luxcore_name + "_volume"
            props.Set(utils.luxutils.create_props("scene.volumes." + volume_name + ".", volume_defs))
            props.Set(pyluxcore.Property(
                "scene.materials." + luxcore_name + ".volume.interior", volume_name))
        # If None, _volume already logged a warning

    return luxcore_name, props


def get_displacement_link(material):
    """
    Returns the link feeding the Cycles output's Displacement socket, or
    None. Used by the object cache to decide whether to wrap the mesh in a
    LuxCore "displacement" shape.
    """
    node_tree = getattr(material, "node_tree", None)
    if node_tree is None:
        return None
    output = node_tree.get_output_node("CYCLES")
    if output is None:
        return None
    disp_input = output.inputs.get("Displacement")
    if disp_input is None or not disp_input.is_linked:
        return None
    return utils_node.get_link(disp_input)


def export_displacement(link, props, material, obj_name):
    """
    Exports the textures driving a Cycles Displacement/Vector Displacement
    node into props and returns the parameters for a LuxCore "displacement"
    shape, or None when the link is not a supported displacement node.
    """
    node = link.from_node

    if node.bl_idname == "ShaderNodeDisplacement":
        if getattr(node, "space", "OBJECT") != "OBJECT":
            LuxCoreErrorLog.add_warning(
                'Displacement node "%s": world space is not supported, '
                "object space is used instead" % node.name, obj_name=obj_name)
        height = _socket(node.inputs["Height"], props, material, obj_name, None)
        if height == ERROR_VALUE:
            return None
        scale = _scalar_or_warn(node.inputs["Scale"], 1.0, node, obj_name)
        midlevel = _scalar_or_warn(node.inputs["Midlevel"], 0.5, node, obj_name)
        # LuxCore: disp = (map * scale + offset) * N
        # Cycles:  disp = (height - midlevel) * scale * N
        return {
            "map": height,
            "map.type": "height",
            "scale": scale,
            "offset": -midlevel * scale,
        }

    if node.bl_idname == "ShaderNodeVectorDisplacement":
        if getattr(node, "space", "OBJECT") != "OBJECT":
            LuxCoreErrorLog.add_warning(
                'Vector Displacement node "%s": world space is not supported, '
                "object space is used instead" % node.name, obj_name=obj_name)
        vector = _socket(node.inputs["Vector"], props, material, obj_name, None)
        if vector == ERROR_VALUE:
            return None
        scale = _scalar_or_warn(node.inputs["Scale"], 1.0, node, obj_name)
        return {
            "map": vector,
            "map.type": "vector",
            "scale": scale,
            "offset": 0.0,
        }

    # Anything else plugged straight into Displacement behaves like bump in
    # Cycles — the material-level Normal/bump path covers that case.
    return None


def _scalar_or_warn(socket, fallback, node, obj_name):
    """ Reads a scalar socket; warns and falls back when it is textured. """
    if socket.is_linked:
        LuxCoreErrorLog.add_warning(
            'Node "%s": textured "%s" input is not supported for '
            "displacement, using default value" % (node.name, socket.name),
            obj_name=obj_name)
        return fallback
    return socket.default_value


def black(luxcore_name="__BLACK__"):
    props = pyluxcore.Properties()
    props.SetFromString("""
    scene.materials.{mat_name}.type = matte
    scene.materials.{mat_name}.kd = 0
    """.format(mat_name=luxcore_name))
    return luxcore_name, props


def _warn_unsupported(node, reason, fallback, obj_name=""):
    """
    Log a warning about an unsupported node/output/feature and return a
    neutral fallback so the material still renders plausibly instead of
    silently turning black.
    """
    LuxCoreErrorLog.add_warning(
        f'Node "{node.name}" ({node.bl_idname}): {reason}', obj_name=obj_name)
    return fallback


def _tex_helper(props, name, definitions):
    """Emit a scene.textures.* definition under the given name, return the name."""
    tex_name = utils.sanitize_luxcore_name(name)
    props.Set(utils.luxutils.create_props("scene.textures." + tex_name + ".", definitions))
    return tex_name


def _socket_nondefault(socket, default, eps=1e-4):
    """
    True when an input socket is linked or its constant value differs from
    `default` (the Principled node's factory value). Vector/color defaults are
    compared per channel; `socket` may be None on Blender versions where the
    input does not exist.
    """
    if socket is None:
        return False
    if socket.is_linked:
        return True
    value = getattr(socket, "default_value", default)
    if isinstance(default, (list, tuple)):
        try:
            return any(abs(v - d) > eps for v, d in zip(list(value)[:3], default))
        except TypeError:
            return True
    try:
        return abs(float(value) - float(default)) > eps
    except (TypeError, ValueError):
        return True


def _socket_active(socket):
    """True when a [0,1] weight socket is linked or has a non-zero constant."""
    return socket is not None and (
        socket.is_linked or socket.default_value != 0.0)


def _color_is_gray(value, eps=5e-2):
    """True when a color constant is (near) grayscale, i.e. carries no hue."""
    if _is_textured(value):
        return False
    try:
        v = list(value)[:3]
    except TypeError:
        return True
    return max(v) - min(v) <= eps


def _const_binary(op, value1, value2):
    """Fold a two-operand math op on plain constants (floats or 3-lists)."""
    def as_vec(v):
        return list(v)[:3] if isinstance(v, (list, tuple)) else [v, v, v]

    try:
        if op == "dotproduct":
            a, b = as_vec(value1), as_vec(value2)
            return sum(x * y for x, y in zip(a, b))
        if isinstance(value1, (list, tuple)) or isinstance(value2, (list, tuple)):
            a, b = as_vec(value1), as_vec(value2)
            if op == "add":
                return [x + y for x, y in zip(a, b)]
            if op == "subtract":
                return [x - y for x, y in zip(a, b)]
            if op in {"scale", "multiply"}:
                return [x * y for x, y in zip(a, b)]
            if op == "divide":
                return [x / y if y != 0 else 0.0 for x, y in zip(a, b)]
            return None
        if op == "add":
            return value1 + value2
        if op == "subtract":
            return value1 - value2
        if op in {"scale", "multiply"}:
            return value1 * value2
        if op == "divide":
            return value1 / value2 if value2 != 0 else 0.0
        if op == "power":
            return value1 ** value2
        if op == "lessthan":
            return 1.0 if value1 < value2 else 0.0
        if op == "greaterthan":
            return 1.0 if value1 > value2 else 0.0
    except (TypeError, IndexError):
        pass
    return None


def _tex_binary(op, texture1, texture2, name, props):
    """
    Emit a two-operand math texture (add/subtract/scale/divide/dotproduct),
    folding constants when both operands are plain values.
    """
    if not _is_textured(texture1) and not _is_textured(texture2):
        folded = _const_binary(op, texture1, texture2)
        if folded is not None:
            return folded
    return _tex_helper(props, name, {
        "type": op,
        "texture1": texture1,
        "texture2": texture2,
    })


def _tex_mix(texture1, texture2, amount, name, props):
    """Emit a mix texture, folding constants."""
    if not _is_textured(texture1) and not _is_textured(texture2) \
            and not _is_textured(amount):
        def as_vec(v):
            return list(v)[:3] if isinstance(v, (list, tuple)) else [v, v, v]
        a, b = as_vec(texture1), as_vec(texture2)
        folded = [x * (1 - amount) + y * amount for x, y in zip(a, b)]
        if not isinstance(texture1, (list, tuple)) and not isinstance(texture2, (list, tuple)):
            return folded[0]
        return folded
    return _tex_helper(props, name, {
        "type": "mix",
        "texture1": texture1,
        "texture2": texture2,
        "amount": amount,
    })


def _tex_lessthan(t1, t2, name, props):
    """lessthan with constant folding (1 when t1 < t2)."""
    if not _is_textured(t1) and not _is_textured(t2):
        if isinstance(t1, (list, tuple)) or isinstance(t2, (list, tuple)):
            a = list(t1)[:3] if isinstance(t1, (list, tuple)) else [t1] * 3
            b = list(t2)[:3] if isinstance(t2, (list, tuple)) else [t2] * 3
            return [1.0 if x < y else 0.0 for x, y in zip(a, b)]
        return 1.0 if t1 < t2 else 0.0
    return _tex_helper(props, name, {
        "type": "lessthan", "texture1": t1, "texture2": t2})


def _tex_greaterthan(t1, t2, name, props):
    """greaterthan with constant folding."""
    if not _is_textured(t1) and not _is_textured(t2):
        if isinstance(t1, (list, tuple)) or isinstance(t2, (list, tuple)):
            a = list(t1)[:3] if isinstance(t1, (list, tuple)) else [t1] * 3
            b = list(t2)[:3] if isinstance(t2, (list, tuple)) else [t2] * 3
            return [1.0 if x > y else 0.0 for x, y in zip(a, b)]
        return 1.0 if t1 > t2 else 0.0
    return _tex_helper(props, name, {
        "type": "greaterthan", "texture1": t1, "texture2": t2})


_MATHFUNC_UNARY_OPS = {
    "SINE": "sin", "COSINE": "cos", "TANGENT": "tan",
    "ARCSINE": "asin", "ARCCOSINE": "acos", "ARCTANGENT": "atan",
    "SINH": "sinh", "COSH": "cosh", "TANH": "tanh",
    "INVERSE_SQRT": "invsqrt",
}

_MATHFUNC_BINARY_OPS = {
    "ARCTAN2": "atan2", "FLOORED_MODULO": "floormod",
}


def _floormod_fold(a, b):
    return 0.0 if b == 0.0 else a - b * math.floor(a / b)


_MATHFUNC_FOLD = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2, "exp": math.exp, "ln": math.log,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "invsqrt": lambda a: 1.0 / math.sqrt(max(a, 1e-9)),
    "floormod": _floormod_fold,
}


def _tex_mathfunc(op, tex1, tex2, name, props):
    """Emit a mathfunc texture (trig/exp/log/mod), folding constants."""
    binary = op in ("atan2", "floormod")
    textured = _is_textured(tex1) or (binary and _is_textured(tex2))
    if not textured:
        def val(t):
            return t[0] if isinstance(t, (list, tuple)) else t
        try:
            if binary:
                return _MATHFUNC_FOLD[op](val(tex1), val(tex2))
            return _MATHFUNC_FOLD[op](val(tex1))
        except (ValueError, OverflowError, ZeroDivisionError):
            pass  # domain error at fold time — let the texture evaluate it
    definitions = {"type": "mathfunc", "op": op, "texture1": tex1}
    if binary:
        definitions["texture2"] = tex2
    return _tex_helper(props, name, definitions)


def _tex_unary(op, tex, arg, name, props):
    """
    Fold/emit single-input math textures: abs (arg=None),
    rounding (arg=increment), modulo (arg=modulus).
    """
    if not _is_textured(tex) and (arg is None or not _is_textured(arg)):
        def ap(f, v):
            if isinstance(v, (list, tuple)):
                return [f(x) for x in v[:3]]
            return f(v)
        if op == "abs":
            return ap(abs, tex)
        if op == "rounding":
            inc = arg if arg else 1.0
            return ap(lambda v: inc * round(v / inc) if inc else v, tex)
        if op == "modulo":
            return ap(lambda v: v % arg if arg else 0.0, tex)
    if op == "abs":
        return _tex_helper(props, name, {"type": "abs", "texture": tex})
    if op == "rounding":
        return _tex_helper(props, name, {
            "type": "rounding", "texture": tex, "increment": arg})
    if op == "modulo":
        return _tex_helper(props, name, {
            "type": "modulo", "texture": tex, "modulo": arg})
    raise ValueError(op)


def _split_chan(value, channel, name, props):
    """Extract one channel of a float3 value or texture (constants fold)."""
    if _is_textured(value):
        return _tex_helper(props, name, {
            "type": "splitfloat3", "texture": value, "channel": channel})
    if isinstance(value, (list, tuple)):
        return list(value)[channel]
    return value


def _combine3(x, y, z, name, props):
    """Reassemble three channel values into a float3 (constants fold)."""
    if _is_textured(x) or _is_textured(y) or _is_textured(z):
        return _tex_helper(props, name, {
            "type": "makefloat3",
            "texture1": x, "texture2": y, "texture3": z})
    def f(v):
        return v[0] if isinstance(v, (list, tuple)) else v
    return [f(x), f(y), f(z)]


def _smooth_min(a, b, k, name, props):
    """
    Polynomial smooth-min: h = clamp(0.5 + 0.5*(b-a)/k, 0, 1);
    result = mix(b, a, h) - k*h*(1-h). Folds constants.
    """
    if not any(_is_textured(t) for t in (a, b, k)):
        def _s(x):
            return x[0] if isinstance(x, (list, tuple)) else x
        va, vb, vk = _s(a), _s(b), max(_s(k), 1e-9)
        h = min(1.0, max(0.0, 0.5 + 0.5 * (vb - va) / vk))
        return vb * (1 - h) + va * h - vk * h * (1 - h)
    d = _tex_binary("subtract", b, a, f"{name}_d", props)
    hd = _tex_binary("divide", d, k, f"{name}_hd", props)
    hh = _tex_binary("scale", hd, 0.5, f"{name}_hh", props)
    hu = _tex_binary("add", 0.5, hh, f"{name}_hu", props)
    h = _tex_helper(props, f"{name}_h", {
        "type": "clamp", "texture": hu, "min": 0.0, "max": 1.0})
    mixv = _tex_mix(b, a, h, f"{name}_mx", props)
    one_h = _tex_binary("subtract", 1.0, h, f"{name}_1h", props)
    hh1 = _tex_binary("scale", h, one_h, f"{name}_hh1", props)
    corr = _tex_binary("scale", k, hh1, f"{name}_cr", props)
    return _tex_binary("subtract", mixv, corr, f"{name}_r", props)


def _v3_mathfunc(op, vec, name, props):
    """Elementwise mathfunc on a vector value or texture (folds consts)."""
    if not _is_textured(vec):
        v = list(vec)[:3] if isinstance(vec, (list, tuple)) else [vec] * 3
        try:
            return [_MATHFUNC_FOLD[op](x) for x in v]
        except (ValueError, OverflowError):
            pass
    comps = [_tex_mathfunc(op, _split_chan(vec, c, f"{name}_c{c}", props),
                           None, f"{name}_f{c}", props)
             for c in range(3)]
    return _combine3(comps[0], comps[1], comps[2], name, props)


def _const_mat_mul_vec(mat_rows, vec, name, props):
    """
    Apply a constant 3x3 matrix (3 rows of 3 floats) to a vector value or
    texture. Rows emit scale/add/makefloat3 helper textures when vec is
    texture-driven; constant vectors fold in Python.
    """
    if not _is_textured(vec):
        v = list(vec)[:3] if isinstance(vec, (list, tuple)) else [vec] * 3
        return [sum(mat_rows[r][c] * v[c] for c in range(3)) for r in range(3)]
    comps = [_split_chan(vec, c, f"{name}_s{c}", props) for c in range(3)]
    out = []
    for r in range(3):
        terms = [
            _tex_binary("scale", comps[c], mat_rows[r][c], f"{name}_r{r}{c}", props)
            for c in range(3) if mat_rows[r][c] != 0
        ]
        if not terms:
            out.append(0.0)
            continue
        acc = terms[0]
        for i, t in enumerate(terms[1:]):
            acc = _tex_binary("add", acc, t, f"{name}_r{r}p{i}", props)
        out.append(acc)
    return _combine3(out[0], out[1], out[2], name + "_mat", props)


def _vtransform_matrix(cfrom, cto, obj_name):
    """
    Constant world-space 4x4 matrix converting from coordinate space
    `cfrom` to `cto` ("WORLD"/"OBJECT"/"CAMERA"), or None when a space
    cannot be resolved (no object of that name, no scene camera).
    """
    def world_mat(space):
        if space == "WORLD":
            return Matrix.Identity(4)
        if space == "CAMERA":
            cam = bpy.context.scene.camera
            return cam.matrix_world.copy() if cam else None
        obj = bpy.data.objects.get(obj_name)
        return obj.matrix_world.copy() if obj else None

    m_from = world_mat(cfrom)
    m_to = world_mat(cto)
    if m_from is None or m_to is None:
        return None
    return m_to.inverted_safe() @ m_from


def _blend_rgb(node, blend_type, fac, tex1, tex2, luxcore_name, props, obj_name):
    """
    Shared implementation for ShaderNodeMixRGB and the RGBA variant of the
    unified ShaderNodeMix node.
    Returns (definitions, luxcore_name, early_result); when early_result is not
    None the caller returns it directly.
    """
    # TODO (in LuxCore):
    #  "DARKEN", "BURN", "LIGHTEN", "SCREEN", "DODGE", "OVERLAY", "SOFT_LIGHT",
    #  "LINEAR_LIGHT", "DIFFERENCE", "HUE", "SATURATION", "COLOR", "VALUE"
    definitions = {}

    if fac == 0:
        return None, luxcore_name, tex1

    if blend_type in {"MIX", "MULTIPLY", "ADD", "SUBTRACT", "DIVIDE"}:
        if blend_type == "MULTIPLY":
            definitions["type"] = "scale"
        else:
            definitions["type"] = blend_type.lower()

        definitions["texture1"] = tex1
        definitions["texture2"] = tex2

        if blend_type == "MIX":
            definitions["amount"] = fac
            if fac == 1:
                return None, luxcore_name, tex2
    else:
        # Never silently black: warn and degrade to a plain mix
        LuxCoreErrorLog.add_warning(
            f'Node "{node.name}": unsupported blend mode "{blend_type}", '
            'falling back to "mix"', obj_name=obj_name)
        definitions = {
            "type": "mix",
            "texture1": tex1,
            "texture2": tex2,
            "amount": fac,
        }
        return definitions, luxcore_name, None

    if (_is_textured(fac) or (fac > 0 and fac < 1)) and blend_type != "MIX":
        # Here we need to insert a helper texture *after* the current texture
        props.Set(utils.luxutils.create_props("scene.textures." + luxcore_name + ".", definitions))
        definitions = {
            "type": "mix",
            "texture1": tex1,
            "texture2": luxcore_name,
            "amount": fac,
        }
        luxcore_name = luxcore_name + "fac"

    return definitions, luxcore_name, None


def _mapping_node_values(mapping_node, obj_name):
    """
    Constant (location, rotation, scale) of a Cycles Mapping node.
    Linked transform inputs are approximated by their default value.
    """
    def const_input(name, fallback):
        socket = mapping_node.inputs.get(name)
        if socket is None:
            return fallback
        if socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Mapping node "{mapping_node.name}": linked "{name}" input is not '
                "supported, using its constant default value", obj_name=obj_name)
        try:
            return list(socket.default_value)[:3]
        except TypeError:
            return socket.default_value

    return (const_input("Location", [0, 0, 0]),
            const_input("Rotation", [0, 0, 0]),
            const_input("Scale", [1, 1, 1]))


def _mapping_matrix(location, rotation, scale):
    """Blender Mapping node transform: scale, then rotate, then translate."""
    return (Matrix.Translation(Vector(location)) @
            Euler(rotation).to_matrix().to_4x4() @
            Matrix.Diagonal(Vector(scale)).to_4x4())


def _mapping_uv_defs(location, rotation, scale, flip_v):
    """
    uvmapping2d definitions for a Mapping node transform (rotation around the
    origin, an approximation of Blender's transform order).
    With flip_v the v-flip required by Blender image space is folded in.
    """
    v_scale = scale[1] if len(scale) > 1 else scale[0]
    if flip_v:
        # Image sampling applies v_img = 1 - v_uv, so the user transform
        # composes to v_img = -sy * v + (1 - ly); the rotation direction flips
        # together with the v axis
        return {
            "mapping.type": "uvmapping2d",
            "mapping.uvscale": [scale[0], -v_scale],
            "mapping.uvdelta": [location[0], 1 - location[1]],
            "mapping.rotation": -degrees(rotation[2]),
        }
    return {
        "mapping.type": "uvmapping2d",
        "mapping.uvscale": [scale[0], v_scale],
        "mapping.uvdelta": [location[0], location[1]],
        "mapping.rotation": degrees(rotation[2]),
    }


def _uv_layer_index(obj_name, uv_map_name):
    """Resolve a UV layer name to the exported uvindex of the object's mesh."""
    obj = bpy.data.objects.get(obj_name) if obj_name else None
    uv_layers = getattr(getattr(obj, "data", None), "uv_layers", None)
    if uv_layers is None:
        return None
    index = uv_layers.find(uv_map_name)
    return index if index >= 0 else None


def _color_attribute_index(obj_name, attribute_name):
    """Resolve a color attribute name to the exported dataindex of the mesh."""
    obj = bpy.data.objects.get(obj_name) if obj_name else None
    attributes = getattr(getattr(obj, "data", None), "color_attributes", None)
    if attributes is None:
        return None
    for index, attribute in enumerate(attributes):
        if attribute.name == attribute_name:
            return index
    return None


def _vector_mapping_defs(vector_socket, is_2d, flip_v, props, material, obj_name,
                         group_node_stack):
    """
    `mapping.*` definitions honoring the node linked to a texture's Vector
    input. Returns an empty dict when the engine default (UV mapping) applies.
    """
    link = utils_node.get_link(vector_socket)
    if link is None:
        return {}
    source, source_socket = link.from_node, link.from_socket

    if source.bl_idname == "ShaderNodeMapping":
        location, rotation, scale = _mapping_node_values(source, obj_name)
        if is_2d:
            return _mapping_uv_defs(location, rotation, scale, flip_v)
        # Note: chained Mapping nodes are not composed (single mapping block)
        return {
            "mapping.type": "localmapping3d",
            "mapping.transformation": utils.luxutils.matrix_to_list(
                _mapping_matrix(location, rotation, scale)),
        }

    if source.bl_idname == "ShaderNodeUVMap":
        index = _uv_layer_index(obj_name, getattr(source, "uv_map", ""))
        if index is None:
            LuxCoreErrorLog.add_warning(
                f'UV map "{getattr(source, "uv_map", "")}" of node "{source.name}" '
                "could not be resolved, using the default UV layer",
                obj_name=obj_name)
            return {}
        return {
            "mapping.type": "uvmapping2d" if is_2d else "uvmapping3d",
            "mapping.uvindex": index,
        }

    if source.bl_idname == "ShaderNodeTexCoord":
        if source_socket.name == "UV":
            return {}  # the default UV mapping already matches
        if source_socket.name == "Generated":
            LuxCoreErrorLog.add_warning(
                "Generated texture coordinates are approximated by the UV mapping "
                "(no bounding-box normalization in LuxCore)", obj_name=obj_name)
            return {}
        if source_socket.name == "Object" and not is_2d:
            # LocalMapping3D evaluates the hit point in object space
            return {"mapping.type": "localmapping3d"}
        LuxCoreErrorLog.add_warning(
            f'Texture coordinate output "{source_socket.name}" of node '
            f'"{source.name}" is approximated by the default UV mapping',
            obj_name=obj_name)
        return {}

    LuxCoreErrorLog.add_warning(
        f'Node "{source.name}" cannot drive a texture Vector input; '
        "the default UV mapping is used", obj_name=obj_name)
    return {}


def _evaluate_curve(curve_map, curve_mapping, position):
    """Sample a CurveMap, tolerating signature differences between versions."""
    try:
        return curve_map.evaluate(curve_mapping, position)
    except TypeError:
        return curve_map.evaluate(position)


def _socket(socket, props, material, obj_name, group_node, luxcore_name=None):
    link = utils_node.get_link(socket)
    if link:
        # Pass luxcore_name through so pass-through nodes can re-emit the
        # upstream subtree under the requested name (convert() relies on the
        # top-level node returning the name it was given)
        return _node(link.from_node, link.from_socket, props, material,
                     luxcore_name, obj_name, group_node)

    if not hasattr(socket, "default_value"):
        return ERROR_VALUE

    try:
        return list(socket.default_value)[:3]
    except TypeError:
        # Not iterable
        return socket.default_value


def _principled_thin_film(node, definitions, props, material, obj_name,
                          group_node_stack, with_amount):
    """
    Principled v2 Thin Film Thickness/IOR -> LuxCore thin-film interference
    params. Disney uses filmamount + filmthickness + filmior; glass-family
    materials only have filmthickness/filmior (thickness > 0 enables it).
    """
    tf_thickness_socket = node.inputs.get("Thin Film Thickness")
    if tf_thickness_socket is not None and (
        tf_thickness_socket.is_linked
        or tf_thickness_socket.default_value != 0.0
    ):
        if with_amount:
            definitions["filmamount"] = 1.0
        definitions["filmthickness"] = _socket(
            tf_thickness_socket, props, material, obj_name, group_node_stack
        )
        tf_ior_socket = node.inputs.get("Thin Film IOR")
        if tf_ior_socket is not None and (
            tf_ior_socket.is_linked
            or abs(tf_ior_socket.default_value - 1.33) > 1e-4
        ):
            definitions["filmior"] = _socket(
                tf_ior_socket, props, material, obj_name, group_node_stack
            )


def _principled_disney_warnings(node, transmission, thin_wall_on, obj_name):
    """
    Emit warnings for Principled v2 inputs that LuxCore's Disney material
    cannot express. Each warning only fires when the feature is actually
    used (weight active + input linked/non-default) to avoid noise on
    untouched sockets.
    """
    # Sheen Roughness / Sheen Tint (Disney sheen is a fixed-gloss lobe whose
    # tint blends white<->base color; Principled's tint is a free color)
    if _socket_active(node.inputs.get("Sheen Weight")):
        if _socket_nondefault(node.inputs.get("Sheen Roughness"), 0.5):
            _warn_unsupported(
                node, "Sheen Roughness is not supported by LuxCore's Disney "
                "material (the sheen lobe has no roughness); ignored",
                None, obj_name)
        sheen_tint_socket = node.inputs.get("Sheen Tint")
        if sheen_tint_socket is not None and (
                sheen_tint_socket.is_linked
                or not _color_is_gray(sheen_tint_socket.default_value)):
            _warn_unsupported(
                node, "Sheen Tint is a free color in Principled but a "
                "white<->basecolor blend factor in the Disney material; "
                "approximated by its luminance", None, obj_name)

    # Specular Tint has the same color-vs-blend-factor mismatch
    spec_tint_socket = node.inputs.get("Specular Tint")
    if spec_tint_socket is not None and (
            spec_tint_socket.is_linked
            or not _color_is_gray(spec_tint_socket.default_value)):
        _warn_unsupported(
            node, "Specular Tint is a free color in Principled but a "
            "white<->basecolor blend factor in the Disney material; "
            "approximated by its luminance", None, obj_name)

    # Diffuse Roughness: Disney drives the diffuse lobe with the specular
    # roughness, there is no separate parameter
    if _socket_nondefault(node.inputs.get("Diffuse Roughness"), 0.0):
        _warn_unsupported(
            node, "Diffuse Roughness is not supported by LuxCore's Disney "
            "material (the specular Roughness drives the diffuse lobe); "
            "ignored", None, obj_name)

    # Anisotropic Rotation / Tangent: Disney anisotropy is axis-aligned with
    # the shading frame and has no rotation parameter
    if _socket_active(node.inputs.get("Anisotropic")) and (
            _socket_nondefault(node.inputs.get("Anisotropic Rotation"), 0.0)
            or _socket_nondefault(node.inputs.get("Tangent"), (0.0, 0.0, 0.0))):
        _warn_unsupported(
            node, "anisotropy direction (Anisotropic Rotation, Tangent) is "
            "not supported by LuxCore's Disney material; the anisotropy "
            "follows the shading frame", None, obj_name)

    # Subsurface: Disney's subsurface is the weight-only diffuse-profile
    # lobe (a Burley-style approximation) — radius/scale/IOR/anisotropy and
    # the random-walk methods need real volumetric scattering
    if _socket_active(node.inputs.get("Subsurface Weight")):
        sss_ignored = [name for name, socket, default in (
            ("Subsurface Radius", node.inputs.get("Subsurface Radius"),
             (1.0, 0.2, 0.1)),
            ("Subsurface Scale", node.inputs.get("Subsurface Scale"), 0.005),
            ("Subsurface IOR", node.inputs.get("Subsurface IOR"), 1.4),
            ("Subsurface Anisotropy", node.inputs.get("Subsurface Anisotropy"),
             0.0),
        ) if _socket_nondefault(socket, default)]
        sss_method = getattr(node, "subsurface_method", "BURLEY")
        if sss_ignored or sss_method != "BURLEY":
            details = []
            if sss_ignored:
                details.append("ignored inputs: " + ", ".join(sss_ignored))
            if sss_method != "BURLEY":
                details.append(
                    "'%s' scattering requires a scattering interior volume, "
                    "which the Disney material cannot drive"
                    % sss_method.replace("_", " ").title())
            _warn_unsupported(
                node, "subsurface is approximated by the Disney "
                "diffuse-profile lobe (weight only); " + "; ".join(details),
                None, obj_name)

    # Thin Wall only changes the transmission lobe — warn when transmission
    # can actually occur (partial transmission path; the sharp full-
    # transmission case is handled by the archglass mapping)
    transmission_active = _is_textured(transmission) or transmission != 0
    if thin_wall_on and transmission_active:
        _warn_unsupported(
            node, "Thin Wall is not supported by LuxCore's Disney material; "
            "transmission refracts as a solid volume (total internal "
            "reflection and interior volumes apply)", None, obj_name)


def _node(node, output_socket, props, material, luxcore_name=None, obj_name="", group_node_stack=None):
    if luxcore_name is None:
        luxcore_name = str(node.as_pointer()) + output_socket.name
        if group_node_stack:
            for n in group_node_stack:
                luxcore_name += str(n.as_pointer())
        luxcore_name = utils.sanitize_luxcore_name(luxcore_name)

    if node.bl_idname == "ShaderNodeBsdfPrincipled":
        prefix = "scene.materials."
        base_color = _socket(node.inputs["Base Color"], props, material, obj_name, group_node_stack)
        metallic_socket = node.inputs["Metallic"]
        metallic = _socket(metallic_socket, props, material, obj_name, group_node_stack)
        transmission_socket = node.inputs["Transmission Weight"]
        transmission = _socket(transmission_socket, props, material, obj_name, group_node_stack)

        # --- Principled v2 feature detection (sockets may not exist on
        # older Blender versions -> .get() everywhere) ---

        # Thin Wall: transmission behaves as an infinitely thin slab (no ray
        # offset, no TIR, no interior volume). Only const-True can switch the
        # material type; a linked flag falls back to the warning path.
        thin_wall_socket = node.inputs.get("Thin Wall")
        thin_wall_linked = thin_wall_socket is not None and thin_wall_socket.is_linked
        thin_wall_on = thin_wall_socket is not None and (
            thin_wall_linked or bool(thin_wall_socket.default_value))

        # Coat. Disney's built-in clearcoat is a fixed-IOR (1.5), untinted
        # lobe sharing the material normal. When Coat IOR / Coat Tint / Coat
        # Normal are actually used, wrap the base material in a real
        # dielectric coat layer (glossycoating) instead — see below.
        coat_weight_socket = node.inputs.get("Coat Weight")
        coat_weight = _socket(coat_weight_socket, props, material, obj_name,
                              group_node_stack) if coat_weight_socket is not None else 0.0
        coat_active = _socket_active(coat_weight_socket)
        coat_ior_socket = node.inputs.get("Coat IOR")
        coat_tint_socket = node.inputs.get("Coat Tint")
        coat_normal_socket = node.inputs.get("Coat Normal")
        coat_extra = (_socket_nondefault(coat_ior_socket, 1.5)
                      or _socket_nondefault(coat_tint_socket, (1.0, 1.0, 1.0))
                      or _socket_nondefault(coat_normal_socket, (0.0, 0.0, 0.0)))

        if transmission == 1 and metallic == 0:
            # It's effectively glass instead of a disney material.
            # Don't use mix for performance reasons.
            roughness = _squared_roughness_to_linear(node.inputs["Roughness"], props, material,
                                                     luxcore_name, obj_name, group_node_stack)
            # Glass materials have no built-in coat lobe, so any active coat
            # needs the glossycoating wrap (even with default coat params).
            use_coating = coat_active

            if thin_wall_on and not thin_wall_linked and roughness == 0:
                # Thin-walled sharp transmission: rays refract in and out of
                # the thin slab and exit parallel to the incident ray, so
                # archglass (Fresnel reflection + unbent transmission, no
                # interior volume, no TIR) is the physically matching model.
                definitions = {
                    "type": "archglass",
                    "kt": base_color,
                    "kr": [1, 1, 1],
                    "interiorior": _socket(node.inputs["IOR"], props, material, obj_name, group_node_stack),
                }
            else:
                if thin_wall_on:
                    _warn_unsupported(
                        node, "Thin Wall is only supported for sharp full "
                        "transmission (mapped to archglass); rough or textured "
                        "transmission is exported as solid glass — rays are "
                        "refracted and the interior volume applies",
                        None, obj_name)
                definitions = {
                    "type": "glass" if roughness == 0 else "roughglass",
                    "kt": base_color,
                    "kr": [1, 1, 1],
                    "interiorior": _socket(node.inputs["IOR"], props, material, obj_name, group_node_stack),
                }

                if roughness != 0:
                    definitions["uroughness"] = roughness
                    definitions["vroughness"] = roughness

            # Thin film works on glass/roughglass/archglass too (activated by
            # filmthickness > 0; there is no filmamount on these materials)
            _principled_thin_film(node, definitions, props, material, obj_name,
                                  group_node_stack, with_amount=False)
        else:
            use_coating = coat_active and coat_extra
            definitions = {
                # TODO (needs OpenPBR material — no Disney params):
                #  - subsurface radius/scale/IOR/anisotropy (Disney has weight only)
                #  - sheen roughness/tint color, diffuse roughness
                #  - anisotropic rotation, tangent
                "type": "disney",
                "basecolor": base_color,
                "subsurface": _socket(node.inputs["Subsurface Weight"], props, material, obj_name, group_node_stack),
                "metallic": metallic,
                "specular": _socket(node.inputs["Specular IOR Level"], props, material, obj_name, group_node_stack),
                "speculartint": _socket(node.inputs["Specular Tint"], props, material, obj_name, group_node_stack),
                # Both LuxCore and Cycles use squared roughness here, no need to convert
                "roughness": _socket(node.inputs["Roughness"], props, material, obj_name, group_node_stack),
                "anisotropic": _socket(node.inputs["Anisotropic"], props, material, obj_name, group_node_stack),
                "sheen": _socket(node.inputs["Sheen Weight"], props, material, obj_name, group_node_stack),
                "sheentint": _socket(node.inputs["Sheen Tint"], props, material, obj_name, group_node_stack),
                # When a glossycoating wraps this material, the Disney
                # clearcoat lobe is disabled so the coat is not applied twice.
                "clearcoat": 0.0 if use_coating else coat_weight,
                # Disney clearcoatgloss = 1 - coat_roughness
                "clearcoatgloss": 1.0 if use_coating else (_tex_helper(props, luxcore_name + "coatgloss", {
                    "type": "subtract",
                    "texture1": 1.0,
                    "texture2": _socket(node.inputs["Coat Roughness"], props, material, obj_name, group_node_stack),
                }) if node.inputs["Coat Roughness"].is_linked
                    or node.inputs["Coat Roughness"].default_value != 0.0
                    else 1.0),
                # Integrated dielectric transmission lobe (no disney+glass
                # mix hack): weight, roughness and IOR map directly.
                "transmission": transmission,
                "ior": _socket(node.inputs["IOR"], props, material, obj_name, group_node_stack),
            }

            # Transmission Roughness: Principled default 0 (sharp). Always emit
            # the socket value — the Disney material otherwise falls back to
            # the base roughness, which would break sharp-transmission looks.
            tr_roughness_socket = node.inputs.get("Transmission Roughness")
            if tr_roughness_socket is not None:
                definitions["transmissionroughness"] = _socket(
                    tr_roughness_socket, props, material, obj_name, group_node_stack
                )

            # Thin film (Principled v2): thickness + IOR -> Disney film params
            _principled_thin_film(node, definitions, props, material, obj_name,
                                  group_node_stack, with_amount=True)

            # --- Honest warnings for the remaining Principled inputs the
            # Disney material cannot express ---
            _principled_disney_warnings(node, transmission, thin_wall_on,
                                        obj_name)

        # Attach these props to the right-most material node (regardless if it's glass, disney or a mix mat)
        # Principled v2: emission = Emission Color * Emission Strength
        emission_strength = _socket(node.inputs["Emission Strength"], props, material, obj_name, group_node_stack)
        emission_color = _socket(node.inputs["Emission Color"], props, material, obj_name, group_node_stack)
        if emission_color == [1.0, 1.0, 1.0] or emission_color == 1.0:
            emission = emission_strength
        elif emission_strength == 0 or emission_strength == 0.0:
            emission = emission_strength
        else:
            emission = _tex_helper(props, luxcore_name + "emission_col", {
                "type": "scale",
                "texture1": emission_strength,
                "texture2": emission_color,
            })
        transparency = _socket(node.inputs["Alpha"], props, material, obj_name, group_node_stack)
        bump = _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack)

        if use_coating:
            # Wrap the base in a real dielectric coat layer (glossycoating):
            #   index = Coat IOR, ks = Coat Weight scales the coat Fresnel F0
            #   (LuxCore multiplies ks by ((ior-1)/(ior+1))^2, exactly the
            #   Cycles coat F0), uroughness/vroughness = Coat Roughness
            #   (Cycles squared -> LuxCore linear), bumptex = Coat Normal
            #   (the coating evaluates with its own bumped frame while the
            #   base keeps the regular Normal input).
            base_name = utils.sanitize_luxcore_name(luxcore_name + "_coatbase")
            base_definitions = dict(definitions)
            # Emission/transparency/normal live on the substrate — the
            # coating delegates passthrough transparency and (when it has no
            # emission itself) emitted radiance to the base material.
            base_definitions.update({
                "emission": emission,
                "transparency": transparency,
                "bumptex": bump,
            })
            props.Set(utils.luxutils.create_props(
                "scene.materials." + base_name + ".", base_definitions))

            coat_roughness = _squared_roughness_to_linear(
                node.inputs["Coat Roughness"], props, material,
                luxcore_name + "_coat", obj_name, group_node_stack)
            coat_ior = _socket(coat_ior_socket, props, material, obj_name,
                               group_node_stack) if coat_ior_socket is not None else 1.5
            definitions = {
                "type": "glossycoating",
                "base": base_name,
                "ks": coat_weight,
                "uroughness": coat_roughness,
                "vroughness": coat_roughness,
                "index": coat_ior,
                # Cycles' default MULTI_GGX coat = multibounce microfacet
                "multibounce": 1 if getattr(node, "distribution", "MULTI_GGX") == "MULTI_GGX" else 0,
            }
            if _socket_nondefault(coat_normal_socket, (0.0, 0.0, 0.0)):
                definitions["bumptex"] = _socket(
                    coat_normal_socket, props, material, obj_name, group_node_stack)
            if _socket_nondefault(coat_tint_socket, (1.0, 1.0, 1.0)):
                # Cycles Coat Tint is volumetric absorption inside the coat:
                # transmittance = pow(tint, weight / cosNT). LuxCore computes
                # exp(-ka * d * (1/cosi + 1/coso)), so ka = -ln(tint) and
                # d = weight/2 reproduce tint^weight at perpendicular
                # incidence (the angle Cycles normalizes to).
                coat_tint = _socket(coat_tint_socket, props, material, obj_name,
                                    group_node_stack)
                definitions["ka"] = _tex_binary(
                    "scale",
                    _v3_mathfunc("ln", coat_tint, luxcore_name + "_coatln", props),
                    -1.0, luxcore_name + "_coatka", props)
                definitions["d"] = _tex_binary(
                    "scale", coat_weight, 0.5, luxcore_name + "_coatd", props)
        else:
            definitions.update({
                "emission": emission,
                "transparency": transparency,
                "bumptex": bump,
            })
    elif node.bl_idname == "ShaderNodeMixShader":
        prefix = "scene.materials."

        def convert_mat_socket(index):
            mat_name = _socket(node.inputs[index], props, material, obj_name, group_node_stack)
            if mat_name == ERROR_VALUE:
                mat_name, mat_props = black()
                props.Set(mat_props)
            return mat_name

        fac_input = node.inputs["Fac"]
        amount = _socket(fac_input, props, material, obj_name, group_node_stack)
        if fac_input.is_linked and amount == ERROR_VALUE:
            amount = 0.5

        definitions = {
            "type": "mix",
            "material1": convert_mat_socket(1),
            "material2": convert_mat_socket(2),
            "amount": amount,
        }
    elif node.bl_idname == "ShaderNodeAddShader":
        prefix = "scene.materials."

        link1 = utils_node.get_link(node.inputs[0])
        link2 = utils_node.get_link(node.inputs[1])

        # An unlinked input counts as "adding nothing" -> pass the other side,
        # re-emitted under this node's name to keep the name invariant
        if link1 is None:
            return _socket(node.inputs[1], props, material, obj_name,
                           group_node_stack, luxcore_name)
        if link2 is None:
            return _socket(node.inputs[0], props, material, obj_name,
                           group_node_stack, luxcore_name)

        # Adding a Transparent BSDF is adding nothing -> pass the other side (exact)
        if link1.from_node.bl_idname == "ShaderNodeBsdfTransparent":
            return _socket(node.inputs[1], props, material, obj_name,
                           group_node_stack, luxcore_name)
        if link2.from_node.bl_idname == "ShaderNodeBsdfTransparent":
            return _socket(node.inputs[0], props, material, obj_name,
                           group_node_stack, luxcore_name)

        def emission_of(emission_node):
            # Recreate the emission texture the Emission branch below produces
            color = _socket(emission_node.inputs["Color"], props, material,
                            obj_name, group_node_stack)
            strength = _socket(emission_node.inputs["Strength"], props, material,
                               obj_name, group_node_stack)
            return _tex_helper(props, str(emission_node.as_pointer()) + "emission_col", {
                "type": "scale",
                "texture1": strength,
                "texture2": color,
            })

        is_emission1 = link1.from_node.bl_idname == "ShaderNodeEmission"
        is_emission2 = link2.from_node.bl_idname == "ShaderNodeEmission"

        if is_emission1 and is_emission2:
            # Exact: a non-scattering material emitting the sum of both emissions
            emission = _tex_binary("add", emission_of(link1.from_node),
                                   emission_of(link2.from_node),
                                   luxcore_name + "emission_add", props)
            definitions = {
                "type": "matte",
                "kd": [0, 0, 0],
                "emission": emission,
                "emission.gain": [1] * 3,
                "emission.power": 0,
                "emission.efficency": 0,
            }
        elif is_emission1 or is_emission2:
            # LuxCore has no additive material type; exact approach for the common
            # "Emission + surface shader" case: attach the emission to the other
            # material's emission slot
            emission_link = link1 if is_emission1 else link2
            base_socket = node.inputs[1] if is_emission1 else node.inputs[0]
            base_name = _socket(base_socket, props, material, obj_name,
                                group_node_stack, luxcore_name)
            if base_name == ERROR_VALUE or not isinstance(base_name, str):
                base_name, mat_props = black(luxcore_name)
                props.Set(mat_props)

            emission_key = "scene.materials." + base_name + ".emission"
            try:
                already_has_emission = props.IsDefined(emission_key)
            except AttributeError:
                try:
                    already_has_emission = emission_key in props.GetAllNames()
                except AttributeError:
                    already_has_emission = False

            if already_has_emission:
                _warn_unsupported(
                    node, "adding emission to a material that already emits is not "
                    "supported; using a 50/50 mix instead", None, obj_name)
            else:
                props.Set(utils.luxutils.create_props("scene.materials." + base_name + ".", {
                    "emission": emission_of(emission_link.from_node),
                    "emission.gain": [1] * 3,
                    "emission.power": 0,
                    "emission.efficency": 0,
                }))
                return base_name

        if not is_emission1 and not is_emission2:
            # Approximation: LuxCore materials cannot be added; a 50/50 mix halves
            # the combined energy of both closures but keeps them visible
            _warn_unsupported(
                node, "LuxCore materials cannot be added; approximated by a 50/50 "
                "mix (energy is halved)", None, obj_name)

        if (is_emission1 != is_emission2 and already_has_emission) or \
                (not is_emission1 and not is_emission2):
            def add_mat_socket(index):
                mat_name = _socket(node.inputs[index], props, material, obj_name,
                                   group_node_stack)
                if mat_name == ERROR_VALUE or not isinstance(mat_name, str):
                    mat_name, mat_props = black()
                    props.Set(mat_props)
                return mat_name

            definitions = {
                "type": "mix",
                "material1": add_mat_socket(0),
                "material2": add_mat_socket(1),
                "amount": 0.5,
            }
    elif node.bl_idname == "ShaderNodeBsdfDiffuse":
        prefix = "scene.materials."
        # TODO roughmatte and roughness -> sigma conversion (if possible)
        definitions = {
            "type": "matte",
            "kd": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeBsdfGlossy":
        prefix = "scene.materials."

        # Implicitly create a fresnelcolor texture with unique name
        tex_name = luxcore_name + "fresnel_helper"
        helper_prefix = "scene.textures." + tex_name + "."
        helper_defs = {
            "type": "fresnelcolor",
            "kr": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
        }
        props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

        roughness = _squared_roughness_to_linear(node.inputs["Roughness"], props, material,
                                                 luxcore_name, obj_name, group_node_stack)

        definitions = {
            "type": "metal2",
            "fresnel": tex_name,
            "uroughness": roughness,
            "vroughness": roughness,
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeTexImage":
        if node.image:
            prefix = "scene.textures."
            extension_map = {
                "REPEAT": "repeat",
                "EXTEND": "clamp",
                "CLIP": "black",
            }

            try:
                filepath = ImageExporter.export_cycles_node_reader(node.image)
            except OSError as error:
                LuxCoreErrorLog.add_warning(error, obj_name=obj_name)
                return MISSING_IMAGE_COLOR

            definitions = {
                "type": "imagemap",
                # TODO image sequences
                "file": filepath,
                "wrap": extension_map[node.extension],
                "channel": "alpha" if output_socket == node.outputs["Alpha"] else "rgb",
                # Crude approximation, not sure if we can do better
                "gamma": 2.2 if node.image.colorspace_settings.name == "sRGB" else 1,
                "gain": 1,

                "mapping.type": "uvmapping2d",
                "mapping.uvscale": [1, -1],
                "mapping.rotation": 0,
                "mapping.uvdelta": [0, 1],
            }

            # A linked Vector input (e.g. a Mapping node) overrides the default
            # UV flip mapping
            vector_input = node.inputs.get("Vector")
            if vector_input is not None:
                definitions.update(_vector_mapping_defs(
                    vector_input, True, True, props, material, obj_name,
                    group_node_stack))
        else:
            return MISSING_IMAGE_COLOR
    elif node.bl_idname == "ShaderNodeBsdfGlass":
        prefix = "scene.materials."
        color = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)
        roughness = _squared_roughness_to_linear(node.inputs["Roughness"], props, material,
                                                 luxcore_name, obj_name, group_node_stack)

        definitions = {
            "type": "glass" if roughness == 0 else "roughglass",
            "kt": color,
            "kr": color, # Nonsense, maybe leave white even if it breaks compatibility with Cycles?
            "interiorior": _socket(node.inputs["IOR"], props, material, obj_name, group_node_stack),
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }

        if roughness != 0:
            definitions["uroughness"] = roughness
            definitions["vroughness"] = roughness
    elif node.bl_idname == "ShaderNodeBsdfRefraction":
        prefix = "scene.materials."
        color = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)
        roughness = _squared_roughness_to_linear(node.inputs["Roughness"], props, material,
                                                 luxcore_name, obj_name, group_node_stack)

        definitions = {
            "type": "glass" if roughness == 0 else "roughglass",
            "kt": color,
            "kr": [0, 0, 0],
            "interiorior": _socket(node.inputs["IOR"], props, material, obj_name, group_node_stack),
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }

        if roughness != 0:
            definitions["uroughness"] = roughness
            definitions["vroughness"] = roughness
    elif node.bl_idname == "ShaderNodeBsdfAnisotropic":
        prefix = "scene.materials."

        # Implicitly create a fresnelcolor texture with unique name
        tex_name = luxcore_name + "fresnel_helper"
        helper_prefix = "scene.textures." + tex_name + "."
        helper_defs = {
            "type": "fresnelcolor",
            "kr": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
        }
        props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

        # TODO emulate actual anisotropy and rotation somehow ...
        roughness = _squared_roughness_to_linear(node.inputs["Roughness"], props, material,
                                                 luxcore_name, obj_name, group_node_stack)

        definitions = {
            "type": "metal2",
            "fresnel": tex_name,
            "uroughness": roughness,
            "vroughness": 0.05,
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeBsdfMetallic":
        prefix = "scene.materials."

        ior_socket = node.inputs.get("IOR")
        extinction_socket = node.inputs.get("Extinction")
        physical_ior = ior_socket is not None and getattr(ior_socket, "enabled", True)

        roughness = _socket(node.inputs["Roughness"], props, material,
                            obj_name, group_node_stack)
        anisotropy = _socket(node.inputs["Anisotropy"], props, material,
                             obj_name, group_node_stack)

        # vroughness shrinks with anisotropy (directional streaks); a scalar
        # anisotropy can't compose with a textured roughness — warn then
        if isinstance(anisotropy, str):
            LuxCoreErrorLog.add_warning(
                f'Metallic node "{node.name}": textured anisotropy is not '
                "supported, isotropic roughness is used", obj_name=obj_name)
            vroughness = roughness
        elif isinstance(roughness, str):
            LuxCoreErrorLog.add_warning(
                f'Metallic node "{node.name}": anisotropy with a textured '
                "roughness is approximated", obj_name=obj_name)
            vroughness = roughness
        else:
            vroughness = max(1e-4, roughness * (1.0 - anisotropy))

        if getattr(node, "fresnel_type", "F82") == "F82" and \
                node.inputs.get("Edge Tint") is not None and \
                (node.inputs["Edge Tint"].is_linked or
                 list(node.inputs["Edge Tint"].default_value)[:3] != [0, 0, 0]):
            LuxCoreErrorLog.add_warning(
                f'Metallic node "{node.name}": Edge Tint (F82) is not '
                "supported by the conductor model", obj_name=obj_name)

        definitions = {"type": "metal2"}

        if physical_ior:
            # PHYSICAL mode: explicit complex IOR (n) + extinction (k)
            definitions["n"] = _socket(ior_socket, props, material, obj_name,
                                       group_node_stack)
            definitions["k"] = _socket(extinction_socket, props, material,
                                       obj_name, group_node_stack)
        else:
            # F82 mode (default): derive n,k from the Base Color reflectance
            base_color = _socket(node.inputs["Base Color"], props, material,
                                 obj_name, group_node_stack)
            n_tex = luxcore_name + "approxn"
            k_tex = luxcore_name + "approxk"
            props.Set(utils.luxutils.create_props(
                "scene.textures." + n_tex + ".",
                {"type": "fresnelapproxn", "texture": base_color}))
            props.Set(utils.luxutils.create_props(
                "scene.textures." + k_tex + ".",
                {"type": "fresnelapproxk", "texture": base_color}))
            definitions["n"] = n_tex
            definitions["k"] = k_tex

        definitions["uroughness"] = roughness
        definitions["vroughness"] = vroughness
        if node.inputs.get("Rotation") is not None and \
                (node.inputs["Rotation"].is_linked or
                 node.inputs["Rotation"].default_value != 0.0):
            LuxCoreErrorLog.add_warning(
                f'Metallic node "{node.name}": anisotropy rotation is not '
                "supported", obj_name=obj_name)
        if node.inputs.get("Normal") is not None:
            definitions["bumptex"] = _socket(node.inputs["Normal"], props,
                                             material, obj_name, group_node_stack)
        if node.inputs.get("Thin Film Thickness") is not None and \
                (node.inputs["Thin Film Thickness"].is_linked or
                 node.inputs["Thin Film Thickness"].default_value != 0.0):
            LuxCoreErrorLog.add_warning(
                f'Metallic node "{node.name}": thin film on conductors is not '
                "supported by metal2", obj_name=obj_name)
    elif node.bl_idname == "ShaderNodeBsdfHairPrincipled":
        prefix = "scene.materials."

        # Cycles' Principled Hair maps onto LuxCore's Marschner "hairmat".
        def _sock(name, fallback):
            s = node.inputs.get(name)
            return _socket(s, props, material, obj_name, group_node_stack) \
                if s is not None else fallback

        # Cycles' Offset is radians; LuxCore's alpha is degrees.
        offset_sock = node.inputs.get("Offset")
        offset = _socket(offset_sock, props, material, obj_name, group_node_stack) \
            if offset_sock is not None else 0.0
        if offset_sock is not None and offset_sock.is_linked and offset != ERROR_VALUE:
            alpha = luxcore_name + "offset_to_deg"
            props.Set(utils.luxutils.create_props("scene.textures." + alpha + ".", {
                "type": "scale",
                "texture1": offset,
                "texture2": 57.29577951308232,
            }))
        else:
            alpha = offset * 57.29577951308232

        definitions = {
            "type": "hairmat",
            "eta": _sock("IOR", 1.55),
            # Roughness/Radial Roughness -> beta_m/beta_n. Both are 0..1 and
            # drive the same longitudinal/azimuthal roughness axes; the exact
            # parameterizations differ so this is a first-order match.
            "beta_m": _sock("Roughness", 0.3),
            "beta_n": _sock("Radial Roughness", 0.3),
            "alpha": alpha,
        }
        # Color parameterization is mutually exclusive in both engines.
        if node.parametrization == "ABSORPTION":
            definitions["sigma_a"] = _sock("Absorption Coefficient", [0.0, 0.0, 0.0])
        elif node.parametrization == "COLOR":
            definitions["color"] = _sock("Color", [0.5, 0.5, 0.5])
        else:  # "MELANIN" - eumelanin/pheomelanin concentration model
            mel_sock = node.inputs.get("Melanin")
            red_sock = node.inputs.get("Melanin Redness")
            mel_linked = mel_sock is not None and mel_sock.is_linked
            red_linked = red_sock is not None and red_sock.is_linked
            if not mel_linked and not red_linked:
                melanin = mel_sock.default_value if mel_sock is not None else 0.8
                redness = red_sock.default_value if red_sock is not None else 0.0
                # Cycles: melanin_qty = -ln(1 - Melanin); the concentration is
                # split into eumelanin/pheomelanin by the redness fraction.
                qty = -log(max(1.0 - melanin, 0.0001))
                definitions["eumelanin"] = qty * (1.0 - redness)
                definitions["pheomelanin"] = qty * redness
            else:
                LuxCoreErrorLog.add_warning(
                    'Principled Hair node "%s": textured Melanin inputs are '
                    "approximated by constant melanin concentrations" % node.name,
                    obj_name=obj_name)
                definitions["eumelanin"] = 1.3
                definitions["pheomelanin"] = 0.0
    elif node.bl_idname == "ShaderNodeBsdfTranslucent":
        prefix = "scene.materials."
        definitions = {
            "type": "mattetranslucent",
            # TODO kt and kr don't really match Cycles result yet
            "kt": [1, 1, 1],
            "kr": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "bumptex": _socket(node.inputs["Normal"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeBsdfTransparent":
        prefix = "scene.materials."
        definitions = {
            "type": "null",
        }
        color = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)
        if color != 1 and color != [1, 1, 1]:
            definitions["transparency"] = color
    elif node.bl_idname == "ShaderNodeHoldout":
        prefix = "scene.materials."
        definitions = {
            "type": "matte",
            "kd": [0, 0, 0],
            "holdout.enable": True,
        }
    elif node.bl_idname == "ShaderNodeMixRGB":
        prefix = "scene.textures."

        fac_input = node.inputs["Fac"]
        fac = _socket(fac_input, props, material, obj_name, group_node_stack)
        if fac_input.is_linked and fac == ERROR_VALUE:
            fac = 0.5

        tex1 = _socket(node.inputs["Color1"], props, material, obj_name, group_node_stack)
        tex2 = _socket(node.inputs["Color2"], props, material, obj_name, group_node_stack)

        definitions, luxcore_name, early = _blend_rgb(
            node, node.blend_type, fac, tex1, tex2, luxcore_name, props, obj_name)
        if early is not None:
            return early
    elif node.bl_idname == "ShaderNodeMath":
        # Trig/exp/log ops are backed by LuxCore's native "mathfunc"
        # texture (requires a pyluxcore build with MATHFUNC_TEX).

        prefix = "scene.textures."
        definitions = {}

        tex1 = _socket(node.inputs[0], props, material, obj_name, group_node_stack)
        tex2 = _socket(node.inputs[1], props, material, obj_name, group_node_stack)

        # In Cycles, the inputs are converted to float values (e.g. averaged in case of RGB input).
        # The following LuxCore textures would perform RGB operations if we didn't convert the inputs to floats.
        if node.operation in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "ABSOLUTE"}:
            tex1 = _convert_to_float(tex1, props)
            tex2 = _convert_to_float(tex2, props)

        if node.operation in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "GREATER_THAN", "LESS_THAN"}:
            try:
                definitions["type"] = math_operation_map[node.operation]
            except KeyError:
                definitions["type"] = node.operation.lower()
            definitions["texture1"] = tex1
            definitions["texture2"] = tex2
        elif node.operation == "POWER":
            definitions["type"] = "power"
            definitions["base"] = tex1
            definitions["exponent"] = tex2
        elif node.operation == "ABSOLUTE":
            definitions["type"] = "abs"
            definitions["texture"] = tex1
        elif node.operation == "ROUND":
            definitions["type"] = "rounding"
            definitions["texture"] = tex1
            definitions["increment"] = 1
        elif node.operation == "MODULO":
            definitions["type"] = "modulo"
            definitions["texture"] = tex1
            definitions["modulo"] = tex2
        elif node.operation == "SQRT":
            return _tex_binary("power", tex1, 0.5, luxcore_name + "_sqrt",
                               props)
        elif node.operation == "EXPONENT":
            return _tex_mathfunc("exp", tex1, None, luxcore_name, props)
        elif node.operation in {"MINIMUM", "MAXIMUM"}:
            lt = _tex_lessthan(tex1, tex2, luxcore_name + "_lt", props)
            if node.operation == "MINIMUM":
                diff = _tex_binary("subtract", tex1, tex2,
                                   luxcore_name + "_df", props)
                sel = _tex_binary("scale", diff, lt,
                                  luxcore_name + "_sl", props)
                return _tex_binary("add", tex2, sel,
                                   luxcore_name + "_min", props)
            else:
                diff = _tex_binary("subtract", tex2, tex1,
                                   luxcore_name + "_df", props)
                sel = _tex_binary("scale", diff, lt,
                                  luxcore_name + "_sl", props)
                return _tex_binary("add", tex1, sel,
                                   luxcore_name + "_max", props)
        elif node.operation == "FLOOR":
            # floor(x) = round_nearest(x - 0.5); differs from floor only at
            # exact half-integers, where both agree anyway
            shifted = _tex_binary("subtract", tex1, 0.5,
                                  luxcore_name + "_sh", props)
            return _tex_unary("rounding", shifted, 1.0,
                              luxcore_name + "_floor", props)
        elif node.operation == "CEIL":
            # ceil(x) = -floor(-x)
            neg = _tex_binary("scale", tex1, -1.0, luxcore_name + "_neg",
                              props)
            shifted = _tex_binary("subtract", neg, 0.5,
                                  luxcore_name + "_sh", props)
            rounded = _tex_unary("rounding", shifted, 1.0,
                                 luxcore_name + "_r", props)
            return _tex_binary("scale", rounded, -1.0,
                               luxcore_name + "_ceil", props)
        elif node.operation == "TRUNC":
            # trunc(x) = sign(x) * floor(|x|)
            lt0 = _tex_lessthan(tex1, 0.0, luxcore_name + "_lt0", props)
            sgn = _tex_binary("subtract",
                              _tex_binary("scale", lt0, 2.0,
                                          luxcore_name + "_lt2", props),
                              1.0, luxcore_name + "_sgn", props)
            absv = _tex_unary("abs", tex1, None, luxcore_name + "_abs", props)
            shifted = _tex_binary("subtract", absv, 0.5,
                                  luxcore_name + "_sh", props)
            fl = _tex_unary("rounding", shifted, 1.0,
                            luxcore_name + "_fl", props)
            return _tex_binary("scale", fl, sgn, luxcore_name + "_tr", props)
        elif node.operation == "FRACT":
            # fract(x) = x - floor(x)
            shifted = _tex_binary("subtract", tex1, 0.5,
                                  luxcore_name + "_sh", props)
            fl = _tex_unary("rounding", shifted, 1.0,
                            luxcore_name + "_fl", props)
            return _tex_binary("subtract", tex1, fl,
                               luxcore_name + "_fract", props)
        elif node.operation == "RADIANS":
            return _tex_binary("scale", tex1, 0.017453292519943295,
                               luxcore_name + "_rad", props)
        elif node.operation == "DEGREES":
            return _tex_binary("scale", tex1, 57.29577951308232,
                               luxcore_name + "_deg", props)
        elif node.operation == "COMPARE":
            # compare(a, b, eps) = 1 if |a-b| <= eps else 0;
            # = gt(eps, |a-b|) using lessthan swapped
            tex3 = _socket(node.inputs[2], props, material, obj_name,
                           group_node_stack)
            diff = _tex_binary("subtract", tex1, tex2,
                               luxcore_name + "_df", props)
            absd = _tex_unary("abs", diff, None, luxcore_name + "_ad", props)
            return _tex_lessthan(absd, tex3, luxcore_name + "_cmp", props)
        elif node.operation == "PINGPONG":
            # pingpong(x, s) = s - |mod(x, 2s) - s|
            two_s = _tex_binary("scale", tex2, 2.0, luxcore_name + "_2s",
                                props)
            mod = _tex_unary("modulo", tex1, two_s,
                             luxcore_name + "_mod", props)
            dev = _tex_unary("abs",
                             _tex_binary("subtract", mod, tex2,
                                         luxcore_name + "_sub", props),
                             None, luxcore_name + "_dev", props)
            return _tex_binary("subtract", tex2, dev,
                               luxcore_name + "_pp", props)
        elif node.operation in _MATHFUNC_UNARY_OPS:
            return _tex_mathfunc(_MATHFUNC_UNARY_OPS[node.operation],
                                 tex1, None, luxcore_name, props)
        elif node.operation in _MATHFUNC_BINARY_OPS:
            return _tex_mathfunc(_MATHFUNC_BINARY_OPS[node.operation],
                                 tex1, tex2, luxcore_name, props)
        elif node.operation in {"SMOOTH_MIN", "SMOOTH_MAX"}:
            # Polynomial smooth-min/max: h = clamp(0.5 + 0.5*(b-a)/k, 0, 1);
            # smin = mix(b, a, h) - k*h*(1-h), smax = -smin(-a, -b).
            # Third input is the smoothing distance k.
            tex3 = _socket(node.inputs[2], props, material, obj_name,
                           group_node_stack)
            if node.operation == "SMOOTH_MAX":
                na = _tex_binary("scale", tex1, -1.0,
                                 luxcore_name + "_na", props)
                nb = _tex_binary("scale", tex2, -1.0,
                                 luxcore_name + "_nb", props)
                smin = _smooth_min(na, nb, tex3, luxcore_name + "_sm",
                                   props)
                return _tex_binary("scale", smin, -1.0,
                                   luxcore_name + "_smax", props)
            return _smooth_min(tex1, tex2, tex3, luxcore_name + "_smin",
                               props)
        elif node.operation == "LOGARITHM":
            # log_b(x) = ln(x) / ln(b); Cycles' second input is the base
            num = _tex_mathfunc("ln", tex1, None, luxcore_name + "_num",
                                props)
            den = _tex_mathfunc("ln", tex2, None, luxcore_name + "_den",
                                props)
            return _tex_binary("divide", num, den, luxcore_name + "_log",
                               props)
        elif node.operation == "SIGN":
            lt0 = _tex_lessthan(tex1, 0.0, luxcore_name + "_lt0", props)
            gt0 = _tex_greaterthan(tex1, 0.0, luxcore_name + "_gt0", props)
            return _tex_binary("subtract", gt0, lt0,
                               luxcore_name + "_sign", props)
        elif node.operation == "MULTIPLY_ADD":
            tex3 = _socket(node.inputs[2], props, material, obj_name,
                           group_node_stack)
            prod = _tex_binary("scale", tex1, tex2, luxcore_name + "_mp",
                               props)
            return _tex_binary("add", prod, tex3, luxcore_name + "_ma",
                               props)
        elif node.operation == "WRAP":
            # wrap(x, min, max) = min + mod(x - min, max - min)
            rng = _tex_binary("subtract",
                              _socket(node.inputs[2], props, material,
                                      obj_name, group_node_stack),
                              tex2, luxcore_name + "_rng", props)
            shifted = _tex_binary("subtract", tex1, tex2,
                                  luxcore_name + "_sh", props)
            mod = _tex_unary("modulo", shifted, rng,
                             luxcore_name + "_mod", props)
            return _tex_binary("add", tex2, mod, luxcore_name + "_wr", props)
        elif node.operation == "SNAP":
            # snap(x, s) = round(x / s) * s — nearest multiple like
            # VectorMath SNAP (Blender floors; documented difference)
            _warn_unsupported(
                node, "'Snap' approximated by round-to-nearest-multiple "
                "(Blender floors to the increment)", None, obj_name)
            definitions = {
                "type": "rounding",
                "texture": tex1,
                "increment": tex2,
            }
        else:
            # Never silently black: pass through the first input
            return _warn_unsupported(
                node, f"unsupported math operation '{node.operation}', passing through "
                "the first input", tex1, obj_name)
    elif node.bl_idname == "ShaderNodeHueSaturation":
        prefix = "scene.textures."

        hue = _socket(node.inputs["Hue"], props, material, obj_name, group_node_stack)
        saturation = _socket(node.inputs["Saturation"], props, material, obj_name, group_node_stack)
        value = _socket(node.inputs["Value"], props, material, obj_name, group_node_stack)
        fac = _socket(node.inputs["Fac"], props, material, obj_name, group_node_stack)  # TODO
        color = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)

        definitions = {
            "type": "hsv",
            "texture": color,
            "hue": hue,
            "saturation": saturation,
            "value": value,
        }
    elif node.bl_idname == "ShaderNodeGroup":
        active_output = None
        for subnode in node.node_tree.nodes:
            if subnode.bl_idname == "NodeGroupOutput" and subnode.is_active_output:
                active_output = subnode
                break

        current_input = active_output.inputs[output_socket.name]
        if not current_input.is_linked:
            return ERROR_VALUE

        link = utils_node.get_link(current_input)
        
        if group_node_stack is None:
            _group_node_stack = []
        else:
            _group_node_stack = group_node_stack.copy()
        
        _group_node_stack.append(node)
        
        # I call _node instead of _socket here because I need to pass the
        # luxcore_name in case the node group is the first node in the tree
        return _node(link.from_node, link.from_socket, props, material, luxcore_name, obj_name, _group_node_stack)
    elif node.bl_idname == "NodeGroupInput":
        return _socket(group_node_stack[-1].inputs[output_socket.name], props,
                       material, obj_name, group_node_stack[:-1], luxcore_name)
    elif node.bl_idname == "ShaderNodeEmission":
        prefix = "scene.materials."

        color = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)
        # According to the Blender manual, strength is in Watts/m² when the node is used on meshes.
        strength = _socket(node.inputs["Strength"], props, material, obj_name, group_node_stack)

        emission_col = luxcore_name + "emission_col"
        helper_prefix = "scene.textures." + emission_col + "."
        helper_defs = {
            "type": "scale",
            "texture1": strength,
            "texture2": color,
        }
        props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))

        definitions = {
            "type": "matte",
            "kd": [0, 0, 0],
            "emission": emission_col,
            "emission.gain": [1] * 3,
            "emission.power": 0,
            "emission.efficency": 0,
        }
    elif node.bl_idname == "ShaderNodeValue":
        prefix = "scene.textures."

        definitions = {
            "type": "constfloat1",
            "value": node.outputs[0].default_value,
        }
    elif node.bl_idname == "ShaderNodeRGB":
        prefix = "scene.textures."

        definitions = {
            "type": "constfloat3",
            "value": list(node.outputs[0].default_value)[:3],
        }
    elif node.bl_idname == "ShaderNodeValToRGB":
        # Color ramp
        prefix = "scene.textures."
        ramp = node.color_ramp

        if ramp.interpolation == "CONSTANT":
            interpolation = "none"
        elif ramp.interpolation == "LINEAR":
            interpolation = "linear"
        else:
            # TODO: not all interpolation modes are supported by LuxCore
            interpolation = "cubic"

        definitions = {
            "type": "band",
            "amount": _socket(node.inputs["Fac"], props, material, obj_name, group_node_stack),
            "offsets": len(ramp.elements),
            "interpolation": interpolation,
        }

        for i in range(len(ramp.elements)):
            definitions[f"offset{i}"] = ramp.elements[i].position
            definitions[f"value{i}"] = list(ramp.elements[i].color[:3])  # Ignore alpha
    elif node.bl_idname == "ShaderNodeTexChecker":
        prefix = "scene.textures."

        # Note: Only "Object" texture coordinates are supported. Textured scale is not supported.
        scale = Matrix()
        for i in range(3):
            scale[i][i] = node.inputs["Scale"].default_value

        # Compose a linked Mapping node transform (applied before the scale)
        vector_link = utils_node.get_link(node.inputs["Vector"])
        if vector_link is not None and \
                vector_link.from_node.bl_idname == "ShaderNodeMapping":
            scale = scale @ _mapping_matrix(*_mapping_node_values(
                vector_link.from_node, obj_name))

        definitions = {
            "type": "checkerboard3d",
            "texture1": _socket(node.inputs["Color2"], props, material, obj_name, group_node_stack),
            "texture2": _socket(node.inputs["Color1"], props, material, obj_name, group_node_stack),
            "mapping.type": "localmapping3d",
            "mapping.transformation": utils.luxutils.matrix_to_list(scale),
        }
    elif node.bl_idname == "ShaderNodeInvert":
        prefix = "scene.textures."

        fac_input = node.inputs["Fac"]
        fac = _socket(fac_input, props, material, obj_name, group_node_stack)
        if fac_input.is_linked and fac == ERROR_VALUE:
            fac = 1

        tex = _socket(node.inputs["Color"], props, material, obj_name, group_node_stack)

        if fac == 0:
            return tex

        definitions = {
            "type": "subtract",
            "texture1": 1,
            "texture2": tex,
        }

        if _is_textured(fac) or (fac > 0 and fac < 1):
            # Here we need to insert a helper texture *after* the current texture
            props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
            definitions = {
                "type": "mix",
                "texture1": tex,
                "texture2": luxcore_name,
                "amount": fac,
            }
            luxcore_name = luxcore_name + "fac"
    elif node.bl_idname in {"ShaderNodeSeparateRGB", "ShaderNodeSeparateXYZ",
                            "ShaderNodeSeparateColor"}:
        prefix = "scene.textures."

        if node.bl_idname == "ShaderNodeSeparateColor":
            # Blender 5.x renamed Separate RGB; only the RGB mode maps to channels
            if getattr(node, "mode", "RGB") != "RGB":
                return _warn_unsupported(
                    node, f'Separate Color mode "{node.mode}" is not supported '
                    "(only RGB channels can be split)", FALLBACK_FLOAT, obj_name)
            channels = ["Red", "Green", "Blue"]
            tex_socket_name = "Color"
        elif node.bl_idname == "ShaderNodeSeparateRGB":
            channels = ["R", "G", "B"]
            tex_socket_name = "Image"
        else:
            channels = ["X", "Y", "Z"]
            tex_socket_name = "Vector"

        definitions = {
            "type": "splitfloat3",
            "texture": _socket(node.inputs[tex_socket_name], props, material, obj_name, group_node_stack),
            "channel": channels.index(output_socket.name),
        }
    elif node.bl_idname in {"ShaderNodeCombineRGB", "ShaderNodeCombineXYZ",
                            "ShaderNodeCombineColor"}:
        # Blender 5.x renamed Combine RGB; only the RGB mode maps to channels
        if node.bl_idname == "ShaderNodeCombineColor" \
                and getattr(node, "mode", "RGB") != "RGB":
            return _warn_unsupported(
                node, f'Combine Color mode "{node.mode}" is not supported '
                "(only RGB channels can be combined)", FALLBACK_COLOR, obj_name)

        prefix = "scene.textures."

        definitions = {
            "type": "makefloat3",
            "texture1": _socket(node.inputs[0], props, material, obj_name, group_node_stack),
            "texture2": _socket(node.inputs[1], props, material, obj_name, group_node_stack),
            "texture3": _socket(node.inputs[2], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeRGBToBW":
        prefix = "scene.textures."

        definitions = {
            "type": "dotproduct",
            "texture1": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            # From Cycles source code:
            # intern/cycles/render/shader.cpp:726: float ShaderManager::linear_rgb_to_gray(float3 c)
            "texture2": [0.2126729, 0.7151522, 0.0721750],
        }
    elif node.bl_idname == "ShaderNodeBrightContrast":
        prefix = "scene.textures."

        definitions = {
            "type": "brightcontrast",
            "texture": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "brightness": _socket(node.inputs["Bright"], props, material, obj_name, group_node_stack),
            "contrast": _socket(node.inputs["Contrast"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeGamma":
        #print(f"ShaderNodeGamma inputs: {[input.name for input in node.inputs]}")

        prefix = "scene.textures."

        # Check if the Gamma node input is a Texture Image node
        texture_input = utils_node.get_link(node.inputs["Color"])
        if texture_input and texture_input.from_node.bl_idname == "ShaderNodeTexImage":
            tex_node = texture_input.from_node
        
            # Extract the image filepath and gamma value
            gamma_value = _socket(node.inputs["Gamma"], props, material, obj_name, group_node_stack)

            if not gamma_value:
                gamma_value = 1  # Default to 1 if no value is provided
        
            filepath = ImageExporter.export_cycles_node_reader(tex_node.image)
            extension_map = {
                "REPEAT": "repeat",
                "EXTEND": "clamp",
                "CLIP": "black",
            }
        
            definitions = {
                "type": "imagemap",
                "file": filepath,
                "wrap": extension_map.get(tex_node.extension, "repeat"),
                "gamma": gamma_value,
                "gain": 1,  # Adjust as necessary
                "mapping.type": "uvmapping2d",
                "mapping.uvscale": [1, -1],
                "mapping.rotation": 0,
                "mapping.uvdelta": [0, 1],
            }
        
            # Define the LuxCore texture node
            props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
            return luxcore_name
        else:
            # Only image textures carry their own gamma; anything else
            # falls through with stale definitions (or UnboundLocalError).
            LuxCoreErrorLog.add_warning(
                "Gamma node without image input is not supported", obj_name=obj_name)
            return ERROR_VALUE

    elif node.bl_idname == "ShaderNodeNormalMap":
        if node.space != "TANGENT":
            LuxCoreErrorLog.add_warning(f"Unsupported normal map space: {node.space}", obj_name=obj_name)
            return ERROR_VALUE

        prefix = "scene.textures."

        definitions = {
            "type": "normalmap",
            "texture": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
        }

        strength_socket = node.inputs["Strength"]
        if strength_socket.is_linked:
            # Use scale texture because normalmap scale can't be textured
            # Here we need to insert a helper texture *after* the current texture
            props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
            definitions = {
                "type": "scale",
                "texture1": luxcore_name,
                "texture2": _socket(strength_socket, props, material, obj_name, group_node_stack),
            }
            luxcore_name = luxcore_name + "strength"
        else:
            definitions["scale"] = strength_socket.default_value
    elif node.bl_idname == "ShaderNodeBump":
        if node.inputs["Distance"].is_linked:
            LuxCoreErrorLog.add_warning("Bump node Distance socket is not supported", obj_name=obj_name)
        if node.inputs["Normal"].is_linked:
            LuxCoreErrorLog.add_warning("Bump node Normal socket is not supported", obj_name=obj_name)

        prefix = "scene.textures."

        definitions = {
            "type": "scale",
            "texture1": _socket(node.inputs["Height"], props, material, obj_name, group_node_stack),
            "texture2": _socket(node.inputs["Strength"], props, material, obj_name, group_node_stack),
        }

        if node.invert:
            props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
            definitions = {
                "type": "scale",
                "texture1": luxcore_name,
                "texture2": -1,
            }
            luxcore_name = luxcore_name + "invert"
    elif node.bl_idname == "ShaderNodeNewGeometry":
        prefix = "scene.textures."
        definitions = {}
        
        # TODO: when support for pointiness and random per island is added, we have to:
        #  - make sure the necessary shapes are added during object export
        #  - make sure the mesh is re-exported when one of these outputs is used the first time during viewport render, 
        #    otherwise we crash LuxCore in case of random per island, or the feature doesn't work in case of pointiness
        if output_socket.name == "Position":
            definitions["type"] = "position"
        elif output_socket.name == "Normal":
            definitions["type"] = "shadingnormal"
        else:
            LuxCoreErrorLog.add_warning(f"Unsupported Geometry output socket: {output_socket.name}", obj_name=obj_name)
            return ERROR_VALUE
    elif node.bl_idname == "ShaderNodeObjectInfo":
        prefix = "scene.textures."
        definitions = {}
        
        if output_socket.name == "Object Index":
            definitions["type"] = "objectid"
        elif output_socket.name == "Material Index":
            definitions["type"] = "constfloat1"
            definitions["value"] = material.pass_index
        elif output_socket.name == "Random":
            definitions["type"] = "objectidnormalized"
        else:
            LuxCoreErrorLog.add_warning(f"Unsupported Object Info output socket: {output_socket.name}", obj_name=obj_name)
            return ERROR_VALUE
    elif node.bl_idname == "ShaderNodeHairInfo":
        prefix = "scene.textures."
        definitions = {}

        if output_socket.name == "Intercept":
            # Normalized position along the strand (0 = root, 1 = tip), written
            # by the strands tessellation into vertex AOV layer
            # HAIR_STRAND_U_DATA_INDEX (see slg/shapes/strands.h).
            definitions["type"] = "hitpointvertexaov"
            definitions["dataindex"] = 7
        elif output_socket.name == "Random":
            # Deterministic per-strand random in [0,1), written by the strands
            # tessellation into vertex AOV layer HAIR_STRAND_RANDOM_DATA_INDEX.
            definitions["type"] = "hitpointvertexaov"
            definitions["dataindex"] = 0
        elif output_socket.name == "Is Strand":
            # HairInfo is only meaningful on strand geometry, where every
            # shaded point is a strand.
            definitions["type"] = "constfloat1"
            definitions["value"] = 1.0
        else:
            LuxCoreErrorLog.add_warning(
                f"Unsupported Hair Info output socket: {output_socket.name}",
                obj_name=obj_name)
            return ERROR_VALUE
    elif node.bl_idname == "ShaderNodeParticleInfo":
        prefix = "scene.textures."
        definitions = {}

        # Particle instances are exported as duplicated objects, each carrying
        # its Blender random_id as the LuxCore object id (see object_cache.py).
        # hitPoint.objectID is therefore unique per particle.
        if output_socket.name == "Index":
            # Unique id per particle instance (deterministic, not sequential).
            definitions["type"] = "objectid"
        elif output_socket.name == "Random":
            # Per-particle random in [0, 1) derived from the instance id.
            definitions["type"] = "objectidnormalized"
        else:
            # Age/Lifetime/Location/Size/Velocity/Angular Velocity require
            # particle simulation state that is not exported to LuxCore.
            LuxCoreErrorLog.add_warning(
                f"Unsupported Particle Info output socket: {output_socket.name}",
                obj_name=obj_name)
            return ERROR_VALUE
    elif node.bl_idname == "ShaderNodeVolumeInfo":
        # Reads one of the object's OpenVDB grids (density / color / flame /
        # temperature) as a world-space densitygrid texture — the same
        # sampling the auto-built heterogeneous volume uses.
        from . import volume  # lazy: volume->object_cache->cycles_node_reader cycle
        definitions = volume.volume_info_grid_defs(node, output_socket.name, obj_name)
        if definitions is None:
            return ERROR_VALUE
        prefix = "scene.textures."
    elif node.bl_idname == "ShaderNodeBlackbody":
        temperature_socket = node.inputs["Temperature"]
        prefix = "scene.textures."

        if temperature_socket.is_linked:
            # A linked temperature (e.g. a density grid or attribute) drives the
            # per-point Planckian eval on the LuxCore side.
            temperature = _socket(temperature_socket, props, material, obj_name, group_node_stack)
            temperature = _convert_to_float(temperature, props)
        else:
            temperature = temperature_socket.default_value

        definitions = {
            "type": "blackbody",
            "temperature": temperature,
            "normalize": True,
        }
    elif node.bl_idname == "ShaderNodeMapRange":
        if node.interpolation_type != "LINEAR":
            LuxCoreErrorLog.add_warning(f"In material {material.name}: Unsupported map range interpolation type: " + node.interpolation_type,
                                        obj_name=obj_name)
            return ERROR_VALUE

        if not node.clamp:
            # TODO: LuxCore's remap texture always clamps, at the moment
            LuxCoreErrorLog.add_warning(f"In material {material.name}: map range node will be clamped", obj_name=obj_name)

        prefix = "scene.textures."

        value = _socket(node.inputs["Value"], props, material, obj_name, group_node_stack)
        value = _convert_to_float(value, props)

        definitions = {
            "type": "remap",
            "value": value,
            "sourcemin": _socket(node.inputs["From Min"], props, material, obj_name, group_node_stack),
            "sourcemax": _socket(node.inputs["From Max"], props, material, obj_name, group_node_stack),
            "targetmin": _socket(node.inputs["To Min"], props, material, obj_name, group_node_stack),
            "targetmax": _socket(node.inputs["To Max"], props, material, obj_name, group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeSubsurfaceScattering":
        prefix = "scene.materials."

        # Approximation: LuxCore has no BSSRDF material; the Disney subsurface
        # parameter gives a plausible diffuse-translucent blend. The mean free
        # path (Radius), IOR and anisotropy inputs cannot be mapped.
        scale = _socket(node.inputs["Scale"], props, material, obj_name, group_node_stack)
        if scale == ERROR_VALUE:
            scale = 1.0
        subsurface = scale if _is_textured(scale) else max(0.0, min(1.0, scale))

        roughness_socket = node.inputs.get("Roughness")
        roughness = _socket(roughness_socket, props, material, obj_name,
                            group_node_stack) if roughness_socket else 0.5
        if roughness == ERROR_VALUE:
            roughness = 0.5

        LuxCoreErrorLog.add_warning(
            f'Subsurface Scattering node "{node.name}" is approximated by the Disney '
            "subsurface parameter (no radius/anisotropy)", obj_name=obj_name)

        definitions = {
            "type": "disney",
            "basecolor": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "subsurface": subsurface,
            "metallic": 0,
            "roughness": roughness,
        }
        if node.inputs.get("Normal") is not None:
            definitions["bumptex"] = _socket(node.inputs["Normal"], props, material,
                                             obj_name, group_node_stack)
    elif node.bl_idname == "ShaderNodeBsdfVelvet":
        prefix = "scene.materials."

        # Approximation: LuxCore's velvet has no sigma input; sigma is folded
        # into the thickness parameter
        sigma = _socket(node.inputs["Sigma"], props, material, obj_name, group_node_stack)
        if sigma == ERROR_VALUE:
            sigma = 0.5
        thickness = sigma * 0.2 if not _is_textured(sigma) else \
            _tex_binary("scale", sigma, 0.2, luxcore_name + "_thickness", props)

        definitions = {
            "type": "velvet",
            "kd": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "thickness": thickness,
        }
        if node.inputs.get("Normal") is not None:
            definitions["bumptex"] = _socket(node.inputs["Normal"], props, material,
                                             obj_name, group_node_stack)
    elif node.bl_idname == "ShaderNodeBsdfSheen":
        prefix = "scene.materials."

        # Approximation: Disney sheen is a diffuse-like retro-reflective lobe;
        # it cannot reproduce a standalone sheen closure exactly
        LuxCoreErrorLog.add_warning(
            f'Sheen node "{node.name}" is approximated by a Disney material',
            obj_name=obj_name)

        roughness_socket = node.inputs.get("Roughness")
        roughness = _socket(roughness_socket, props, material, obj_name,
                            group_node_stack) if roughness_socket else 0.5
        if roughness == ERROR_VALUE:
            roughness = 0.5

        definitions = {
            "type": "disney",
            "basecolor": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
            "sheen": 1.0,
            "roughness": roughness,
        }
        if node.inputs.get("Normal") is not None:
            definitions["bumptex"] = _socket(node.inputs["Normal"], props, material,
                                             obj_name, group_node_stack)
    elif node.bl_idname == "ShaderNodeBsdfToon":
        prefix = "scene.materials."

        # Approximation: no toon closure in LuxCore; matte keeps the base color
        LuxCoreErrorLog.add_warning(
            f'Toon BSDF node "{node.name}" is approximated by a matte material',
            obj_name=obj_name)

        definitions = {
            "type": "matte",
            "kd": _socket(node.inputs["Color"], props, material, obj_name, group_node_stack),
        }
        if node.inputs.get("Normal") is not None:
            definitions["bumptex"] = _socket(node.inputs["Normal"], props, material,
                                             obj_name, group_node_stack)
    elif node.bl_idname == "ShaderNodeFresnel":
        prefix = "scene.textures."

        if node.inputs["Normal"].is_linked:
            LuxCoreErrorLog.add_warning(
                f'Fresnel node "{node.name}": the Normal input is not supported',
                obj_name=obj_name)

        ior_socket = node.inputs["IOR"]
        ior = _socket(ior_socket, props, material, obj_name, group_node_stack)
        if ior == ERROR_VALUE:
            ior = 1.45

        # LuxCore has no texture evaluating the angular Fresnel term (the
        # fresnel* texture types only carry conductor n/k data for materials).
        # Approximation: the Schlick F0 normal-incidence reflectance
        # ((ior - 1) / (ior + 1))^2, exact for rays perpendicular to the surface.
        _warn_unsupported(
            node, "no angular Fresnel texture in LuxCore; using the "
            "normal-incidence reflectance (Schlick F0)", None, obj_name)

        if _is_textured(ior):
            # Build F0 = ((ior - 1) / (ior + 1))^2 as a helper texture chain
            n_minus_1 = _tex_helper(props, luxcore_name + "_f0sub", {
                "type": "subtract", "texture1": ior, "texture2": 1})
            n_plus_1 = _tex_helper(props, luxcore_name + "_f0add", {
                "type": "add", "texture1": ior, "texture2": 1})
            ratio = _tex_helper(props, luxcore_name + "_f0div", {
                "type": "divide", "texture1": n_minus_1, "texture2": n_plus_1})
            definitions = {"type": "power", "base": ratio, "exponent": 2}
        else:
            return ((ior - 1) / (ior + 1)) ** 2
    elif node.bl_idname == "ShaderNodeLayerWeight":
        prefix = "scene.textures."

        if node.inputs["Normal"].is_linked:
            LuxCoreErrorLog.add_warning(
                f'Layer Weight node "{node.name}": the Normal input is not supported',
                obj_name=obj_name)

        if output_socket.name == "Fresnel":
            # Approximation: no angular falloff texture in LuxCore; use the
            # normal-incidence dielectric reflectance of Cycles' fixed IOR 1.45
            return _warn_unsupported(
                node, "'Fresnel' output approximated by normal-incidence "
                "reflectance (IOR 1.45)", 0.0334, obj_name)
        else:
            # "Facing": no per-ray falloff information is available to LuxCore
            # textures, so use a constant mid value
            return _warn_unsupported(
                node, "'Facing' output is not supported (no angular falloff "
                "texture); using 0.5", FALLBACK_FLOAT, obj_name)
    elif node.bl_idname == "ShaderNodeLightPath":
        # The LuxCore "rayinfo" texture exposes the context of the ray that
        # generated the current hit point (stored in HitPoint by
        # Scene::Intersect()). All Light Path outputs are supported.
        channel = _LIGHT_PATH_CHANNELS.get(output_socket.name)
        if channel is None:
            return _warn_unsupported(
                node, f"unknown Light Path output '{output_socket.name}'; "
                "using constant 0", 0.0, obj_name)

        prefix = "scene.textures."
        definitions = {
            "type": "rayinfo",
            "channel": channel,
        }
    elif node.bl_idname == "ShaderNodeMix":
        prefix = "scene.textures."
        data_type = node.data_type

        # The unified Mix node keeps one "Factor"/"A"/"B" socket per data type;
        # only the ones matching the active type are enabled. Match both the
        # name and the socket type for robustness.
        socket_type = {"FLOAT": "NodeSocketFloat",
                       "VECTOR": "NodeSocketVector",
                       "RGBA": "NodeSocketColor"}.get(data_type)
        factor_type = "NodeSocketVector" if (
            data_type == "VECTOR" and
            getattr(node, "factor_mode", "UNIFORM") == "NON_UNIFORM"
        ) else "NodeSocketFloat"

        def mix_input(name, bl_socket):
            candidates = [s for s in node.inputs if s.name == name]
            for socket in candidates:
                if socket.bl_idname == bl_socket and socket.enabled:
                    return socket
            for socket in candidates:
                if socket.bl_idname == bl_socket:
                    return socket
            return candidates[0] if candidates else None

        factor_socket = mix_input("Factor", factor_type)
        fac = _socket(factor_socket, props, material, obj_name,
                      group_node_stack) if factor_socket else 0.5
        if factor_socket is not None and factor_socket.is_linked and fac == ERROR_VALUE:
            fac = 0.5

        socket_a = mix_input("A", socket_type)
        socket_b = mix_input("B", socket_type)
        if socket_a is None or socket_b is None:
            return _warn_unsupported(
                node, "could not resolve the A/B inputs", FALLBACK_FLOAT, obj_name)
        # Note: a legitimate 0 input compares equal to ERROR_VALUE; a failed
        # upstream node already logged its own warning, so just use the value
        tex1 = _socket(socket_a, props, material, obj_name, group_node_stack)
        tex2 = _socket(socket_b, props, material, obj_name, group_node_stack)

        if data_type == "RGBA":
            definitions, luxcore_name, early = _blend_rgb(
                node, node.blend_type, fac, tex1, tex2, luxcore_name, props, obj_name)
            if early is not None:
                return early
        elif data_type in {"FLOAT", "VECTOR"}:
            # The mix amount is a texture, so a non-uniform vector factor works
            # natively (elementwise)
            definitions = {
                "type": "mix",
                "texture1": tex1,
                "texture2": tex2,
                "amount": fac,
            }
        else:
            # ROTATION
            return _warn_unsupported(
                node, f'data type "{data_type}" is not supported, passing through '
                "input A", tex1, obj_name)

        if getattr(node, "clamp", False):
            # Clamp the mix result (mirrors the use_clamp handling below)
            props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
            definitions = {
                "type": "clamp",
                "texture": luxcore_name,
                "min": 0,
                "max": 1,
            }
            luxcore_name = luxcore_name + "clamp"
    elif node.bl_idname == "ShaderNodeVectorMath":
        prefix = "scene.textures."
        operation = node.operation
        vector_out = output_socket.name != "Value"

        vector1 = _socket(node.inputs[0], props, material, obj_name, group_node_stack)
        vector2 = _socket(node.inputs[1], props, material, obj_name, group_node_stack)

        # Elementwise Spectrum ops double as vector math ops in LuxCore
        direct_ops = {
            "ADD": "add",
            "SUBTRACT": "subtract",
            "MULTIPLY": "scale",
            "DIVIDE": "divide",
            "DOT_PRODUCT": "dotproduct",
        }

        if operation in direct_ops:
            definitions = {
                "type": direct_ops[operation],
                "texture1": vector1,
                "texture2": vector2,
            }
        elif operation == "ABSOLUTE":
            definitions = {"type": "abs", "texture": vector1}
        elif operation == "MODULO":
            definitions = {
                "type": "modulo",
                "texture": vector1,
                "modulo": vector2,
            }
        elif operation == "SCALE":
            definitions = {
                "type": "scale",
                "texture1": vector1,
                "texture2": _socket(node.inputs["Scale"], props, material,
                                    obj_name, group_node_stack),
            }
        elif operation == "LENGTH":
            # |v| = sqrt(v . v)
            squared = _tex_helper(props, luxcore_name + "_sq", {
                "type": "dotproduct", "texture1": vector1, "texture2": vector1})
            definitions = {"type": "power", "base": squared, "exponent": 0.5}
        elif operation == "DISTANCE":
            # |a - b| = sqrt((a - b) . (a - b))
            diff = _tex_helper(props, luxcore_name + "_diff", {
                "type": "subtract", "texture1": vector1, "texture2": vector2})
            squared = _tex_helper(props, luxcore_name + "_sq", {
                "type": "dotproduct", "texture1": diff, "texture2": diff})
            definitions = {"type": "power", "base": squared, "exponent": 0.5}
        elif operation == "NORMALIZE":
            # v / |v|; the scalar length broadcasts to all 3 channels
            squared = _tex_helper(props, luxcore_name + "_sq", {
                "type": "dotproduct", "texture1": vector1, "texture2": vector1})
            length = _tex_helper(props, luxcore_name + "_len", {
                "type": "power", "base": squared, "exponent": 0.5})
            definitions = {
                "type": "divide",
                "texture1": vector1,
                "texture2": length,
            }
        elif operation == "CROSS_PRODUCT":
            a = [_split_chan(vector1, i, luxcore_name + f"_a{i}", props)
                 for i in range(3)]
            b = [_split_chan(vector2, i, luxcore_name + f"_b{i}", props)
                 for i in range(3)]

            def _mul(t1, t2, tag):
                return _tex_binary("scale", t1, t2, luxcore_name + tag, props)

            cross = [
                _tex_binary("subtract", _mul(a[1], b[2], "_x0p"),
                            _mul(a[2], b[1], "_x0m"), luxcore_name + "_cx", props),
                _tex_binary("subtract", _mul(a[2], b[0], "_x1p"),
                            _mul(a[0], b[2], "_x1m"), luxcore_name + "_cy", props),
                _tex_binary("subtract", _mul(a[0], b[1], "_x2p"),
                            _mul(a[1], b[0], "_x2m"), luxcore_name + "_cz", props),
            ]
            return _combine3(cross[0], cross[1], cross[2],
                             luxcore_name + "_cross", props)
        elif operation == "REFLECT":
            # r = i - 2 (i . n) n
            dot = _tex_binary("dotproduct", vector1, vector2,
                              luxcore_name + "_dot", props)
            two_dot = _tex_binary("scale", dot, 2.0, luxcore_name + "_2d", props)
            scaled_n = _tex_binary("scale", vector2, two_dot,
                                   luxcore_name + "_sn", props)
            return _tex_binary("subtract", vector1, scaled_n,
                               luxcore_name + "_refl", props)
        elif operation == "PROJECT":
            # proj of a onto b = b * (a . b) / (b . b)
            dot = _tex_binary("dotproduct", vector1, vector2,
                              luxcore_name + "_dot", props)
            len2 = _tex_binary("dotproduct", vector2, vector2,
                               luxcore_name + "_len2", props)
            frac = _tex_binary("divide", dot, len2, luxcore_name + "_fr", props)
            return _tex_binary("scale", vector2, frac,
                               luxcore_name + "_proj", props)
        elif operation == "FACEFORWARD":
            # Blender: returns n if dot(i, nref) < 0 else -n, i.e.
            # n * (2*lt(dot,0) - 1). Inputs: Vector, Incident, Reference.
            incident = _socket(node.inputs[1], props, material, obj_name,
                               group_node_stack)
            reference = _socket(node.inputs[2], props, material, obj_name,
                                group_node_stack)
            dot = _tex_binary("dotproduct", incident, reference,
                              luxcore_name + "_dot", props)
            lt = _tex_lessthan(dot, 0.0, luxcore_name + "_lt", props)
            sign = _tex_binary("subtract",
                               _tex_binary("scale", lt, 2.0,
                                           luxcore_name + "_lt2", props),
                               1.0, luxcore_name + "_sgn", props)
            return _tex_binary("scale", vector1, sign,
                               luxcore_name + "_ff", props)
        elif operation == "MULTIPLY_ADD":
            # a * b + c (elementwise)
            vector3 = _socket(node.inputs[2], props, material, obj_name,
                              group_node_stack)
            prod = _tex_binary("scale", vector1, vector2,
                               luxcore_name + "_mp", props)
            return _tex_binary("add", prod, vector3,
                               luxcore_name + "_madd", props)
        elif operation in {"MINIMUM", "MAXIMUM"}:
            # min(a,b) = b + lt(a,b)*(a-b);  max(a,b) = a + lt(a,b)*(b-a)
            lt = _tex_lessthan(vector1, vector2, luxcore_name + "_lt", props)
            if operation == "MINIMUM":
                diff = _tex_binary("subtract", vector1, vector2,
                                   luxcore_name + "_df", props)
                sel = _tex_binary("scale", diff, lt, luxcore_name + "_sl", props)
                return _tex_binary("add", vector2, sel,
                                   luxcore_name + "_min", props)
            else:
                diff = _tex_binary("subtract", vector2, vector1,
                                   luxcore_name + "_df", props)
                sel = _tex_binary("scale", diff, lt, luxcore_name + "_sl", props)
                return _tex_binary("add", vector1, sel,
                                   luxcore_name + "_max", props)
        elif operation == "SNAP":
            # Approximation: LuxCore's rounding texture snaps to the nearest
            # multiple of the increment; Blender's SNAP floors to it. The
            # difference is at most half an increment per component.
            _warn_unsupported(
                node, "'Snap' approximated by round-to-nearest-multiple "
                "(Blender floors to the increment)", None, obj_name)
            definitions = {
                "type": "rounding",
                "texture": vector1,
                "increment": vector2,
            }
        elif operation in {"SINE", "COSINE", "TANGENT"}:
            # Elementwise trig via mathfunc (matches Cycles' per-component
            # semantics); constants fold to plain vectors
            op = {"SINE": "sin", "COSINE": "cos", "TANGENT": "tan"}[operation]
            return _v3_mathfunc(op, vector1, luxcore_name, props)
        else:
            # Unsupported ops (WRAP, FLOORMOD, DIVIDE modes, REFRACT, ...):
            # pass through instead of blacking out
            if vector_out:
                return _warn_unsupported(
                    node, f"vector math operation '{operation}' is not supported, "
                    "passing through the first input", vector1, obj_name)
            return _warn_unsupported(
                node, f"vector math operation '{operation}' is not supported, "
                "passing through the luminance of the first input",
                _convert_to_float(vector1, props), obj_name)
    elif node.bl_idname == "ShaderNodeTexCoord":
        prefix = "scene.textures."
        coord = output_socket.name

        if coord == "UV":
            definitions = {"type": "uv"}
        elif coord == "Normal":
            definitions = {"type": "shadingnormal"}
        elif coord == "Object":
            if getattr(node, "object", None) is not None:
                LuxCoreErrorLog.add_warning(
                    f'Texture Coordinate node "{node.name}": coordinates relative to '
                    "another object are not supported, using own object space",
                    obj_name=obj_name)
            # "position" evaluates the hit point in object space, which matches
            # Cycles' Object output (for the shading object)
            definitions = {"type": "position"}
        elif coord == "Generated":
            # Approximation: no bounding-box normalized coordinates in LuxCore;
            # UV coordinates are the closest match for typical 2D usage (z = 0)
            _warn_unsupported(
                node, "'Generated' coordinates are approximated by the UV map "
                "(no bounding-box normalization)", None, obj_name)
            definitions = {"type": "uv"}
        elif coord == "Reflection":
            # Approximation: the reflected view direction is not available to
            # LuxCore textures; the shading normal is the closest varying field
            _warn_unsupported(
                node, "'Reflection' direction is approximated by the shading "
                "normal", None, obj_name)
            definitions = {"type": "shadingnormal"}
        elif coord == "Window":
            return _warn_unsupported(
                node, "'Window' (screen space) coordinates are not supported; "
                "using the screen center", [0.5, 0.5, 0.0], obj_name)
        else:
            # "Camera" and any future outputs
            return _warn_unsupported(
                node, f"texture coordinate output '{coord}' is not supported; "
                "returning a zero vector", FALLBACK_VECTOR, obj_name)
    elif node.bl_idname == "ShaderNodeUVMap":
        prefix = "scene.textures."
        definitions = {"type": "uv"}

        uv_map = getattr(node, "uv_map", "")
        if uv_map:
            index = _uv_layer_index(obj_name, uv_map)
            if index is None:
                LuxCoreErrorLog.add_warning(
                    f'UV map "{uv_map}" of node "{node.name}" could not be resolved '
                    f'on object "{obj_name}", using the default UV layer',
                    obj_name=obj_name)
            else:
                definitions["mapping.uvindex"] = index
    elif node.bl_idname == "ShaderNodeMapping":
        link = utils_node.get_link(node.inputs["Vector"])
        if link is None:
            return _warn_unsupported(
                node, "Mapping node without a Vector input; returning a zero "
                "vector", FALLBACK_VECTOR, obj_name)

        # LuxCore has no standalone "mapping" texture: each texture carries its
        # own "mapping.*" block. Re-emit the upstream node under this node's name
        # and attach the transform to it (works for texture types that parse a
        # mapping block, e.g. uv, imagemap and the procedural textures).
        result = _node(link.from_node, link.from_socket, props, material,
                       luxcore_name, obj_name, group_node_stack)
        if result == ERROR_VALUE or not _is_textured(result):
            return _warn_unsupported(
                node, "cannot map a non-texture input; returning a zero vector",
                FALLBACK_VECTOR, obj_name)

        is_2d = link.from_socket.name == "UV" or \
            link.from_node.bl_idname in {"ShaderNodeTexImage", "ShaderNodeUVMap"}
        location, rotation, scale = _mapping_node_values(node, obj_name)
        if is_2d:
            mapping_defs = _mapping_uv_defs(location, rotation, scale, False)
        else:
            # vector_type NORMAL/VECTOR would need a direction transform; the
            # full TRS matrix is an approximation here
            mapping_defs = {
                "mapping.type": "localmapping3d",
                "mapping.transformation": utils.luxutils.matrix_to_list(
                    _mapping_matrix(location, rotation, scale)),
            }
        props.Set(utils.luxutils.create_props(
            "scene.textures." + result + ".", mapping_defs))
        return result
    elif node.bl_idname == "ShaderNodeNormal":
        # The "Normal" output is the fixed direction set in the node widget
        try:
            normal = list(node.outputs["Normal"].default_value)[:3]
        except (TypeError, KeyError):
            normal = [0.0, 0.0, 1.0]

        if output_socket.name == "Dot":
            prefix = "scene.textures."
            other = _socket(node.inputs["Normal"], props, material, obj_name,
                            group_node_stack)
            if _is_textured(other):
                definitions = {
                    "type": "dotproduct",
                    "texture1": other,
                    "texture2": normal,
                }
            elif isinstance(other, (list, tuple)):
                return sum(a * b for a, b in zip(other, normal))
            else:
                return 0.0
        else:
            return normal
    elif node.bl_idname == "ShaderNodeTangent":
        # No tangent texture in LuxCore (hitPoint.dpdu/dpdv is not exposed)
        return _warn_unsupported(
            node, "Tangent node is not supported; returning a zero vector",
            FALLBACK_VECTOR, obj_name)
    elif node.bl_idname == "ShaderNodeBevel":
        prefix = "scene.textures."
        # The "bevel" texture exists but is disabled in the engine's SDL parser;
        # use the unmodified shading normal instead
        _warn_unsupported(
            node, "Bevel is not supported by this engine version; using the "
            "unmodified shading normal", None, obj_name)
        definitions = {"type": "shadingnormal"}
    elif node.bl_idname == "ShaderNodeAmbientOcclusion":
        # No AO texture in LuxCore; approximate "fully lit"
        if output_socket.name == "AO":
            return _warn_unsupported(
                node, "Ambient Occlusion is not supported; returning 1 "
                "(unoccluded)", 1.0, obj_name)
        color = _socket(node.inputs["Color"], props, material, obj_name,
                        group_node_stack)
        if color == ERROR_VALUE:
            color = [1.0, 1.0, 1.0]
        return _warn_unsupported(
            node, "Ambient Occlusion is not supported; passing through the "
            "unoccluded color", color, obj_name)
    elif node.bl_idname == "ShaderNodeWireframe":
        prefix = "scene.textures."

        if node.use_pixel_size:
            LuxCoreErrorLog.add_warning(
                f'Wireframe node "{node.name}": pixel size mode is not supported',
                obj_name=obj_name)

        definitions = {
            "type": "wireframe",
            # Fac output: 1 on edges (border), 0 inside
            "border": 1.0,
            "inside": 0.0,
            "width": _socket(node.inputs["Size"], props, material, obj_name,
                             group_node_stack),
        }
    elif node.bl_idname in {"ShaderNodeAttribute", "ShaderNodeVertexColor"}:
        prefix = "scene.textures."
        # ShaderNodeVertexColor is the legacy (pre-3.0) version of the
        # Attribute node and only exposes the layer name plus Color/Alpha
        attribute_name = node.attribute_name \
            if node.bl_idname == "ShaderNodeAttribute" \
            else getattr(node, "layer_name", "")

        data_index = _color_attribute_index(obj_name, attribute_name)

        if output_socket.name == "Vector" and data_index is None:
            # A named UV layer can serve the Vector output
            uv_index = _uv_layer_index(obj_name, attribute_name)
            if uv_index is not None:
                definitions = {
                    "type": "uv",
                    "mapping.uvindex": uv_index,
                }
                data_index = -2  # marker: handled

        if data_index is None:
            # Generic named attribute (e.g. written by Geometry Nodes):
            # exported by mesh_converter as vertex/triangle AOV or an
            # extra color layer.
            named = named_attributes.resolve(obj_name, attribute_name)
            if named is not None:
                kind, named_index = named
                if output_socket.name == "Alpha":
                    # Scalar/vector attributes carry no alpha channel
                    definitions = {"type": "constfloat1", "value": 1.0}
                elif output_socket.name == "Fac":
                    definitions = {
                        "type": {
                            named_attributes.KIND_VERTEX_AOV:
                                "hitpointvertexaov",
                            named_attributes.KIND_TRIANGLE_AOV:
                                "hitpointtriangleaov",
                            named_attributes.KIND_COLOR: "hitpointgrey",
                        }[kind],
                        "dataindex": named_index,
                    }
                    if kind == named_attributes.KIND_COLOR:
                        definitions["channel"] = -1
                else:
                    # "Color" and "Vector" outputs
                    definitions = {
                        "type": {
                            named_attributes.KIND_VERTEX_AOV:
                                "hitpointvertexaov",
                            named_attributes.KIND_TRIANGLE_AOV:
                                "hitpointtriangleaov",
                            named_attributes.KIND_COLOR: "hitpointcolor",
                        }[kind],
                        "dataindex": named_index,
                    }
                data_index = -2  # marker: handled

        if data_index is None:
            return _warn_unsupported(
                node, f'attribute "{attribute_name}" could not be resolved to an '
                "exported vertex color, UV layer, or named attribute; "
                "returning mid grey",
                FALLBACK_COLOR if output_socket.name != "Fac" else FALLBACK_FLOAT,
                obj_name)

        if data_index != -2:
            if output_socket.name == "Fac":
                definitions = {
                    "type": "hitpointgrey",
                    "dataindex": data_index,
                    "channel": -1,
                }
            elif output_socket.name == "Alpha":
                definitions = {
                    "type": "hitpointalpha",
                    "dataindex": data_index,
                }
            else:
                # "Color" and "Vector"
                definitions = {
                    "type": "hitpointcolor",
                    "dataindex": data_index,
                }
    elif node.bl_idname == "ShaderNodeTexVoronoi":
        prefix = "scene.textures."

        if node.voronoi_dimensions != "3D":
            LuxCoreErrorLog.add_warning(
                f'Voronoi node "{node.name}": {node.voronoi_dimensions} mode is '
                "approximated by 3D (extra inputs ignored)", obj_name=obj_name)

        scale_socket = node.inputs["Scale"]
        if scale_socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Voronoi node "{node.name}": textured scale is not supported',
                obj_name=obj_name)
            noisesize = 0.25
        else:
            noisesize = 1.0 / max(scale_socket.default_value, 1e-6)

        # Blender voronoi returns a weighted sum of feature distances
        # (w1..w4 = F1..F4 weights); approximate the Cycles features:
        #   F1 -> w1, F2 -> w2, DISTANCE_TO_EDGE ~ F2 - F1
        if node.feature == "F2":
            weights = (0.0, 1.0, 0.0, 0.0)
        elif node.feature == "DISTANCE_TO_EDGE":
            weights = (-1.0, 1.0, 0.0, 0.0)
        else:
            if node.feature in {"SMOOTH_F1", "N_SPHERE_RADIUS"}:
                LuxCoreErrorLog.add_warning(
                    f'Voronoi node "{node.name}": feature "{node.feature}" is '
                    "approximated by plain F1", obj_name=obj_name)
            weights = (1.0, 0.0, 0.0, 0.0)

        distance_map = {
            "EUCLIDEAN": "actual_distance",
            "MANHATTAN": "manhattan",
            "CHEBYCHEV": "chebychev",
            "MINKOWSKI": "minkowski",
        }
        distmetric = distance_map.get(node.distance, "actual_distance")

        exponent_socket = node.inputs.get("Exponent")
        exponent = exponent_socket.default_value if exponent_socket is not None else 2.0

        definitions = {
            "type": "blender_voronoi",
            "intensity": 1,
            "exponent": exponent,
            "distmetric": distmetric,
            "w1": weights[0],
            "w2": weights[1],
            "w3": weights[2],
            "w4": weights[3],
            "noisesize": noisesize,
        }
        definitions.update(_vector_mapping_defs(
            node.inputs["Vector"], False, False, props, material, obj_name,
            group_node_stack))

        if output_socket.name not in {"Distance", "Color"}:
            return _warn_unsupported(
                node, f'Voronoi output "{output_socket.name}" is not supported; '
                "returning a neutral value",
                FALLBACK_VECTOR if output_socket.name == "Position" else FALLBACK_FLOAT,
                obj_name)
        if output_socket.name == "Color":
            # Approximation: blender_voronoi is monochrome; the distance value
            # stands in for the per-cell random color
            LuxCoreErrorLog.add_warning(
                f'Voronoi node "{node.name}": the Color output is approximated by '
                "the monochrome distance texture", obj_name=obj_name)
    elif node.bl_idname == "ShaderNodeTexNoise":
        prefix = "scene.textures."

        # Closest match: blender_distortednoise is the only engine texture with
        # a distortion amount like Cycles' Noise. Divergences: monochrome result
        # (Color == Fac) and no roughness/lacunarity parameters.
        if node.noise_dimensions != "3D":
            LuxCoreErrorLog.add_warning(
                f'Noise node "{node.name}": {node.noise_dimensions} mode is '
                "approximated by 3D (extra inputs ignored)", obj_name=obj_name)

        scale_socket = node.inputs["Scale"]
        if scale_socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Noise node "{node.name}": textured scale is not supported',
                obj_name=obj_name)
            noisesize = 0.25
        else:
            noisesize = 1.0 / max(scale_socket.default_value, 1e-6)

        detail_socket = node.inputs["Detail"]
        noisedepth = 2 if detail_socket.is_linked else \
            max(0, min(25, round(detail_socket.default_value)))

        distortion_socket = node.inputs["Distortion"]
        distortion = 0.0 if distortion_socket.is_linked else \
            distortion_socket.default_value
        if distortion_socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Noise node "{node.name}": textured distortion is not supported',
                obj_name=obj_name)

        definitions = {
            "type": "blender_distortednoise",
            "noisebasis": "blender_original",
            "noise_distortion": "blender_original",
            "noisesize": noisesize,
            "noisedepth": noisedepth,
            "distortion": distortion,
        }
        definitions.update(_vector_mapping_defs(
            node.inputs["Vector"], False, False, props, material, obj_name,
            group_node_stack))
    elif node.bl_idname == "ShaderNodeTexWhiteNoise":
        prefix = "scene.textures."

        # Deterministic hash of a 3D vector seed -> Value + Color.
        # The same whitenoise texture serves both outputs (LuxCore selects
        # float/spectrum evaluation by usage).
        if node.noise_dimensions != "3D":
            LuxCoreErrorLog.add_warning(
                f'White Noise node "{node.name}": {node.noise_dimensions} mode '
                "is approximated by 3D (extra inputs ignored)",
                obj_name=obj_name)

        vector_socket = node.inputs["Vector"]
        if vector_socket.is_linked:
            seed_tex = _socket(vector_socket, props, material, obj_name,
                               group_node_stack)
        else:
            # Unlinked Vector defaults to the position seed
            seed_tex = node.name + "::wnseed"
            props.Set(utils.luxutils.create_props(
                f"{prefix}{seed_tex}.", {"type": "position"}))

        definitions = {
            "type": "whitenoise",
            "texture": seed_tex,
        }
    elif node.bl_idname == "ShaderNodeTexGabor":
        prefix = "scene.textures."

        # LuxCore gabornoise implements Lagae 2009 sparse Gabor
        # convolution (2D), normalized after Tavernier 2019, with phasor
        # phase/intensity outputs (Tricard 2019).
        if node.gabor_type != "2D":
            LuxCoreErrorLog.add_warning(
                f'Gabor node "{node.name}": 3D mode is approximated by 2D '
                "evaluation of xy", obj_name=obj_name)

        def _fin(name, default):
            sk = node.inputs[name]
            if sk.is_linked:
                LuxCoreErrorLog.add_warning(
                    f'Gabor node "{node.name}": linked {name} input is not '
                    "supported, using its default", obj_name=obj_name)
            return sk.default_value if not sk.is_linked else default

        vector_socket = node.inputs["Vector"]
        if vector_socket.is_linked:
            vec_tex = _socket(vector_socket, props, material, obj_name,
                              group_node_stack)
        else:
            vec_tex = node.name + "::gaborpos"
            props.Set(utils.luxutils.create_props(
                f"{prefix}{vec_tex}.", {"type": "position"}))

        out_map = {"Value": "value", "Phase": "phase",
                   "Intensity": "intensity"}
        definitions = {
            "type": "gabornoise",
            "vector": vec_tex,
            "scale": _fin("Scale", 1.0),
            "frequency": _fin("Frequency", 2.0),
            "isotropy": _fin("Anisotropy", 0.0),
            # Orientation 2D/3D share the display name "Orientation";
            # address the 2D one by identifier
            "orientation": next((sk.default_value for sk in node.inputs
                                 if sk.identifier == "Orientation 2D"), 0.0),
            "output": out_map.get(output_socket.name, "value"),
        }
    elif node.bl_idname == "ShaderNodeTexBrick":
        prefix = "scene.textures."

        def _finput(name, default):
            s = node.inputs.get(name)
            if s is None:
                return default
            if s.is_linked:
                LuxCoreErrorLog.add_warning(
                    f'Brick node "{node.name}": textured "{name}" is not '
                    "supported, using its default", obj_name=obj_name)
                return default
            return s.default_value

        if node.inputs.get("Mortar Smooth") is not None and \
                _finput("Mortar Smooth", 0.0) != 0.0:
            LuxCoreErrorLog.add_warning(
                f'Brick node "{node.name}": mortar smoothing is not supported',
                obj_name=obj_name)
        if getattr(node, "squash", 0.0) != 0.0:
            LuxCoreErrorLog.add_warning(
                f'Brick node "{node.name}": squash is not supported',
                obj_name=obj_name)

        # Color2 is approximated as the per-brick modulation texture
        # (LuxCore modulates each brick by brickmodtex; Cycles alternates
        # deterministically) — the pattern is preserved, the alternation
        # is randomized instead.
        color2_socket = node.inputs.get("Color2")
        if color2_socket is not None:
            LuxCoreErrorLog.add_warning(
                f'Brick node "{node.name}": Color2 is approximated as random '
                "per-brick modulation", obj_name=obj_name)

        offset = getattr(node, "offset", 0.5)
        definitions = {
            "type": "brick",
            "bricktex": _socket(node.inputs["Color1"], props, material,
                                obj_name, group_node_stack),
            "brickmodtex": _socket(color2_socket, props, material, obj_name,
                                   group_node_stack) if color2_socket else 1.0,
            "mortartex": _socket(node.inputs["Mortar"], props, material,
                                 obj_name, group_node_stack),
            "mortarsize": _finput("Mortar Size", 0.01),
            "brickmodbias": _finput("Bias", 0.0),
            "brickwidth": _finput("Brick Width", 0.5),
            "brickheight": _finput("Row Height", 0.25),
            "brickdepth": _finput("Brick Width", 0.5),
            "brickbond": "running",
            "brickrun": max(0.0, min(1.0, 1.0 - offset)),
        }
        definitions.update(_vector_mapping_defs(
            node.inputs["Vector"], False, False, props, material, obj_name,
            group_node_stack))

        if output_socket.name == "Fac":
            LuxCoreErrorLog.add_warning(
                f'Brick node "{node.name}": the Fac output is approximated by '
                "the brick color texture", obj_name=obj_name)
    elif node.bl_idname == "ShaderNodeTexWave":
        prefix = "scene.textures."

        # Closest match: blender_wood provides bands/rings with sin/saw/tri
        # profiles plus turbulence for distortion. Divergences: no phase offset,
        # no detail roughness, direction only via the mapping rotation.
        wave_profile_map = {"SIN": "sin", "SAW": "saw", "TRI": "tri"}
        noisebasis2 = wave_profile_map.get(node.wave_profile, "sin")

        distortion_socket = node.inputs.get("Distortion")
        distortion = 0.0
        if distortion_socket is not None:
            if distortion_socket.is_linked:
                LuxCoreErrorLog.add_warning(
                    f'Wave node "{node.name}": textured distortion is not supported',
                    obj_name=obj_name)
            else:
                distortion = distortion_socket.default_value

        if node.wave_type == "RINGS":
            woodtype = "ringnoise" if distortion != 0 else "rings"
            direction = node.rings_direction
        else:
            woodtype = "bandnoise" if distortion != 0 else "bands"
            direction = node.bands_direction

        scale_socket = node.inputs["Scale"]
        if scale_socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Wave node "{node.name}": textured scale is not supported',
                obj_name=obj_name)
            noisesize = 0.25
        else:
            noisesize = 1.0 / max(scale_socket.default_value, 1e-6)

        if node.wave_type == "RINGS" and node.rings_direction == "SPHERICAL":
            LuxCoreErrorLog.add_warning(
                f'Wave node "{node.name}": spherical rings are approximated by '
                "planar rings along Z", obj_name=obj_name)

        # Rotate the texture space so the bands/rings axis maps onto Z
        direction_vectors = {
            "X": Vector((1, 0, 0)),
            "Y": Vector((0, 1, 0)),
            "Z": Vector((0, 0, 1)),
            "DIAGONAL": Vector((0.5, 0.5, 0)).normalized(),
            "SPHERICAL": Vector((0, 0, 1)),
        }
        direction_vector = direction_vectors.get(direction, Vector((0, 0, 1)))
        transform = Vector((0, 0, 1)).rotation_difference(
            direction_vector).to_matrix().to_4x4()

        link = utils_node.get_link(node.inputs["Vector"])
        if link is not None and link.from_node.bl_idname == "ShaderNodeMapping":
            user_matrix = _mapping_matrix(*_mapping_node_values(
                link.from_node, obj_name))
            transform = transform @ user_matrix
        elif link is not None and not (
                link.from_node.bl_idname == "ShaderNodeTexCoord"
                and link.from_socket.name in {"UV", "Generated", "Object"}):
            LuxCoreErrorLog.add_warning(
                f'Wave node "{node.name}": unsupported Vector input source is '
                "ignored", obj_name=obj_name)

        definitions = {
            "type": "blender_wood",
            "woodtype": woodtype,
            "noisebasis2": noisebasis2,
            "noisesize": noisesize,
            "turbulence": distortion if distortion != 0 else 5.0,
            "mapping.type": "localmapping3d",
            "mapping.transformation": utils.luxutils.matrix_to_list(transform),
        }
    elif node.bl_idname == "ShaderNodeTexGradient":
        prefix = "scene.textures."

        # blender_blend covers the classic gradient progressions
        progression_map = {
            "LINEAR": "linear",
            "QUADRATIC": "quadratic",
            "EASING": "easing",
            "DIAGONAL": "diagonal",
            "SPHERICAL": "spherical",
            "QUADRATIC_SPHERE": "halo",
            "RADIAL": "radial",
        }
        if node.gradient_type not in progression_map:
            _warn_unsupported(
                node, f'gradient type "{node.gradient_type}" is approximated by '
                '"linear"', None, obj_name)

        definitions = {
            "type": "blender_blend",
            "progressiontype": progression_map.get(node.gradient_type, "linear"),
            "direction": "horizontal",
        }
        definitions.update(_vector_mapping_defs(
            node.inputs["Vector"], False, False, props, material, obj_name,
            group_node_stack))
    elif node.bl_idname == "ShaderNodeTexMagic":
        prefix = "scene.textures."

        depth_socket = node.inputs["Depth"]
        noisedepth = 2 if depth_socket.is_linked else \
            max(1, min(10, round(depth_socket.default_value)))

        distortion_socket = node.inputs["Distortion"]
        turbulence = 5.0 if distortion_socket.is_linked else \
            distortion_socket.default_value

        # blender_magic has no noisesize parameter; the Scale input is folded
        # into the texture space transform instead
        scale_socket = node.inputs["Scale"]
        scale = scale_socket.default_value if not scale_socket.is_linked else 1.0
        if scale_socket.is_linked:
            LuxCoreErrorLog.add_warning(
                f'Magic node "{node.name}": textured scale is not supported',
                obj_name=obj_name)
        transform = Matrix.Diagonal(Vector((scale, scale, scale))).to_4x4()

        vector_link = utils_node.get_link(node.inputs["Vector"])
        if vector_link is not None and \
                vector_link.from_node.bl_idname == "ShaderNodeMapping":
            transform = transform @ _mapping_matrix(*_mapping_node_values(
                vector_link.from_node, obj_name))

        definitions = {
            "type": "blender_magic",
            "noisedepth": noisedepth,
            "turbulence": turbulence,
            "mapping.type": "localmapping3d",
            "mapping.transformation": utils.luxutils.matrix_to_list(transform),
        }
    elif node.bl_idname in {"ShaderNodeRGBCurve", "ShaderNodeFloatCurve"}:
        prefix = "scene.textures."

        # Approximation: the curve is sampled into a "band" texture; for the
        # RGB curve node only the combined (C) curve is used and the color input
        # is evaluated via its luminance. The Fac mix input is ignored.
        is_rgb_curve = node.bl_idname == "ShaderNodeRGBCurve"
        input_socket = node.inputs["Color" if is_rgb_curve else "Value"]

        try:
            node.mapping.update()
            curve_map = node.mapping.curves[3 if is_rgb_curve else 0]
            samples = [max(0.0, min(1.0, _evaluate_curve(curve_map, node.mapping,
                                                        i / 8)))
                       for i in range(9)]
        except Exception as error:
            return _warn_unsupported(
                node, f"curve evaluation failed ({error}); passing through the "
                "input", _socket(input_socket, props, material, obj_name,
                                 group_node_stack), obj_name)

        if is_rgb_curve:
            LuxCoreErrorLog.add_warning(
                f'RGB Curves node "{node.name}": only the combined curve is used '
                "(per-channel curves are approximated)", obj_name=obj_name)

        definitions = {
            "type": "band",
            "amount": _socket(input_socket, props, material, obj_name,
                              group_node_stack),
            "offsets": len(samples),
            "interpolation": "linear",
        }
        for i, sample in enumerate(samples):
            definitions[f"offset{i}"] = i / 8
            definitions[f"value{i}"] = [sample] * 3
    elif node.bl_idname == "ShaderNodeVectorCurve":
        prefix = "scene.textures."

        # split the vector, run each channel through its own curve (sampled
        # into "band" textures), recombine — the Fac mix input is ignored
        vector_socket = node.inputs["Vector"]
        fac_socket = node.inputs.get("Factor")
        if fac_socket is not None and \
                (fac_socket.is_linked or fac_socket.default_value != 1.0):
            LuxCoreErrorLog.add_warning(
                f'Vector Curves node "{node.name}": the Factor input is not '
                "supported", obj_name=obj_name)

        vector = _socket(vector_socket, props, material, obj_name,
                         group_node_stack)
        try:
            node.mapping.update()
            channel_names = ("_x", "_y", "_z")
            tex_names = []
            for channel in range(3):
                curve_map = node.mapping.curves[channel + 1]
                samples = [max(-4.0, min(4.0, _evaluate_curve(
                    curve_map, node.mapping, i / 8))) for i in range(9)]

                split_name = luxcore_name + channel_names[channel]
                props.Set(utils.luxutils.create_props(
                    prefix + split_name + ".",
                    {"type": "splitfloat3", "texture": vector,
                     "channel": channel}))
                band_name = luxcore_name + channel_names[channel] + "_curve"
                band_defs = {
                    "type": "band", "amount": split_name,
                    "offsets": len(samples), "interpolation": "linear",
                }
                for i, sample in enumerate(samples):
                    band_defs[f"offset{i}"] = i / 8
                    band_defs[f"value{i}"] = [sample] * 3
                props.Set(utils.luxutils.create_props(
                    prefix + band_name + ".", band_defs))
                tex_names.append(band_name)

            definitions = {
                "type": "makefloat3",
                "texture1": tex_names[0],
                "texture2": tex_names[1],
                "texture3": tex_names[2],
            }
        except Exception as error:
            return _warn_unsupported(
                node, f"curve evaluation failed ({error}); passing through the "
                "input", vector, obj_name)
    elif node.bl_idname == "ShaderNodeTexEnvironment":
        if node.image:
            prefix = "scene.textures."
            try:
                filepath = ImageExporter.export_cycles_node_reader(node.image)
            except OSError as error:
                LuxCoreErrorLog.add_warning(error, obj_name=obj_name)
                return MISSING_IMAGE_COLOR

            # Approximation: LuxCore imagemaps have no equirectangular or
            # mirror-ball projection, so the environment image is sampled
            # through the regular UV mapping (works for Generated/UV coords)
            LuxCoreErrorLog.add_warning(
                f'Environment Texture node "{node.name}": '
                f'"{getattr(node, "projection", "EQUIRECTANGULAR")}" projection '
                "is approximated by the UV mapping", obj_name=obj_name)

            definitions = {
                "type": "imagemap",
                "file": filepath,
                "wrap": "repeat",
                "channel": "rgb",
                "gamma": 2.2 if node.image.colorspace_settings.name == "sRGB" else 1,
                "gain": 1,
            }
            vector_input = node.inputs.get("Vector")
            if vector_input is not None:
                definitions.update(_vector_mapping_defs(
                    vector_input, True, False, props, material, obj_name,
                    group_node_stack))
        else:
            return MISSING_IMAGE_COLOR
    elif node.bl_idname == "ShaderNodeWavelength":
        prefix = "scene.textures."

        # Approximation: the wavelength (nm) is remapped from [380, 780] to
        # [0, 1] and looked up in a coarse piecewise sRGB spectrum table
        # (Bruton-style), ignoring the intensity rolloff at the range ends
        wl_socket = node.inputs.get("Wavelength")
        wl = _socket(wl_socket, props, material, obj_name, group_node_stack) \
            if wl_socket is not None else 550.0

        amount = _tex_helper(props, luxcore_name + "_wl_remap", {
            "type": "remap",
            "value": wl,
            "sourcemin": 380.0,
            "sourcemax": 780.0,
            "targetmin": 0.0,
            "targetmax": 1.0,
        })

        # (wavelength, linear sRGB) samples, piecewise approximation
        spectrum = [
            (380.0, [0.0, 0.0, 1.0]),
            (400.0, [0.67, 0.0, 1.0]),
            (440.0, [0.0, 0.0, 1.0]),
            (460.0, [0.0, 0.4, 1.0]),
            (490.0, [0.0, 1.0, 1.0]),
            (510.0, [0.0, 1.0, 0.0]),
            (540.0, [0.43, 1.0, 0.0]),
            (580.0, [1.0, 1.0, 0.0]),
            (610.0, [1.0, 0.54, 0.0]),
            (645.0, [1.0, 0.0, 0.0]),
            (780.0, [1.0, 0.0, 0.0]),
        ]
        definitions = {
            "type": "band",
            "amount": amount,
            "offsets": len(spectrum),
            "interpolation": "linear",
        }
        for i, (wl_value, rgb) in enumerate(spectrum):
            definitions[f"offset{i}"] = (wl_value - 380.0) / 400.0
            definitions[f"value{i}"] = rgb
    elif node.bl_idname == "ShaderNodeClamp":
        # Legacy clamp node (removed in Blender 4.0 where it is upgraded to
        # Map Range); maps exactly onto the LuxCore clamp texture
        prefix = "scene.textures."
        definitions = {
            "type": "clamp",
            "texture": _socket(node.inputs["Value"], props, material, obj_name,
                               group_node_stack),
            "min": _socket(node.inputs["Min"], props, material, obj_name,
                           group_node_stack),
            "max": _socket(node.inputs["Max"], props, material, obj_name,
                           group_node_stack),
        }
    elif node.bl_idname == "ShaderNodeVectorRotate":
        vector = _socket(node.inputs["Vector"], props, material, obj_name,
                         group_node_stack)
        center = _socket(node.inputs["Center"], props, material, obj_name,
                         group_node_stack)
        rtype = getattr(node, "rotation_type", "AXIS_ANGLE")

        # LuxCore has no vector-rotate texture, but a rotation about a
        # constant axis/euler is just a constant 3x3 matrix, which composes
        # out of splitfloat3/scale/add/makefloat3 (see _const_mat_mul_vec).
        rot_mat = None
        if rtype == "AXIS_ANGLE":
            axis = _socket(node.inputs["Axis"], props, material, obj_name,
                           group_node_stack)
            angle = _socket(node.inputs["Angle"], props, material, obj_name,
                            group_node_stack)
            if not _is_textured(axis) and not _is_textured(angle):
                try:
                    rot_mat = Matrix.Rotation(
                        angle, 3, Vector(list(axis)[:3]).normalized())
                except (ValueError, TypeError):
                    rot_mat = Matrix.Identity(3)
        elif rtype in {"X_AXIS", "Y_AXIS", "Z_AXIS"}:
            angle = _socket(node.inputs["Angle"], props, material, obj_name,
                            group_node_stack)
            if not _is_textured(angle):
                rot_mat = Matrix.Rotation(angle, 3, rtype[0])
        else:  # EULER
            rot = _socket(node.inputs["Rotation"], props, material, obj_name,
                          group_node_stack)
            if not _is_textured(rot):
                rot_mat = Euler(list(rot)[:3]).to_matrix()

        if rot_mat is None:
            return _warn_unsupported(
                node, "texture-driven axis/angle/rotation inputs are not "
                "supported; passing through the vector", vector, obj_name)

        if getattr(node, "invert", False):
            rot_mat = rot_mat.transposed()

        # v' = R (v - center) + center
        shifted = vector if _is_zero(center) else _tex_binary(
            "subtract", vector, center, luxcore_name + "_sh", props)
        rotated = _const_mat_mul_vec([list(r) for r in rot_mat], shifted,
                                     luxcore_name + "_rot", props)
        if _is_zero(center):
            return rotated
        return _tex_binary("add", rotated, center,
                           luxcore_name + "_rotc", props)
    elif node.bl_idname == "ShaderNodeVectorTransform":
        vector = _socket(node.inputs["Vector"], props, material, obj_name,
                         group_node_stack)
        cfrom, cto = node.convert_from, node.convert_to
        if cfrom == cto:
            return vector

        # The from->to matrix is constant per material instance, so the
        # transform composes out of the same linear-map helpers as
        # VectorRotate. Note: for dupli/instanced objects the base object's
        # matrix is used (per-instance object spaces are not expressible).
        mat = _vtransform_matrix(cfrom, cto, obj_name)
        if mat is None:
            return _warn_unsupported(
                node, f"cannot resolve the {cfrom.lower()} -> {cto.lower()} "
                "transform (missing object/camera); passing through the "
                "vector", vector, obj_name)

        vtype = getattr(node, "vector_type", "VECTOR")
        if vtype == "NORMAL":
            # Normals transform by the inverse-transpose
            lin = mat.inverted_safe().transposed().to_3x3()
            trans = None
        else:
            lin = mat.to_3x3()
            trans = [mat[0][3], mat[1][3], mat[2][3]] if vtype == "POINT" else None

        transformed = _const_mat_mul_vec([list(r) for r in lin], vector,
                                         luxcore_name + "_xf", props)
        if trans is not None and any(t != 0 for t in trans):
            return _tex_binary("add", transformed, trans,
                               luxcore_name + "_xft", props)
        return transformed
    elif node.bl_idname == "ShaderNodeBsdfHair":
        # Legacy Cycles hair BSDF (pre-Principled). Map onto the Marschner
        # "hairmat" like ShaderNodeBsdfHairPrincipled; the Reflection/
        # Transmission lobe split cannot be expressed.
        prefix = "scene.materials."
        component = getattr(node, "component", "Reflection")
        _warn_unsupported(
            node, f"legacy Hair BSDF approximated by hairmat (the "
            f"'{component}' lobe weighting is not separable)", None, obj_name)

        offset_sock = node.inputs.get("Offset")
        offset = _socket(offset_sock, props, material, obj_name,
                         group_node_stack) if offset_sock is not None else 0.0
        if offset_sock is not None and offset_sock.is_linked \
                and offset != ERROR_VALUE:
            alpha = luxcore_name + "offset_to_deg"
            props.Set(utils.luxutils.create_props(
                "scene.textures." + alpha + ".", {
                    "type": "scale",
                    "texture1": offset,
                    "texture2": 57.29577951308232,
                }))
        else:
            alpha = offset * 57.29577951308232

        def _hair_sock(name, fallback):
            s = node.inputs.get(name)
            return _socket(s, props, material, obj_name, group_node_stack) \
                if s is not None else fallback

        definitions = {
            "type": "hairmat",
            "eta": 1.55,
            "beta_m": _hair_sock("RoughnessU", 0.1),
            "beta_n": _hair_sock("RoughnessV", 0.1),
            "alpha": alpha,
            "color": _hair_sock("Color", [0.5, 0.5, 0.5]),
        }
    elif node.bl_idname == "ShaderNodeBsdfRayPortal":
        # No portal BSDF in LuxCore; transparent is the closest match (rays
        # continue through the surface unaltered)
        prefix = "scene.materials."
        _warn_unsupported(
            node, "Ray Portal BSDF has no LuxCore equivalent; approximated "
            "by transparent", None, obj_name)
        definitions = {
            "type": "transparent",
            "kt": _socket(node.inputs.get("Color"), props, material, obj_name,
                          group_node_stack)
                  if node.inputs.get("Color") is not None else 1.0,
        }
    elif node.bl_idname == "ShaderNodeEeveeSpecular":
        # Legacy Eevee-only specular BSDF; approximate with glossy2.
        # Cycles' Specular input scales F0 by 0.08.
        prefix = "scene.materials."
        _warn_unsupported(
            node, "Eevee Specular BSDF approximated by glossy2", None,
            obj_name)

        def _eevee_sock(name, fallback):
            s = node.inputs.get(name)
            return _socket(s, props, material, obj_name, group_node_stack) \
                if s is not None else fallback

        spec = _eevee_sock("Specular", 0.0)
        ks = _tex_binary("scale", spec, 0.08, luxcore_name + "_f0", props) \
            if spec != 0 else [0.0, 0.0, 0.0]
        roughness = _eevee_sock("Roughness", 0.0)
        definitions = {
            "type": "glossy2" if roughness != 0 else "glass",
            "kd": _eevee_sock("Base Color", [0.8, 0.8, 0.8]),
            "ks": ks,
            "interiorior": 1.46,
        }
        if roughness != 0:
            definitions["uroughness"] = roughness
            definitions["vroughness"] = roughness
    elif node.bl_idname == "ShaderNodePointInfo":
        prefix = "scene.textures."
        if output_socket.name == "Position":
            _warn_unsupported(
                node, "'Position' is approximated by the hit position on the "
                "instanced sphere (the point's surface, not its center)",
                None, obj_name)
            definitions = {"type": "position"}
        elif output_socket.name == "Random":
            # Points are exported as per-point instances, so the per-object
            # normalized id doubles as a stable per-point random
            definitions = {"type": "objectidnormalized"}
        else:  # Radius
            return _warn_unsupported(
                node, "'Radius' has no per-instance texture channel in "
                "LuxCore; using 1.0", 1.0, obj_name)
    elif node.bl_idname == "ShaderNodeSqueeze":
        # Sigmoid: out = 1 / (1 + exp(-(v - c) * w))
        v = _socket(node.inputs["Value"], props, material, obj_name,
                    group_node_stack)
        w = _socket(node.inputs["Width"], props, material, obj_name,
                    group_node_stack)
        c = _socket(node.inputs["Center"], props, material, obj_name,
                    group_node_stack)
        if not any(_is_textured(t) for t in (v, w, c)):
            def _s(x):
                return x[0] if isinstance(x, (list, tuple)) else x
            try:
                return 1.0 / (1.0 + math.exp(-(_s(v) - _s(c)) * _s(w)))
            except OverflowError:
                return 0.0
        d = _tex_binary("subtract", v, c, luxcore_name + "_d", props)
        x = _tex_binary("scale", d, w, luxcore_name + "_x", props)
        nx = _tex_binary("scale", x, -1.0, luxcore_name + "_nx", props)
        e = _tex_mathfunc("exp", nx, None, luxcore_name + "_e", props)
        den = _tex_binary("add", 1.0, e, luxcore_name + "_den", props)
        return _tex_binary("divide", 1.0, den, luxcore_name, props)
    else:
        note = _UNSUPPORTED_NODE_NOTES.get(node.bl_idname)
        LuxCoreErrorLog.add_warning(
            f"Unsupported node type: {node.name}"
            + (f" ({node.bl_idname}): {note}" if note else ""),
            obj_name=obj_name)

        # TODO do this for unsupported mixRGB and math modes, too
        # Try to skip this node by looking at its internal links (the same that are used when the node is muted)
        if node.internal_links:
            links = node.internal_links[0].from_socket.links
            if links:
                link = links[0]
                print("current node", node.name, "failed, testing next node:", link.from_node.name)
                return _node(link.from_node, link.from_socket, props, material, luxcore_name, obj_name, group_node_stack)

        # Return a neutral fallback matching the output type instead of a
        # black/error result so unsupported nodes don't silently break renders
        socket_type = getattr(output_socket, "bl_idname", "")
        if socket_type == "NodeSocketShader":
            # Emit a plain grey material under the requested name
            prefix = "scene.materials."
            definitions = {
                "type": "matte",
                "kd": FALLBACK_COLOR,
            }
        elif socket_type == "NodeSocketColor":
            return list(FALLBACK_COLOR)
        elif socket_type.startswith("NodeSocketVector"):
            return list(FALLBACK_VECTOR)
        else:
            # Float/Int/Bool and everything else
            return FALLBACK_FLOAT

    if node.bl_idname in {"ShaderNodeMixRGB", "ShaderNodeMath"} and node.use_clamp:
        # Here we need to insert a helper texture *after* the current texture
        props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
        definitions = {
            "type": "clamp",
            "texture": luxcore_name,
            "min": 0,
            "max": 1,
        }
        luxcore_name = luxcore_name + "clamp"

    props.Set(utils.luxutils.create_props(prefix + luxcore_name + ".", definitions))
    return luxcore_name


def _squared_roughness_to_linear(socket, props, material, luxcore_name, obj_name, group_node):
    roughness = _socket(socket, props, material, obj_name, group_node)
    if socket.is_linked and roughness != ERROR_VALUE:
        # Implicitly create a math texture with unique name
        tex_name = luxcore_name + "roughness_converter"
        helper_prefix = "scene.textures." + tex_name + "."
        helper_defs = {
            "type": "power",
            "base": roughness,
            "exponent": 2,
        }
        props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))
        return tex_name
    else:
        return roughness ** 2


def _is_textured(value):
    return isinstance(value, str)


def _convert_to_float(color_or_texture, props):
    if _is_textured(color_or_texture):
        # This is more or less a hack because we don't have a dedicated "RGB to BW" texture
        tex_name = color_or_texture + "to_float"
        helper_prefix = "scene.textures." + tex_name + "."
        helper_defs = {
            "type": "power",
            "base": color_or_texture,
            "exponent": 1,
        }
        props.Set(utils.luxutils.create_props(helper_prefix, helper_defs))
        return tex_name
    elif isinstance(color_or_texture, list):
        return sum(color_or_texture) / len(color_or_texture)
    # Scalar constants pass through unchanged — dropping them here turned
    # e.g. MULTIPLY(attr, 50.0) into attr * 0.
    return color_or_texture


def _is_zero(value):
    if _is_textured(value):
        return False
    if isinstance(value, (list, tuple)):
        return all(v == 0 for v in value)
    return value == 0


def _volume_asymmetry(anisotropy):
    """LuxCore expects a 3-channel asymmetry; broadcast a scalar anisotropy."""
    if anisotropy is ERROR_VALUE:
        return [0, 0, 0]
    if _is_textured(anisotropy) or isinstance(anisotropy, (list, tuple)):
        return anisotropy
    return [anisotropy] * 3


def _volume(node, output_socket, props, material, name_base, obj_name,
            group_node_stack=None):
    """
    Convert a Cycles volume shader subtree into scene.volumes.* definitions.
    Returns a dict for create_props("scene.volumes.<name>.", defs), or None
    (after logging a warning) when the node cannot be converted.
    Coefficients are floats, [r, g, b] lists or texture names.
    """
    def coeff(socket_name, fallback):
        socket = node.inputs.get(socket_name)
        if socket is None:
            return fallback
        value = _socket(socket, props, material, obj_name, group_node_stack)
        return fallback if value is ERROR_VALUE else value

    if node.bl_idname == "ShaderNodeVolumeAbsorption":
        # Pure absorption maps exactly onto a LuxCore "clear" volume
        density = coeff("Density", 1.0)
        color = coeff("Color", FALLBACK_COLOR)
        return {
            "type": "clear",
            "absorption": _tex_binary("scale", color, density,
                                      name_base + "_absorption", props),
        }

    if node.bl_idname == "ShaderNodeVolumeScatter":
        density = coeff("Density", 1.0)
        color = coeff("Color", [1.0, 1.0, 1.0])
        return {
            "type": "homogeneous",
            "absorption": 0,
            # Approximation: Cycles' color times density acts as sigma_s
            "scattering": _tex_binary("scale", color, density,
                                      name_base + "_scattering", props),
            "asymmetry": _volume_asymmetry(coeff("Anisotropy", 0.0)),
        }

    if node.bl_idname == "ShaderNodeEmission":
        # Emission in the Volume socket is volume emission; a clear volume
        # carries it (LuxCore volumes have a dedicated emission channel)
        return {
            "type": "clear",
            "absorption": 0,
            "emission": _tex_binary("scale", coeff("Color", [1.0, 1.0, 1.0]),
                                    coeff("Strength", 1.0),
                                    name_base + "_emission", props),
        }

    if node.bl_idname == "ShaderNodeVolumePrincipled":
        density = coeff("Density", 1.0)
        color = coeff("Color", [1.0, 1.0, 1.0])
        for blackbody_input in ("Blackbody Tint", "Temperature"):
            blackbody_socket = node.inputs.get(blackbody_input)
            if blackbody_socket is not None and blackbody_socket.is_linked:
                LuxCoreErrorLog.add_warning(
                    f'Principled Volume node "{node.name}": blackbody emission '
                    "inputs are not supported", obj_name=obj_name)
                break

        definitions = {
            "type": "homogeneous",
            # Approximation: the absorption color is scaled by the density
            "absorption": _tex_binary("scale", coeff("Absorption Color", [0, 0, 0]),
                                      density, name_base + "_absorption", props),
            "scattering": _tex_binary("scale", color, density,
                                      name_base + "_scattering", props),
            "asymmetry": _volume_asymmetry(coeff("Anisotropy", 0.0)),
        }
        emission = _tex_binary("scale", coeff("Emission Color", [0, 0, 0]),
                               coeff("Emission Strength", 0.0),
                               name_base + "_emission", props)
        if not _is_zero(emission):
            definitions["emission"] = emission
        return definitions

    if node.bl_idname == "ShaderNodeVolumeCoefficients":
        # Coefficients are already physical sigma_a / sigma_s — a cleaner
        # mapping than the Principled color*density approximation
        definitions = {
            "type": "homogeneous",
            "absorption": coeff("Absorption Coefficients", [0.0, 0.0, 0.0]),
            "scattering": coeff("Scatter Coefficients", [0.0, 0.0, 0.0]),
            "asymmetry": _volume_asymmetry(coeff("Anisotropy", 0.0)),
        }
        ior = coeff("IOR", 1.0)
        if ior != 1.0:
            definitions["ior"] = ior
        emission = coeff("Emission Coefficients", [0.0, 0.0, 0.0])
        if not _is_zero(emission):
            definitions["emission"] = emission
        weight = coeff("Weight", 1.0)
        if weight != 1.0 and not _is_zero(weight):
            for key in ("absorption", "scattering", "emission"):
                if key in definitions and not _is_zero(definitions[key]):
                    definitions[key] = _tex_binary(
                        "scale", definitions[key], weight,
                        f"{name_base}_{key}_w", props)
        for unsupported in ("Backscatter", "Alpha", "Diameter"):
            sock = node.inputs.get(unsupported)
            if sock is not None and \
                    (sock.is_linked or sock.default_value != 0.0):
                LuxCoreErrorLog.add_warning(
                    f'Volume Coefficients node "{node.name}": "{unsupported}" '
                    "is not supported", obj_name=obj_name)
        return definitions

    if node.bl_idname in {"ShaderNodeAddShader", "ShaderNodeMixShader"}:
        # LuxCore allows only one interior volume per material, so merge the
        # children coefficient-wise. Add sums the coefficients (physically
        # correct for overlapping volumes); Mix interpolates them.
        is_add = node.bl_idname == "ShaderNodeAddShader"
        indices = (0, 1) if is_add else (1, 2)

        children = []
        for index in indices:
            link = utils_node.get_link(node.inputs[index])
            if link is None:
                continue
            child = _volume(link.from_node, link.from_socket, props, material,
                            name_base + f"_child{index}", obj_name, group_node_stack)
            if child is not None:
                children.append(child)

        if not children:
            return _warn_unsupported(
                node, "no convertible volume shader on the inputs", None, obj_name)
        if len(children) == 1:
            return children[0]

        amount = 1.0
        if not is_add:
            fac_input = node.inputs["Fac"]
            amount = _socket(fac_input, props, material, obj_name, group_node_stack)
            if fac_input.is_linked and amount is ERROR_VALUE:
                amount = 0.5

        child1, child2 = children[0], children[1]
        merged = {
            "type": "homogeneous" if "clear" not in {child1["type"], child2["type"]}
                    else "clear",
        }
        for key in ("absorption", "scattering", "emission"):
            value1 = child1.get(key, 0)
            value2 = child2.get(key, 0)
            if _is_zero(value1) and _is_zero(value2):
                continue
            if is_add:
                merged[key] = _tex_binary("add", value1, value2,
                                          f"{name_base}_{key}", props)
            else:
                merged[key] = _tex_mix(value1, value2, amount,
                                       f"{name_base}_{key}", props)

        # Approximation: asymmetry should be weighted by the scattering
        # coefficients; a plain 50/50 (respectively fac) mix is used instead
        merged["asymmetry"] = _tex_mix(child1.get("asymmetry", [0, 0, 0]),
                                       child2.get("asymmetry", [0, 0, 0]),
                                       0.5 if is_add else amount,
                                       name_base + "_asymmetry", props)
        return merged

    if node.bl_idname == "ShaderNodeGroup" and node.node_tree:
        active_output = None
        for subnode in node.node_tree.nodes:
            if subnode.bl_idname == "NodeGroupOutput" and subnode.is_active_output:
                active_output = subnode
                break

        group_input = active_output.inputs.get(output_socket.name) \
            if active_output is not None else None
        link = utils_node.get_link(group_input) if group_input is not None else None
        if link is None:
            return _warn_unsupported(
                node, "node group has no usable linked volume output", None, obj_name)

        stack = list(group_node_stack or [])
        stack.append(node)
        return _volume(link.from_node, link.from_socket, props, material,
                       name_base, obj_name, stack)

    if node.bl_idname == "NodeGroupInput" and group_node_stack:
        socket = group_node_stack[-1].inputs.get(output_socket.name)
        link = utils_node.get_link(socket) if socket is not None else None
        if link is None:
            return _warn_unsupported(
                node, "unlinked volume group input", None, obj_name)
        return _volume(link.from_node, link.from_socket, props, material,
                       name_base, obj_name, group_node_stack[:-1])

    return _warn_unsupported(
        node, "cannot be used as a volume shader", None, obj_name)
