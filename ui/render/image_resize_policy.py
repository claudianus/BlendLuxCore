from ...icons import icon_manager
from bl_ui.properties_render import RenderButtonsPanel
from bpy.types import Panel

class SUPERLUXCORE_RENDER_PT_image_resize_policy(Panel, RenderButtonsPanel):
    bl_label = "Image Resolution"
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_devices"

    @classmethod
    def poll(cls, context):
        if context.scene.render.engine != "SUPERLUXCORE":
            return False
        # Quick Setup: hide advanced panels unless explicitly shown
        simple = context.scene.superluxcore.config.simple
        return (not simple.enabled) or simple.show_advanced

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))
        col = layout.column(align=True)
        col.prop(context.scene.superluxcore.config.image_resize_policy, "enabled", text="")

    def draw(self, context):
        resize_policy = context.scene.superluxcore.config.image_resize_policy

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = resize_policy.enabled

        layout.prop(resize_policy, "type")
        layout.prop(resize_policy, "scale")
        layout.prop(resize_policy, "min_size")
