from bpy.utils import register_class, unregister_class
from . import clear, heterogeneous, homogeneous, output, tree
import nodeitems_utils
from .tree import superluxcore_node_categories_volume

classes = [
    clear.SuperLuxCoreNodeVolClear,
    heterogeneous.SuperLuxCoreNodeVolHeterogeneous,
    homogeneous.SuperLuxCoreNodeVolHomogeneous,
    output.SuperLuxCoreNodeVolOutput,
    tree.SuperLuxCoreVolumeNodeTree,
]

def register():
    nodeitems_utils.register_node_categories("SUPERLUXCORE_VOLUME_TREE", superluxcore_node_categories_volume)

    for cls in classes:
        register_class(cls)

def unregister():
    nodeitems_utils.unregister_node_categories("SUPERLUXCORE_VOLUME_TREE")

    for cls in classes:
        unregister_class(cls)
