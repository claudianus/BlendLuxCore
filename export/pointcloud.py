import math
import bpy
import mathutils
import numpy as np

from .. import utils
from ..utils.errorlog import SuperLuxCoreErrorLog
from .caches.exported_data import ExportedObject


def _build_icosphere(subdivisions=1):
    """
    Returns (loop_points, loop_normals, triangles) for a unit icosphere
    centered at the origin, in per-loop form for DefineMeshExt.
    """
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = np.array(
        [
            [-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0],
            [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
            [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1],
        ],
        dtype=np.float64,
    )
    verts /= np.linalg.norm(verts, axis=1, keepdims=True)

    faces = np.array(
        [
            [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
            [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
            [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
            [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
        ],
        dtype=np.int64,
    )

    for _ in range(subdivisions):
        verts_list = list(verts)
        index_cache = {}
        new_faces = []

        def midpoint_index(i, j):
            key = (min(i, j), max(i, j))
            if key not in index_cache:
                mid = verts_list[i] + verts_list[j]
                mid /= np.linalg.norm(mid)
                index_cache[key] = len(verts_list)
                verts_list.append(mid)
            return index_cache[key]

        for a, b, c in faces:
            ab = midpoint_index(a, b)
            bc = midpoint_index(b, c)
            ca = midpoint_index(c, a)
            new_faces += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]

        verts = np.array(verts_list, dtype=np.float64)
        faces = np.array(new_faces, dtype=np.int64)

    loop_points = verts[faces.ravel()].astype(np.float32)
    loop_normals = loop_points.copy()
    triangles = np.arange(len(loop_points), dtype=np.uint32).reshape(-1, 3)
    return loop_points, loop_normals, triangles


def _read_pointcloud_data(obj):
    """Returns (positions[N,3] float32, radii[N] float32)."""
    pc = obj.data
    count = len(pc.points)
    if count == 0:
        return None, None

    pos_attr = pc.attributes.get("position")
    if pos_attr is None:
        return None, None
    positions = np.empty(count * 3, dtype=np.float32)
    pos_attr.data.foreach_get("vector", positions)
    positions = positions.reshape(count, 3)

    radii = np.ones(count, dtype=np.float32)
    radius_attr = pc.attributes.get("radius")
    if radius_attr is not None and len(radius_attr.data) == count:
        radius_attr.data.foreach_get("value", radii)

    # Degenerate radii produce singular matrices; clamp to a tiny sphere
    np.maximum(radii, 1e-8, out=radii)
    return positions, radii


def _point_matrices(positions, radii, matrix_world):
    """World-space 4x4 matrix per point: Translation(p) @ Scale(r)."""
    count = len(positions)
    inner = np.zeros((count, 4, 4), dtype=np.float32)
    inner[:, 0, 0] = radii
    inner[:, 1, 1] = radii
    inner[:, 2, 2] = radii
    inner[:, 0, 3] = positions[:, 0]
    inner[:, 1, 3] = positions[:, 1]
    inner[:, 2, 3] = positions[:, 2]
    inner[:, 3, 3] = 1.0
    mw = np.asarray(matrix_world, dtype=np.float32)
    return np.matmul(mw, inner)


def _point_matrices_flat(positions, radii, matrix_world):
    """_point_matrices() flattened the way SuperLuxCore wants (transposed)."""
    mats = _point_matrices(positions, radii, matrix_world)
    return np.ascontiguousarray(
        mats.transpose(0, 2, 1).reshape(-1), dtype=np.float32
    )


def convert_pointcloud_obj(
    exporter,
    dg_obj_instance,
    obj,
    obj_key,
    depsgraph,
    superluxcore_scene,
    scene_props,
    is_viewport_render,
    view_layer,
    pending_duplicates,
):
    """
    Convert a Blender POINTCLOUD object to instanced icospheres.

    Point 0 becomes the base object; the remaining points are queued in
    pending_duplicates and instanced with Scene.DuplicateObject after the
    scene is parsed.
    """
    positions, radii = _read_pointcloud_data(obj)
    if positions is None:
        SuperLuxCoreErrorLog.add_warning(
            'Point cloud object "%s" has no points' % obj.name
        )
        return None

    count = len(positions)
    if count > 500000:
        print(
            'INFO: Point cloud "%s" has %d points; export may be slow'
            % (obj.name, count)
        )

    matrix_world = dg_obj_instance.matrix_world.copy()

    # World-space point matrices; SuperLuxCore wants them transposed + flattened
    mats = _point_matrices(positions, radii, matrix_world)
    mats_flat = np.ascontiguousarray(
        mats.transpose(0, 2, 1).reshape(-1), dtype=np.float32
    )

    # Shared icosphere mesh (one per scene)
    mesh_name = "BLC_PointCloudSphere"
    if not superluxcore_scene.IsMeshDefined(mesh_name):
        loop_points, loop_normals, triangles = _build_icosphere(subdivisions=1)
        superluxcore_scene.DefineMeshExt(
            name=mesh_name,
            points=loop_points,
            triangles=triangles,
            normals=loop_normals,
            uvs=None,
            colors=None,
            alphas=None,
            transformation=None,
        )

    # Local import: export_material lives in object_cache which imports us
    from .caches.object_cache import export_material

    lux_mat, mat_props, _node_tree = export_material(
        obj, 0, exporter, depsgraph, is_viewport_render
    )
    scene_props.Set(mat_props)

    # Point 0 is the base object; the rest are instanced post-parse
    base_transform = mathutils.Matrix(
        [
            [mats[0, 0, 0], mats[0, 0, 1], mats[0, 0, 2], mats[0, 0, 3]],
            [mats[0, 1, 0], mats[0, 1, 1], mats[0, 1, 2], mats[0, 1, 3]],
            [mats[0, 2, 0], mats[0, 2, 1], mats[0, 2, 2], mats[0, 2, 3]],
            [mats[0, 3, 0], mats[0, 3, 1], mats[0, 3, 2], mats[0, 3, 3]],
        ]
    )

    obj_id = utils.make_object_id(dg_obj_instance)
    visible_to_cam = utils.visible_to_camera(
        dg_obj_instance, is_viewport_render, view_layer
    )
    exported = ExportedObject(
        obj_key,
        [(mesh_name, 0)],
        [lux_mat],
        base_transform,
        visible_to_cam,
        obj_id,
    )
    # Point-transform motion blur (A5 follow-up): motion_blur.convert()
    # re-evaluates the point data at every shutter step and rebuilds
    # per-point matrices on this record.
    exported.is_pointcloud = True
    part = exported.parts[0]

    if count > 1:
        dup_ids = np.empty(count - 1, dtype=np.uint32)
        if obj_id != -1:
            dup_ids.fill(obj_id & 0xFFFFFFFE)
        else:
            rng = np.random.default_rng(count)
            dup_ids[:] = rng.integers(0, 0x7FFFFFFF, size=count - 1, dtype=np.uint32) & 0xFFFFFFFE

        exported.duplicate_count = count - 1
        pending_duplicates.append(
            (part.lux_obj, mats_flat[16:], count - 1, dup_ids, obj_key)
        )

    return exported
