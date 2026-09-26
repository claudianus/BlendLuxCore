from ... import icons
from ...icons import icon_manager
from ... import utils
from ...export.config import SamplingOverlap

from bpy.types import Panel
from bl_ui.properties_render import RenderButtonsPanel


def calc_samples_per_pass(config):
    if config.using_tiled_path():
        return config.tile.path_sampling_aa_size**2
    elif config.get_sampler() in {"SOBOL", "RANDOM", "PMJ02"}:
        if config.using_out_of_core():
            return int(config.out_of_core_supersampling) * SamplingOverlap.OUT_OF_CORE
        else:
            if config.sampler_pattern == "PROGRESSIVE":
                return SamplingOverlap.PROGRESSIVE
            elif config.sampler_pattern == "CACHE_FRIENDLY":
                return SamplingOverlap.CACHE_FRIENDLY
    return -1


def draw_limited_prop(layout, pg, check_prop, value_prop, label, enabled=True):
    """Cycles-style `[x] Label [value]` row aligned with property-split layouts.

    The checkbox toggles the limit; the row is greyed out entirely when the
    master switch (`enabled`, e.g. halt.enable) is off.
    """
    split = layout.split(factor=0.4, align=True)
    split.active = enabled
    row = split.row(align=True)
    row.prop(pg, check_prop, text="")
    row.label(text=label)
    sub = split.row(align=True)
    sub.active = enabled and getattr(pg, check_prop)
    sub.prop(pg, value_prop, text="")


class SUPERLUXCORE_RENDER_PT_sampling(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Sampling"
    bl_order = 10

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))

    def draw(self, context):
        layout = self.layout

        config = context.scene.superluxcore.config
        halt = context.scene.superluxcore.halt
        sampler = config.get_sampler()
        denoiser = context.scene.superluxcore.denoiser

        layout.use_property_split = True
        layout.use_property_decorate = False

        # Render length controls (Cycles-style: the numbers artists reach
        # for first). Details live in the "Stop Conditions" subpanel.
        col = layout.column(align=True)
        draw_limited_prop(col, halt, "use_samples", "samples",
                         "Render Samples", enabled=halt.enable)
        draw_limited_prop(col, halt, "use_noise_thresh", "noise_thresh",
                         "Noise Threshold", enabled=halt.enable)
        draw_limited_prop(col, halt, "use_time", "time",
                         "Time Limit (s)", enabled=halt.enable)
        if not halt.enable:
            layout.label(
                text="Renders until stopped (enable Stop Conditions below)",
                icon=icons.INFO,
            )

        # Tiled path
        if config.engine == "PATH":
            layout.prop(config, "use_tiles")

        if config.using_tiled_path():
            layout.label(text="Tiled path uses its own sampler", icon=icons.INFO)

            col = layout.column(align=True)
            col.prop(config.tile, "size")
            col.prop(config.tile, "path_sampling_aa_size")

            if utils.use_two_tiled_passes(context.scene):
                layout.label(
                    text="(Doubling amount of samples because of denoiser)",
                    icon=icons.INFO,
                )
        else:
            # Not tiled, regular sampling
            if config.effective_device() == "OCL" and config.engine == "PATH":
                layout.prop(config, "sampler_gpu")
            else:
                layout.prop(config, "sampler")

            if sampler in ["SOBOL", "RANDOM", "PMJ02"]:
                col = layout.column()
                col.active = not config.using_out_of_core()
                col.prop(config, "sampler_pattern")
            elif sampler == "METROPOLIS":
                if denoiser.enabled and denoiser.type == "BCD":
                    layout.label(
                        text="Can lead to artifacts in the denoiser!",
                        icon=icons.WARNING,
                    )

                col = layout.column(align=True)
                col.prop(config, "metropolis_largesteprate", slider=True)
                col.prop(config, "metropolis_maxconsecutivereject")
                col.prop(config, "metropolis_imagemutationrate", slider=True)

        # Samples (per pixel) per pass info
        samples_per_pass = calc_samples_per_pass(config)
        if samples_per_pass != -1:
            row = layout.row()
            row.alignment = "RIGHT"
            row.label(text=f"Samples per Pass: {samples_per_pass}")


class SUPERLUXCORE_RENDER_PT_sampling_tiled_multipass(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_sampling"
    bl_label = "Tile Multipass"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        return config.using_tiled_path()

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        layout.prop(config.tile, "multipass_enable", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.enabled = config.tile.multipass_enable

        col = layout.column(align=True)
        col.prop(config.tile, "multipass_convtest_threshold")
        col.prop(config.tile, "multipass_convtest_threshold_reduction")
        col.prop(config.tile, "multipass_convtest_warmup")


class SUPERLUXCORE_RENDER_PT_sampling_adaptivity(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_sampling"
    bl_label = "Adaptive Sampling"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        return config.get_sampler() in {"SOBOL", "RANDOM", "PMJ02"} and not config.using_tiled_path()

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(config, "sobol_adaptive_strength", slider=True)

        if config.get_sampler() == "SOBOL":
            col.prop(config, "sobol_owen_enable")
            sub = col.column(align=True)
            sub.active = config.sobol_owen_enable
            sub.prop(config, "sobol_owen_tile_enable")
            col.prop(config, "sobol_bluenoise_enable")

        if config.sobol_adaptive_strength > 0:
            if config.get_sampler() == "SOBOL":
                col.prop(config, "sobol_adaptive_moments_enable")
                sub = col.column(align=True)
                sub.active = config.sobol_adaptive_moments_enable
                sub.prop(config, "sobol_adaptive_relerr", slider=True)
            col.prop(config.noise_estimation, "warmup")
            col.prop(config.noise_estimation, "step")


class SUPERLUXCORE_RENDER_PT_sampling_pixel_filtering(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_sampling"
    bl_label = "Pixel Filtering"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        denoiser_enabled = context.scene.superluxcore.denoiser.enabled
        filter_forced_disabled = utils.is_pixel_filtering_forced_disabled(context.scene, denoiser_enabled)

        if filter_forced_disabled and config.filter_enabled:
            layout.label(text="", icon=icons.INFO)

        row = layout.row()
        row.active = not filter_forced_disabled
        row.prop(config, "filter_enabled", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        denoiser_enabled = context.scene.superluxcore.denoiser.enabled

        layout.use_property_split = True
        layout.use_property_decorate = False

        filter_forced_disabled = utils.is_pixel_filtering_forced_disabled(context.scene, denoiser_enabled)
        if filter_forced_disabled:
            layout.label(text="Filtering disabled (required by denoiser)", icon=icons.INFO)

        col = layout.column(align=True)
        col.active = config.filter_enabled and not filter_forced_disabled
        col.prop(config, "filter")

        col = layout.column(align=True)
        col.active = config.filter_enabled and not filter_forced_disabled
        col.prop(config, "filter_width")
        if config.filter == "GAUSSIAN":
            layout.prop(config, "gaussian_alpha")
        elif config.filter == "SINC":
            layout.prop(config, "sinc_tau")


class SUPERLUXCORE_RENDER_PT_sampling_advanced(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_sampling"
    bl_label = "Advanced"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        # Seed settings
        row = layout.row(align=True)
        row.active = not config.use_animated_seed
        row.prop(config, "seed")
        row.prop(config, "use_animated_seed", text="", icon="TIME", toggle=True)
