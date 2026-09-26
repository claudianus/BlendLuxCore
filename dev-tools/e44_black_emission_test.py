# SPDX-License-Identifier: Apache-2.0
#
# E44: regression test — a *statically black* emission must not turn a mesh
# into a light source.
#
# Blender 5.x defaults Principled to Emission Strength=1.0 with a black
# Emission Color. The exporter used to emit a non-constant
# ``scale(strength, color)`` texture for that combination; SuperLuxCore only
# nulls literal constant-zero emissions, so every triangle of every ordinary
# material registered as a mesh light (546,123 "lights" on the ASiO Cycles
# scene — mostly zero-flux triangles that still cost BVH/light-strategy
# memory and sampling time).
#
# The exporter now folds/skips provably black emission before it reaches the
# scene properties. This test exports small scenes built purely from
# Cycles-native node trees and asserts the SuperLuxCore light count.
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --factory-startup --python dev-tools/e44_black_emission_test.py
#
# Exits 0 on PASS, 1 on FAIL.

import os
import sys

import bpy

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"[E44-TEST] {'ok' if ok else 'FAIL'}: {name} {detail}", flush=True)


def ensure_superluxcore():
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
    except TypeError:
        bpy.ops.preferences.addon_enable(module="superluxcore")
        bpy.context.scene.render.engine = "SUPERLUXCORE"


def reset_scene():
    scene = bpy.context.scene
    scene.world = None
    scene.camera = None
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.lights, bpy.data.cameras, bpy.data.worlds):
        for item in list(coll):
            coll.remove(item)
    return scene


def new_mat(name):
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    return mat, nt, out


def add_cube(scene, mat, tris_estimate=12):
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def add_camera_and_light(scene):
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (4, -4, 3)
    scene.camera = cam

    ld = bpy.data.lights.new("pt", type="POINT")
    ld.energy = 50.0
    ld.superluxcore.use_cycles_settings = True
    lamp = bpy.data.objects.new("pt", ld)
    scene.collection.objects.link(lamp)
    lamp.location = (0, 0, 3)


def export_light_count(scene):
    """Run the real exporter, return the native scene's light count."""
    import importlib
    key = next(a.module for a in bpy.context.preferences.addons
               if "superluxcore" in a.module.lower())
    export = importlib.import_module(key + ".export")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    exporter = export.Exporter()
    result = exporter.export_scene(
        depsgraph, None, None, bpy.context.view_layer)
    assert result is not None, "export_scene returned None"
    superluxcore_scene, _config_props = result
    return superluxcore_scene.GetLightCount()


def principled_material(name, emission_color, emission_strength):
    """Cycles-native Principled with the given emission settings."""
    mat, nt, out = new_mat(name)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Emission Color"].default_value = emission_color
    bsdf.inputs["Emission Strength"].default_value = emission_strength
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def emission_node_material(name, color, strength):
    """Standalone ShaderNodeEmission (non-Principled)."""
    mat, nt, out = new_mat(name)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = color
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return mat


def add_shader_material(name, emission_strength):
    """Diffuse + Emission through an Add Shader node."""
    mat, nt, out = new_mat(name)
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (1.0, 0.5, 0.1, 1.0)
    em.inputs["Strength"].default_value = emission_strength
    add = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(diff.outputs["BSDF"], add.inputs[0])
    nt.links.new(em.outputs["Emission"], add.inputs[1])
    nt.links.new(add.outputs["Shader"], out.inputs["Surface"])
    return mat


def main():
    ensure_superluxcore()
    BLACK = (0.0, 0.0, 0.0, 1.0)
    RED = (1.0, 0.2, 0.05, 1.0)

    # 1) Principled, Blender 5.x defaults: strength=1.0 + black color.
    #    Must NOT create a mesh light (the scene's only light is the
    #    point lamp -> count == 1).
    scene = reset_scene()
    add_cube(scene, principled_material("m_black_default", BLACK, 1.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("principled_black_default", n == 1, f"lights={n} (expect 1)")

    # 2) Principled with real emission -> mesh light IS created
    #    (one cube = 12 triangles = 12 triangle lights + 1 point light).
    scene = reset_scene()
    add_cube(scene, principled_material("m_emit", RED, 5.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("principled_real_emission", n == 13, f"lights={n} (expect 13)")

    # 3) Principled with emission strength 0 (colored) -> no mesh light.
    scene = reset_scene()
    add_cube(scene, principled_material("m_zero_str", RED, 0.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("principled_zero_strength", n == 1, f"lights={n} (expect 1)")

    # 4) Standalone Emission node, strength 0 -> no mesh light.
    scene = reset_scene()
    add_cube(scene, emission_node_material("m_em0", RED, 0.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("emission_node_zero", n == 1, f"lights={n} (expect 1)")

    # 5) Standalone Emission node, black color -> no mesh light.
    scene = reset_scene()
    add_cube(scene, emission_node_material("m_emblack", BLACK, 10.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("emission_node_black", n == 1, f"lights={n} (expect 1)")

    # 6) Add Shader with a black emission branch -> adds nothing.
    scene = reset_scene()
    add_cube(scene, add_shader_material("m_add0", 0.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("addshader_black_emission", n == 1, f"lights={n} (expect 1)")

    # 7) Add Shader with real emission -> mesh light created.
    scene = reset_scene()
    add_cube(scene, add_shader_material("m_add1", 5.0))
    add_camera_and_light(scene)
    n = export_light_count(scene)
    check("addshader_real_emission", n == 13, f"lights={n} (expect 13)")

    failed = [name for name, ok in RESULTS if not ok]
    print(f"[E44-TEST] {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("[E44-TEST] FAILURES:", ", ".join(failed))
        sys.exit(1)
    print("[E44-TEST] PASS")


main()
