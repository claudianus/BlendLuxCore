from bl_ui.properties_render import RenderButtonsPanel
from bl_ui.properties_view_layer import ViewLayerButtonsPanel
from bpy.types import Panel
from ... import utils
from ...utils import ui as utils_ui
from ... import icons
from ...icons import icon_manager
from .sampling import calc_samples_per_pass


def _draw_full(layout, context, halt):
    """Complete halt-condition UI — used for the per-view-layer override,
    where every stop option must be editable in one place."""
    layout.active = halt.enable

    layout.prop(halt, "use_time")
    col = layout.column(align=True)
    col.active = halt.use_time
    col.prop(halt, "time")

    if halt.use_time and halt.time > 60:
        time_humanized = utils_ui.humanize_time(halt.time)

        col = layout.column(align=True)
        col.label(text=time_humanized, icon="TIME")

    layout.prop(halt, "use_samples")
    col = layout.column(align=True)
    col.active = halt.use_samples
    col.prop(halt, "samples")

    config = context.scene.superluxcore.config
    denoiser = context.scene.superluxcore.denoiser

    using_hybridbackforward = utils.using_hybridbackforward(context.scene)
    using_only_lighttracing = config.using_only_lighttracing()

    if halt.use_samples:
        samples_per_pass = calc_samples_per_pass(config)

        if config.engine == "PATH" and config.use_tiles:
            # some special warnings about tile path usage
            if config.tile.multipass_enable and halt.samples % samples_per_pass != 0:
                layout.label(text="Should be a multiple of %d" % samples_per_pass, icon=icons.WARNING)

            if denoiser.enabled and denoiser.type == "BCD":
                # BCD Denoiser needs one warmup pass plus at least one sample collecting pass
                min_samples = samples_per_pass * 2
            else:
                min_samples = samples_per_pass

            if halt.samples < min_samples:
                layout.label(text="Use at least %d samples!" % min_samples, icon=icons.WARNING)

            if not config.tile.multipass_enable and halt.samples > min_samples:
                layout.label(text="Samples halt condition overriden by disabled multipass", icon=icons.INFO)
        elif config.get_sampler() in {"SOBOL", "RANDOM", "PMJ02"} and config.using_out_of_core() or config.sampler_pattern == "CACHE_FRIENDLY":
            if halt.samples % samples_per_pass != 0:
                layout.label(text="Should be a multiple of %d" % samples_per_pass, icon=icons.WARNING)

            if denoiser.enabled and denoiser.type == "BCD":
                # BCD Denoiser needs one warmup pass plus at least one sample collecting pass
                min_samples = samples_per_pass * 2
            else:
                min_samples = samples_per_pass

            if halt.samples < min_samples:
                layout.label(text="Use at least %d samples!" % min_samples, icon=icons.WARNING)

    if using_hybridbackforward and not using_only_lighttracing:
        layout.prop(halt, "use_light_samples")
        col = layout.column(align=True)
        col.active = halt.use_light_samples
        col.prop(halt, "light_samples")

    layout.prop(halt, "use_noise_thresh")
    col = layout.column(align=True)
    if halt.use_noise_thresh:
        col.prop(halt, "noise_thresh")
        col.prop(halt, "noise_thresh_warmup")
        col.prop(halt, "noise_thresh_step")


def _draw_detail(layout, context, halt):
    """Advanced stop options — drawn under Render > Sampling > Stop
    Conditions. The primary limits (samples, noise threshold, time) are
    exposed directly in the Sampling panel, Cycles-style."""
    layout.active = halt.enable

    config = context.scene.superluxcore.config
    using_hybridbackforward = utils.using_hybridbackforward(context.scene)
    using_only_lighttracing = config.using_only_lighttracing()

    if using_hybridbackforward and not using_only_lighttracing:
        layout.prop(halt, "use_light_samples")
        col = layout.column(align=True)
        col.active = halt.use_light_samples
        col.prop(halt, "light_samples")

    if halt.use_noise_thresh:
        col = layout.column(align=True)
        col.prop(halt, "noise_thresh_warmup")
        col.prop(halt, "noise_thresh_step")


class SUPERLUXCORE_RENDER_PT_halt_conditions(Panel, RenderButtonsPanel):
    """
    Advanced stop conditions — a subpanel of Sampling. The everyday
    limits (render samples, noise threshold, time limit) are drawn
    directly in the Sampling panel.
    """

    bl_label = "Stop Conditions"
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_sampling"

    @classmethod
    def poll(cls, context):
        if context.scene.render.engine != "SUPERLUXCORE":
            return False
        # Quick Setup: hide advanced panels unless explicitly shown
        simple = context.scene.superluxcore.config.simple
        return (not simple.enabled) or simple.show_advanced

    def draw_header(self, context):
        layout = self.layout
        halt = context.scene.superluxcore.halt
        col = layout.column(align=True)
        col.prop(halt, "enable", text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        config = context.scene.superluxcore.config
        halt = context.scene.superluxcore.halt
        _draw_detail(layout, context, halt)

        layers = context.scene.view_layers
        overriding_layers = [layer for layer in layers if layer.use and layer.superluxcore.halt.enable]

        if overriding_layers:
            layout.separator()

            col = layout.column(align=True)
            row = col.row()
            split = row.split(factor=0.8)
            split.label(text="View Layers Overriding Stop Conditions:")
            op = split.operator("superluxcore.switch_space_data_context",
                                text="Show", icon="RENDERLAYERS")
            op.target = "VIEW_LAYER"

            using_hybridbackforward = utils.using_hybridbackforward(context.scene)
            using_only_lighttracing = config.using_only_lighttracing()

            for layer in overriding_layers:
                halt = layer.superluxcore.halt
                conditions = []

                if halt.use_time:
                    conditions.append("Time (%ds)" % halt.time)
                if halt.use_samples:
                    conditions.append("Samples (%d)" % halt.samples)
                if (halt.use_light_samples and using_hybridbackforward
                        and not using_only_lighttracing):
                    conditions.append("Light Path Samples (%d)" % halt.light_samples)
                if halt.use_noise_thresh:
                    conditions.append("Noise (%d)" % halt.noise_thresh)

                if conditions:
                    text = layer.name + ": " + ", ".join(conditions)
                    col.label(text=text, icon="RENDERLAYERS")
                else:
                    text = layer.name + ": No Stop Condition!"
                    col.label(text=text, icon=icons.ERROR)


class SUPERLUXCORE_RENDERLAYER_PT_halt_conditions(Panel, ViewLayerButtonsPanel):
    """
    These are the per-renderlayer halt condition settings,
    they can override the global settings and are shown in the renderlayer settings
    """

    bl_label = "Override Stop Conditions"
    bl_order = 40
    COMPAT_ENGINES = {"SUPERLUXCORE"}

    @classmethod
    def poll(cls, context):
        if context.scene.render.engine != "SUPERLUXCORE":
            return False
        # Quick Setup: hide advanced panels unless explicitly shown
        simple = context.scene.superluxcore.config.simple
        return (not simple.enabled) or simple.show_advanced

    def draw_header(self, context):
        vl = context.view_layer
        halt = vl.superluxcore.halt
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))
        col = layout.column(align=True)
        col.prop(halt, "enable", text="")

    def draw(self, context):
        vl = context.view_layer
        halt = vl.superluxcore.halt
        self.layout.use_property_split = True
        self.layout.use_property_decorate = False
        _draw_full(self.layout, context, halt)
