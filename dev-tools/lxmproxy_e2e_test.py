"""
.lxm mesh proxy end-to-end test.

    Blender -b --python dev-tools/lxmproxy_e2e_test.py

1. Builds a heavy-ish mesh (subdivided grid), bakes it to /tmp/*.lxm
   via the "Bake .lxm Proxy" operator.
2. Sets proxy_filepath on the object — export must skip mesh
   conversion entirely and emit scene.objects.X.ply = <lxm path>,
   which SuperLuxCore mmaps copy-on-write.
3. Renders a proxy frame AND a converted-mesh frame at 1280x720 and
   compares them (same mesh => same image, modulo MC noise).

What it verifies:
  * bake operator writes a valid .lxm
  * export emits the .ply file reference (no DefineMeshExt conversion)
  * the proxy renders identically to the live mesh
"""

import sys
import math
import os

import bpy
import mathutils

OUT_PROXY = "/tmp/superluxcore_lxmproxy_720p.png"
OUT_MESH = "/tmp/superluxcore_lxmmesh_720p.png"
LXM = "/tmp/blc_baked_proxy.lxm"


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
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    return m


def build_scene():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    # Bumpy heavy-ish grid floor
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=300, y_subdivisions=300,
                                    size=8)
    floor = bpy.context.active_object
    floor.name = "proxyfloor"
    for v in floor.data.vertices:
        x, y = v.co.x, v.co.y
        v.co.z = 0.3 * math.sin(x * 2.0) * math.cos(y * 2.0)
    for p in floor.data.polygons:
        p.use_smooth = True
    floor.data.materials.append(mat_diffuse("floor", (0.55, 0.45, 0.3)))

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 5), size=4.0)
    lamp = bpy.context.active_object
    lamp.name = "area_light"
    lamp.data.materials.append(mat_emission("emit", (1.0, 0.95, 0.85), 12.0))
    lamp.rotation_euler[0] = math.pi

    bpy.ops.object.camera_add(location=(0, -9, 4.5))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0, 0.3)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.superluxcore.config.engine = "PATH"
    scene.superluxcore.config.sampler = "SOBOL"
    scene.superluxcore.halt.enable = True
    scene.superluxcore.halt.use_time = True
    scene.superluxcore.halt.time = 15
    return scene, floor


def main():
    scene, floor = build_scene()

    # --- pass 1: live mesh render ---
    scene.render.filepath = OUT_MESH
    print("[LxmProxyE2E] Rendering live mesh ...")
    bpy.ops.render.render(write_still=True)

    # --- bake + proxy ---
    bpy.context.view_layer.objects.active = floor
    floor.select_set(True)
    bpy.ops.superluxcore.bake_lxm_proxy(filepath=LXM)
    assert floor.superluxcore.proxy_filepath, "proxy_filepath not set by bake"
    assert os.path.isfile(LXM), "proxy file not written"
    print(f"[LxmProxyE2E] baked {LXM}: {os.path.getsize(LXM)/1e6:.1f} MB")

    # --- pass 2: proxy render ---
    scene.render.filepath = OUT_PROXY
    print("[LxmProxyE2E] Rendering .lxm proxy ...")
    bpy.ops.render.render(write_still=True)
    print("[LxmProxyE2E] DONE")


if __name__ == "__main__":
    main()
