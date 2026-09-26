"""
Adaptive Robust Clamping visual verification through the real addon path.

    Blender -b --python dev-tools/clamp_visual_test.py

Builds a dark studio showcase (glossy floor + glass objects + a tiny
intense emitter) that produces BOTH indirect-path fireflies and
legitimate extreme brightness (emitter itself, specular glints, caustic
patches). Renders 720p twice - unclamped vs ARC (scope=indirect,
adaptive margin) - with the AgX Punchy view transform, and saves a
side-by-side PNG pair for visual inspection.
"""

import sys
import math
import numpy as np

import bpy
import mathutils

OUT_OFF = "/tmp/arc_off_720p.png"
OUT_ARC = "/tmp/arc_on_720p.png"
OUT_SIDE = "/tmp/arc_side_by_side.png"


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


def mat_glossy(name, color, roughness):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    gl = nt.nodes.new("ShaderNodeBsdfGlossy")
    gl.inputs["Color"].default_value = (*color, 1.0)
    gl.inputs["Roughness"].default_value = roughness
    nt.links.new(gl.outputs["BSDF"], out.inputs["Surface"])
    return m


def mat_glass(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    gl = nt.nodes.new("ShaderNodeBsdfGlass")
    gl.inputs["IOR"].default_value = 1.5
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


def build_scene():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    wall = mat_diffuse("wall", (0.035, 0.035, 0.04))
    floor = mat_glossy("floor", (0.08, 0.07, 0.06), 0.12)
    accent = mat_glossy("accent", (0.9, 0.5, 0.15), 0.05)
    emit_hot = mat_emission("hot", (1.0, 0.85, 0.6), 250.0)
    emit_soft = mat_emission("soft", (0.7, 0.8, 1.0), 6.0)
    glass = mat_glass("glass")

    # Dark room
    cube("floor", (0, 0, -0.05), (3, 3, 0.05), floor)
    cube("ceiling", (0, 0, 3.05), (3, 3, 0.05), wall)
    cube("back", (0, 3.05, 1.5), (3, 0.05, 1.55), wall)
    cube("left", (-3.05, 0, 1.5), (0.05, 3, 1.55), wall)
    cube("right", (3.05, 0, 1.5), (0.05, 3, 1.55), wall)
    cube("front", (0, -3.05, 1.5), (3, 0.05, 1.55), wall)

    # Glass sphere + glass cube: refractive caustics -> indirect fireflies
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.55,
                                         location=(-0.7, 0.4, 0.55))
    s = bpy.context.active_object
    s.name = "glass_sphere"
    s.data.materials.append(glass)
    bpy.ops.object.shade_smooth()

    c = cube("glass_cube", (0.75, 0.9, 0.5), (0.45, 0.45, 0.45), glass)
    c.rotation_euler[2] = 0.5

    # Mirror-ball accent: specular glints of the hot emitter
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35,
                                         location=(0.4, -0.5, 0.35))
    a = bpy.context.active_object
    a.name = "accent_sphere"
    a.data.materials.append(accent)
    bpy.ops.object.shade_smooth()

    # Tiny intense emitter (the firefly source): small sphere near
    # ceiling over the glass sphere
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.08,
                                         location=(-0.7, 0.4, 2.4))
    hot = bpy.context.active_object
    hot.name = "hot_emitter"
    hot.data.materials.append(emit_hot)

    # Soft area light for fill (visible in frame top-right)
    bpy.ops.mesh.primitive_plane_add(location=(1.8, 1.8, 2.99), size=1.4)
    soft = bpy.context.active_object
    soft.name = "soft_light"
    soft.data.materials.append(emit_soft)
    soft.rotation_euler[0] = math.pi

    # Camera: low dramatic angle over the glossy floor
    bpy.ops.object.camera_add(location=(0.2, -2.6, 1.35))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0.0, 0.8, 0.75)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 42
    scene.camera = cam

    # Render settings
    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"

    scene.superluxcore.config.engine = "PATH"
    scene.superluxcore.config.sampler = "SOBOL"
    # Fixed sample budget so both images are directly comparable
    scene.superluxcore.config.simple.enabled = False
    halt = scene.superluxcore.halt
    halt.enable = True
    halt.use_time = False
    halt.use_samples = True
    halt.samples = 96
    halt.use_noise_thresh = False


def render_once(path, use_clamp):
    scene = bpy.context.scene
    cfg = scene.superluxcore.config.path
    cfg.use_clamping = use_clamp
    if use_clamp:
        cfg.clamping = 5.0
        cfg.clamp_scope = "INDIRECT"
        cfg.clamp_adaptive = True
        cfg.clamp_sigma = 6.0
    cfg.auto_clamping = False
    scene.render.filepath = path
    print(f"[ARC] Rendering {'ARC-indirect' if use_clamp else 'unclamped'} ...")
    bpy.ops.render.render(write_still=True)
    print("[ARC] Saved:", path)


def main():
    build_scene()
    render_once(OUT_OFF, False)
    render_once(OUT_ARC, True)

    # Side-by-side for a single-glance comparison
    off = bpy.data.images.load(OUT_OFF)
    arc = bpy.data.images.load(OUT_ARC)
    w, h = off.size
    a = np.array(off.pixels[:], dtype=np.float32).reshape(h, w, 4)
    b = np.array(arc.pixels[:], dtype=np.float32).reshape(h, w, 4)
    side = np.concatenate([a[:, : w // 2], b[:, w // 2 :]], axis=1)
    img = bpy.data.images.new("side", width=w, height=h)
    img.pixels[:] = side.reshape(-1)
    img.filepath_raw = OUT_SIDE
    img.file_format = "PNG"
    img.save()
    print("[ARC] Saved:", OUT_SIDE)


main()
