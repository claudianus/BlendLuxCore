import math
import os
import bpy
import mathutils
import numpy as np
import pysuperluxcore

from .. import utils
from ..utils import node as utils_node
from ..utils.errorlog import SuperLuxCoreErrorLog
from .caches.exported_data import ExportedObject


# Peak Kelvin a fully-developed flame (fire field = 1) maps to. Mantaflow fire
# tops out around 3000 K; lower field values cool toward a deep red edge.
_FIRE_TEMPERATURE_K = 3000.0


# A unit cube spanning [0, 1]^3, with outward-facing normals.
# Each face contributes 4 loop vertices so normals stay flat-shaded.
_CUBE_LOOP_POINTS = np.array(
    [
        # -Z face
        [0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0],
        # +Z face
        [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1],
        # -Y face
        [0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1],
        # +Y face
        [0, 1, 0], [0, 1, 1], [1, 1, 1], [1, 1, 0],
        # -X face
        [0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0],
        # +X face
        [1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1],
    ],
    dtype=np.float32,
)

_CUBE_TRIANGLES = np.array(
    [
        [0, 1, 2], [0, 2, 3],
        [4, 5, 6], [4, 6, 7],
        [8, 9, 10], [8, 10, 11],
        [12, 13, 14], [12, 14, 15],
        [16, 17, 18], [16, 18, 19],
        [20, 21, 22], [20, 22, 23],
    ],
    dtype=np.uint32,
)

_CUBE_LOOP_NORMALS = np.array(
    [
        [0, 0, -1]] * 4
    + [[0, 0, 1]] * 4
    + [[0, -1, 0]] * 4
    + [[0, 1, 0]] * 4
    + [[-1, 0, 0]] * 4
    + [[1, 0, 0]] * 4,
    dtype=np.float32,
)


def _resolve_frame_filepath(vol_data, scene):
    """Return the .vdb file for the current frame, honoring sequence settings."""
    if not vol_data.filepath:
        return ""
    filepath = bpy.path.abspath(vol_data.filepath)
    if not vol_data.is_sequence:
        return filepath

    indexed_filepaths = utils.openVDB_sequence_resolve_all(filepath)
    if not indexed_filepaths:
        return filepath

    # Blender Volume datablock sequence semantics:
    # frame_current - frame_start + frame_offset selects the grid index.
    frame = scene.frame_current - vol_data.frame_start + vol_data.frame_offset
    duration = max(1, vol_data.frame_duration)
    mode = getattr(vol_data, "sequence_mode", "CLIP")

    if mode == "REPEAT":
        index = frame % duration
    elif mode == "PINGPONG":
        period = duration * 2 - 2
        f = frame % period if period > 0 else 0
        index = f if f < duration else period - f
    else:
        # CLIP / EXTEND both clamp to the available range
        index = utils.clamp(frame, 0, duration - 1)

    index = utils.clamp(index, 0, len(indexed_filepaths) - 1)
    return indexed_filepaths[index][1]


def _pick_grids(filepath):
    """
    Returns (density_grid, color_grid, fire_grid) — names of the grids used
    for the density, scattering albedo and fire emission. Either may be None.
    """
    try:
        names = list(pysuperluxcore.GetOpenVDBGridNames(filepath))
    except Exception as e:
        raise Exception('Could not read OpenVDB file "%s": %s' % (filepath, e))

    if not names:
        raise Exception('OpenVDB file "%s" contains no grids' % filepath)

    density = "density" if "density" in names else names[0]
    color = "color" if "color" in names else None
    # Fire emission source. "flame" (Mantaflow) is normalized to [0,1]; fall
    # back to "temperature"/"heat" if that is what the file stores.
    fire = None
    for candidate in ("flame", "temperature", "heat"):
        if candidate in names:
            fire = candidate
            break
    return density, color, fire


def volume_info_grid_defs(node, output_socket_name, obj_name):
    """
    ShaderNodeVolumeInfo -> densitygrid texture definitions bound to this
    object's OpenVDB grids (reuses the VOLUME-object grid/mapping logic so the
    result samples the same voxels as the auto-built volume).

    Returns the ``scene.textures.*`` defs dict, or None when the object has no
    usable volume grid for that output (a warning is logged).
    """
    obj = bpy.data.objects.get(obj_name)
    vol_data = getattr(obj, "data", None)
    if obj is None or obj.type != "VOLUME" or vol_data is None:
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": object "%s" is not an OpenVDB volume '
            "object" % (node.name, obj_name), obj_name=obj_name)
        return None

    filepath = _resolve_frame_filepath(vol_data, bpy.context.scene)
    if not filepath or not os.path.isfile(filepath):
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": no readable OpenVDB file on object "%s"'
            % (node.name, obj_name), obj_name=obj_name)
        return None

    try:
        density_grid, color_grid, fire_grid = _pick_grids(filepath)
    except Exception as e:
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": %s' % (node.name, e), obj_name=obj_name)
        return None

    # Map the requested output to a grid; the node's attribute-name field (the
    # grid it is configured to read) wins over the auto-detected default.
    socket_to_grid = {
        "Density":     getattr(node, "density_attribute", "") or density_grid,
        "Color":       getattr(node, "color_attribute", "") or color_grid,
        "Flame":       getattr(node, "flame_attribute", "") or fire_grid,
        "Temperature": getattr(node, "temperature_attribute", "") or fire_grid,
    }
    grid = socket_to_grid.get(output_socket_name)
    if not grid:
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": no OpenVDB grid for output "%s"'
            % (node.name, output_socket_name), obj_name=obj_name)
        return None

    try:
        _creator, bbox, bbox_world, _trans, _gridtype, _metadata = (
            pysuperluxcore.GetOpenVDBGridInfo(filepath, grid)
        )
    except Exception as e:
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": could not read grid "%s": %s'
            % (node.name, grid, e), obj_name=obj_name)
        return None

    nx = abs(bbox[0] - bbox[3])
    ny = abs(bbox[1] - bbox[4])
    nz = abs(bbox[2] - bbox[5])

    # Same world -> [0,1]^3 active-voxel mapping the auto-built volume uses.
    bb_min = mathutils.Vector(bbox_world[0:3])
    bb_max = mathutils.Vector(bbox_world[3:6])
    extent = bb_max - bb_min
    if extent.length < 1e-9:
        SuperLuxCoreErrorLog.add_warning(
            'Volume Info node "%s": degenerate grid bounds' % node.name,
            obj_name=obj_name)
        return None
    bbox_matrix = mathutils.Matrix.Translation(bb_min) @ mathutils.Matrix.Diagonal(
        extent.to_4d()
    )
    world_box = obj.matrix_world @ bbox_matrix
    mapping_transform = utils.luxutils.matrix_to_list(world_box, invert=True)

    return {
        "type": "densitygrid",
        "wrap": "black",
        "storage": "half",
        "nx": nx,
        "ny": ny,
        "nz": nz,
        "openvdb.file": filepath,
        "openvdb.grid": grid,
        "mapping.type": "globalmapping3d",
        "mapping.transformation": mapping_transform,
    }


def _subtree_uses(node, bl_idname, _seen=None):
    """True when the subtree feeding `node` contains a node of `bl_idname`."""
    if _seen is None:
        _seen = set()
    if node is None or node in _seen:
        return False
    _seen.add(node)
    if node.bl_idname == bl_idname:
        return True
    for socket in node.inputs:
        for link in socket.links:
            if _subtree_uses(link.from_node, bl_idname, _seen):
                return True
    return False


def _material_volume_defs(obj, obj_key, props):
    """
    If the object's Cycles material has a Volume subtree that reads this
    object's grids (a Volume Info node), convert it and return the volume
    coefficient defs. Otherwise return None so the standard auto-build runs.
    """
    mat = obj.material_slots[0].material if len(obj.material_slots) else None
    node_tree = getattr(mat, "node_tree", None)
    if mat is None or node_tree is None:
        return None
    output = node_tree.get_output_node("CYCLES")
    if output is None or "Volume" not in output.inputs:
        return None
    link = utils_node.get_link(output.inputs["Volume"])
    if link is None or not _subtree_uses(link.from_node, "ShaderNodeVolumeInfo"):
        return None

    from . import cycles_node_reader  # lazy: object_cache->cycles_node_reader cycle
    return cycles_node_reader._volume(
        link.from_node, link.from_socket, props, mat,
        obj_key + "_vol", obj.name)


def convert_volume_obj(
    exporter,
    dg_obj_instance,
    obj,
    obj_key,
    depsgraph,
    superluxcore_scene,
    scene_props,
    is_viewport_render,
    view_layer,
):
    """
    Convert a Blender VOLUME object (OpenVDB file) to a bounded box mesh
    carrying a heterogeneous SuperLuxCore volume fed by densitygrid textures.
    """
    vol_data = obj.data
    scene = depsgraph.scene_eval if depsgraph.scene_eval else bpy.context.scene

    filepath = _resolve_frame_filepath(vol_data, scene)
    if not filepath:
        SuperLuxCoreErrorLog.add_warning(
            'Volume object "%s": generated (in-memory) volumes are not '
            "supported yet; import an OpenVDB file instead" % obj.name
        )
        return None
    if not os.path.isfile(filepath):
        SuperLuxCoreErrorLog.add_warning(
            'Volume object "%s": file not found: %s' % (obj.name, filepath)
        )
        return None

    try:
        density_grid, color_grid, fire_grid = _pick_grids(filepath)
    except Exception as e:
        SuperLuxCoreErrorLog.add_warning('Volume object "%s": %s' % (obj.name, e))
        return None

    try:
        _creator, bbox, bbox_world, _trans, gridtype, _metadata = (
            pysuperluxcore.GetOpenVDBGridInfo(filepath, density_grid)
        )
    except Exception as e:
        SuperLuxCoreErrorLog.add_warning(
            'Volume object "%s": could not read grid info: %s' % (obj.name, e)
        )
        return None

    nx = abs(bbox[0] - bbox[3])
    ny = abs(bbox[1] - bbox[4])
    nz = abs(bbox[2] - bbox[5])

    # SuperLuxCore normalizes the grid's *active voxel* bbox to [0,1]^3, so the
    # carrier box and the texture mapping must span exactly that region.
    # bbox_world is the active bbox in the grid's world frame, which Blender
    # presents as object-local space.
    bb_min = mathutils.Vector(bbox_world[0:3])
    bb_max = mathutils.Vector(bbox_world[3:6])
    extent = bb_max - bb_min
    if extent.length < 1e-9:
        SuperLuxCoreErrorLog.add_warning(
            'Volume object "%s": degenerate bounds' % obj.name
        )
        return None

    transform = dg_obj_instance.matrix_world.copy()

    # The box mesh spans the local bound box directly (unit cube scaled by B,
    # baked into vertices so instances transform like regular objects).
    bbox_matrix = mathutils.Matrix.Translation(bb_min) @ mathutils.Matrix.Diagonal(
        extent.to_4d()
    )
    world_box = transform @ bbox_matrix

    # Vertices of the local-space bounding box
    loop_points = np.array(
        [tuple(bbox_matrix @ mathutils.Vector(p)) for p in _CUBE_LOOP_POINTS],
        dtype=np.float32,
    )

    mesh_name = obj_key + "_volumebox"
    superluxcore_scene.DefineMeshExt(
        name=mesh_name,
        points=loop_points,
        triangles=_CUBE_TRIANGLES,
        normals=_CUBE_LOOP_NORMALS,
        uvs=None,
        colors=None,
        alphas=None,
        transformation=None,
    )

    # World -> [0,1]^3 grid mapping (inverse of the box's world transform)
    mapping_transform = utils.luxutils.matrix_to_list(world_box, invert=True)

    # Blender 5.x: density scale lives on display settings; older versions had
    # it on render settings. Both default to 1.0.
    density_scale = getattr(
        vol_data.render,
        "density_scale",
        getattr(vol_data.display, "density_scale", 1.0),
    )
    density_scale = max(0.0, density_scale)

    # Density clipping: voxels below the threshold are dropped entirely.
    # greaterthan() gives a 0/1 mask, scaled back by the density value.
    clipping = max(0.0, getattr(vol_data.render, "clipping", 0.0))

    tex_density = obj_key + "_density"
    props = scene_props

    # A Cycles material whose Volume subtree reads this object's grids (a
    # Volume Info node) overrides the standard auto-built coefficients below.
    mat_vol = _material_volume_defs(obj, obj_key, props)

    vol_absorption = [0.0, 0.0, 0.0]
    vol_asymmetry = [0.0, 0.0, 0.0]
    if mat_vol is not None:
        # Material drives the volume; its Volume Info -> densitygrid
        # textures already reference this object's grids.
        vol_scattering = mat_vol.get("scattering", [0.0, 0.0, 0.0])
        vol_emission = mat_vol.get("emission", [0.0, 0.0, 0.0])
        vol_absorption = mat_vol.get("absorption", [0.0, 0.0, 0.0])
        vol_asymmetry = mat_vol.get("asymmetry", [0.0, 0.0, 0.0])
    else:
        tex_defs = {
            "type": "densitygrid",
            "wrap": "black",
            "storage": "half",
            "nx": nx,
            "ny": ny,
            "nz": nz,
            "openvdb.file": filepath,
            "openvdb.grid": density_grid,
            "mapping.type": "globalmapping3d",
            "mapping.transformation": mapping_transform,
        }
        props.Set(
            utils.luxutils.create_props("scene.textures.%s." % tex_density, tex_defs)
        )

        # Density clipping mask (Cycles semantics: voxels < clipping render empty)
        tex_eff_density = tex_density
        if clipping > 0.0:
            tex_mask = obj_key + "_clipmask"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_mask,
                    {"type": "greaterthan", "texture1": tex_density, "texture2": clipping},
                )
            )
            tex_eff_density = obj_key + "_clippeddensity"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_eff_density,
                    {"type": "scale", "texture1": tex_density, "texture2": tex_mask},
                )
            )

        # scattering = density * density_scale [* color grid]
        if color_grid:
            tex_color = obj_key + "_color"
            color_defs = dict(tex_defs)
            color_defs["openvdb.grid"] = color_grid
            props.Set(
                utils.luxutils.create_props("scene.textures.%s." % tex_color, color_defs)
            )
            tex_scale = obj_key + "_density_scale"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_scale,
                    {
                        "type": "scale",
                        "texture1": tex_color,
                        "texture2": density_scale,
                    },
                )
            )
            tex_scatter = obj_key + "_scattering"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_scatter,
                    {
                        "type": "scale",
                        "texture1": tex_eff_density,
                        "texture2": tex_scale,
                    },
                )
            )
        else:
            tex_scatter = obj_key + "_density_scale"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_scatter,
                    {
                        "type": "scale",
                        "texture1": tex_eff_density,
                        "texture2": density_scale,
                    },
                )
            )

        # Fire emission: drive a true blackbody with the fire field. The grid is a
        # normalized intensity, so it is scaled to a Kelvin temperature for the
        # Planck colour and reused (scaled up) as the HDR brightness mask. This
        # yields physically-correct flame chromaticity (deep red edges up to a
        # white-hot core) instead of a hand-tuned ramp.
        tex_emission = [0.0, 0.0, 0.0]
        if fire_grid:
            tex_fire = obj_key + "_fire"
            fire_defs = dict(tex_defs)
            fire_defs["openvdb.grid"] = fire_grid
            props.Set(
                utils.luxutils.create_props("scene.textures.%s." % tex_fire, fire_defs)
            )

            tex_temp = obj_key + "_firetemp"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_temp,
                    {
                        "type": "scale",
                        "texture1": tex_fire,
                        "texture2": _FIRE_TEMPERATURE_K,
                    },
                )
            )

            tex_bb = obj_key + "_firebb"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_bb,
                    {
                        "type": "blackbody",
                        "temperature": tex_temp,
                        "normalize": 1,
                    },
                )
            )

            tex_emission = obj_key + "_emission"
            props.Set(
                utils.luxutils.create_props(
                    "scene.textures.%s." % tex_emission,
                    {
                        "type": "scale",
                        "texture1": tex_bb,
                        "texture2": tex_fire,
                    },
                )
            )
        vol_scattering = tex_scatter
        vol_emission = tex_emission

    # Step size: user override via the Blender volume render step size
    # (world-space only), otherwise the smallest world-space cell size.
    res = (nx, ny, nz)
    axis_lengths = [
        mathutils.Vector((world_box[0][i], world_box[1][i], world_box[2][i])).length
        for i in range(3)
    ]
    step_candidates = [
        axis_lengths[i] / res[i] for i in range(3) if res[i] > 0
    ]
    auto_step = min(step_candidates) if step_candidates else 0.05
    user_step = getattr(vol_data.render, "step_size", 0.0) or 0.0
    step_size = (
        user_step
        if user_step > 0.0 and getattr(vol_data.render, "space", "WORLD") == "WORLD"
        else auto_step
    )
    step_size = max(step_size, 1e-5)

    diagonal = math.sqrt(sum(l * l for l in axis_lengths))
    maxcount = max(1, math.ceil(diagonal / step_size)) + 1 if step_candidates else 1024

    vol_name = obj_key + "_volume"
    props.Set(
        utils.luxutils.create_props(
            "scene.volumes.%s." % vol_name,
            {
                "type": "heterogeneous",
                "absorption": vol_absorption,
                "scattering": vol_scattering,
                "asymmetry": vol_asymmetry,
                "emission": vol_emission,
                "steps.size": step_size,
                "steps.maxcount": maxcount,
                "multiscattering": 0,
                "ior": 1.0,
                "priority": 0,
                "emission.id": 0,
            },
        )
    )

    # Fully transparent carrier (ior 1.0 glass -> zero Fresnel reflection)
    # so rays enter the interior volume unmodified.
    mat_name = obj_key + "_volmat"
    props.Set(
        utils.luxutils.create_props(
            "scene.materials.%s." % mat_name,
            {
                "type": "glass",
                "kr": [1.0, 1.0, 1.0],
                "kt": [1.0, 1.0, 1.0],
                "interiorior": 1.0,
                "exteriorior": 1.0,
                "volume.interior": vol_name,
            },
        )
    )

    visible_to_cam = utils.visible_to_camera(
        dg_obj_instance, is_viewport_render, view_layer
    )
    return ExportedObject(
        obj_key,
        [(mesh_name, 0)],
        [mat_name],
        transform,
        visible_to_cam,
        utils.make_object_id(dg_obj_instance),
    )
