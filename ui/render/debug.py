from bl_ui.properties_render import RenderButtonsPanel
from bpy.types import Panel


class SUPERLUXCORE_RENDER_PT_debug_settings(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "SuperLuxCore DEBUG Settings"
    bl_options = {'DEFAULT_CLOSED'}    
    bl_order = 998

    @classmethod
    def poll(cls, context):
        if context.scene.render.engine != "SUPERLUXCORE":
            return False
        # Quick Setup: hide advanced panels unless explicitly shown
        simple = context.scene.superluxcore.config.simple
        return (not simple.enabled) or simple.show_advanced and context.scene.superluxcore.debug.show

    def draw_header(self, context):
        self.layout.label(text="", icon="CONSOLE")

    def draw(self, context):
        layout = self.layout
        debug = context.scene.superluxcore.debug

        layout.operator("superluxcore.toggle_debug_options", text="Hide and Disable Debug Options")
        layout.prop(debug, "enabled")

        col = layout.column()
        col.active = debug.enabled
        col.prop(debug, "use_opencl_cpu")
        col.prop(debug, "print_properties")
