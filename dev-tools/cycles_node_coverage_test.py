# SPDX-License-Identifier: Apache-2.0
#
# Cycles node-reader coverage test (non-render).
#
# For each newly supported node this builds a small Cycles node tree,
# runs blendluxcore.export.cycles_node_reader.convert() on it and checks
# the emitted LuxCore properties (texture types, folded constants,
# material types).
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --python dev-tools/cycles_node_coverage_test.py
#
# Exits 0 when all assertions pass.

import os
import sys

import bpy

# In --background mode Blender quits right after --python, before the
# extension finishes registering — make the installed package importable
# directly instead.
_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

import pyluxcore  # noqa: E402
from blendluxcore.export import cycles_node_reader  # noqa: E402
from blendluxcore.utils.errorlog import LuxCoreErrorLog  # noqa: E402


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(("PASS" if ok else "FAIL") + f" {name} {detail}")


def new_tree():
    """Fresh material with an empty node tree + material output."""
    mat = bpy.data.materials.new("cov")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    return mat, nt, out


def emit_color_via(nt, out, from_socket):
    """Route an arbitrary output through an Emission Color socket."""
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(from_socket, em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return em


def convert(mat, obj_name=""):
    props = pyluxcore.Properties()
    cycles_node_reader.convert(mat, props, "covmat", obj_name=obj_name)
    return props


def prop_str(props, name):
    """Property value as a plain string ('name = v' -> 'v')."""
    try:
        raw = str(props.Get(name))
    except Exception:
        return None
    return raw.split("=", 1)[-1].strip().strip('"') if "=" in raw else raw


def all_prop_names(props):
    try:
        return [str(n) for n in props.GetAllNames()]
    except Exception:
        return []


def emitted_texture_types(props):
    return [prop_str(props, n) for n in all_prop_names(props)
            if n.endswith(".type") and "textures." in n]


def emission_value(props):
    """Emission value for diagnostics (may be a texture name)."""
    return prop_str(props, "scene.materials.covmat.emission")


def has_vec3_texture(props, expected, eps=1e-3):
    """True when some emitted property holds a vec3 matching `expected`
    — folded constants land inline in texture operands (e.g. a scale
    texture's texture2) or inside makefloat3/constfloat3 helpers."""
    for n in all_prop_names(props):
        raw = prop_str(props, n)
        if raw is None:
            continue
        try:
            vals = [float(x) for x in raw.split()]
        except ValueError:
            continue
        if len(vals) == 1:
            vals = vals * 3
        if len(vals) >= 3 and all(abs(v - t) < eps
                                  for v, t in zip(vals[:3], expected)):
            return True
    return False


def test_cross_product_const():
    mat, nt, out = new_tree()
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "CROSS_PRODUCT"
    vm.inputs[0].default_value = (1.0, 0.0, 0.0)
    vm.inputs[1].default_value = (0.0, 1.0, 0.0)
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    check("cross const fold (0,0,1)",
          has_vec3_texture(props, (0.0, 0.0, 1.0)),
          f"emission={emission_value(props)}")


def test_reflect_const():
    mat, nt, out = new_tree()
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "REFLECT"
    vm.inputs[0].default_value = (1.0, 0.0, -1.0)
    vm.inputs[1].default_value = (0.0, 0.0, 1.0)
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    check("reflect const fold (1,0,1)",
          has_vec3_texture(props, (1.0, 0.0, 1.0)),
          f"emission={emission_value(props)}")


def test_project_const():
    mat, nt, out = new_tree()
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "PROJECT"
    vm.inputs[0].default_value = (1.0, 1.0, 0.0)
    vm.inputs[1].default_value = (0.0, 1.0, 0.0)
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    check("project const fold (0,1,0)",
          has_vec3_texture(props, (0.0, 1.0, 0.0)),
          f"emission={emission_value(props)}")


def test_cross_textured():
    """Textured input -> splitfloat3/scale/subtract/makefloat3 composite."""
    mat, nt, out = new_tree()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "CROSS_PRODUCT"
    nt.links.new(tc.outputs["Normal"], vm.inputs[0])
    vm.inputs[1].default_value = (0.0, 0.0, 1.0)
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("cross textured composite",
          "makefloat3" in types and "splitfloat3" in types,
          f"types={types}")


def test_vector_rotate_z90():
    mat, nt, out = new_tree()
    vr = nt.nodes.new("ShaderNodeVectorRotate")
    vr.rotation_type = "Z_AXIS"
    vr.inputs["Angle"].default_value = 1.5707963267948966  # 90 deg
    vr.inputs["Vector"].default_value = (1.0, 0.0, 0.0)
    emit_color_via(nt, out, vr.outputs["Vector"])
    props = convert(mat)
    check("vector_rotate z90 -> (0,1,0)",
          has_vec3_texture(props, (0.0, 1.0, 0.0)),
          f"emission={emission_value(props)}")


def test_vector_rotate_axis_angle():
    mat, nt, out = new_tree()
    vr = nt.nodes.new("ShaderNodeVectorRotate")
    vr.rotation_type = "AXIS_ANGLE"
    vr.inputs["Axis"].default_value = (0.0, 0.0, 1.0)
    vr.inputs["Angle"].default_value = 3.141592653589793  # 180 deg
    vr.inputs["Vector"].default_value = (1.0, 0.0, 0.0)
    emit_color_via(nt, out, vr.outputs["Vector"])
    props = convert(mat)
    check("vector_rotate 180deg -> (-1,0,0)",
          has_vec3_texture(props, (-1.0, 0.0, 0.0)),
          f"emission={emission_value(props)}")


def test_vector_transform_identity():
    mat, nt, out = new_tree()
    vt = nt.nodes.new("ShaderNodeVectorTransform")
    vt.convert_from = "WORLD"
    vt.convert_to = "WORLD"
    vt.inputs["Vector"].default_value = (0.2, 0.3, 0.4)
    emit_color_via(nt, out, vt.outputs["Vector"])
    props = convert(mat)
    check("vector_transform identity",
          has_vec3_texture(props, (0.2, 0.3, 0.4)),
          f"emission={emission_value(props)}")


def test_vector_transform_object():
    """world->object on a translated object folds correctly for a
    constant point input."""
    from mathutils import Matrix
    obj = bpy.data.objects.new("xf_obj", None)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = Matrix.Translation((1.0, 2.0, 3.0))
    mat, nt, out = new_tree()
    vt = nt.nodes.new("ShaderNodeVectorTransform")
    vt.convert_from = "WORLD"
    vt.convert_to = "OBJECT"
    vt.vector_type = "POINT"
    vt.inputs["Vector"].default_value = (4.0, 5.0, 6.0)
    emit_color_via(nt, out, vt.outputs["Vector"])
    props = convert(mat, obj_name="xf_obj")
    check("vector_transform point fold (3,3,3)",
          has_vec3_texture(props, (3.0, 3.0, 3.0)),
          f"emission={emission_value(props)}")
    bpy.data.objects.remove(obj)


def test_bsdf_hair():
    mat, nt, out = new_tree()
    hair = nt.nodes.new("ShaderNodeBsdfHair")
    nt.links.new(hair.outputs["BSDF"], out.inputs["Surface"])
    props = convert(mat)
    mt = prop_str(props, "scene.materials.covmat.type")
    check("bsdf_hair -> hairmat", mt == "hairmat", f"type={mt}")


def test_ray_portal():
    mat, nt, out = new_tree()
    portal = nt.nodes.new("ShaderNodeBsdfRayPortal")
    nt.links.new(portal.outputs[0], out.inputs["Surface"])
    props = convert(mat)
    mt = prop_str(props, "scene.materials.covmat.type")
    check("ray_portal -> transparent", mt == "transparent", f"type={mt}")


def test_eevee_specular():
    mat, nt, out = new_tree()
    sp = nt.nodes.new("ShaderNodeEeveeSpecular")
    nt.links.new(sp.outputs["BSDF"], out.inputs["Surface"])
    props = convert(mat)
    mt = prop_str(props, "scene.materials.covmat.type")
    check("eevee_specular -> glossy2/glass", mt in {"glossy2", "glass"},
          f"type={mt}")


def test_point_info_random():
    mat, nt, out = new_tree()
    pi = nt.nodes.new("ShaderNodePointInfo")
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(pi.outputs["Random"], em.inputs["Strength"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    check("point_info random -> objectidnormalized",
          "objectidnormalized" in emitted_texture_types(props),
          f"types={emitted_texture_types(props)}")


def _math_const(operation, v1, v2=None, v3=None):
    mat, nt, out = new_tree()
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = operation
    m.inputs[0].default_value = v1
    if v2 is not None:
        m.inputs[1].default_value = v2
    if v3 is not None and len(m.inputs) > 2:
        m.inputs[2].default_value = v3
    emit_color_via(nt, out, m.outputs["Value"])
    return convert(mat)


def test_math_sqrt():
    props = _math_const("SQRT", 4.0)
    check("math sqrt(4)=2", has_vec3_texture(props, (2.0, 2.0, 2.0)),
          f"emission={emission_value(props)}")


def test_math_floor():
    props = _math_const("FLOOR", 2.7)
    check("math floor(2.7)=2", has_vec3_texture(props, (2.0, 2.0, 2.0)),
          f"emission={emission_value(props)}")


def test_math_fract():
    props = _math_const("FRACT", 2.25)
    check("math fract(2.25)=0.25",
          has_vec3_texture(props, (0.25, 0.25, 0.25)),
          f"emission={emission_value(props)}")


def test_math_minimum():
    props = _math_const("MINIMUM", 3.0, 1.5)
    check("math min(3,1.5)=1.5", has_vec3_texture(props, (1.5, 1.5, 1.5)),
          f"emission={emission_value(props)}")


def test_math_pingpong():
    # pingpong(2.5, 2) = 2 - |mod(2.5,4) - 2| = 1.5
    props = _math_const("PINGPONG", 2.5, 2.0)
    check("math pingpong(2.5,2)=1.5", has_vec3_texture(props, (1.5, 1.5, 1.5)),
          f"emission={emission_value(props)}")


def test_math_sign():
    props = _math_const("SIGN", -7.5)
    check("math sign(-7.5)=-1",
          has_vec3_texture(props, (-1.0, -1.0, -1.0)),
          f"emission={emission_value(props)}")


def test_math_degrees():
    props = _math_const("DEGREES", 3.141592653589793)
    check("math degrees(pi)=180",
          has_vec3_texture(props, (180.0, 180.0, 180.0), eps=0.01),
          f"emission={emission_value(props)}")


def test_math_wrap():
    # wrap(6.5, 1, 4) = 1 + mod(5.5, 3) = 3.5
    props = _math_const("WRAP", 6.5, 1.0, 4.0)
    check("math wrap(6.5,1,4)=3.5", has_vec3_texture(props, (3.5, 3.5, 3.5)),
          f"emission={emission_value(props)}")


def test_math_sine_const():
    props = _math_const("SINE", 1.5707963267948966)
    check("math sin(pi/2)=1 folded",
          has_vec3_texture(props, (1.0, 1.0, 1.0)),
          f"emission={emission_value(props)}")


def test_math_arctan2_const():
    props = _math_const("ARCTAN2", 1.0, 1.0)
    check("math atan2(1,1)=pi/4 folded",
          has_vec3_texture(props, (0.785398, 0.785398, 0.785398)),
          f"emission={emission_value(props)}")


def test_math_logarithm_const():
    # log2(8) = ln(8)/ln(2) = 3
    props = _math_const("LOGARITHM", 8.0, 2.0)
    check("math log2(8)=3 folded", has_vec3_texture(props, (3.0, 3.0, 3.0)),
          f"emission={emission_value(props)}")


def test_math_sine_textured():
    """Textured input -> mathfunc texture is emitted."""
    mat, nt, out = new_tree()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = "SINE"
    nt.links.new(tc.outputs["Generated"], m.inputs[0])
    emit_color_via(nt, out, m.outputs["Value"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("math sine textured -> mathfunc", "mathfunc" in types,
          f"types={types}")


def test_vmath_sine_const():
    mat, nt, out = new_tree()
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "SINE"
    vm.inputs[0].default_value = (1.5707963267948966, 0.0, 0.0)
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    check("vmath sin const fold (1,0,0)",
          has_vec3_texture(props, (1.0, 0.0, 0.0)),
          f"emission={emission_value(props)}")


def test_vmath_sine_textured():
    mat, nt, out = new_tree()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "SINE"
    nt.links.new(tc.outputs["Normal"], vm.inputs[0])
    emit_color_via(nt, out, vm.outputs["Vector"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("vmath sine textured -> mathfunc x3 + makefloat3",
          types.count("mathfunc") == 3 and "makefloat3" in types,
          f"types={types}")


def test_squeeze_const():
    mat, nt, out = new_tree()
    sq = nt.nodes.new("ShaderNodeSqueeze")
    sq.inputs["Value"].default_value = 0.0
    sq.inputs["Width"].default_value = 1.0
    sq.inputs["Center"].default_value = 0.0
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(sq.outputs[0], em.inputs["Strength"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    # sigmoid(0) = 0.5 — emission strength is scalar, check any prop = 0.5
    check("squeeze(0)=0.5 folded", has_vec3_texture(props, (0.5, 0.5, 0.5)),
          f"emission={emission_value(props)}")


def test_squeeze_textured():
    mat, nt, out = new_tree()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sq = nt.nodes.new("ShaderNodeSqueeze")
    nt.links.new(tc.outputs["Generated"], sq.inputs["Value"])
    sq.inputs["Width"].default_value = 2.0
    sq.inputs["Center"].default_value = 0.5
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(sq.outputs[0], em.inputs["Strength"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("squeeze textured -> mathfunc exp chain", "mathfunc" in types,
          f"types={types}")


def test_math_sinh_const():
    props = _math_const("SINH", 0.0)
    check("math sinh(0)=0 folded", has_vec3_texture(props, (0.0, 0.0, 0.0)),
          f"emission={emission_value(props)}")


def test_math_floormod_const():
    props = _math_const("FLOORED_MODULO", -0.5, 1.0)
    check("math floored_modulo(-0.5,1)=0.5 folded",
          has_vec3_texture(props, (0.5, 0.5, 0.5)),
          f"emission={emission_value(props)}")


def test_math_invsqrt_const():
    props = _math_const("INVERSE_SQRT", 0.25)
    check("math invsqrt(0.25)=2 folded",
          has_vec3_texture(props, (2.0, 2.0, 2.0)),
          f"emission={emission_value(props)}")


def test_math_smooth_min_const():
    # smin(0, 0.4, k=0.5): h=clamp(0.5+0.5*0.4/0.5)=0.9; mix(0.4,0,0.9)=0.04;
    # corr=0.5*0.9*0.1=0.045 -> -0.005
    props = _math_const("SMOOTH_MIN", 0.0, 0.4, 0.5)
    check("math smooth_min(0,0.4,0.5)=-0.005 folded",
          has_vec3_texture(props, (-0.005, -0.005, -0.005), eps=0.01),
          f"emission={emission_value(props)}")


def test_math_floormod_textured():
    mat, nt, out = new_tree()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    m = nt.nodes.new("ShaderNodeMath")
    m.operation = "FLOORED_MODULO"
    nt.links.new(tc.outputs["Generated"], m.inputs[0])
    m.inputs[1].default_value = 0.5
    emit_color_via(nt, out, m.outputs["Value"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("math floored_modulo textured -> mathfunc", "mathfunc" in types,
          f"types={types}")


def _gabor_prop(props, suffix):
    for n in all_prop_names(props):
        if n.endswith(".type") and "textures." in n and \
                prop_str(props, n) == "gabornoise":
            return prop_str(props, n[:-5] + "." + suffix)
    return None


def test_gabor_value_output():
    mat, nt, out = new_tree()
    gabor = nt.nodes.new("ShaderNodeTexGabor")
    gabor.inputs["Frequency"].default_value = 5.0
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(gabor.outputs["Value"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    types = emitted_texture_types(props)
    check("gabor value -> gabornoise texture", "gabornoise" in types,
          f"types={types}")
    check("gabor output=value", _gabor_prop(props, "output") == "value",
          f"output={_gabor_prop(props, 'output')}")
    check("gabor frequency=5", _gabor_prop(props, "frequency") == "5",
          f"freq={_gabor_prop(props, 'frequency')}")


def test_gabor_phase_output():
    mat, nt, out = new_tree()
    gabor = nt.nodes.new("ShaderNodeTexGabor")
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(gabor.outputs["Phase"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    check("gabor phase -> output=phase",
          _gabor_prop(props, "output") == "phase",
          f"output={_gabor_prop(props, 'output')}")


def test_gabor_3d_warns():
    LuxCoreErrorLog.clear(force_ui_update=False)
    mat, nt, out = new_tree()
    gabor = nt.nodes.new("ShaderNodeTexGabor")
    gabor.gabor_type = "3D"
    em = nt.nodes.new("ShaderNodeEmission")
    nt.links.new(gabor.outputs["Value"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    props = convert(mat)
    msgs = [w.message if hasattr(w, "message") else str(w)
            for w in LuxCoreErrorLog.warnings]
    check("gabor 3D warns + still emits gabornoise",
          any("3D" in m for m in msgs) and
          "gabornoise" in emitted_texture_types(props),
          f"warns={msgs} types={emitted_texture_types(props)}")


def main():
    for fn in [v for k, v in sorted(globals().items())
               if k.startswith("test_")]:
        fn()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
    if fails:
        sys.exit(1)


main()
