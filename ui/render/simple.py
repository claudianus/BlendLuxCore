"""Simplified render setup UI (Corona-style).

One top panel with the few things a beginner needs:
- A single quality slider (maps to a curated set of engine settings)
- A denoiser toggle
- A temporary "show advanced" switch

All other render panels are hidden while Quick Setup is enabled and
show_advanced is off (their poll() checks this).
"""
from ... import icons
from bpy.types import Panel
from bl_ui.properties_render import RenderButtonsPanel


def advanced_panels_visible(context):
    """Should the advanced render panels be drawn?"""
    simple = context.scene.superluxcore.config.simple
    if not simple.enabled:
        return True
    return simple.show_advanced


class SUPERLUXCORE_RENDER_PT_simple(RenderButtonsPanel, Panel):
    """Corona-style simplified setup: quality slider + few switches."""
    bl_label = "Quick Setup"
    bl_order = 0  # show first in the render tab

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == "SUPERLUXCORE"

    def draw_header(self, context):
        self.layout.prop(context.scene.superluxcore.config.simple, "enabled",
                         text="")

    def draw(self, context):
        layout = self.layout
        simple = context.scene.superluxcore.config.simple

        if not simple.enabled:
            layout.label(
                text="Enable Quick Setup for a simplified interface",
                icon=icons.INFO,
            )
            return

        # Named presets on top of the quality slider
        row = layout.row(align=True)
        row.operator("superluxcore.set_quality_preset", text="Draft").quality = 0.15
        row.operator("superluxcore.set_quality_preset", text="Standard").quality = 0.5
        row.operator("superluxcore.set_quality_preset", text="Final").quality = 0.9

        # Quality preset slider: the one control that matters
        col = layout.column(align=True)
        col.prop(simple, "quality", slider=True)
        col.label(text=quality_label(simple.quality))

        # Effective values: show exactly what a render will use, so the
        # mapping is transparent (same function as the exporter).
        m = simple.quality_map()
        col.label(text="Depth %d · Clamp %s · %dspp · Guiding %s" % (
            m["depth_total"],
            ("off" if not m["use_clamping"] else "%g" % m["clamping"]),
            m["halt_samples"],
            ("on" if m["guiding"] else "off"),
        ), icon=icons.INFO)

        # Denoiser toggle
        row = layout.row()
        row.prop(simple, "denoise")

        # Advanced unlock
        row = layout.row()
        row.prop(simple, "show_advanced")


def quality_label(value):
    if value < 0.2:
        return "Draft: interactive quality, high noise"
    if value < 0.4:
        return "Preview: quick look with noise"
    if value < 0.6:
        return "Standard: balanced for stills"
    if value < 0.8:
        return "High: clean reflections, subtle GI"
    return "Production: final frame quality"
