"""
Deformation motion-blur regression test for compacted submeshes.

    Blender -b --python dev-tools/motion_blur_submesh_test.py

A two-material mesh is deformed by an animated shape key while camera
motion blur is enabled. Each LuxCore submesh only carries the loops its
material's triangles use, so the per-step vertex series must be
compacted with the same map before SetMeshVertexMotion — otherwise the
vertex count mismatches and motion data is rejected or corrupted.

Renders 720p and saves /tmp/luxcore_mbtest_720p.png for inspection:
the mesh should show a visible deformation streak between shutter
steps while both material halves stay intact.
"""

import math

import bpy
import mathutils

OUT = "/tmp/luxcore_mbtest_720p.png"


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


def build_deforming_multimat():
    """Dense grid, 2 material halves, animated shape key bulge."""
    n = 60
    verts = []
    faces = []
    for y in range(n + 1):
        for x in range(n + 1):
            verts.append((x / n * 4 - 2, y / n * 4 - 2, 0.0))
    for y in range(n):
        for x in range(n):
            i = y * (n + 1) + x
            faces.append((i, i + 1, i + n + 2, i + n + 1))
    mesh = bpy.data.meshes.new("deform_mesh")
    mesh.from_pydata(verts, [], faces)

    mesh.materials.append(mat_diffuse("m_red", (0.85, 0.15, 0.1)))
    mesh.materials.append(mat_diffuse("m_blue", (0.1, 0.25, 0.85)))
    for poly, face in zip(mesh.polygons, faces):
        poly.material_index = 1 if (face[0] % (n + 1)) >= n // 2 else 0
        # smooth shading -> the weld merges loops, so the vertex-motion
        # path is exercised through the welded->loop representative map
        poly.use_smooth = True

    obj = bpy.data.objects.new("deform", mesh)
    bpy.context.collection.objects.link(obj)

    # Shape key: bulge the centre upwards — vertex deformation, so
    # transform blur cannot express it and the vertex series is used.
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="Bulge")
    for v in key.data:
        x, y = v.co.x, v.co.y
        w = math.exp(-(x * x + y * y) / 1.2)
        v.co.z = 0.9 * w
        v.co.x += 0.9 * w  # lateral sweep -> visible motion streak

    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=2)
    # linear interpolation so subframes differ (Blender 5.x slotted
    # actions: fcurves live on the channelbag, not the action)
    action = obj.data.shape_keys.animation_data.action
    for layer in action.layers:
        for strip in layer.strips:
            for cbag in strip.channelbags:
                for fc in cbag.fcurves:
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
    return obj


def main():
    scene = bpy.context.scene
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)

    obj = build_deforming_multimat()

    emit = mat_emission("emit", (1.0, 0.9, 0.75), 6.0)
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 3.5), size=3.0)
    lamp = bpy.context.active_object
    lamp.rotation_euler[0] = math.pi
    lamp.data.materials.append(emit)

    bpy.ops.mesh.primitive_plane_add(location=(0, 0, -0.6), size=20)
    bpy.context.active_object.data.materials.append(
        mat_diffuse("floor_m", (0.6, 0.6, 0.6))
    )

    bpy.ops.object.camera_add(location=(0, -5.5, 1.8))
    cam = bpy.context.active_object
    direction = mathutils.Vector((0, 0.5, 0.4)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    # Motion blur on the camera datablock; shutter straddles the frame
    cam.data.luxcore.motion_blur.enable = True
    cam.data.luxcore.motion_blur.object_blur = True
    cam.data.luxcore.motion_blur.steps = 3
    cam.data.luxcore.motion_blur.shutter = 1.0
    obj.luxcore.enable_motion_blur = True

    scene.frame_set(1)  # shape key mid-transition across the shutter

    scene.render.engine = "LUXCORE"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = OUT
    scene.render.image_settings.file_format = "PNG"

    scene.luxcore.config.engine = "PATH"
    scene.luxcore.config.sampler = "SOBOL"
    scene.luxcore.halt.enable = True
    scene.luxcore.halt.use_time = True
    scene.luxcore.halt.time = 30

    print("[MBTest] Rendering 1280x720 with vertex motion blur ...")
    bpy.ops.render.render(write_still=True)
    print("[MBTest] Saved:", OUT)


main()
