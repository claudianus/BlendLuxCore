"""
Generic (Geometry Nodes / named) attribute export for Cycles Attribute nodes.

Blender stores arbitrary per-element data in ``mesh.attributes`` — either
authored directly or written by a Geometry Nodes "Store Named Attribute"
node. SuperLuxCore carries such data on the ExtTriangleMesh:

  * per-vertex float layers ("vertex AOV") read by the
    ``hitpointvertexaov`` texture,
  * per-triangle float layers ("triangle AOV") read by the
    ``hitpointtriangleaov`` texture,
  * per-vertex color layers read by the ``hitpointcolor`` texture.

Color attributes (FLOAT_COLOR/BYTE_COLOR) and UV layers are already exported
through dedicated channels, so only the remaining generic attributes are
collected here. The mapping ``attr_name -> (kind, index)`` is recorded per
object name so the node reader — which runs after the mesh conversion — can
resolve a ShaderNodeAttribute name to the same channel index.
"""

import numpy as np

import bpy

from ..utils.errorlog import SuperLuxCoreErrorLog

# EXTMESH_MAX_DATA_COUNT in SuperLuxCore — max layers per channel
MAX_DATA_LAYERS = 8

KIND_VERTEX_AOV = "vertexaov"
KIND_TRIANGLE_AOV = "triaov"
KIND_COLOR = "color"

# Built-in attributes that duplicate information the engine already has
# (position/normal via hitpoint textures, material_index via submeshes).
_SKIP_NAMES = {"position", "normal", "material_index", "sharp_edge", "sharp_face"}

# Attributes with a leading dot are Blender-internal bookkeeping
# (.select_vert, .corner_vert, .uv_select_face, ...) — never shading data.


def _is_internal(attr):
    return attr.name.startswith(".")

# bpy attribute data_type -> (foreach_get property, dtype, stride)
_ATTR_FMT = {
    "FLOAT": ("value", np.float32, 1),
    "INT": ("value", np.int32, 1),
    "INT8": ("value", np.int8, 1),
    "BOOLEAN": ("value", np.bool_, 1),
    "FLOAT_VECTOR": ("vector", np.float32, 3),
    "FLOAT2": ("vector", np.float32, 2),
}

_SCALAR_TYPES = {"FLOAT", "INT", "INT8", "BOOLEAN"}
_VECTOR_TYPES = {"FLOAT_VECTOR", "FLOAT2"}

# obj_name -> {attribute_name: (kind, index)}
_attr_maps = {}


def clear():
    _attr_maps.clear()


def resolve(obj_name, attribute_name):
    """Return (kind, index) for a generic named attribute, or None.

    Falls back to the object's mesh-datablock name so objects that share
    a converted mesh via instancing resolve the same attribute channels.
    """
    attr_map = _attr_maps.get(obj_name)
    if attr_map is None:
        obj = bpy.data.objects.get(obj_name) if obj_name else None
        mesh_name = getattr(getattr(obj, "data", None), "name", None)
        attr_map = _attr_maps.get(mesh_name) if mesh_name else None
    return attr_map.get(attribute_name) if attr_map else None


def _iter_exportable(mesh):
    """Yield (attribute, kind) in deterministic mesh attribute order."""
    uv_names = {layer.name for layer in mesh.uv_layers}
    color_names = {attr.name for attr in mesh.color_attributes}
    for attr in mesh.attributes:
        if (
            attr.name in uv_names
            or attr.name in color_names
            or attr.name in _SKIP_NAMES
            or _is_internal(attr)
            or attr.data_type not in _ATTR_FMT
        ):
            continue
        if attr.data_type in _SCALAR_TYPES:
            if attr.domain == "FACE":
                yield attr, KIND_TRIANGLE_AOV
            elif attr.domain in {"POINT", "CORNER"}:
                yield attr, KIND_VERTEX_AOV
        elif attr.domain in {"POINT", "CORNER"}:
            yield attr, KIND_COLOR


def _read(attr):
    prop, dtype, stride = _ATTR_FMT[attr.data_type]
    buf = np.empty((len(attr.data), stride), dtype=dtype)
    attr.data.foreach_get(prop, buf.ravel())
    return buf


def collect(mesh, loop_vertices, base_color_count, obj_name):
    """Collect exportable generic attributes of `mesh`.

    `loop_vertices` maps loop -> vertex index (the mesh is exported in
    loop-expanded form). `base_color_count` is the number of color layers
    already occupied by mesh.color_attributes.

    Returns (vert_aov_layers, face_attrs, extra_color_layers) where
    vert_aov_layers is a list of per-loop float arrays, face_attrs a list
    of FACE-domain attributes (expanded per submesh by the caller), and
    extra_color_layers a list of per-loop float3 arrays to append to the
    exported color layers.
    """
    vert_aovs = []
    face_attrs = []
    extra_cols = []
    attr_map = {}
    color_index = base_color_count

    for attr, kind in _iter_exportable(mesh):
        if kind == KIND_VERTEX_AOV:
            if len(vert_aovs) >= MAX_DATA_LAYERS:
                _warn_budget(attr, "vertex AOV", obj_name)
                continue
            vals = _read(attr).astype(np.float32).ravel()
            if attr.domain == "POINT":
                vals = vals[loop_vertices]
            attr_map[attr.name] = (kind, len(vert_aovs))
            vert_aovs.append(vals)
        elif kind == KIND_TRIANGLE_AOV:
            if len(face_attrs) >= MAX_DATA_LAYERS:
                _warn_budget(attr, "triangle AOV", obj_name)
                continue
            attr_map[attr.name] = (kind, len(face_attrs))
            face_attrs.append(attr)
        else:  # KIND_COLOR
            if color_index >= MAX_DATA_LAYERS:
                _warn_budget(attr, "color", obj_name)
                continue
            vals = _read(attr).astype(np.float32)
            if vals.shape[1] == 2:
                vals = np.column_stack(
                    (vals, np.zeros(len(vals), np.float32))
                )
            if attr.domain == "POINT":
                vals = vals[loop_vertices]
            attr_map[attr.name] = (kind, color_index)
            extra_cols.append(vals)
            color_index += 1

    # Always overwrite — a delta re-export must not keep stale indices
    # when attributes were removed. The mesh-datablock name is a
    # secondary key so instanced duplicates (which share the converted
    # mesh and never re-run collect()) can resolve attributes too.
    _attr_maps[obj_name] = attr_map
    if mesh.name != obj_name:
        _attr_maps.setdefault(mesh.name, attr_map)
    return vert_aovs, face_attrs, extra_cols


def face_values(attr):
    """Per-polygon float values of a FACE-domain attribute."""
    return _read(attr).astype(np.float32).ravel()


def _warn_budget(attr, channel, obj_name):
    SuperLuxCoreErrorLog.add_warning(
        f'Attribute "{attr.name}": more than {MAX_DATA_LAYERS} exportable '
        f"{channel} layers on the mesh; attribute skipped",
        obj_name=obj_name,
    )
