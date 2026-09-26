import bpy
from bpy.app.handlers import persistent
from ..export.caches import persistent_scene

@persistent
def handler(scene):
    # If material name was changed, rename the node tree, too.
    for mat in bpy.data.materials:
        if mat.library is not None:
            continue
        node_tree = mat.superluxcore.node_tree

        if node_tree and node_tree.name != mat.name:
            node_tree.name = mat.name

    persistent_scene.on_depsgraph_update(
        scene, bpy.context.evaluated_depsgraph_get()
    )
