from bl_ui.properties_render import RenderButtonsPanel
from bpy.types import Panel
from ...icons import icon_manager
from ...engine.base import SuperLuxCoreRenderEngine, template_refresh_button
from ... import icons
from ...properties.denoiser import SuperLuxCoreDenoiser


class SUPERLUXCORE_RENDER_PT_denoiser(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Denoiser"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 60

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
        layout.enabled = not SuperLuxCoreRenderEngine.final_running
        col = layout.column(align=True)
        col.prop(context.scene.superluxcore.denoiser, "enabled", text="")

    def draw(self, context):
        config = context.scene.superluxcore.config
        denoiser = context.scene.superluxcore.denoiser
        
        layout = self.layout

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = denoiser.enabled

        sub = layout.column(align=True)
        # The user should not be able to request a refresh when denoiser is disabled
        sub.enabled = denoiser.enabled
        template_refresh_button(SuperLuxCoreDenoiser.refresh, "superluxcore.request_denoiser_refresh",
                                sub, "Running denoiser...")

        col = layout.column(align=True)
        col.prop(denoiser, "type", expand=False)
        col.enabled = denoiser.enabled and not SuperLuxCoreRenderEngine.final_running

        if denoiser.enabled and denoiser.type == "BCD":
            if config.get_sampler() == "METROPOLIS" and not config.use_tiles:
                layout.label(text="Metropolis sampler can lead to artifacts!", icon=icons.WARNING)

        if denoiser.type == "BCD":
            sub = layout.column(align=True)
            # The user should be able to adjust settings even when denoiser is disabled            
            sub.prop(denoiser, "filter_spikes")
            sub = layout.column(align=True)
            sub.prop(denoiser, "hist_dist_thresh")
            sub = layout.column(align=True)
            sub.prop(denoiser, "search_window_radius")
        elif denoiser.type == "OIDN":
            sub = layout.column(align=False)
            sub.prop(denoiser, "oidn_mode")
            if denoiser.oidn_mode == "COMPONENTS":
                sub.prop(denoiser, "oidn_demodulate")
                sub.prop(denoiser, "oidn_denoise_emission")
                sub.prop(denoiser, "oidn_firefly_sigma")
            sub.prop(denoiser, "max_memory_MB")
            sub.prop(denoiser, "albedo_specular_passthrough_mode")
            sub.prop(denoiser, "prefilter_AOVs")


class SUPERLUXCORE_RENDER_PT_denoiser_temporal(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Temporal Accumulation"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_denoiser"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == "SUPERLUXCORE"

    def draw_header(self, context):
        layout = self.layout
        layout.prop(context.scene.superluxcore.denoiser, "temporal_enabled", text="")

    def draw(self, context):
        config = context.scene.superluxcore.config
        denoiser = context.scene.superluxcore.denoiser

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = denoiser.temporal_enabled and not SuperLuxCoreRenderEngine.final_running

        if config.engine == "BIDIR":
            layout.label(text="Not supported by the Bidir engine", icon=icons.WARNING)
        if not config.use_animated_seed:
            layout.label(text="Enable animated seed for frame-independent noise",
                         icon=icons.INFO)

        col = layout.column(align=True)
        col.prop(denoiser, "temporal_history")
        col.prop(denoiser, "temporal_clip_sigma")
        col.prop(denoiser, "temporal_depth_threshold")
        col.prop(denoiser, "temporal_normal_threshold")
        col.prop(denoiser, "temporal_statedir")


class SUPERLUXCORE_RENDER_PT_denoiser_bcd_advanced(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Advanced"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_denoiser"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        denoiser = context.scene.superluxcore.denoiser
        return context.scene.render.engine == "SUPERLUXCORE" and denoiser.type == "BCD"

    def draw(self, context):
        denoiser = context.scene.superluxcore.denoiser
        
        layout = self.layout
        layout.enabled = denoiser.enabled and not SuperLuxCoreRenderEngine.final_running

        layout.use_property_split = True
        layout.use_property_decorate = False
        
        layout.prop(denoiser, "scales")
        layout.prop(denoiser, "patch_radius")


