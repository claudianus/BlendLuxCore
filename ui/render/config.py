import bpy
from ... import icons
from ...icons import icon_manager
from ... import utils
from bpy.types import Panel
from bl_ui.properties_render import RENDER_PT_context
from bl_ui.properties_render import RenderButtonsPanel


def superluxcore_render_draw(panel, context):
    layout = panel.layout
    scene = context.scene

    if scene.render.engine != "SUPERLUXCORE":
        return

    config = context.scene.superluxcore.config

    # Keep our layout flags scoped to a child column so other engines or
    # addons appending to this shared context panel are unaffected
    col = layout.column(align=True)
    col.use_property_split = True
    col.use_property_decorate = False
    col.prop(config, "engine", text="Integrator")

    if config.engine == "PATH":
        col.prop(config, "device", text="Device")

        if config.effective_device() == "OCL":
            gpu_backend = utils.get_addon_preferences(context).gpu_backend

            if gpu_backend == "OPENCL" and not utils.luxutils.is_opencl_build():
                col.label(
                    text="No OpenCL support in this SuperLuxCore version",
                    icon=icons.ERROR,
                )
            if gpu_backend == "CUDA" and not utils.luxutils.is_cuda_build():
                col.label(
                    text="No CUDA support in this SuperLuxCore version",
                    icon=icons.ERROR,
                )
            if gpu_backend == "METAL" and not utils.luxutils.is_metal_build():
                col.label(
                    text="No Metal support in this SuperLuxCore version",
                    icon=icons.ERROR,
                )
            if gpu_backend == "VULKAN" and not utils.luxutils.is_vulkan_build():
                col.label(
                    text="No Vulkan support in this SuperLuxCore version",
                    icon=icons.ERROR,
                )
    else:
        sub = col.column(align=True)
        sub.enabled = False
        sub.prop(config, "bidir_device", text="Device")

    # Onboarding helpers for users coming from Cycles
    row = layout.row(align=True)
    row.operator(
        "superluxcore.use_cycles_settings",
        icon_value=icon_manager.get_icon_id("link"),
    )
    row.operator(
        "superluxcore.render_settings_helper",
        icon_value=icon_manager.get_icon_id("help"),
    )


class SUPERLUXCORE_RENDER_PT_lightpaths(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Light Paths"
    bl_order = 20

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        # Headline SuperLuxCore transport feature: hero-wavelength
        # spectral rendering (dispersion, physical color transport)
        if config.engine == "PATH":
            layout.prop(config, "spectral_enable")


class SUPERLUXCORE_RENDER_PT_lightpaths_bounces(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_label = "Max Bounces"

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        if config.engine == "PATH":
            # Path options
            col.prop(config.path, "depth_total")

            def draw_bounce_prop(layout, name):
                row = layout.row(align=True)
                row.alert = (
                    getattr(config.path, name) > config.path.depth_total
                )
                row.prop(config.path, name)

            col = layout.column(align=True)
            draw_bounce_prop(col, "depth_diffuse")
            draw_bounce_prop(col, "depth_glossy")
            draw_bounce_prop(col, "depth_specular")
        else:
            # Bidir options
            col.prop(config, "bidir_path_maxdepth")
            col.prop(config, "bidir_light_maxdepth")


class SUPERLUXCORE_RENDER_PT_bidir_focus(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_label = "Caustic Focus"

    @classmethod
    def poll(cls, context):
        config = context.scene.superluxcore.config
        return (
            config.engine == "BIDIR"
            and context.scene.render.engine == "SUPERLUXCORE"
        )

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        layout.prop(config.path, "lighttracing_focus", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)
        col.enabled = config.path.lighttracing_focus
        col.prop(config.path, "lighttracing_focus_ratio")
        col.prop(config.path, "lighttracing_focus_radius")


class SUPERLUXCORE_RENDER_PT_add_light_tracing(RenderButtonsPanel, Panel):
    """Caustics: the single place where artists look for caustic
    rendering. Light tracing (hybrid back/forward) resolves them with
    light-side paths, MNEE connects them through specular chains."""
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Caustics"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        engine = context.scene.render.engine
        return config.engine == "PATH" and engine == "SUPERLUXCORE"

    def error(self, context):
        # GPU light tracing runs natively on the device (no CPU threads
        # needed since path.lighttracing.* replaced the CPU light pass)
        return False

    def _lt_available(self, context):
        config = context.scene.superluxcore.config
        # Tiled CPU path has no light pass; TILEPATHOCL does
        return not config.use_tiles or config.effective_device() == "OCL"

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        path = config.path
        lt_available = self._lt_available(context)

        # --- Light tracing (hybrid back/forward) ---
        # The toggle stays interactive even when LT is unavailable (tiled
        # CPU) so a stale enabled flag can still be switched off; only the
        # parameter block is gated.
        layout.prop(path, "hybridbackforward_enable")

        if not lt_available:
            layout.label(
                text="Light tracing is not supported by the tiled CPU engine",
                icon=icons.INFO,
            )

        sub = layout.column(align=True)
        sub.active = lt_available and path.hybridbackforward_enable
        if config.effective_device() == "CPU":
            sub.prop(path, "hybridbackforward_lightpartition")
        else:
            sub.prop(path, "hybridbackforward_lightpartition_opencl")
        sub.prop(path, "hybridbackforward_adaptivecaustic")
        if path.hybridbackforward_adaptivecaustic:
            sub.prop(path, "hybridbackforward_terminalglossiness")
            sub.prop(path, "hybridbackforward_connectprob")
        else:
            sub.prop(path, "hybridbackforward_glossinessthresh")

        # --- Specular chains via MNEE ---
        col = layout.column(align=True)
        col.prop(config, "mnee_enable")
        sub = col.column(align=True)
        sub.active = config.mnee_enable
        sub.prop(config, "mnee_maxspecular")
        sub.prop(config, "mnee_maxiterations")
        sub.prop(config, "mnee_seedcache")

        # --- Advanced GPU light-path controls ---
        if config.effective_device() == "OCL":
            col = layout.column(align=True)
            col.label(text="Advanced Light Paths:")
            sub = col.column(align=True)
            sub.active = path.hybridbackforward_enable
            if not config.use_tiles:
                # light-only mode needs the full-film tile engine
                # (RTPATHOCL); TILEPATHOCL runs the split population
                sub.prop(path, "lighttracing_only")
            sub.prop(path, "vertex_connection")
            if path.vertex_connection:
                sub.prop(path, "vertex_connection_connects")
                sub.prop(path, "vertex_connection_pool")
                sub2 = sub.column(align=True)
                sub2.active = path.vertex_connection_connects > 0
                sub2.prop(path, "vertex_connection_adaptive")
                sub.prop(path, "vertex_connection_merge_radius")
                sub.prop(path, "vertex_connection_reuse")
            sub.prop(path, "lighttracing_focus")
            if path.lighttracing_focus:
                sub.prop(path, "lighttracing_focus_ratio")
                sub.prop(path, "lighttracing_focus_radius")

        if self.error(context):
            layout.label(
                text='Enable "Use CPUs" in SuperLuxCore device settings',
                icon=icons.WARNING,
            )

            col = layout.column(align=True)
            col.use_property_split = False
            col.prop(
                context.scene.superluxcore.devices,
                "use_native_cpu",
                toggle=True,
                text="Fix this problem",
            )


class SUPERLUXCORE_RENDER_PT_lightpaths_strategy(RenderButtonsPanel, Panel):
    """How lights are picked for sampling — the Cycles "Light Tree"
    equivalent, plus reservoir (ReSTIR) options."""
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_label = "Light Strategy"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        return context.scene.render.engine == "SUPERLUXCORE"

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        if config.dls_cache.enabled:
            col.label(text="Using direct light sampling cache", icon=icons.INFO)
            col = layout.column(align=True)
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


class SUPERLUXCORE_RENDER_PT_lightpaths_guiding(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_label = "Path Guiding"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        return (
            config.engine == "PATH"
            and context.scene.render.engine == "SUPERLUXCORE"
        )

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        layout.prop(config, "guiding_enable", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = config.guiding_enable

        col = layout.column(align=True)
        # Optional warm-start table; empty trains inline (GPU engines
        # refine it through the record drain loop)
        col.prop(config, "guiding_tablefile")
        col.prop(config, "guiding_savetable")
        col.prop(config, "guiding_strength")
        # RIS product guiding: resample K mixture candidates against
        # f*cos*Lhat (0 = plain one-sample mixture)
        col.prop(config, "guiding_ris_k")

        # P5 gates: which bounces the guide may steer
        box = layout.box()
        box.label(text="Bounce Gates")
        col = box.column(align=True)
        col.prop(config, "guiding_min_depth")
        col.prop(config, "guiding_glossy_threshold")
        col.prop(config, "guiding_diffuse")

        # P5 tree/fit knobs: field granularity and training cadence
        box = layout.box()
        box.label(text="Field")
        col = box.column(align=True)
        col.prop(config, "guiding_warmup")
        col.prop(config, "guiding_components")
        col.prop(config, "guiding_split")
        col.prop(config, "guiding_max_depth")
        col.prop(config, "guiding_max_leaves")
        col.prop(config, "guiding_swaprecords")
        col.prop(config, "guiding_freeze")
        col.prop(config, "guiding_debug")

        # Light portals (M5): caps the aperture-proposal share; only
        # takes effect when a mesh object is flagged "Light Portal"
        # (object properties). Works with or without guiding - the
        # adaptive share falls back to this fixed value.
        col = layout.column(align=True)
        col.prop(config, "portal_weight")


class SUPERLUXCORE_RENDER_PT_lightpaths_clamping(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_lightpaths"
    bl_label = "Clamping"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config
        layout.prop(config.path, "use_clamping", text="")

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        row = layout.row()
        row.active = config.path.use_clamping
        row.prop(config.path, "clamping")

        # Adaptive Robust Clamping controls apply to manual and
        # auto-suggested clamping alike, so they stay editable whenever a
        # clamp can engage
        col = layout.column()
        col.active = config.path.use_clamping or config.path.auto_clamping
        col.prop(config.path, "clamp_scope")
        col.prop(config.path, "clamp_adaptive")
        sig = col.column()
        sig.active = col.active and config.path.clamp_adaptive
        sig.prop(config.path, "clamp_sigma")

        if not config.path.use_clamping:
            layout.prop(config.path, "auto_clamping")

        if config.path.suggested_clamping_value == -1:
            # Optimal clamp value not yet found, need to start a render first
            if config.path.use_clamping:
                # Can't compute optimal value if clamping is enabled
                layout.label(
                    text="Render without clamping to get suggested clamp value",
                    icon=icons.INFO,
                )
            else:
                layout.label(
                    text="Start a render to get a suggested clamp value",
                    icon=icons.INFO,
                )
        else:
            # Show a button that can be used to set the optimal clamp value
            if config.path.auto_clamping and not config.path.use_clamping:
                layout.label(
                    text="Auto-clamping at %g"
                    % config.path.suggested_clamping_value,
                    icon=icons.INFO,
                )
            op_text = (
                "Set Suggested Value: %f"
                % config.path.suggested_clamping_value
            )
            layout.operator(
                "superluxcore.set_suggested_clamping_value", text=op_text
            )


def compatible_panels():
    panels = [
        "RENDER_PT_color_management",
        "RENDER_PT_color_management_curves",
    ]
    types = bpy.types
    return [getattr(types, p) for p in panels if hasattr(types, p)]


def register():
    # We append our draw function to the existing Blender render panel
    RENDER_PT_context.append(superluxcore_render_draw)
    for panel in compatible_panels():
        panel.COMPAT_ENGINES.add("SUPERLUXCORE")


def unregister():
    RENDER_PT_context.remove(superluxcore_render_draw)
    for panel in compatible_panels():
        panel.COMPAT_ENGINES.remove("SUPERLUXCORE")
