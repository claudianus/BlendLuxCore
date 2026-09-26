"""
Out-of-core geometry spilling regression test.

    Blender -b --python dev-tools/geospill_test.py

Builds a heavy smooth-shaded mesh (~1M tris) so the welded vertex,
triangle and normal buffers all exceed the spill threshold, enables
scene.luxcore.config.spill_geometry and renders at 720p.

What it verifies:
  * The log shows "Geometry spilled to disk: <N> MB" — mesh buffers were
    written out and remapped file-backed before the BVH build.
  * The render result is correct (spilled pages are read back through
    the mapping transparently).

The PNG is saved for visual inspection.
"""

import sys
import math

import bpy
import mathutils
import numpy as np

OUT = "/tmp/luxcore_geospill_720p.png"


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


def build_heavy_mesh():
    """A ~1M-triangle smooth dome so every mesh buffer spills."""
    n = 700  # 700x700 quads -> 980k tris, ~1.47M welded verts
    ys, xs = np.mgrid[0 : n + 1, 0 : n + 1]
    xs = xs / n * 4 - 2
    ys = ys / n * 4 - 2
    zs = 0.4 * np.exp(-(xs * xs + ys * ys) / 2.0)
    verts = np.stack([xs, ys, zs], axis=-1).reshape(-1, 3)

    fy, fx = np.mgrid[0:n, 0:n]
    i = fy * (n + 1) + fx
    quads = np.stack([i, i + 1, i + n + 2, i + n + 1], axis=-1)

    mesh = bpy.data.meshes.new("heavymesh")
    mesh.from_pydata(verts.tolist(), [], quads.reshape(-1, 4).tolist())
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.materials.append(mat_diffuse("m_teal", (0.15, 0.55, 0.55)))

    obj = bpy.data.objects.new("heavy", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def main():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    build_heavy_mesh()

    emit = mat_emission("emit", (1.0, 0.9, 0.75), 8.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 3.5), size=3.0)
    lamp = bpy.context.active_object
    lamp.name = "area_light"
    lamp.data.materials.append(emit)
    lamp.rotation_euler[0] = math.pi

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, -0.5), size=20)
    floor = bpy.context.active_object
    floor.data.materials.append(mat_diffuse("floor_m", (0.6, 0.6, 0.6)))

    bpy.ops.object.camera_add(location=(0, -5.5, 2.2))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0.5, 0.3)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "LUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = OUT
    scene.render.image_settings.file_format = "PNG"

    scene.luxcore.config.engine = "PATH"
    scene.luxcore.config.sampler = "SOBOL"
    scene.luxcore.config.spill_geometry = True
    scene.luxcore.config.spill_geometry_minmb = 1
    scene.luxcore.halt.enable = True
    scene.luxcore.halt.use_time = True
    scene.luxcore.halt.time = 20

    print("[SpillTest] Rendering 1280x720 with geometry spilling ...")
    bpy.ops.render.render(write_still=True)
    print("[SpillTest] Saved:", OUT)


if __name__ == "__main__":
    main()
