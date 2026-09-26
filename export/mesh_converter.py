import hashlib
from contextlib import contextmanager
from time import time
import numpy as np

_needs_reload = "bpy" in locals()

import bpy

from . import caches
from . import named_attributes
from .. import utils
from ..utils.errorlog import SuperLuxCoreErrorLog

if _needs_reload:
    import importlib

    importlib.reload(caches)
    importlib.reload(named_attributes)
    importlib.reload(utils)


# https://blenderartists.org/t/\
# efficient-copying-of-vertex-coords-to-and-from-numpy-arrays/661467/2
def get_ndarray(
    bpy_collection: bpy.types.bpy_prop_collection,
    attr: str,
    stride: int,
    dtype: np.dtype,
):
    """Get a numpy array from a Blender collection.

    If stride == 0, the array is raveled
    """
    count = len(bpy_collection)
    buffer = np.empty(shape=(count, stride if stride else 1), dtype=dtype)
    bpy_collection.foreach_get(attr, np.ravel(buffer))
    if not stride:
        buffer = buffer.ravel()
    return buffer


def convert(
    obj,
    mesh_key,
    depsgraph,
    superluxcore_scene,
    is_viewport_render,
    use_instancing,
    transform,
    exporter=None,
    single_mesh=False,
):
    start_time = time()

    with _prepare_mesh(obj, depsgraph) as mesh:
        if mesh is None:
            return None

        try:
            # loop_triangles can be empty/stale on meshes coming from
            # modifiers or fresh to_mesh() results.
            mesh.calc_loop_triangles()
        except Exception:
            pass

        # Blender API may not be always consistent with naming, for the mesh object.
        # For the sake of clarity, we list here our naming conventions.
        # They may specially differ from attribute domains...
        # https://docs.blender.org/api/current/bpy_types_enum_items/attribute_domain_items.html#rna-enum-attribute-domain-items
        # Point: a point in the 3D-space, (float, float, float)
        # Vertex: an index to the mesh array of points
        # Loop: a vertex and an edge

        # Loop vertices
        loop_vertices = get_ndarray(mesh.loops, "vertex_index", 0, np.uint32)

        # Points
        vertex_points = get_ndarray(mesh.vertices, "co", 3, np.float32)
        loop_points = vertex_points[loop_vertices]

        # Normals: per-LOOP normals (split edges and custom normals
        # survive; the old per-vertex broadcast silently smoothed them).
        try:
            loop_normals = get_ndarray(mesh.loops, "normal", 3, np.float32)
        except Exception:
            vertex_normals = get_ndarray(mesh.vertices, "normal", 3, np.float32)
            loop_normals = vertex_normals[loop_vertices]

        # Triangle loop indices
        triangle_loops = get_ndarray(
            mesh.loop_triangles, "loops", 3, np.uint32
        )

        # Material slot index for each triangle
        loop_triangle_materials = get_ndarray(
            mesh.loop_triangles, "material_index", 1, np.uint32
        ).ravel()
        unique_mats = np.unique(loop_triangle_materials)

        # UV
        uvs = [
            get_ndarray(uv_layer.uv, "vector", 2, np.float32)
            for uv_layer in mesh.uv_layers
        ]

        # Vertex colors
        def reshape_colors(colors, domain):
            if domain == "POINT":
                return colors[loop_vertices]
            elif domain == "CORNER":
                return colors
            else:
                raise ValueError(f"Unhandled attribute domain: '{domain}'")

        rgba_colors = [
            reshape_colors(
                get_ndarray(attribute.data, "color", 4, np.float32),
                attribute.domain,
            )
            for attribute in mesh.color_attributes
        ]
        # ascontiguousarray so the slices can be adopted zero-copy by
        # pysuperluxcore (non-contiguous inputs would force a copy anyway)
        rgb = [np.ascontiguousarray(rgba[:, :3]) for rgba in rgba_colors]
        alphas = [
            np.ascontiguousarray(rgba[:, 3]) for rgba in rgba_colors
        ]

        # Generic named attributes (Geometry Nodes "Store Named Attribute"
        # outputs etc.) → SuperLuxCore vertex AOV / triangle AOV / extra color
        # layers. The name→index map is registered for the node reader.
        vert_aovs, face_attrs, extra_cols = named_attributes.collect(
            mesh, loop_vertices, len(rgb), obj.name
        )
        rgb += extra_cols
        # FACE-domain attrs are per polygon; loop_triangles.polygon_index
        # maps each exported triangle back to its attribute value.
        tri_polygon_index = (
            get_ndarray(mesh.loop_triangles, "polygon_index", 0, np.uint32)
            if face_attrs
            else None
        )

        # Transformation
        if is_viewport_render or use_instancing:
            mesh_transform = None
        else:
            mesh_transform = np.array(
                [
                    transform[0][0:4],
                    transform[1][0:4],
                    transform[2][0:4],
                    transform[3][0:4],
                ],
                dtype=np.float32,
            )

        # Weld the loop-expanded arrays back to indexed vertices: a loop
        # survives as its own exported vertex only when its
        # (vertex, normal, uvs, colors, alphas, vertex AOVs) tuple is
        # unique. On smooth meshes this shrinks the vertex arrays ~6x
        # (loops ~= 3*tris ~= 6*verts) toward Blender's indexed size.
        # Keys are compared bitwise as uint32, so only exact duplicates
        # merge — seams and split normals stay split. np.unique's
        # first-occurrence index doubles as the welded->loop
        # representative map that motion blur uses to compact its
        # loop-domain step samples.
        key_parts = [loop_vertices[:, None], loop_normals.view(np.uint32)]
        key_parts += [uv.view(np.uint32) for uv in uvs]
        key_parts += [c.view(np.uint32) for c in rgb]
        key_parts += [a[:, None].view(np.uint32) for a in alphas]
        key_parts += [v[:, None].view(np.uint32) for v in vert_aovs]
        weld_rep = None
        if len(loop_vertices):
            _uniq_key, weld_rep, weld_inv = np.unique(
                np.concatenate(key_parts, axis=1),
                axis=0, return_index=True, return_inverse=True,
            )
            weld_inv = weld_inv.ravel().astype(np.uint32, copy=False)
            if len(weld_rep) == len(loop_vertices):
                weld_rep = None  # nothing merged — keep the loop domain

        if weld_rep is not None:
            exp_points = loop_points[weld_rep]
            exp_normals = loop_normals[weld_rep]
            exp_uvs = [uv[weld_rep] for uv in uvs]
            exp_rgb = [c[weld_rep] for c in rgb]
            exp_alphas = [a[weld_rep] for a in alphas]
            exp_aovs = [a[weld_rep] for a in vert_aovs]
            exp_tris = weld_inv[triangle_loops]
            # The temporary loop-expanded copies are dead now
            del loop_points, loop_normals
        else:
            exp_points = loop_points
            exp_normals = loop_normals
            exp_uvs, exp_rgb, exp_alphas = uvs, rgb, alphas
            exp_aovs, exp_tris = vert_aovs, triangle_loops

        # Deformation motion blur exports per-step positions in the loop
        # domain; the maps below translate them to the exported domain.
        want_motion_maps = (
            exporter is not None
            and getattr(exporter, "motion_blur_enabled", False)
            and getattr(obj.superluxcore, "enable_motion_blur", False)
        )

        # Log
        def fmt_layer(layers, layer_name):
            nlayers = len(layers)
            suffix = "layers" if nlayers > 1 else "layer"
            return f"{nlayers} {layer_name} {suffix}"
        print(f"[BLC] Exporting '{str(mesh_key)}' - {len(unique_mats)} submesh(es)")
        print(f"[BLC] - {len(exp_points)} points (welded from {len(loop_vertices)} loops)")
        print(f"[BLC] - {len(exp_normals)} normals")
        print(f"[BLC] - {fmt_layer(uvs, 'uv')}")
        print(f"[BLC] - {fmt_layer(rgb, 'color')}")
        print(f"[BLC] - {fmt_layer(alphas, 'alpha')}")

        mesh_definitions = []
        submesh_maps = {}

        # Each submesh only gets the vertices its triangles actually
        # use: previously every material slot carried a full copy of all
        # loop-expanded arrays, so SuperLuxCore-side geometry memory scaled
        # with the material count. The mask+remap scheme is O(V) per
        # submesh instead of np.unique's O(V log V) sort.
        vert_count = len(exp_points)
        used_mask = np.zeros(vert_count, dtype=bool)
        remap = np.empty(vert_count, dtype=np.uint32)
        if single_mesh:
            # Proxy bake: one submesh spanning all triangles, bound to
            # material slot 0 — an .lxm proxy is a single-material mesh.
            unique_mats = [0]
        for mat in unique_mats:
            mat_tri_ids = (
                np.arange(exp_tris.shape[0], dtype=np.uint32)
                if single_mesh
                else np.flatnonzero(loop_triangle_materials == mat)
            )
            mat_triangles = exp_tris[mat_tri_ids]
            name = f"{str(mesh_key)}{mat:03d}"

            used_mask.fill(False)
            used_mask[mat_triangles.ravel()] = True
            uniq = np.flatnonzero(used_mask)
            # uniq is sorted; it is the identity iff it covers all verts
            is_identity = len(uniq) == vert_count
            if is_identity:
                sub_points = exp_points
                sub_normals = exp_normals
                sub_uvs = exp_uvs
                sub_rgb = exp_rgb
                sub_alphas = exp_alphas
                sub_tris = mat_triangles
            else:
                remap[uniq] = np.arange(len(uniq), dtype=np.uint32)
                sub_points = exp_points[uniq]
                sub_normals = exp_normals[uniq]
                sub_uvs = [uv[uniq] for uv in exp_uvs]
                sub_rgb = [c[uniq] for c in exp_rgb]
                sub_alphas = [a[uniq] for a in exp_alphas]
                sub_tris = remap[mat_triangles]

            if want_motion_maps:
                # Per-vertex loop indices that let a loop-domain step
                # sample reproduce this submesh's exported layout.
                if is_identity and weld_rep is None:
                    pass  # exported verts == loop domain
                elif is_identity:
                    submesh_maps[name] = weld_rep
                elif weld_rep is None:
                    submesh_maps[name] = uniq
                else:
                    submesh_maps[name] = weld_rep[uniq]

            print(
                f"[BLC] - Submesh #{mat:03d}: {len(mat_triangles)} triangles, "
                f"{len(sub_points)} points"
            )

            superluxcore_scene.DefineMeshExt(
                name=name,
                points=sub_points,
                triangles=sub_tris,
                normals=sub_normals,
                uvs=sub_uvs,
                colors=sub_rgb,
                alphas=sub_alphas,
                transformation=mesh_transform,
            )
            for aov_index, aov in enumerate(exp_aovs):
                sub_aov = aov if is_identity else aov[uniq]
                superluxcore_scene.SetMeshVertexAOV(name, aov_index, sub_aov.tolist())
            for aov_index, attr in enumerate(face_attrs):
                face_vals = named_attributes.face_values(attr)
                superluxcore_scene.SetMeshTriangleAOV(
                    name,
                    aov_index,
                    face_vals[tri_polygon_index[mat_tri_ids]].tolist(),
                )
            mesh_definitions.append((name, mat))

        duration = time() - start_time
        if exporter and exporter.stats:
            exporter.stats.export_time_meshes.value += duration
        print(f"[BLC] Export duration: {duration:.3f}s")
        print("[BLC]")

        # Deformation motion blur (E9): the vertex series is sampled in
        # the loop domain — remember vertex count and a digest of the
        # loop topology so motion_blur.py can validate each shutter step
        # against the exported mesh. Only kept for meshes that may
        # actually collect a vertex series. The digest keeps the
        # signature O(1)-sized — the raw loop index array would be
        # L*4 bytes retained for the whole session on every
        # motion-blurred mesh. `submesh_maps` maps each exported vertex
        # back to a representative loop index (through the weld map when
        # welding merged loops) so per-step positions can be compacted
        # identically.
        vert_sig = None
        if want_motion_maps:
            vert_sig = (
                len(mesh.vertices),
                len(loop_vertices),
                hashlib.blake2b(
                    loop_vertices.tobytes(), digest_size=16
                ).digest(),
            )
        else:
            submesh_maps = None

        return caches.exported_data.ExportedMesh(mesh_definitions, vert_sig, submesh_maps)


@contextmanager
def _prepare_mesh(obj, depsgraph):
    """
    Create a temporary mesh from an object.
    The mesh is guaranteed to be removed when the calling block ends.
    Can return None if no mesh could be created from the object (e.g. for empties)

    Use it like this:

    with mesh_converter.convert(obj, depsgraph) as mesh:
        if mesh:
            print(mesh.name)
            ...
    """

    mesh = None
    object_eval = None

    try:
        object_eval = obj.evaluated_get(depsgraph)
        if object_eval:
            mesh = object_eval.to_mesh()

            if mesh:
                # TODO test if this makes sense
                # has been tested briefly for_v2.10. Seems to work, also
                # including custom normals now
                ## but leaving this out on purpose because
                ## a) users should clean their meshes themselves, and
                ## b) this will allow some artistic effects
                # if object_eval.matrix_world.determinant() < 0.0:
                #     mesh.flip_normals()

                if not mesh.loop_triangles:
                    object_eval.to_mesh_clear()
                    mesh = None

            # TODO implement new normals handling
            if mesh:
                mesh.split_faces()  # Applies smooth by angle operator

        yield mesh
    finally:
        if object_eval and mesh:
            object_eval.to_mesh_clear()
