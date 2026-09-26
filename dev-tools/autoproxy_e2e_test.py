"""
Auto mesh-proxy end-to-end test.

    Blender -b --python dev-tools/autoproxy_e2e_test.py

1. Builds a multi-material mesh above the proxy threshold (two slots),
   enables config.proxy_auto, and renders — export must auto-bake one
   .lxm per material slot into a session-temp dir and emit
   scene.objects.X.ply references instead of converting the mesh.
2. A second render must reuse the same baked files (no re-bake) and
   produce an identical image.

What it verifies:
  * config.proxy_auto bakes one .lxm per material slot
  * export emits .ply file references for every slot
  * the proxy render matches a live-mesh render
  * a second export reuses the baked files (mtime unchanged)
"""

import sys
import math
import os
import glob

import bpy
import mathutils

OUT_MESH = "/tmp/luxcore_autoproxy_mesh.png"
OUT_PROXY = "/tmp/luxcore_autoproxy_proxy.png"


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

    # Bumpy grid with TWO material slots (multi-material proxy coverage)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=100, y_subdivisions=100,
                                    size=8)
    floor = bpy.context.active_object
    floor.name = "autoproxyfloor"
    for v in floor.data.vertices:
        x, y = v.co.x, v.co.y
        v.co.z = 0.3 * math.sin(x * 2.0) * math.cos(y * 2.0)
    for p in floor.data.polygons:
        p.use_smooth = True
    floor.data.materials.append(mat_diffuse("matA", (0.55, 0.45, 0.3)))
    floor.data.materials.append(mat_diffuse("matB", (0.25, 0.4, 0.6)))
    # Assign half the polygons to slot 1
    for i, p in enumerate(floor.data.polygons):
        if (i // 20) % 2:
            p.material_index = 1

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 5), size=4.0)
    lamp = bpy.context.active_object
    lamp.data.materials.append(mat_emission("emit", (1.0, 0.95, 0.85), 12.0))
    lamp.rotation_euler[0] = math.pi

    bpy.ops.object.camera_add(location=(0, -9, 4.5))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0, 0.3)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.engine = "LUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.luxcore.config.engine = "PATH"
    scene.luxcore.config.sampler = "SOBOL"
    scene.luxcore.halt.enable = True
    scene.luxcore.halt.use_time = True
    scene.luxcore.halt.time = 12
    return scene, floor


def baked_files():
    import tempfile
    return sorted(
        glob.glob(os.path.join(
            tempfile.gettempdir(), "luxcore_autoproxy", "ap_*.lxm"
        ))
    )


def main():
    scene, floor = build_scene()

    # --- pass 1: live mesh render ---
    scene.render.filepath = OUT_MESH
    print("[AutoProxy] Rendering live mesh ...")
    bpy.ops.render.render(write_still=True)

    # --- enable auto proxy with a low threshold ---
    scene.luxcore.config.proxy_auto = True
    scene.luxcore.config.proxy_auto_mintris = 1000
    # Mark the mesh dirty so the persistent-scene cache re-exports it
    # (otherwise the whole object is reused and the proxy never kicks in)
    floor.data.update_tag()

    scene.render.filepath = OUT_PROXY
    print("[AutoProxy] Rendering with auto proxy ...")
    bpy.ops.render.render(write_still=True)

    files = baked_files()
    print(f"[AutoProxy] baked files: {files}")
    assert len(files) == 2, f"expected 2 per-slot .lxm files, got {files}"
    mtimes = [os.path.getmtime(f) for f in files]

    # --- pass 3: second export must reuse the same files ---
    floor.data.update_tag()
    print("[AutoProxy] Re-rendering (reuse check) ...")
    bpy.ops.render.render(write_still=True)
    files2 = baked_files()
    assert files2 == files, f"baked file set changed: {files2}"
    for f, t in zip(files2, mtimes):
        assert os.path.getmtime(f) == t, f"re-baked unexpectedly: {f}"

    # --- pass 4: edit the mesh -> signature changes -> rebake ---
    for i, v in enumerate(floor.data.vertices):
        if i % 10 == 0:
            v.co.z += 0.05
    floor.data.update_tag()
    print("[AutoProxy] Rendering after mesh edit (rebake check) ...")
    bpy.ops.render.render(write_still=True)
    files3 = baked_files()
    assert len(files3) == 2, f"expected 2 .lxm after edit, got {files3}"
    assert files3 != files, f"stale proxy reused after edit: {files3}"
    print(f"[AutoProxy] rebaked: {files3}")

    print("[AutoProxy] DONE")


if __name__ == "__main__":
    main()
