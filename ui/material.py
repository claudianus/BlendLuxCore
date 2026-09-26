from bl_ui.properties_material import MaterialButtonsPanel, MATERIAL_PT_viewport
from bpy.types import Panel, Menu
from ..operators.node_tree_presets import SUPERLUXCORE_OT_preset_material
from .. import icons

original_viewport_draw = None


def lux_mat_template_ID(layout, material):
    row = layout.row(align=True)
    row.operator("superluxcore.material_select", icon=icons.MATERIAL, text="")

    if material:
        row.prop(material, "name", text="")
        if material.users > 1:
            # TODO this thing is too wide
            row.operator("superluxcore.material_copy", text=str(material.users))
        row.prop(material, "use_fake_user", text="")
        row.operator("superluxcore.material_copy", text="", icon=icons.DUPLICATE)
        row.operator("superluxcore.material_unlink", text="", icon=icons.CLEAR)
    else:
        row.operator("superluxcore.material_new", text="New", icon=icons.ADD)
    return row


class SUPERLUXCORE_PT_context_material(MaterialButtonsPanel, Panel):
    """
    Material UI Panel
    """
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = ""
    bl_options = {"HIDE_HEADER"}
    bl_order = 1

    @classmethod
    def poll(cls, context):
        engine = context.scene.render.engine
        return (context.material or context.object) and (engine == "SUPERLUXCORE")

    def draw(self, context):
        layout = self.layout

        mat = context.material
        obj = context.object
        slot = context.material_slot
        space = context.space_data

        # Re-create the Blender material UI, but without the surface/wire/volume/halo buttons        
        if obj:
            is_sortable = len(obj.material_slots) > 1
            rows = 1
            if (is_sortable):
                rows = 4

            row = layout.row()

            row.template_list("MATERIAL_UL_matslots", "", obj, "material_slots", obj, "active_material_index", rows=rows)

            col = row.column(align=True)
            col.operator("object.material_slot_add", icon='ADD', text="")
            col.operator("object.material_slot_remove", icon='REMOVE', text="")

            col.menu("MATERIAL_MT_context_menu", icon='DOWNARROW_HLT', text="")

            if is_sortable:
                col.separator()

                col.operator("object.material_slot_move", icon='TRIA_UP', text="").direction = 'UP'
                col.operator("object.material_slot_move", icon='TRIA_DOWN', text="").direction = 'DOWN'

            if obj.mode == 'EDIT':
                row = layout.row(align=True)
                row.operator("object.material_slot_assign", text="Assign")
                row.operator("object.material_slot_select", text="Select")
                row.operator("object.material_slot_deselect", text="Deselect")

        if obj:
            # Note that we don't use layout.template_ID() because we can't
            # control the copy operator in that template.
            # So we mimic our own template_ID.
            row = lux_mat_template_ID(layout, obj.active_material)

            if slot:
                row = row.row()
                row.prop(slot, "link", text="")
            else:
                row.label()
        elif mat:
            layout.template_ID(space, "pin_id")
            layout.separator()

        if mat:
            if mat.superluxcore.node_tree or (mat.use_nodes and mat.node_tree and mat.superluxcore.use_cycles_nodes):
                layout.operator("superluxcore.material_show_nodetree", icon=icons.SHOW_NODETREE)

            if not mat.superluxcore.node_tree and not mat.superluxcore.use_cycles_nodes:
                layout.operator("superluxcore.mat_nodetree_new", icon=icons.NODETREE, text="Use SuperLuxCore Material Nodes")

            if mat.use_nodes and mat.node_tree:
                layout.prop(mat.superluxcore, "use_cycles_nodes")
                # Principled BSDF -> material target only matters when the
                # Blender/Cycles node path is actually used (explicit toggle
                # or automatic Blender-first conversion without a Lux tree)
                if mat.superluxcore.use_cycles_nodes or not mat.superluxcore.node_tree:
                    layout.prop(mat.superluxcore, "principled_target")


class SUPERLUXCORE_PT_material_presets(MaterialButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Node Tree Presets"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        engine = context.scene.render.engine
        if context.material and context.material.superluxcore.use_cycles_nodes:
            return False
        return engine == "SUPERLUXCORE"

    def draw(self, context):
        layout = self.layout

        row = layout.row()

        for category, presets in SUPERLUXCORE_OT_preset_material.categories.items():
            col = row.column()
            col.label(text=category)

            for preset in presets:
                op = col.operator("superluxcore.preset_material", text=preset)
                op.preset = preset


class SUPERLUXCORE_PT_material_preview(MaterialButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Preview"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        engine = context.scene.render.engine
        return context.material and (engine == "SUPERLUXCORE")

    def draw(self, context):
        layout = self.layout
        layout.template_preview(context.material)
        row = layout.row(align=True)
        preview = context.material.superluxcore.preview
        row.prop(preview, "zoom")
        
        
class SUPERLUXCORE_PT_material_settings(MaterialButtonsPanel, Panel):
    COMPAT_ENGINES = {"SUPERLUXCORE"}
    bl_label = "Settings"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    @classmethod
    def poll(cls, context):
        engine = context.scene.render.engine
        return context.material and (engine == "SUPERLUXCORE") and context.material.superluxcore.use_cycles_nodes

    def draw(self, context):
        layout = self.layout
        layout.prop(context.material, "pass_index")


# Since we can't disable the original MATERIAL_PT_viewport panel, it makes no sense to add our own
# (see register function below)

#class SUPERLUXCORE_PT_settings(MaterialButtonsPanel, Panel):
#    bl_label = "Settings"
#    bl_context = "material"
#    bl_options = {'DEFAULT_CLOSED'}
#
#    @classmethod
#    def poll(cls, context):
#        engine = context.scene.render.engine
#        return context.material and engine == "SUPERLUXCORE"
#
#    def draw(self, context):
#        layout = self.layout
#        mat = context.material
#
#        if mat.superluxcore.auto_vp_color:
#            split = layout.split(factor=0.8)
#            split.prop(mat.superluxcore, "auto_vp_color")
#            row = split.row()
#            row.enabled = not mat.superluxcore.auto_vp_color
#            row.prop(mat, "diffuse_color", text="")
#        else:
#            layout.prop(mat.superluxcore, "auto_vp_color")
#            layout.prop(mat, "diffuse_color", text="Viewport Color")


# The poll method of MATERIAL_PT_viewport does not check the renderengine, so we have to patch
# the draw method if we want to display stuff differently than other engines
def lux_viewport_draw(self, context):
    layout = self.layout

    mat = context.material

    if context.scene.render.engine == "SUPERLUXCORE":
        if mat.superluxcore.auto_vp_color:
            split = layout.split(factor=0.8)
            split.prop(mat.superluxcore, "auto_vp_color")
            row = split.row()
            row.enabled = not mat.superluxcore.auto_vp_color
            row.prop(mat, "diffuse_color", text="")
        else:
            layout.prop(mat.superluxcore, "auto_vp_color")
            layout.prop(mat, "diffuse_color", text="Viewport Color")
    else:
        layout.use_property_split = True
        col = layout.column()
        col.prop(mat, "diffuse_color", text="Color")
        col.prop(mat, "metallic")
        col.prop(mat, "roughness")


def register():
    global original_viewport_draw
    original_viewport_draw = MATERIAL_PT_viewport.draw
    MATERIAL_PT_viewport.draw = lux_viewport_draw


def unregister():
    MATERIAL_PT_viewport.draw = original_viewport_draw
