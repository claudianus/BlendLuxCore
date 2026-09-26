from bl_ui.properties_object import ObjectButtonsPanel
from bpy.types import Panel
import bpy
from .. import utils
from .. import icons
from ..icons import icon_manager

class SUPERLUXCORE_OBJECT_PT_object(ObjectButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_context = "object"
    bl_label = "SuperLuxCore Object Settings"

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == "SUPERLUXCORE"

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))

    def draw(self, context):
        layout = self.layout
        obj = context.object

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column()
        if not utils.is_obj_visible_in_cycles(obj):
            col.label(text="Object made invisible through Cycles settings", icon=icons.INFO)
            col = layout.column()
            col.active = False

        col.prop(obj.superluxcore, "id")
        col.prop(obj.superluxcore, "visible_to_camera")
        col.prop(obj.superluxcore, "exclude_from_render")
        if obj.type == "MESH":
            col.prop(obj.superluxcore, "is_light_portal")

        # Light linking: restrict which grouped lights illuminate this object
        box = layout.box()
        box.label(text="Light Linking", icon=icons.LIGHTGROUP)
        box.prop(obj.superluxcore, "link_groups")
        if obj.superluxcore.link_groups:
            box.prop(obj.superluxcore, "link_mode")

        # Motion blur settings
        cam = context.scene.camera
        if cam:
            motion_blur = cam.data.superluxcore.motion_blur
            object_blur = motion_blur.enable and motion_blur.object_blur

            if not motion_blur.enable:
                col.label(text="Motion blur disabled in camera settings", icon=icons.INFO)
            elif not motion_blur.object_blur:
                col.label(text="Object blur disabled in camera settings", icon=icons.INFO)
        else:
            col.label(text="No camera in scene", icon=icons.INFO)
            object_blur = False

        sub = col.column(align=True)
        sub.enabled = object_blur
        sub.prop(obj.superluxcore, "enable_motion_blur")
        
        # Instancing can cost performance, so inform the user when it happens
        if utils.use_obj_motion_blur(obj, context.scene):
            col.label(text="Object will be exported as instance", icon=icons.INFO)

        # .lxm mesh proxy (memory-mapped geometry for heavy static assets)
        if obj.type == "MESH":
            box = layout.box()
            box.label(text="Mesh Proxy", icon=icons.INFO)
            row = box.row(align=True)
            row.prop(obj.superluxcore, "proxy_filepath", text="")
            row.operator("superluxcore.bake_lxm_proxy", text="", icon="EXPORT")
            if obj.superluxcore.proxy_filepath:
                box.label(text="Mesh data stays on disk (mmap, out-of-core)",
                          icon=icons.INFO)
                box.label(text="First material slot only; no displacement "
                          "or motion blur", icon=icons.INFO)


def compatible_panels():
    panels = [
        # Mesh, etc.
        "DATA_PT_context_mesh",
        "DATA_PT_normals",
        "DATA_PT_normals_auto_smooth",
        "DATA_PT_texture_space",
        "DATA_PT_vertex_groups",
        "DATA_PT_face_maps",
        "DATA_PT_shape_keys",
        "DATA_PT_uv_texture",
        "DATA_PT_vertex_colors",
        "DATA_PT_customdata",
        "DATA_PT_custom_props_mesh",
        "DATA_PT_remesh",
        # Speaker
        "DATA_PT_context_speaker",
        "DATA_PT_speaker",
        "DATA_PT_distance",
        "DATA_PT_cone",
        "DATA_PT_custom_props_speaker",
    ]
    types = bpy.types
    return [getattr(types, p) for p in panels if hasattr(types, p)]


def register():
    for panel in compatible_panels():
        panel.COMPAT_ENGINES.add("SUPERLUXCORE")


def unregister():
    for panel in compatible_panels():
        panel.COMPAT_ENGINES.remove("SUPERLUXCORE")
