from bl_ui.properties_render import RenderButtonsPanel
from bpy.types import Panel
from ... import icons
from ...icons import icon_manager

class LUXCORE_RENDER_PT_tools(Panel, RenderButtonsPanel):
    bl_label = "LuxCore Tools"
    COMPAT_ENGINES = {"LUXCORE"}
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 999

    @classmethod
    def poll(cls, context):
        if context.scene.render.engine != "LUXCORE":
            return False
        # Quick Setup: hide advanced panels unless explicitly shown
        simple = context.scene.luxcore.config.simple
        return (not simple.enabled) or simple.show_advanced

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Buttons for Network Render and Wiki
        flow = layout.grid_flow(row_major=True, columns=0, even_columns=True, even_rows=False, align=True)
        col = flow.column(align=True)
        col.operator("luxcore.start_pyluxcoretools")
        col = flow.column(align=True)
        op = col.operator("luxcore.open_website", icon=icons.URL, text="Wiki")
        op.url = "https://wiki.luxcorerender.org/BlendLuxCore_Network_Rendering"

        layout.operator("luxcore.convert_to_v23")
    
    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))

class LUXCORE_RENDER_PT_filesaver(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_label = "LuxCore Filesaver"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "LUXCORE_RENDER_PT_tools"

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        layout.prop(config, "use_filesaver", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.enabled = config.use_filesaver
        layout.label(text="Only write LuxCore scene to disk", icon=icons.INFO)

        col = layout.column(align=True)
        col.prop(config, "filesaver_format")
        col.prop(config, "filesaver_path")


class LUXCORE_RENDER_PT_external(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_label = "External Process Render"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "LUXCORE_RENDER_PT_tools"

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        layout.prop(config, "external_process", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = config.external_process

        layout.label(
            text="Renders in a detached process; Blender's scene", icon=icons.INFO
        )
        layout.label(text="memory is released while rendering.")
        layout.label(text="Result lands in the render output path.")

        if not context.scene.luxcore.halt.enable:
            layout.label(
                text="No halt condition set — render runs until the",
                icon=icons.ERROR,
            )
            layout.label(text="process is killed manually.")


class LUXCORE_RENDER_PT_geospill(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_label = "Out-of-Core Spilling"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "LUXCORE_RENDER_PT_tools"

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        layout.prop(config, "spill_geometry", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = config.spill_geometry

        layout.label(
            text="Mesh buffers are file-backed; the OS evicts cold", icon=icons.INFO
        )
        layout.label(text="pages under memory pressure.")
        layout.prop(config, "spill_geometry_minmb")
        layout.prop(config, "spill_images")


class LUXCORE_RENDER_PT_autoproxy(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_label = "Automatic Mesh Proxy"
    bl_options = {"DEFAULT_CLOSED"}
    bl_parent_id = "LUXCORE_RENDER_PT_tools"

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        layout.prop(config, "proxy_auto", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = config.proxy_auto

        layout.label(
            text="Heavy meshes are baked to .lxm files and rendered", icon=icons.INFO
        )
        layout.label(text="via memory mapping — never re-converted.")
        layout.prop(config, "proxy_auto_mintris")
        layout.prop(config, "proxy_cluster_stride")
