import bpy
import pysuperluxcore
from .. import utils
from ..utils import node as utils_node
from ..utils.node import get_active_output
from ..utils.errorlog import SuperLuxCoreErrorLog
from . import cycles_node_reader


GLOBAL_FALLBACK_MAT = "__CLAY__"

is_blender_5 = bpy.app.version[0] >= 5 # only test of Blender 5 for now

def convert(exporter, depsgraph, material, is_viewport_render, obj_name=""):
    try:
        if material is None:
            return fallback()

        props = pysuperluxcore.Properties()
        superluxcore_name = utils.get_superluxcore_name(material, is_viewport_render)
        node_tree = material.superluxcore.node_tree

        if is_blender_5:
            # material.use_nodes is deprecated in Blender 5.0.
            # Technically still OK to use for now but made explicit by this.
            matusenodes = True
        else:
            matusenodes = material.use_nodes

        # LuxCore node trees export natively; a Blender (Cycles-style)
        # tree only serves as the translation source when no LuxCore
        # tree exists. Both are read straight from the file - nothing
        # is converted or rewritten.
        use_cycles = utils.misc.material_use_cycles_nodes(material) and \
            getattr(material, "node_tree", None) is not None

        if matusenodes and use_cycles:
            name, props = cycles_node_reader.convert(
                material, props, superluxcore_name, obj_name)
            from . import cycles_compat
            cycles_compat.apply_material_scene_flags(
                material, props, cycles_compat._warned_set(exporter),
                name)
            return name, props

        if node_tree is None:
            SuperLuxCoreErrorLog.add_warning(f'Material "{material.name}": Missing node tree', obj_name=obj_name)
            return fallback(superluxcore_name)

        active_output = get_active_output(node_tree)

        if active_output is None:
            SuperLuxCoreErrorLog.add_warning(f'Node tree "{node_tree.name}": Missing active output node', obj_name=obj_name)
            return fallback(superluxcore_name)

        if _has_volumes_and_transparency(node_tree, active_output):
            msg = f'Material "{material.name}": Combining volumes and materials with opacity < 1 can lead to artifacts!'
            SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj_name)

        # Now export the material node tree, starting at the output node
        active_output.export(exporter, depsgraph, props, superluxcore_name)

        from . import cycles_compat
        cycles_compat.apply_material_scene_flags(
            material, props, cycles_compat._warned_set(exporter),
            superluxcore_name)

        return superluxcore_name, props
    except Exception as error:
        msg = f'Material "{material.name}": {error}'
        SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj_name)
        import traceback
        traceback.print_exc()
        return fallback()


def fallback(superluxcore_name=GLOBAL_FALLBACK_MAT):
    props = pysuperluxcore.Properties()
    props.SetFromString("""
    scene.materials.{mat_name}.type = matte
    scene.materials.{mat_name}.kd = 0.5
    """.format(mat_name=superluxcore_name))
    return superluxcore_name, props


def _has_volumes_and_transparency(node_tree, active_output):
    if (utils_node.get_linked_node(active_output.inputs["Interior Volume"])
            or utils_node.get_linked_node(active_output.inputs["Exterior Volume"])):
        for node in node_tree.nodes:
            if "Opacity" in node.inputs:
                opacity_socket = node.inputs["Opacity"]
                if opacity_socket.is_linked or opacity_socket.default_value < 1:
                    return True
    return False
