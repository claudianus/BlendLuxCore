import bpy
from bpy.props import BoolProperty, IntProperty
from ..output import SuperLuxCoreNodeOutput, update_active
from ..materials.output import MATERIAL_ID_DESC
from ... import utils
from ...utils import node as utils_node
import pysuperluxcore
from ...utils.errorlog import SuperLuxCoreErrorLog
from ... import icons


class SuperLuxCoreNodeVolOutput(bpy.types.Node, SuperLuxCoreNodeOutput):
    """
    Volume output node.
    This is where the export starts (if the output is active).
    """
    bl_label = "Volume Output"
    bl_width_default = 160
    enable_update_callback: BoolProperty(default=False)
    active: BoolProperty(name="Active", default=True, update=update_active)
    id: IntProperty(name="Volume ID", default=-1, min=-1, soft_max=32767,
                     description=MATERIAL_ID_DESC)
    use_photongi: BoolProperty(name="Use PhotonGI Cache", default=False,
                                description="Store PhotonGI entries in this volume. This only affects "
                                            "homogeneous and heterogeneous volumes, entries are never "
                                            "stored on clear volumes. You might want to disable this "
                                            "for volumes that take up a lot of space while having low "
                                            "scattering, like a fog volume in a large open scene")

    def init(self, context):
        self.inputs.new("SuperLuxCoreSocketVolume", "Volume")
        super().init(context)

    def draw_buttons(self, context, layout):
        super().draw_buttons(context, layout)

        layout.prop(self, "id")

        # PhotonGI currently only works with Path engine
        if (context.scene.superluxcore.config.photongi.enabled
                and context.scene.superluxcore.config.engine == "PATH"):
            # PhotonGI only affects homogeneous and heterogeneous volumes, make the setting inactive for others
            linked_node = self.inputs["Volume"].links[0].from_node if self.inputs["Volume"].is_linked else None
            row = layout.row()
            row.active = bool(linked_node and linked_node.bl_idname in utils_node.expand_legacy(
                {"SuperLuxCoreNodeVolHomogeneous", "SuperLuxCoreNodeVolHeterogeneous"}))
            row.prop(self, "use_photongi")

            world = context.scene.world
            if self.use_photongi and world and world.superluxcore.volume == self.id_data:
                col = layout.column(align=True)
                col.label(text="PhotonGI on the world volume can", icon=icons.WARNING)
                col.label(text="lead to VERY long cache computation time!")

    def export(self, exporter, depsgraph, props, superluxcore_name):
        prefix = "scene.volumes." + superluxcore_name + "."
        definitions = {}
        # Invalidate node cache
        # TODO have one global properties object so this is no longer necessary
        exporter.node_cache.clear()

        if self.inputs["Volume"].is_linked:
            self.inputs["Volume"].export(exporter, depsgraph, props, superluxcore_name)
        else:
            # We need a fallback (black volume)
            msg = 'Node "%s" in tree "%s": No volume attached' % (self.name, self.id_data.name)
            SuperLuxCoreErrorLog.add_warning(msg)

            definitions["type"] = "clear"
            definitions["absorption"] = [100, 100, 100]

        definitions["photongi.enable"] = self.use_photongi

        if self.id != -1:
            # SuperLuxCore only assigns a random ID if the ID is not set at all
            definitions["id"] = self.id

        props.Set(utils.luxutils.create_props(prefix, definitions))
