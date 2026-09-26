"""
Final-render verification through the real addon path (create_session ->
RenderConfig + kernel prefill -> RenderSession -> Start -> film).

    Blender -b --python dev-tools/render_verify_test.py

Builds a small Cornell-style scene procedurally (Cycles nodes), renders at
720p with the SuperLuxCore engine and saves a PNG for visual inspection.
"""

import sys
import math

import bpy
import mathutils

OUT = "/tmp/superluxcore_verify_720p.png"


def mat_diffuse(name, color):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    bsdf.inputs["Color"].default_value = (*color, 1.0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_emission(name, color, strength):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (*color, 1.0)
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def mat_glossy(name, color):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    gl = nt.nodes.new("ShaderNodeBsdfGlossy")
    gl.inputs["Color"].default_value = (*color, 1.0)
    gl.inputs["Roughness"].default_value = 0.15
    nt.links.new(gl.outputs["BSDF"], out.inputs["Surface"])
    return m


def cube(name, loc, scale, material):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    bpy.ops.object.transform_apply(scale=True)
    if material:
        o.data.materials.append(material)
    return o


def main():
    scene = bpy.context.scene
    # read_factory_settings(use_empty=True) would drop the extension's
    # render engine in -b mode, so build on the default scene instead.
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    white = mat_diffuse("white", (0.8, 0.8, 0.8))
    red = mat_diffuse("red", (0.7, 0.05, 0.05))
    green = mat_diffuse("green", (0.05, 0.6, 0.05))
    glossy = mat_glossy("glossy", (0.9, 0.6, 0.2))
    emit = mat_emission("emit", (1.0, 0.9, 0.7), 8.0)

    # Cornell-style box
    cube("floor", (0, 0, -0.05), (2, 2, 0.05), white)
    cube("ceiling", (0, 0, 2.05), (2, 2, 0.05), white)
    cube("back", (0, 2.05, 1), (2, 0.05, 1.05), white)
    cube("left", (-2.05, 0, 1), (0.05, 2, 1.05), red)
    cube("right", (2.05, 0, 1), (0.05, 2, 1.05), green)
    b1 = cube("box1", (-0.6, 0.8, 0.6), (0.55, 0.55, 0.55), white)
    b1.rotation_euler[2] = 0.4
    cube("box2", (0.7, 1.1, 0.35), (0.35, 0.35, 0.35), glossy)

    # Area light on ceiling
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 1.99), size=1.2)
    lamp = bpy.context.active_object
    lamp.name = "area_light"
    lamp.data.materials.append(emit)

    # Camera
    bpy.ops.object.camera_add(location=(0, -3.2, 1.0))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 1.6, 1.0)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Render settings: SuperLuxCore, 720p
    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = OUT
    scene.render.image_settings.file_format = "PNG"

    scene.superluxcore.config.engine = "PATH"
    scene.superluxcore.config.sampler = "SOBOL"
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_time = True
    # batch.halttime measures sampling time only (the engine restarts
    # the clock after kernel compilation), so this is a real budget
    scene.superluxcore.halt.time = 60

    print("[Verify] Rendering 1280x720 ...")
    bpy.ops.render.render(write_still=True)
    print("[Verify] Saved:", OUT)


main()
