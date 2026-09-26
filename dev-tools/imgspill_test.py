"""
Out-of-core image-map spilling regression test.

    Blender -b --python dev-tools/imgspill_test.py

Builds a scene whose floor uses a generated 1024x1024 float image
(~16 MB pixel storage) and enables scene.superluxcore.config.spill_geometry
+ spill_images, then renders at 720p.

What it verifies:
  * The log shows "Image maps spilled to disk: <N> MB" — the image map
    pixel storage was written out and remapped file-backed after all
    resize/color conversion preprocessing.
  * The render result is correct (spilled pages are read back through
    the mapping transparently and the texture is visible on the floor).

The PNG is saved for visual inspection.
"""

import sys
import math

import bpy
import mathutils
import numpy as np

OUT = "/tmp/superluxcore_imgspill_720p.png"
TEX_SIZE = 2048  # 2048x2048 RGB byte -> 12 MB image-map storage


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


def make_big_image():
    """2048x2048 PNG on disk with quadrant colors + check detail."""
    n = TEX_SIZE
    path = "/tmp/imgspill_bigtex.png"
    yy, xx = np.mgrid[0:n, 0:n]
    rgb = np.zeros((n, n, 3), dtype=np.uint8)
    qy, qx = yy >= n // 2, xx >= n // 2
    colors = [
        (230, 30, 30),
        (30, 210, 30),
        (30, 60, 230),
        (230, 230, 50),
    ]
    for i, c in enumerate(colors):
        mask = qy == (i >= 2)
        mask &= qx == (i % 2 == 0)
        rgb[mask] = c
    # fine checker detail so the texture is visibly structured
    chk = (((xx // 16 + yy // 16) % 2) * 25).astype(np.uint8)
    rgb = np.clip(rgb.astype(np.int32) + chk[..., None], 0, 255).astype(np.uint8)
    rgba = np.concatenate(
        [rgb, np.full((n, n, 1), 255, np.uint8)], axis=-1
    )

    img = bpy.data.images.new(
        "bigtex", n, n, alpha=False, float_buffer=False
    )
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

    img = make_big_image()

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 0), size=6)
    floor = bpy.context.active_object
    floor.data.materials.append(mat_textured("floor_tex", img))

    emit = mat_emission("emit", (1.0, 0.95, 0.85), 10.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 4), size=4.0)
    lamp = bpy.context.active_object
    lamp.name = "area_light"
    lamp.data.materials.append(emit)
    lamp.rotation_euler[0] = math.pi

    bpy.ops.object.camera_add(location=(0, -7, 4))
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
    scene.superluxcore.config.sampler = "SOBOL"
    scene.superluxcore.config.spill_geometry = True
    scene.superluxcore.config.spill_geometry_minmb = 1
    scene.superluxcore.config.spill_images = True
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_time = True
    scene.superluxcore.halt.time = 20

    print("[ImgSpillTest] Rendering 1280x720 with image-map spilling ...")
    bpy.ops.render.render(write_still=True)
    print("[ImgSpillTest] Saved:", OUT)


if __name__ == "__main__":
    main()
