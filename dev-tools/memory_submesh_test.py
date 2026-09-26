"""
Submesh vertex-compaction + Blender image buffer release regression test.

    Blender -b --python dev-tools/memory_submesh_test.py

Builds one high-density mesh with 4 material slots assigned to disjoint
face regions, plus a texture, and renders at 720p.

What it verifies:
  * The "[BLC] - Submesh" log lines show each submesh carrying only its
    own loops (sum over submeshes ~= mesh loops + seam overhead, not
    slots x total loops as before).
  * The render itself stays correct (all four materials visible).
  * ImageExporter.free_blender_buffers ran without errors (log line).

The PNG is saved for visual inspection.
"""

import sys
import math

import bpy
import mathutils
import numpy as np

OUT = "/tmp/superluxcore_memtest_720p.png"


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


def build_multimat_mesh():
    """A dense grid mesh split into 4 material quadrants."""
    n = 200  # 200x200 quads -> ~80k tris, ~240k loops
    verts = []
    faces = []
    for y in range(n + 1):
        for x in range(n + 1):
            verts.append((x / n * 4 - 2, y / n * 4 - 2, 0.0))
    for y in range(n):
        for x in range(n):
            i = y * (n + 1) + x
            faces.append((i, i + 1, i + n + 2, i + n + 1))
    mesh = bpy.data.meshes.new("gridmesh")
    mesh.from_pydata(verts, [], faces)

    mats = [
        mat_diffuse("m_red", (0.8, 0.1, 0.1)),
        mat_diffuse("m_green", (0.1, 0.7, 0.1)),
        mat_diffuse("m_blue", (0.1, 0.2, 0.8)),
        mat_diffuse("m_yellow", (0.8, 0.7, 0.1)),
    ]
    for m in mats:
        mesh.materials.append(m)

    # Assign materials by quadrant
    for poly, face in zip(mesh.polygons, faces):
        cx = (face[0] % (n + 1)) >= n // 2
        cy = (face[0] // (n + 1)) >= n // 2
        poly.material_index = (1 if cx else 0) + (2 if cy else 0)

    obj = bpy.data.objects.new("grid", mesh)
    bpy.context.collection.objects.link(obj)
    # Give the grid a slight dome so shading is visible; smooth shading
    # exercises the vertex weld (flat shading keeps per-face normals and
    # legitimately cannot merge loops)
    for v in mesh.vertices:
        x, y = v.co.x, v.co.y
        v.co.z = 0.4 * math.exp(-(x * x + y * y) / 2.0)
    for poly in mesh.polygons:
        poly.use_smooth = True
    return obj


def add_textured_card():
    """A card with an image texture -> exercises image export + free."""
    image = bpy.data.images.new("checker", width=256, height=256)
    image.generated_type = "COLOR_GRID"
    image.source = "GENERATED"
    # Save to disk so it becomes file-backed (freeable)
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), "blc_memtest_checker.png")
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()

    m = bpy.data.materials.new("m_tex")
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 2.4), size=1.5)
    card = bpy.context.active_object
    card.name = "texcard"
    card.data.materials.append(m)


def main():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    obj = build_multimat_mesh()
    # Ground + light
    emit = mat_emission("emit", (1.0, 0.9, 0.75), 6.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 3.5), size=3.0)
    lamp = bpy.context.active_object
    lamp.name = "area_light"
    lamp.data.materials.append(emit)
    lamp.rotation_euler[0] = math.pi  # face down

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, -0.5), size=20)
    floor = bpy.context.active_object
    floor.data.materials.append(mat_diffuse("floor_m", (0.6, 0.6, 0.6)))

    add_textured_card()

    bpy.ops.object.camera_add(location=(0, -5.5, 2.2))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0.5, 0.3)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

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
    scene.superluxcore.halt.time = 30

    print("[MemTest] Rendering 1280x720 ...")
    bpy.ops.render.render(write_still=True)
    print("[MemTest] Saved:", OUT)


if __name__ == "__main__":
    main()
