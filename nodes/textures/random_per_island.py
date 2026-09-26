import bpy
from ..base import SuperLuxCoreNodeTexture
from ...utils import node as utils_node
from ...export.caches.object_cache import TriAOVDataIndices


class SuperLuxCoreNodeTexRandomPerIsland(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Random Per Island"
    bl_width_default = 130

    def init(self, context):
        self.outputs.new("SuperLuxCoreSocketFloatUnbounded", "Value")
        # This node potentially requires a mesh re-export in viewport, 
        # because it depends on a SuperLuxCore shape to pre-process the data
        utils_node.force_viewport_mesh_update2(self.id_data)
        

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        definitions = {
            "type": "hitpointtriangleaov",
            "dataindex": TriAOVDataIndices.RANDOM_PER_ISLAND_FLOAT,
        }

        return self.create_props(props, definitions, superluxcore_name)
