import bpy
from bpy.props import StringProperty, IntProperty
from .utils import (
    poll_camera, init_vol_node_tree, SUPERLUXCORE_OT_set_node_tree, 
    SUPERLUXCORE_MT_node_tree, show_nodetree,
)


class SUPERLUXCORE_OT_camera_new_volume_node_tree(bpy.types.Operator):
    """ Attach new node tree to camera """

    bl_idname = "superluxcore.camera_new_volume_node_tree"
    bl_label = "New"
    bl_description = "Create a volume node tree"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return poll_camera(context)

    def execute(self, context):
        name = ""
        if context.camera:
            name = context.camera.name
        name += "_Volume"

        node_tree = bpy.data.node_groups.new(name=name, type="superluxcore_volume_nodes")
        init_vol_node_tree(node_tree, default_IOR=1)

        if context.camera:
            context.camera.superluxcore.volume = node_tree

        return {"FINISHED"}


class SUPERLUXCORE_OT_camera_unlink_volume_node_tree(bpy.types.Operator):
    bl_idname = "superluxcore.camera_unlink_volume_node_tree"
    bl_label = "Unlink"
    bl_description = "Unlink this volume node tree"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return poll_camera(context)

    def execute(self, context):
        context.camera.superluxcore.volume = None
        return {"FINISHED"}


class SUPERLUXCORE_OT_camera_set_volume_node_tree(bpy.types.Operator, SUPERLUXCORE_OT_set_node_tree):
    """ Dropdown operator volume version """

    bl_idname = "superluxcore.camera_set_volume_node_tree"

    node_tree_index: IntProperty()

    @classmethod
    def poll(cls, context):
        return poll_camera(context)

    def execute(self, context):
        node_tree = bpy.data.node_groups[self.node_tree_index]
        self.set_node_tree(context.camera, context.camera.superluxcore, "volume", node_tree)
        return {"FINISHED"}


# This is a menu, not an operator
class SUPERLUXCORE_VOLUME_MT_camera_select_volume_node_tree(bpy.types.Menu, SUPERLUXCORE_MT_node_tree):
    """ Dropdown menu camera version """

    bl_idname = "SUPERLUXCORE_VOLUME_MT_camera_select_volume_node_tree"
    bl_description = "Select a volume node tree"

    @classmethod
    def poll(cls, context):
        return poll_camera(context)

    def draw(self, context):
        self.custom_draw("superluxcore_volume_nodes",
                         "superluxcore.camera_set_volume_node_tree")


class SUPERLUXCORE_OT_camera_show_volume_node_tree(bpy.types.Operator):
    bl_idname = "superluxcore.camera_show_volume_node_tree"
    bl_label = "Show"
    bl_description = "Switch to the node tree of this camera volume"

    @classmethod
    def poll(cls, context):
        return context.camera and context.camera.superluxcore.volume

    def execute(self, context):
        node_tree = context.camera.superluxcore.volume

        if show_nodetree(context, node_tree):
            return {"FINISHED"}

        self.report({"ERROR"}, "Open a node editor first")
        return {"CANCELLED"}
