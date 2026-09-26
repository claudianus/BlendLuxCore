import bpy
from mathutils import Matrix
import math
import pysuperluxcore
from .. import utils
from .caches.exported_data import ExportedObject, ExportedLight
from .image import ImageExporter
from ..utils.errorlog import SuperLuxCoreErrorLog
from ..utils import node as utils_node
from ..utils.node import get_active_output

WORLD_BACKGROUND_LIGHT_NAME = "__WORLD_BACKGROUND_LIGHT__"
MISSING_IMAGE_COLOR = [1, 0, 1]
TYPES_SUPPORTING_ENVLIGHTCACHE = {"sky2", "infinite", "constantinfinite"}

is_blender_5 = bpy.app.version[0] >= 5 # only test of Blender 5 for now

def _ies_node_to_blob(ies_node, obj_name):
    """Read a Cycles ShaderNodeTexIES photometric profile into a byte blob
    for SuperLuxCore's `<light>.iesblob` property. Returns None (with a warning)
    when the profile cannot be resolved."""
    try:
        if ies_node.mode == "EXTERNAL":
            path = bpy.path.abspath(ies_node.filepath)
            with open(path, "rb") as f:
                return f.read()
        else:
            # INTERNAL mode: the profile lives in a Text datablock
            if ies_node.ies is None:
                SuperLuxCoreErrorLog.add_warning("IES node has no text datablock", obj_name)
                return None
            return ies_node.ies.as_string().encode("utf-8")
    except Exception as error:
        SuperLuxCoreErrorLog.add_warning(f"Could not read IES profile: {error}", obj_name)
        return None

def convert_light(exporter, obj, obj_key, depsgraph, superluxcore_scene, transform, is_viewport_render):
    try:
        superluxcore_name = obj_key
        scene = depsgraph.scene_eval

        # If this light was previously defined as an area lamp, delete the area lamp mesh
        superluxcore_scene.DeleteObject(_get_area_obj_name(superluxcore_name))
        # If this light was previously defined as a light, delete it
        superluxcore_scene.DeleteLight(superluxcore_name)

        prefix = "scene.lights." + superluxcore_name + "."

        if utils.misc.use_cycles_compat(obj.data.superluxcore):
            props, exported = _convert_cycles_light(
                exporter, obj, depsgraph, superluxcore_scene, transform,
                is_viewport_render, superluxcore_name, scene, prefix)
        else:
            props, exported = _convert_superluxcore_light(
                exporter, obj, depsgraph, superluxcore_scene, transform,
                is_viewport_render, superluxcore_name, scene, prefix)

        # Cycles light linking (receiver collection) -> linkgroups mask.
        # Engine-level property, so it applies to both light modes.
        # Area lights are mesh lights: the group goes onto the exported
        # scene object (linkGroupMask feeds the triangle light). A light
        # entry with only linkgroups and no .type would parse as sky2,
        # so it must never be emitted on its own.
        from . import cycles_compat
        link_group = cycles_compat.light_linking_link_group(
            obj, depsgraph, cycles_compat._warned_set(exporter))
        if link_group:
            if isinstance(exported, ExportedObject):
                exported.link_groups = (link_group,)
            elif (prefix + "type") in props.GetAllNames():
                props.Set(pysuperluxcore.Property(
                    prefix + "linkgroups", link_group))
        return props, exported
    except Exception as error:
        msg = 'Light "%s": %s' % (obj.name, error)
        SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
        import traceback
        traceback.print_exc()
        return pysuperluxcore.Properties(), None


def _convert_cycles_light(exporter, obj, depsgraph, superluxcore_scene, transform, is_viewport_render,
                          superluxcore_name, scene, prefix):
    from . import cycles_compat
    light = obj.data
    cycles_compat.warn_cycles_light_flags(
        light, cycles_compat._warned_set(exporter), obj.name)
    definitions = {}

    color = list(light.color)
    gain = light.energy

    ies_blob = None

    if light.use_nodes and light.node_tree:
        # Modify color and gain according to node setup
        output_node = light.node_tree.get_output_node("CYCLES")
        if output_node:
            surface_node = utils_node.get_linked_node(output_node.inputs["Surface"])
            if surface_node:
                node_gain = 1
                node_color = [1, 1, 1]

                if surface_node.bl_idname == "ShaderNodeEmission":
                    strength_socket = surface_node.inputs["Strength"]
                    node_gain = strength_socket.default_value
                    strength_node = utils_node.get_linked_node(strength_socket)
                    if strength_node:
                        if strength_node.bl_idname == "ShaderNodeTexIES":
                            # Cycles IES: Fac drives the emission strength.
                            # SuperLuxCore maps the photometric profile to
                            # mappoint/mapsphere via the iesblob property.
                            ies_blob = _ies_node_to_blob(strength_node, obj.name)
                            if utils_node.get_linked_node(strength_node.inputs["Vector"]):
                                SuperLuxCoreErrorLog.add_warning(
                                    "IES node Vector input not supported, "
                                    "the light's local direction is used", obj.name)
                            # The IES node's own Strength socket scales Fac
                            node_gain = strength_node.inputs["Strength"].default_value
                        else:
                            SuperLuxCoreErrorLog.add_warning("Light strength nodes not supported", obj.name)

                    color_socket = surface_node.inputs["Color"]
                    color_node = utils_node.get_linked_node(color_socket)

                    if color_node:
                        if color_node.bl_idname == "ShaderNodeRGB":
                            node_color = list(color_node.outputs[0].default_value)[:3]
                        else:
                            SuperLuxCoreErrorLog.add_warning("Unsupported color node type: " + color_node.bl_idname, obj.name)
                    else:
                         node_color = list(color_socket.default_value)[:3]
                else:
                    SuperLuxCoreErrorLog.add_warning("Unsupported surface node type: " + surface_node.bl_idname, obj.name)

                gain *= node_gain
                color = [a * b for a, b in zip(color, node_color)]

    if light.type == "POINT":
        if ies_blob is not None:
            definitions["type"] = "mappoint" if light.shadow_soft_size == 0 else "mapsphere"
            definitions["iesblob"] = [ies_blob]
            # Cycles measures the IES vertical angle from the light's
            # local -Z (nadir for a light pointing down), while SuperLuxCore's
            # emission map places nadir at +Z. flipz corrects the mapping.
            definitions["flipz"] = True
            # Match the SuperLuxCore light UI defaults (export_ies)
            definitions["map.width"] = 512
            definitions["map.height"] = 256
        else:
            definitions["type"] = "point" if light.shadow_soft_size == 0 else "sphere"
        definitions["transformation"] = utils.luxutils.matrix_to_list(transform)

        if light.shadow_soft_size > 0:
            definitions["radius"] = light.shadow_soft_size
    elif light.type == "SUN":
        sun_dir = _calc_sun_dir(transform)
        distant_dir = [-sun_dir[0], -sun_dir[1], -sun_dir[2]]
        definitions["direction"] = distant_dir

        half_angle = math.degrees(light.angle) / 2

        if half_angle < 0.05:
            definitions["type"] = "sharpdistant"
        else:
            definitions["type"] = "distant"
            definitions["theta"] = half_angle
            gain *= _get_distant_light_normalization_factor(half_angle)
    elif light.type == "SPOT":
        if light.shadow_soft_size > 0:
            SuperLuxCoreErrorLog.add_warning("Size (soft shadows) not supported by SuperLuxCore spotlights", obj.name)

        definitions["type"] = "spot"
        # TODO Cycles has a different falloff, probably needs to be implemented in SuperLuxCore
        definitions["coneangle"] = math.degrees(light.spot_size) / 2
        definitions["conedeltaangle"] = math.degrees(light.spot_size / 2 * light.spot_blend)

        # Position and direction are set by transformation property
        definitions["position"] = [0, 0, 0]
        definitions["target"] = [0, 0, -1]

        spot_fix = Matrix.Rotation(math.radians(-90.0), 4, "Z")
        definitions["transformation"] = utils.luxutils.matrix_to_list(transform @ spot_fix)

        # Multiplier to reach similar brightness as Cycles, found by eyeballing.
        gain *= 0.07
    elif light.type == "AREA":
        if getattr(light.cycles, "is_portal", False):
            # A Cycles light portal emits no light - it is a sampling
            # aperture. Its quad is emitted as a path.portal.<i> rect by
            # export/config.py (cycles_compat.cycles_portal_rects).
            return pysuperluxcore.Properties(), None

        if light.shape not in {"SQUARE", "RECTANGLE"}:
            SuperLuxCoreErrorLog.add_warning("Unsupported area light shape: " + light.shape.title(), obj.name)

        props = pysuperluxcore.Properties()

        # Calculate gain similar to Cycles (scaling with light surface area)
        transform_matrix = calc_area_light_transformation(light, transform)
        scale = transform_matrix.to_scale()
        area_gain = gain / (scale.x * scale.y)
        # Multiplier to reach similar brightness as Cycles.
        # Found through render comparisons, not super precise.
        area_gain *= 0.06504

        # Material
        mat_name = superluxcore_name + "_AREA_LIGHT_MAT"
        mat_prefix = "scene.materials." + mat_name + "."
        mat_definitions = {
            "type": "matte",
            # Black base material to avoid any bounce light from the mesh
            "kd": [0, 0, 0],
            "emission": color,
            "emission.gain": [area_gain] * 3,
            "emission.power": 0.0,
            "emission.efficency": 0.0,
            "emission.normalizebycolor": False,
            "emission.importance": light.superluxcore.importance,
            "transparency.shadow": [1, 1, 1],
        }

        mat_props = utils.luxutils.create_props(mat_prefix, mat_definitions)
        props.Set(mat_props)

        # Object
        use_instancing = utils.use_instancing(obj, scene, is_viewport_render)
        visible_to_camera = False
        obj_props, exported_obj = _create_superluxcore_meshlight(obj, transform, use_instancing, superluxcore_name,
                                                            superluxcore_scene, mat_name, visible_to_camera)
        props.Set(obj_props)
        return props, exported_obj
    else:
        # Can only happen if Blender changes its light types
        raise Exception("Unkown light type", light.type, 'in light "%s"' % obj.name)

    if ies_blob is not None and light.type != "POINT":
        SuperLuxCoreErrorLog.add_warning(
            "IES profile nodes are only supported on point lights",
            obj.name)

    definitions["gain"] = [gain] * 3
    definitions["color"] = color
    definitions["efficency"] = 0.0
    definitions["power"] = 0.0
    definitions["normalizebycolor"] = False
    definitions["importance"] = light.superluxcore.importance

    # Cycles light settings in Blender 4.2+ no longer expose cast_shadow
    if not getattr(light.cycles, "cast_shadow", True):
        SuperLuxCoreErrorLog.add_warning("Cast Shadow is disabled, but unsupported by SuperLuxCore", obj.name)

    if light.superluxcore.link_groups:
        definitions["linkgroups"] = light.superluxcore.link_groups

    props = utils.luxutils.create_props(prefix, definitions)
    return props, ExportedLight(superluxcore_name)


def _convert_superluxcore_light(exporter, obj, depsgraph, superluxcore_scene, transform, is_viewport_render,
                           superluxcore_name, scene, prefix):
    definitions = {}
    light = obj.data
    sun_dir = _calc_sun_dir(transform)

    # Common light settings shared by all light types
    # Note: these variables are also passed to the area light export function
    gain, importance, lightgroup_id = _convert_common_props(exporter, scene, light)
    definitions["gain"] = apply_exposure(gain, light.superluxcore.exposure)
    definitions["importance"] = importance
    definitions["id"] = lightgroup_id

    if light.type == "POINT":
        if light.superluxcore.image or light.superluxcore.ies.use:
            # mappoint/mapsphere
            definitions["type"] = "mappoint" if light.shadow_soft_size == 0 else "mapsphere"

            has_image = False
            if light.superluxcore.image:
                try:
                    filepath = ImageExporter.export(light.superluxcore.image,
                                                    light.superluxcore.image_user,
                                                    scene)
                    definitions["mapfile"] = filepath
                    definitions["gamma"] = light.superluxcore.gamma
                    has_image = True
                except OSError as error:
                    msg = 'Light "%s": %s' % (obj.name, error)
                    SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
                    # Fallback
                    definitions["type"] = "point" if light.shadow_soft_size == 0 else "sphere"
                    # Signal that the image is missing
                    definitions["gain"] = [x * light.superluxcore.gain * pow(2, light.superluxcore.exposure)
                                           for x in MISSING_IMAGE_COLOR]

            has_ies = False
            try:
                has_ies = export_ies(definitions, light.superluxcore.ies, light.library)
            except OSError as error:
                msg = 'Light "%s": %s' % (obj.name, error)
                SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
            finally:
                if not has_ies and not has_image:
                    # Fallback
                    definitions["type"] = "point" if light.shadow_soft_size == 0 else "sphere"
        else:
            # point/sphere
            definitions["type"] = "point" if light.shadow_soft_size == 0 else "sphere"

        _define_brightness_and_color(light, definitions)

        # Position is set by transformation property
        definitions["position"] = [0, 0, 0]
        definitions["transformation"] = utils.luxutils.matrix_to_list(transform)

        if light.shadow_soft_size > 0:
            definitions["radius"] = light.shadow_soft_size

    elif light.type == "SUN":
        distant_dir = [-sun_dir[0], -sun_dir[1], -sun_dir[2]]

        _define_brightness_and_color(light, definitions)

        if light.superluxcore.light_type == "sun":
            # sun
            definitions["type"] = "sun"
            definitions["dir"] = sun_dir
            definitions["turbidity"] = light.superluxcore.turbidity
            definitions["relsize"] = light.superluxcore.relsize

            if light.superluxcore.color_mode == "rgb":
                # The sun doesn't support have a "color" property, but its color can be tinted via the gain
                tint_color = light.superluxcore.rgb_gain
                for i in range(3):
                    definitions["gain"][i] *= tint_color[i]
        elif light.superluxcore.light_type == "hemi":
            # hemi
            if light.superluxcore.image:
                _convert_infinite(definitions, light, scene, transform)
            else:
                # Fallback
                definitions["type"] = "constantinfinite"
        elif light.superluxcore.theta < 0.05:
            # sharpdistant
            definitions["type"] = "sharpdistant"
            definitions["direction"] = distant_dir
        else:
            # distant
            definitions["type"] = "distant"
            definitions["direction"] = distant_dir
            definitions["theta"] = light.superluxcore.theta
            if light.superluxcore.normalize_distant:
                normalization_factor = _get_distant_light_normalization_factor(light.superluxcore.theta)
                definitions["gain"] = [normalization_factor * x for x in definitions["gain"]]

    elif light.type == "SPOT":
        coneangle = math.degrees(light.spot_size) / 2
        conedeltaangle = math.degrees(light.spot_size / 2 * light.spot_blend)

        if light.superluxcore.image:
            # projection
            try:
                definitions["mapfile"] = ImageExporter.export(light.superluxcore.image,
                                                              light.superluxcore.image_user,
                                                              scene)
                definitions["type"] = "projection"
                definitions["fov"] = coneangle * 2
                definitions["gamma"] = light.superluxcore.gamma
            except OSError as error:
                msg = 'Light "%s": %s' % (obj.name, error)
                SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
                # Fallback
                definitions["type"] = "spot"
                # Signal that the image is missing
                definitions["gain"] = [x * light.superluxcore.gain * pow(2, light.superluxcore.exposure)
                                       for x in MISSING_IMAGE_COLOR]
        else:
            # spot
            definitions["type"] = "spot"
            definitions["coneangle"] = coneangle
            definitions["conedeltaangle"] = conedeltaangle

        _define_brightness_and_color(light, definitions)

        # Position and direction are set by transformation property
        definitions["position"] = [0, 0, 0]
        definitions["target"] = [0, 0, -1]

        spot_fix = Matrix.Rotation(math.radians(-90.0), 4, "Z")
        definitions["transformation"] = utils.luxutils.matrix_to_list(transform @ spot_fix)

    elif light.type == "AREA":
        if light.superluxcore.is_laser:
            # laser
            definitions["type"] = "laser"
            definitions["radius"] = light.size / 2

            _define_brightness_and_color(light, definitions)

            # Position and direction are set by transformation property
            definitions["position"] = [0, 0, 0]
            definitions["target"] = [0, 0, -1]

            spot_fix = Matrix.Rotation(math.radians(-90.0), 4, "Z")
            definitions["transformation"] = utils.luxutils.matrix_to_list(transform @ spot_fix)
        else:
            # area (mesh light)
            return _convert_area_light(obj, scene, is_viewport_render, exporter, depsgraph, superluxcore_scene, gain,
                                       importance, superluxcore_name, transform)

    else:
        # Can only happen if Blender changes its light types
        raise Exception("Unkown light type", light.type, 'in light "%s"' % obj.name)

    _indirect_light_visibility(definitions, light)

    if not is_viewport_render and definitions["type"] in TYPES_SUPPORTING_ENVLIGHTCACHE:
        _envlightcache(definitions, light, scene, is_viewport_render)

    props = utils.luxutils.create_props(prefix, definitions)

    # Exterior volume of the light
    volume_node_tree = light.superluxcore.volume

    if volume_node_tree:
        superluxcore_name = utils.get_superluxcore_name(volume_node_tree)
        active_output = get_active_output(volume_node_tree)

        try:
            active_output.export(exporter, depsgraph, props, superluxcore_name)
            props.Set(pysuperluxcore.Property(prefix + "volume", superluxcore_name))
        except Exception as error:
            msg = f'Light "{obj.name}": {error}'
            SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)

    if light.superluxcore.link_groups:
        props.Set(pysuperluxcore.Property(prefix + "linkgroups",
                                          light.superluxcore.link_groups))

    return props, ExportedLight(superluxcore_name)


def convert_world(exporter, world, scene, is_viewport_render):
    try:
        assert isinstance(world, bpy.types.World)
        superluxcore_name = WORLD_BACKGROUND_LIGHT_NAME
        prefix = "scene.lights." + superluxcore_name + "."

        if utils.misc.use_cycles_compat(world.superluxcore):
            definitions = _convert_cycles_world(exporter, scene, world, is_viewport_render)
        else:
            definitions = _convert_superluxcore_world(exporter, scene, world, is_viewport_render)

        if definitions:
            if world.superluxcore.link_groups:
                definitions["linkgroups"] = world.superluxcore.link_groups
            return utils.luxutils.create_props(prefix, definitions)
        else:
            return None
    except Exception as error:
        msg = 'World "%s": %s' % (world.name, error)
        SuperLuxCoreErrorLog.add_warning(msg)
        import traceback
        traceback.print_exc()
        return None

def _define_constantinfinite(definitions, color):
    definitions["type"] = "constantinfinite"
    definitions["color"] = color
    return color != [0, 0, 0]

def _convert_cycles_world(exporter, scene, world, is_viewport_render):
    definitions = {
        "importance": world.superluxcore.importance,
    }

    # Cycles world ray visibility -> the world light's visibility.*
    # flags (camera visibility maps to transparent film in aovs.py)
    from . import cycles_compat
    cycles_compat.apply_world_cycles_visibility(
        world, definitions, cycles_compat._warned_set(exporter))

    node_tree = world.node_tree

    if is_blender_5:
        # world.use_nodes is deprecated in Blender 5.0.
        # Technically still OK to use for now but made explicit by this.
        not_worldusenodes = False
    else:
        not_worldusenodes = not world.use_nodes

    if not_worldusenodes or not node_tree:
        if not _define_constantinfinite(definitions, list(world.color)):
            return None

    output_node = node_tree.get_output_node("CYCLES")
    if not output_node:
        return None

    surface_node = utils_node.get_linked_node(output_node.inputs["Surface"])
    if not surface_node:
        return None

    if surface_node.bl_idname == "ShaderNodeBackground":
        gain = surface_node.inputs["Strength"].default_value

        color_socket = surface_node.inputs["Color"]
        color_node = utils_node.get_linked_node(color_socket)

        if color_node:
            if color_node.bl_idname == "ShaderNodeRGB":
                color = list(color_node.outputs[0].default_value)[:3]
                if not _define_constantinfinite(definitions, color):
                    return None
            elif color_node.bl_idname == "ShaderNodeTexEnvironment":
                image = color_node.image
                if not image:
                    image_missing = True
                else:
                    try:
                        filepath = ImageExporter.export_cycles_node_reader(image)
                        image_missing = False
                        definitions["type"] = "infinite"
                        definitions["file"] = filepath
                        definitions["gamma"] = 2.2 if image.colorspace_settings.name == "sRGB" else 1
                        definitions["cdfdim"] = world.superluxcore.cdfdim

                        # Transformation: the mirror fix matches Cycles'
                        # direction convention. A Mapping node on the
                        # Vector input is folded in via its INVERSE:
                        # Cycles 'point' mode computes R*(v*S)+T on the
                        # lookup direction (svm_mapping), and the engine
                        # evaluates inv(lightToWorld)*(-dir) - so the
                        # composed transform is fix @ inv(M).
                        infinite_fix = Matrix.Scale(1.0, 4)
                        infinite_fix[0][0] = -1.0  # mirror the hdri map to match Cycles and old LuxBlend

                        mapping_node = utils_node.get_linked_node(color_node.inputs["Vector"])
                        mapping_matrix = None
                        if mapping_node:
                            from . import cycles_compat
                            mapping_matrix = cycles_compat.mapping_node_matrix(
                                mapping_node, obj_name=world.name)

                        transformation = infinite_fix @ (
                            mapping_matrix.inverted()
                            if mapping_matrix is not None
                            else Matrix.Identity(4))

                        definitions["transformation"] = utils.luxutils.matrix_to_list(transformation)
                    except OSError as image_missing:
                        SuperLuxCoreErrorLog.add_warning("World: " + str(image_missing))
                        image_missing = True

                if image_missing:
                    _define_constantinfinite(definitions, MISSING_IMAGE_COLOR)
            elif color_node.bl_idname == "ShaderNodeTexSky":
                if color_node.sky_type != "HOSEK_WILKIE":
                    SuperLuxCoreErrorLog.add_warning("World: Unsupported sky type: " + color_node.sky_type)

                definitions["type"] = "sky2"
                definitions["ground.enable"] = False
                definitions["groundalbedo"] = [color_node.ground_albedo] * 3
                definitions["turbidity"] = color_node.turbidity
                definitions["dir"] = list(color_node.sun_direction)
                # Found by eyeballing, not super precise
                gain *= 0.000014
        else:
            # No color node linked
            definitions["type"] = "constantinfinite"
            # Alpha not supported
            color = list(color_socket.default_value)[:3]
            if not _define_constantinfinite(definitions, color):
                return None
    else:
        raise Exception("Unsupported node type:", surface_node.bl_idname)

    if gain == 0:
        return None

    definitions["gain"] = [gain] * 3
    return definitions


def _convert_superluxcore_world(exporter, scene, world, is_viewport_render):
    if world.superluxcore.light == "none":
        return None

    definitions = {}

    gain, importance, lightgroup_id = _convert_common_props(exporter, scene, world)
    definitions["gain"] = apply_exposure(gain, world.superluxcore.exposure)
    definitions["importance"] = importance
    definitions["id"] = lightgroup_id

    if world.superluxcore.color_mode == "rgb":
        tint_color = list(world.superluxcore.rgb_gain)
    elif world.superluxcore.color_mode == "temperature":
        tint_color = [1, 1, 1]
        definitions["temperature"] = world.superluxcore.temperature
        definitions["temperature.normalize"] = True
    else:
        raise Exception("Unkown color mode")

    light_type = world.superluxcore.light
    if light_type == "sky2":
        definitions["type"] = "sky2"
        definitions["ground.enable"] = world.superluxcore.ground_enable
        definitions["ground.color"] = list(world.superluxcore.ground_color)
        definitions["groundalbedo"] = list(world.superluxcore.groundalbedo)

        if world.superluxcore.sun and world.superluxcore.sun.data:
            # Use sun turbidity and direction so the user does not have to keep two values in sync
            definitions["turbidity"] = world.superluxcore.sun.data.superluxcore.turbidity
            definitions["dir"] = _calc_sun_dir(world.superluxcore.sun.matrix_world)
            if world.superluxcore.use_sun_gain_for_sky:
                sun = world.superluxcore.sun.data
                gain, _, _ = _convert_common_props(exporter, scene, sun)
                definitions["gain"] = apply_exposure(gain, sun.superluxcore.exposure)
        else:
            # Use world turbidity
            definitions["turbidity"] = world.superluxcore.turbidity
        
        for i in range(3):
            definitions["gain"][i] *= tint_color[i]

    elif light_type == "infinite":
        if world.superluxcore.image:
            transformation = Matrix.Rotation(world.superluxcore.rotation, 4, "Z")
            _convert_infinite(definitions, world, scene, transformation)
            for i in range(3):
                definitions["gain"][i] *= tint_color[i]
        else:
            # Fallback if no image is set
            definitions["type"] = "constantinfinite"
            definitions["color"] = tint_color
    else:
        definitions["type"] = "constantinfinite"
        definitions["color"] = tint_color

    _indirect_light_visibility(definitions, world)

    if not is_viewport_render and definitions["type"] in TYPES_SUPPORTING_ENVLIGHTCACHE:
        _envlightcache(definitions, world, scene, is_viewport_render)

    return definitions


def _get_distant_light_normalization_factor(theta):
    epsilon = 1e-9
    cos_theta_max = min(math.cos(math.radians(theta)), 1 - epsilon)
    return 1 / (2 * math.pi * (1 - cos_theta_max))


def _calc_sun_dir(transform):
    matrix_inv = transform.inverted()
    return [matrix_inv[2][0], matrix_inv[2][1], matrix_inv[2][2]]


def _convert_common_props(exporter, scene, light_or_world):
    if isinstance(light_or_world, bpy.types.Light):
        if light_or_world.type == "SUN" and light_or_world.superluxcore.light_type == "sun":
            raw_gain = light_or_world.superluxcore.sun_sky_gain
        else:
            raw_gain = light_or_world.superluxcore.gain
    else:
        # It's a bpy.types.World
        if light_or_world.superluxcore.light == "sky2":
            raw_gain = light_or_world.superluxcore.sun_sky_gain
        else:
            raw_gain = light_or_world.superluxcore.gain

    gain = [raw_gain] * 3


    importance = light_or_world.superluxcore.importance
    lightgroup_id = scene.superluxcore.lightgroups.get_id_by_name(light_or_world.superluxcore.lightgroup)
    exporter.lightgroup_cache.add(lightgroup_id)
    return gain, importance, lightgroup_id


def _convert_infinite(definitions, light_or_world, scene, transformation=None):
    assert light_or_world.superluxcore.image is not None

    try:
        filepath = ImageExporter.export(light_or_world.superluxcore.image,
                                        light_or_world.superluxcore.image_user,
                                        scene)
    except OSError as error:
        error_context = "Light" if isinstance(light_or_world, bpy.types.Light) else "World"
        msg = '%s "%s": %s' % (error_context, light_or_world.name, error)
        SuperLuxCoreErrorLog.add_warning(msg)
        # Fallback
        definitions["type"] = "constantinfinite"
        # Signal that the image is missing
        definitions["gain"] = [x * light_or_world.superluxcore.gain for x in MISSING_IMAGE_COLOR]
        return

    definitions["type"] = "infinite"
    definitions["file"] = filepath
    definitions["gamma"] = light_or_world.superluxcore.gamma
    definitions["sampleupperhemisphereonly"] = light_or_world.superluxcore.sampleupperhemisphereonly
    # CDF resolution cap is a world-level control; plain lights reuse the
    # same property name when they exist (guarded — light props lack it)
    cdfdim = getattr(light_or_world.superluxcore, "cdfdim", None)
    if cdfdim is not None:
        definitions["cdfdim"] = cdfdim

    if transformation:
        infinite_fix = Matrix.Scale(1.0, 4)
        # TODO one axis still not correct
        infinite_fix[0][0] = -1.0  # mirror the hdri map to match Cycles and old LuxBlend
        transformation = utils.luxutils.matrix_to_list(infinite_fix @ transformation.inverted())
        definitions["transformation"] = transformation


def calc_area_light_transformation(light, transform_matrix):
    scale_x = Matrix.Scale(light.size / 2, 4, (1, 0, 0))
    if light.shape in {"RECTANGLE", "ELLIPSE"}:
        scale_y = Matrix.Scale(light.size_y / 2, 4, (0, 1, 0))
    else:
        # basically scale_x, but for the y axis (note the last tuple argument)
        scale_y = Matrix.Scale(light.size / 2, 4, (0, 1, 0))

    transform_matrix = transform_matrix.copy()
    transform_matrix @= scale_x
    transform_matrix @= scale_y
    return transform_matrix


def _get_area_obj_name(superluxcore_name):
    fake_material_index = 0
    # The material index after the superluxcore_name is expected by ExportedObject
    return superluxcore_name + str(fake_material_index)


def _create_superluxcore_meshlight(obj, transform, use_instancing, superluxcore_name, superluxcore_scene,
                              mat_name, visible_to_camera):
    light = obj.data
    transform_matrix = calc_area_light_transformation(light, transform)
    if light.shape not in {"SQUARE", "RECTANGLE"}:
        SuperLuxCoreErrorLog.add_warning("Unsupported area light shape: " + light.shape.title(), obj_name=obj.name)

    if transform_matrix.determinant() == 0:
        # Objects with non-invertible matrices cannot be loaded by SuperLuxCore (RuntimeError)
        # This happens if the light size is set to 0
        raise Exception("Area light has size 0 (can not be exported)")

    transform_list = utils.luxutils.matrix_to_list(transform_matrix)
    # Only bake the transform into the mesh for final renders (disables instancing which
    # is needed for viewport render so we can move the light object)

    # Instancing just means that we transform the object instead of the mesh
    if use_instancing:
        obj_transform = transform_list
        mesh_transform = None
    else:
        obj_transform = None
        mesh_transform = transform_list

    shape_name = superluxcore_name
    if not superluxcore_scene.IsMeshDefined(shape_name):
        vertices = [
            (1, 1, 0),
            (1, -1, 0),
            (-1, -1, 0),
            (-1, 1, 0),
        ]
        faces = [
            (0, 1, 2),
            (2, 3, 0),
        ]
        normals = [
            (0, 0, -1),
            (0, 0, -1),
            (0, 0, -1),
            (0, 0, -1),
        ]
        uvs = [
            (1, 1),
            (1, 0),
            (0, 0),
            (0, 1),
        ]
        superluxcore_scene.DefineMesh(shape_name, vertices, faces, normals, uvs, None, None, mesh_transform)

    fake_material_index = 0
    # The material index after the superluxcore_name is expected by ExportedObject
    obj_prefix = "scene.objects." + _get_area_obj_name(superluxcore_name) + "."
    obj_definitions = {
        "material": mat_name,
        "shape": shape_name,
        "camerainvisible": not visible_to_camera,
    }
    if obj_transform:
        # Use instancing for viewport render so we can interactively move the light
        obj_definitions["transformation"] = obj_transform

    obj_props = utils.luxutils.create_props(obj_prefix, obj_definitions)

    mesh_definition = [superluxcore_name, fake_material_index]
    exported_obj = ExportedObject(superluxcore_name, [mesh_definition], ["fake_mat_name"],
                                  transform.copy(), visible_to_camera,
                                  link_groups=obj.data.superluxcore.link_groups)
    return obj_props, exported_obj


def _convert_area_light(obj, scene, is_viewport_render, exporter, depsgraph, superluxcore_scene,
                        gain, importance, superluxcore_name, transform):
    """
    An area light is a plane object with emissive material in SuperLuxCore
    """
    light = obj.data
    props = pysuperluxcore.Properties()

    # Light emitting material
    mat_name = superluxcore_name + "_AREA_LIGHT_MAT"
    mat_prefix = "scene.materials." + mat_name + "."
    mat_definitions = {
        "type": "matte",
        # Black base material to avoid any bounce light from the mesh
        "kd": [0, 0, 0],
        "emission": list(light.superluxcore.rgb_gain),
        "emission.gain": apply_exposure(gain, light.superluxcore.exposure),
        "emission.gain.normalizebycolor": False,
        "emission.power": 0.0,
        "emission.efficency": 0.0,
        "emission.normalizebycolor": False,
        "emission.theta": math.degrees(light.superluxcore.spread_angle),
        "emission.id": scene.superluxcore.lightgroups.get_id_by_name(light.superluxcore.lightgroup),
        "emission.importance": importance,
        "transparency.shadow": [0, 0, 0] if light.superluxcore.visible else [1, 1, 1],
        # Note: if any of these is disabled, we lose MIS, which can lead to more noise.
        # However, in some rare cases it's needed to disable some of them.
        "visibility.indirect.diffuse.enable": light.superluxcore.visibility_indirect_diffuse,
        "visibility.indirect.glossy.enable": light.superluxcore.visibility_indirect_glossy,
        "visibility.indirect.specular.enable": light.superluxcore.visibility_indirect_specular,
    }

    if light.superluxcore.color_mode == "rgb":
        mat_definitions["emission"] = list(light.superluxcore.rgb_gain)
    elif light.superluxcore.color_mode == "temperature":
        mat_definitions["emission"] = [1, 1, 1]
        mat_definitions["emission.temperature"] = light.superluxcore.temperature
        mat_definitions["emission.temperature.normalize"] = True
    else:
        raise Exception("Unkown color mode")

    if light.superluxcore.light_unit == "power":
        mat_definitions["emission.power"] = light.superluxcore.power / ( 2 * math.pi * (1 - math.cos(light.superluxcore.spread_angle/2) ))
        mat_definitions["emission.efficency"] = light.superluxcore.efficacy
        mat_definitions["emission.normalizebycolor"] = light.superluxcore.normalizebycolor

        if light.superluxcore.efficacy == 0 or light.superluxcore.power == 0:
            mat_definitions["emission.gain"] = [0, 0, 0]
        else:
            mat_definitions["emission.gain"] = apply_exposure([1, 1, 1], light.superluxcore.exposure)

    if light.superluxcore.light_unit == "lumen":
        mat_definitions["emission.power"] = light.superluxcore.lumen / ( 2 * math.pi * (1 - math.cos(light.superluxcore.spread_angle/2) ))
        mat_definitions["emission.efficency"] = 1.0
        mat_definitions["emission.normalizebycolor"] = light.superluxcore.normalizebycolor
        if light.superluxcore.lumen == 0:
            mat_definitions["emission.gain"] = [0, 0, 0]
        else:
            mat_definitions["emission.gain"] = apply_exposure([1, 1, 1], light.superluxcore.exposure)
    
    if light.superluxcore.light_unit == "candela":
        if light.superluxcore.per_square_meter:
            mat_definitions["emission.power"] = 0.0
            mat_definitions["emission.efficency"] = 0.0
            mat_definitions["emission.gain"] = [light.superluxcore.candela] * 3
            mat_definitions["emission.gain.normalizebycolor"] = light.superluxcore.normalizebycolor
        else:
            # Multiply with pi to match brightness with other light types
            mat_definitions["emission.power"] = light.superluxcore.candela * math.pi
            mat_definitions["emission.efficency"] = 1.0
            mat_definitions["emission.normalizebycolor"] = light.superluxcore.normalizebycolor
            if light.superluxcore.candela == 0:
                mat_definitions["emission.gain"] = [0, 0, 0]
            else:
                mat_definitions["emission.gain"] = apply_exposure([1, 1, 1], light.superluxcore.exposure)

    node_tree = light.superluxcore.node_tree
    if node_tree:
        tex_props = pysuperluxcore.Properties()
        tex_name = superluxcore_name + "_AREA_LIGHT_TEX"

        active_output = get_active_output(node_tree)

        if active_output is None:
            msg = 'Node tree "%s": Missing active output node' % node_tree.name
            SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
        else:
            # Now export the texture node tree, starting at the output node
            active_output.export(exporter, depsgraph, tex_props, tex_name)
            mat_definitions["emission"] = tex_name
            props.Set(tex_props)

    # IES data
    if light.superluxcore.ies.use:
        try:
            export_ies(mat_definitions, light.superluxcore.ies, light.library, is_meshlight=True)
        except OSError as error:
            msg = 'light "%s": %s' % (obj.name, error)
            SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj.name)

    mat_props = utils.luxutils.create_props(mat_prefix, mat_definitions)
    props.Set(mat_props)

    # SuperLuxCore object
    use_instancing = utils.use_instancing(obj, scene, is_viewport_render)
    visible_to_camera = obj.superluxcore.visible_to_camera and light.superluxcore.visible
    obj_props, exported_obj = _create_superluxcore_meshlight(obj, transform, use_instancing, superluxcore_name,
                                                        superluxcore_scene, mat_name, visible_to_camera)
    props.Set(obj_props)
    return props, exported_obj


def _indirect_light_visibility(definitions, light_or_world):
    definitions.update({
        "visibility.indirect.diffuse.enable": light_or_world.superluxcore.visibility_indirect_diffuse,
        "visibility.indirect.glossy.enable": light_or_world.superluxcore.visibility_indirect_glossy,
        "visibility.indirect.specular.enable": light_or_world.superluxcore.visibility_indirect_specular,
    })


def _envlightcache(definitions, light_or_world, scene, is_viewport_render):
    envlight_cache = scene.superluxcore.config.envlight_cache
    enabled = envlight_cache.enabled and light_or_world.superluxcore.use_envlight_cache
    definitions["visibilitymapcache.enable"] = enabled
    if enabled:
        # All env. light caches share the same properties (it is very rare to have more than one anyway)
        definitions["visibilitymapcache.map.quality"] = envlight_cache.quality
        # Automatically chosen by SuperLuxCore according to the quality and HDRI map size
        definitions["visibilitymapcache.map.tilewidth"] = 0
        definitions["visibilitymapcache.map.tileheight"] = 0
        definitions["visibilitymapcache.map.tilesamplecount"] = 0

        definitions["visibilitymapcache.map.sampleupperhemisphereonly"] = light_or_world.superluxcore.sampleupperhemisphereonly

        file_path = utils.get_persistent_cache_file_path(envlight_cache.file_path, envlight_cache.save_or_overwrite,
                                                         is_viewport_render, scene, default_suffix="env")
        definitions["visibilitymapcache.persistent.file"] = file_path


def apply_exposure(gain, exposure):
    return [x * pow(2, exposure) for x in gain]


def _define_brightness_and_color(light, definitions):
    # Brightness
    normalize_by_color = light.superluxcore.normalizebycolor
    gain = None

    if light.superluxcore.light_unit == "power":
        efficency = light.superluxcore.efficacy
        power = light.superluxcore.power

        if light.superluxcore.efficacy == 0 or light.superluxcore.power == 0:
            gain = [0, 0, 0]
        else:
            gain = [1, 1, 1]

    elif light.superluxcore.light_unit == "lumen":
        efficency = 1.0

        if light.type == "SPOT":
            power = light.superluxcore.lumen
        else:
            power = light.superluxcore.lumen

        if light.superluxcore.lumen == 0:
            gain = [0, 0, 0]
        else:
            gain = [1, 1, 1]
    
    elif light.superluxcore.light_unit == "candela":
        efficency = 1.0

        if light.type == "SPOT":
            power = light.superluxcore.candela * 2 * math.pi * (1 - math.cos(light.spot_size/2))
        else:
            power = light.superluxcore.candela * 4 * math.pi

        if light.superluxcore.candela == 0:
            gain = [0, 0, 0]
        else:
            gain = [1, 1, 1]
        
    elif light.superluxcore.light_unit == "artistic":
        efficency = 0.0
        power = 0.0
        normalize_by_color = False
    else:
        raise Exception("Unknown light unit")

    definitions["efficency"] = efficency
    definitions["power"] = power
    definitions["normalizebycolor"] = normalize_by_color
    if gain is not None:
        definitions["gain"] = gain

    # Color
    if light.superluxcore.color_mode == "rgb":
        definitions["color"] = list(light.superluxcore.rgb_gain)
    elif light.superluxcore.color_mode == "temperature":
        definitions["color"] = [1, 1, 1]
        definitions["temperature"] = light.superluxcore.temperature
        definitions["temperature.normalize"] = True
    else:
        raise Exception("Unkown color mode")


def export_ies(definitions, ies, library, is_meshlight=False):
    """
    ies is a SuperLuxCoreIESProps PropertyGroup
    """
    prefix = "emission." if is_meshlight else ""
    has_ies = (ies.file_type == "TEXT" and ies.file_text) or (ies.file_type == "PATH" and ies.file_path)

    if not has_ies:
        return False

    definitions[prefix + "flipz"] = ies.flipz
    definitions[prefix + "map.width"] = ies.map_width
    definitions[prefix + "map.height"] = ies.map_height

    # There are two ways to specify IES data: filepath or blob (ascii text)
    if ies.file_type == "TEXT":
        # Blender text block
        text = ies.file_text

        if text:
            blob = text.as_string().encode("ascii")

            if blob:
                definitions[prefix + "iesblob"] = [blob]
    else:
        # File path
        iesfile = ies.file_path

        if iesfile:
            try:
                filepath = utils.get_abspath(iesfile, library, must_exist=True, must_be_existing_file=True)
                definitions[prefix + "iesfile"] = filepath
            except OSError as error:
                # Make the error message more precise
                raise OSError('Could not find .ies file at path "%s" (%s)'
                              % (iesfile, error))

    # has ies
    return True
