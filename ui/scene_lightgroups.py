from bl_ui.properties_scene import SceneButtonsPanel
from bpy.types import Panel
from ..properties.lightgroups import MAX_CUSTOM_LIGHTGROUPS
from .. import icons
from ..icons import icon_manager


def lightgroup_icon(enabled):
    return icons.LIGHTGROUP_ENABLED if enabled else icons.LIGHTGROUP_DISABLED


def settings_toggle_icon(enabled):
    return icons.EXPANDABLE_OPENED if enabled else icons.EXPANDABLE_CLOSED


class SUPERLUXCORE_SCENE_PT_lightgroups(SceneButtonsPanel, Panel):
    bl_label = "SuperLuxCore Light Groups"
    COMPAT_ENGINES = {"SUPERLUXCORE"}

    @classmethod
    def poll(cls, context):
        engine = context.scene.render.engine
        return engine == "SUPERLUXCORE"

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icon_manager.get_icon_id("logotype"))

    def draw(self, context):
        layout = self.layout
        groups = context.scene.superluxcore.lightgroups

        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.operator("superluxcore.create_lightgroup_nodes", icon=icons.COMPOSITOR)

        self.draw_lightgroup(layout, groups.default, -1,
                             is_default_group=True)

        for i, group in enumerate(groups.custom):
            self.draw_lightgroup(layout, group, i)

        if len(groups.custom) < MAX_CUSTOM_LIGHTGROUPS:
            layout.operator("superluxcore.add_lightgroup", icon=icons.ADD)

    @staticmethod
    def draw_lightgroup(layout, group, index, is_default_group=False):
        col = layout.column(align=True)

        box = col.box()
        row = box.row()
        
        if not is_default_group:
            col = row.column()
            op = col.operator("superluxcore.select_objects_in_lightgroup", icon=icons.OBJECT, text="")
            op.index = index

        col = row.column()
        col.enabled = group.enabled

        if is_default_group:
            col.label(text="All lights without a group are in the default light group.", icon=icons.INFO)
        else:
            col.prop(group, "name", text="")            

        # Can't delete the default lightgroup
        if not is_default_group:
            op = row.operator("superluxcore.remove_lightgroup",
                              text="", icon=icons.CLEAR, emboss=False)
            op.index = index
