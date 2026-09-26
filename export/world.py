import pysuperluxcore
from .. import utils
from ..utils.node import get_active_output
from . import light
from ..utils.errorlog import SuperLuxCoreErrorLog

# TODO: currently it is not possible to remove the world volume during viewport render


def convert(exporter, depsgraph, scene, is_viewport_render):
    props = pysuperluxcore.Properties()
    world = scene.world

    if not world:
        return props

    # World light (this is a SuperLuxCore concept)
    world_light_props = light.convert_world(exporter, world, scene, is_viewport_render)
    if world_light_props:
        props.Set(world_light_props)

    # World volume
    volume_node_tree = world.superluxcore.volume

    if volume_node_tree:
        superluxcore_name = utils.get_superluxcore_name(volume_node_tree)
        active_output = get_active_output(volume_node_tree)
        try:
            active_output.export(exporter, depsgraph, props, superluxcore_name)
            props.Set(pysuperluxcore.Property("scene.world.volume.default", superluxcore_name))
        except Exception as error:
            msg = 'World "%s": %s' % (world.name, error)
            SuperLuxCoreErrorLog.add_warning(msg)

    return props
