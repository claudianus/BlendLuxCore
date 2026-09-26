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

class LUXCORE_RENDER_PT_sampling(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_label = "Sampling"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 25
    
    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value= icon_manager.get_icon_id("logotype"))

    def draw(self, context):
        layout = self.layout

        config = context.scene.luxcore.config
        sampler = config.get_sampler()
        denoiser = context.scene.luxcore.denoiser

        layout.use_property_split = True
        layout.use_property_decorate = False
        
        # Tiled path
        if config.engine == "PATH":
            layout.prop(config, "use_tiles")
            
        if config.using_tiled_path():
            row = layout.row()
            row.label(text="Tiled path uses special sampler", icon=icons.INFO)
            
            col = layout.column(align=True)
            col.prop(config.tile, "size")
            col.prop(config.tile, "path_sampling_aa_size")

            if utils.use_two_tiled_passes(context.scene):
                layout.label(text="(Doubling amount of samples because of denoiser)")
        else:
            # Not tiled, regular sampling
            row = layout.row()
            
            if config.effective_device() == "OCL" and config.engine == "PATH":
                row.prop(config, "sampler_gpu")
            else:
                row.prop(config, "sampler")

            if sampler in ["SOBOL", "RANDOM", "PMJ02"]:
                col = layout.column()
                col.active = not config.using_out_of_core()
                col.prop(config, "sampler_pattern")
                
                if config.effective_device() == "OCL":
                    col = layout.column()
                    col.prop(config, "out_of_core")
                    if config.out_of_core:
                        col.prop(config, "out_of_core_mode")
                    if config.using_out_of_core():
                        col.prop(config, "out_of_core_supersampling")
            elif sampler == "METROPOLIS":
                if denoiser.enabled and denoiser.type == "BCD":
                    layout.label(text="Can lead to artifacts in the denoiser!", icon=icons.WARNING)

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

class LUXCORE_RENDER_PT_sampling_tiled_multipass(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_parent_id = "LUXCORE_RENDER_PT_sampling"
    bl_label = "Tile Multipass"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        simple = context.scene.luxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.luxcore.config
        return config.using_tiled_path()

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        layout.prop(config.tile, "multipass_enable", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.enabled = config.tile.multipass_enable

        col = layout.column(align=True)
        col.prop(config.tile, "multipass_convtest_threshold")
        col.prop(config.tile, "multipass_convtest_threshold_reduction")
        col.prop(config.tile, "multipass_convtest_warmup")

class LUXCORE_RENDER_PT_sampling_adaptivity(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_parent_id = "LUXCORE_RENDER_PT_sampling"
    bl_label = "Adaptive Sampling"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        simple = context.scene.luxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.luxcore.config
        return config.get_sampler() in {"SOBOL", "RANDOM", "PMJ02"} and not config.using_tiled_path()

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        
        col = layout.column(align=True)
        col.prop(config, "sobol_adaptive_strength", slider=True)

        if config.get_sampler() == "SOBOL":
            col.prop(config, "sobol_bluenoise_enable")

        if config.sobol_adaptive_strength > 0:
            col.prop(config.noise_estimation, "warmup")
            col.prop(config.noise_estimation, "step")

class LUXCORE_RENDER_PT_sampling_pixel_filtering(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_parent_id = "LUXCORE_RENDER_PT_sampling"
    bl_label = "Pixel Filtering"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        denoiser_enabled = context.scene.luxcore.denoiser.enabled
        filter_forced_disabled = utils.is_pixel_filtering_forced_disabled(context.scene, denoiser_enabled)

        if filter_forced_disabled and config.filter_enabled:
            layout.label(text="", icon=icons.INFO)

        row = layout.row()
        row.active = not filter_forced_disabled
        row.prop(config, "filter_enabled", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config
        denoiser_enabled = context.scene.luxcore.denoiser.enabled

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

class LUXCORE_RENDER_PT_sampling_advanced(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"LUXCORE"}
    bl_parent_id = "LUXCORE_RENDER_PT_sampling"
    bl_label = "Advanced"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        config = context.scene.luxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        
        # Seed settings
        row = layout.row(align=True)      
        row.active = not config.use_animated_seed
        row.prop(config, "seed")
        row.prop(config, "use_animated_seed", text="", icon="TIME", toggle=True)
        
        # Light strategy
        col = layout.column()
        if config.dls_cache.enabled:
            col.label(text="Using direct light sampling cache", icon=icons.INFO)
            col = layout.column()
            col.active = False
        
        col.prop(config, "light_strategy")

        if config.light_strategy == "RESTIR_DI":
            col.prop(config, "restir_temporal_enable")
            col.prop(config, "restir_spatial_enable")
            col.prop(config, "restir_visibility_enable")
            col.prop(config, "restir_candidates")

        col.prop(config, "restir_gi_enable")
        if config.restir_gi_enable:
            col.prop(config, "restir_gi_temporal_enable")
            col.prop(config, "restir_gi_spatial_enable")
            col.prop(config, "restir_gi_candidates")

        col.prop(config, "mnee_enable")
        if config.mnee_enable:
            col.prop(config, "mnee_maxspecular")

        col.prop(config, "guiding_enable")

        if config.engine == "PATH":
            col.prop(config, "spectral_enable")
