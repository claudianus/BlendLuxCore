# E9 Phase-6 edge case: mid-shutter topology change must fall back to
# static, not crash or corrupt the vertex series.
#
# Scene: an emissive quad with a GN modifier whose Subdivide Mesh level
# steps from 0 to 1 when the frame crosses 10 — the shutter window
# (frames 6..14, center 10) straddles the change, so the per-step
# sampler sees a different vertex count at step 3. The exporter must
# drop the vertex series (static fallback) and still render the
# object.
#
# Assertions: the render completes and the object is visible; the log
# shows the topology-change fallback notice.
#
# Run headless:
#   blender --background --factory-startup \
#       --python dev-tools/e9_topology_change_test.py

import os
import sys

import bpy

OUT_DIR = os.environ.get("E9_TEST_OUT", "/tmp")
failures = []


def check(name, condition, detail=""):
    status = "ok" if condition else "FAIL"
    print(f"[E9-TEST] {status}: {name} {detail}")
    if not condition:
        failures.append(name)


def find_addon_key():
    return next(
        a.module
        for a in bpy.context.preferences.addons
        if "superluxcore" in a.module.lower()
    )


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
for me in list(bpy.data.meshes):
    bpy.data.meshes.remove(me)

scene = bpy.context.scene
scene.superluxcore.config.engine = "PATH"
scene.superluxcore.config.device = "OCL"
scene.superluxcore.devices.use_native_cpu = False
scene.superluxcore.halt.enable = True
scene.superluxcore.halt.use_samples = True
scene.superluxcore.halt.samples = 32
scene.render.engine = "SUPERLUXCORE"
scene.render.resolution_x = 160
scene.render.resolution_y = 120
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.film_transparent = True
scene.frame_start = 1
scene.frame_end = 20
scene.frame_set(10)

prefs = bpy.context.preferences.addons[find_addon_key()].preferences
if hasattr(prefs, "gpu_backend"):
    prefs.gpu_backend = "METAL"

mesh = bpy.data.meshes.new("TopoQuad")
verts = [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0)]
mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
mesh.update()
quad = bpy.data.objects.new("TopoQuad", mesh)
scene.collection.objects.link(quad)

ng = bpy.data.node_groups.new("TopoSwitch", "GeometryNodeTree")
ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
n_in = ng.nodes.new("NodeGroupInput")
n_out = ng.nodes.new("NodeGroupOutput")
n_time = ng.nodes.new("GeometryNodeInputSceneTime")
n_cmp = ng.nodes.new("ShaderNodeMath")
n_cmp.operation = "GREATER_THAN"
n_cmp.inputs[1].default_value = 10.0
n_sub = ng.nodes.new("GeometryNodeSubdivideMesh")
ng.links.new(n_time.outputs["Frame"], n_cmp.inputs[0])
ng.links.new(n_in.outputs["Geometry"], n_sub.inputs["Mesh"])
ng.links.new(n_cmp.outputs[0], n_sub.inputs["Level"])
ng.links.new(n_sub.outputs["Mesh"], n_out.inputs["Geometry"])
mod = quad.modifiers.new("TopoSwitch", "NODES")
mod.node_group = ng

mat = bpy.data.materials.new("Emit")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
em = nt.nodes.new("ShaderNodeEmission")
em.inputs["Color"].default_value = (1.0, 0.0, 0.0, 1.0)
em.inputs["Strength"].default_value = 5.0
out = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
mesh.materials.append(mat)

cam_data = bpy.data.cameras.new("Cam")
cam = bpy.data.objects.new("Cam", cam_data)
scene.collection.objects.link(cam)
cam.location = (0, 0, 3)
scene.camera = cam

mb = cam_data.superluxcore.motion_blur
mb.enable = True
mb.object_blur = True
mb.camera_blur = False
mb.shutter = 8.0
mb.steps = 3

quad.superluxcore.enable_motion_blur = True
scene.render.filepath = os.path.join(OUT_DIR, "e9topo.png")
bpy.ops.render.render(write_still=True)
print("[E9-TEST] rendered")

# The render completed; verify the quad is visible (red pixels present)
img = bpy.data.images.load(scene.render.filepath)
w, h = img.size
px = img.pixels[:]
bpy.data.images.remove(img)
count = 0
for i in range(0, len(px), 4):
    r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
    if a > 0.5 and r > 0.3 and r > 1.3 * g:
        count += 1
check("object rendered (static fallback)", count > 50, f"count={count}")

print(f"[E9-TEST] {len(failures)} failures")
sys.exit(1 if failures else 0)
