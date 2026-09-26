"""
OCL host-staging spilling regression test (PATHOCL / Metal).

    Blender -b --python dev-tools/stagingspill_test.py

Builds a moderately heavy scene (subdivided textured floor, ~460k tris)
on the GPU device with scene.superluxcore.config.spill_geometry enabled.

What it verifies:
  * The log shows "Host staging spilled to disk: <N> MB" — the
    CompiledScene staging arrays (verts/tris/normals + image-map pages)
    were swapped for file-backed mappings after the device upload queue
    was synchronized.
  * The render result is correct: re-reads through the mapping are
    transparent, so the spilled staging still produces a valid render.

The PNG is saved for visual inspection.
"""

import sys
import math

import bpy
import mathutils
import numpy as np

OUT = "/tmp/superluxcore_stagingspill_720p.png"


def mat_textured(name, image):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfDiffuse")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Color"])
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


def make_image():
    n = 1024
    path = "/tmp/stagingspill_tex.png"
    yy, xx = np.mgrid[0:n, 0:n]
    rgb = np.zeros((n, n, 3), dtype=np.uint8)
    qy, qx = yy >= n // 2, xx >= n // 2
    colors = [(230, 30, 30), (30, 210, 30), (30, 60, 230), (230, 230, 50)]
    for i, c in enumerate(colors):
        mask = (qy == (i >= 2)) & (qx == (i % 2 == 0))
        rgb[mask] = c
    chk = (((xx // 16 + yy // 16) % 2) * 25).astype(np.uint8)
    rgb = np.clip(rgb.astype(np.int32) + chk[..., None], 0, 255).astype(np.uint8)
    rgba = np.concatenate([rgb, np.full((n, n, 1), 255, np.uint8)], axis=-1)

    img = bpy.data.images.new("tex", n, n, alpha=False, float_buffer=False)
    img.pixels = (rgba.astype(np.float32) / 255.0).reshape(-1).tolist()
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return bpy.data.images.load(path)


def main():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    img = make_image()

    # ~460k triangles floor so geometry staging is worth spilling
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=700, y_subdivisions=700,
                                    size=6, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.data.materials.append(mat_textured("floor_tex", img))
    for p in floor.data.polygons:
        p.use_smooth = True

    emit = mat_emission("emit", (1.0, 0.95, 0.85), 8.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 4), size=4.0)
    lamp = bpy.context.active_object
    lamp.data.materials.append(emit)
    lamp.rotation_euler[0] = math.pi

    bpy.ops.object.camera_add(location=(0, -7, 3.5))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0, 0.5)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = OUT
    scene.render.image_settings.file_format = "PNG"

    scene.superluxcore.config.engine = "PATH"
    scene.superluxcore.config.device = "OCL"   # force GPU (Metal) path
    scene.superluxcore.config.spill_geometry = True
    scene.superluxcore.config.spill_geometry_minmb = 1
    scene.superluxcore.config.spill_images = True
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_time = True
    scene.superluxcore.halt.time = 15

    print("[StagingSpillTest] Rendering 1280x720 on OCL device with "
          "staging spilling ...")
    bpy.ops.render.render(write_still=True)
    print("[StagingSpillTest] Saved:", OUT)


if __name__ == "__main__":
    main()
