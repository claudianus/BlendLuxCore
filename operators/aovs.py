import bpy
from bpy.props import IntProperty


def _lpe_list(context):
    # The LPE list lives on the UI-active view layer (the same layer the
    # AOV panel edits); the export-time layer tracking is not set here
    layer = context.window.view_layer if context.window else None
    return layer.superluxcore.aovs.lpe_list if layer else None


class SUPERLUXCORE_OT_add_lpe(bpy.types.Operator):
    bl_idname = "superluxcore.add_lpe"
    bl_label = "Add LPE"
    bl_description = "Add a light path expression output (HDR, EXR layer LPE.<name>)"
    bl_options = {"UNDO"}

    def execute(self, context):
        lpes = _lpe_list(context)
        if lpes is None:
            return {"CANCELLED"}
        entry = lpes.add()
        entry.name = "lpe%d" % len(lpes)
        return {"FINISHED"}


class SUPERLUXCORE_OT_remove_lpe(bpy.types.Operator):
    bl_idname = "superluxcore.remove_lpe"
    bl_label = "Remove LPE"
    bl_description = "Remove this light path expression"
    bl_options = {"UNDO"}

    index: IntProperty()

    def execute(self, context):
        lpes = _lpe_list(context)
        if lpes is None:
            return {"CANCELLED"}
        lpes.remove(self.index)
        return {"FINISHED"}
