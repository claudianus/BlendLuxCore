from bl_ui.properties_render import RenderButtonsPanel
from bpy.types import Panel
from ... import utils
from ... import icons
from ...icons import icon_manager


def _show_openCL_device_warning(context):
    config = context.scene.superluxcore.config
    devices = context.scene.superluxcore.devices

    gpu_devices = devices.get_gpu_devices(context)
    # We don't show OpenCL CPU devices, we just need them to check if there are other devices
    cpu_devices = [
        device for device in devices.devices if device.type == "OPENCL_CPU"
    ]
    other_devices = set(devices.devices) - (
        set(gpu_devices) | set(cpu_devices)
    )

    has_gpus = any([device.enabled for device in gpu_devices])
    has_others = any([device.enabled for device in other_devices])

    return (
        config.engine == "PATH"
        and config.device == "OCL"
        and not has_gpus
        and not has_others
    )


class SUPERLUXCORE_RENDER_PT_devices(RenderButtonsPanel, Panel):
    """Performance: compute devices and memory behaviour — Cycles-style
    grouping so artists find hardware settings in one place."""
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Performance"
    bl_order = 40
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))
        if _show_openCL_device_warning(context):
            self.layout.label(
                text="", icon_value=icon_manager.get_icon_id("device")
            )
            layout.label(text="", icon=icons.WARNING)

    def draw(self, context):
        layout = self.layout
        config = context.scene.superluxcore.config

        layout.use_property_split = True
        layout.use_property_decorate = False

        if config.engine == "PATH" and config.device == "OCL":
            if not utils.luxutils.is_opencl_build():
                # pysuperluxcore was compiled without OpenCL support
                layout.label(
                    text="No OpenCL support in this SuperLuxCore version",
                    icon_value=icon_manager.get_icon_id("device"),
                )
        else:
            # CPU settings for native C++ threads
            layout.prop(
                context.scene.render,
                "threads_mode",
                text="CPU threads",
                expand=False,
                icon_value=icon_manager.get_icon_id("cpu"),
            )
            sub = layout.column(align=True)
            sub.enabled = context.scene.render.threads_mode == "FIXED"
            sub.prop(context.scene.render, "threads")


class SUPERLUXCORE_RENDER_PT_gpu_devices(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "GPU Devices"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_devices"

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        return (
            context.scene.render.engine == "SUPERLUXCORE"
            and config.engine == "PATH"
            and config.device == "OCL"
        )

    def draw_header(self, context):
        layout = self.layout
        if _show_openCL_device_warning(context):
            layout.label(
                text="", icon_value=icon_manager.get_icon_id("device")
            )

    def draw(self, context):
        layout = self.layout
        devices = context.scene.superluxcore.devices

        layout.use_property_split = True
        layout.use_property_decorate = False

        if not devices.devices:
            layout.label(text="No devices available.", icon=icons.WARNING)

        layout.operator("superluxcore.update_opencl_devices")

        if devices.devices:
            if _show_openCL_device_warning(context):
                layout.label(
                    text="Select at least one device!", icon=icons.WARNING
                )

            for device in devices.get_gpu_devices(context):
                layout.prop(device, "enabled", text=device.name)


class SUPERLUXCORE_RENDER_PT_cpu_devices(RenderButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "CPU Devices"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_devices"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        simple = context.scene.superluxcore.config.simple
        if simple.enabled and not simple.show_advanced:
            return False
        config = context.scene.superluxcore.config
        return (
            context.scene.render.engine == "SUPERLUXCORE"
            and config.engine == "PATH"
            and config.device == "OCL"
        )

    def draw_header(self, context):
        opencl = context.scene.superluxcore.devices
        layout = self.layout
        layout.prop(opencl, "use_native_cpu", text="")

    def draw(self, context):
        layout = self.layout
        opencl = context.scene.superluxcore.devices

        layout.enabled = opencl.use_native_cpu

        layout.use_property_split = True
        layout.use_property_decorate = False

        # CPU settings for native C++ threads
        layout.prop(
            context.scene.render,
            "threads_mode",
            text="CPU threads",
            expand=False,
        )
        sub = layout.column(align=True)
        sub.enabled = context.scene.render.threads_mode == "FIXED"
        sub.prop(context.scene.render, "threads")


class SUPERLUXCORE_RENDER_PT_memory(RenderButtonsPanel, Panel):
    """GPU/CPU memory behaviour: out-of-core rendering and texture
    buffer handling — where big scenes are made to fit."""
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Memory"
    bl_parent_id = "SUPERLUXCORE_RENDER_PT_devices"
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

        on_gpu = config.effective_device() == "OCL" and config.engine == "PATH"

        col = layout.column(align=True)
        col.active = on_gpu
        col.prop(config, "out_of_core")
        sub = col.column(align=True)
        sub.active = on_gpu and config.out_of_core
        sub.prop(config, "out_of_core_mode")
        # stays editable when a low-VRAM GPU forces out-of-core on
        if config.using_out_of_core():
            col.prop(config, "out_of_core_supersampling")

        if not on_gpu:
            layout.label(
                text="Out-of-core applies to GPU rendering",
                icon=icons.INFO,
            )
        elif config.low_vram():
            layout.label(
                text="Low-VRAM GPU detected: out-of-core is forced on",
                icon=icons.INFO,
            )

        layout.prop(config, "free_blender_image_buffers")
