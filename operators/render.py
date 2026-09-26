import bpy
from ..properties.denoiser import SuperLuxCoreDenoiser
from ..properties.display import SuperLuxCoreDisplaySettings


class SUPERLUXCORE_OT_request_denoiser_refresh(bpy.types.Operator):
    bl_idname = "superluxcore.request_denoiser_refresh"
    bl_label = "Refresh Denoiser"
    bl_description = "Update the denoised image (takes a few seconds to minutes, progress is shown in the status bar)"

    def execute(self, context):
        SuperLuxCoreDenoiser.refresh = True
        return {"FINISHED"}


class SUPERLUXCORE_OT_request_display_refresh(bpy.types.Operator):
    bl_idname = "superluxcore.request_display_refresh"
    bl_label = "Refresh Image"
    bl_description = "Update the rendered image"

    def execute(self, context):
        SuperLuxCoreDisplaySettings.refresh = True
        return {"FINISHED"}


class SUPERLUXCORE_OT_toggle_pause(bpy.types.Operator):
    bl_idname = "superluxcore.toggle_pause"
    bl_label = ""
    bl_description = "Pause/Resume render"

    def execute(self, context):
        SuperLuxCoreDisplaySettings.paused = not SuperLuxCoreDisplaySettings.paused
        return {"FINISHED"}


class SUPERLUXCORE_OT_stop_render(bpy.types.Operator):
    bl_idname = "superluxcore.stop_render"
    bl_label = "Stop the render?"
    bl_description = "Stop the render and run compositing"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        SuperLuxCoreDisplaySettings.stop_requested = True
        return {"FINISHED"}
