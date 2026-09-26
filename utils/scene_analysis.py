"""Material/emitter scene scans shared by exporters.

Used by the AUTO light strategy (emitter counting) and Cycles light
linking (emissive-material detection). Node walks cover BOTH material
systems (SuperLuxCore node trees and Cycles node trees); heuristics are
cheap and conservative: a false positive only enables a correct-but-
slower path, never a wrong result.
"""

# --- node classifiers --------------------------------------------------------

# Nodes that transmit light => caustics are plausible
_TRANSMISSIVE_LUX = frozenset((
    "SuperLuxCoreNodeMatGlass",  # glass / roughglass / archglass
    "SuperLuxCoreNodeMatMatteTranslucent",
    "SuperLuxCoreNodeMatGlossyTranslucent",
))
# Containers that can hide a transmissive material behind an input link
_CONTAINER_LUX = frozenset((
    "SuperLuxCoreNodeMatMix",
    "SuperLuxCoreNodeMatTwoSided",
))
_SSS_LUX = frozenset((
    "SuperLuxCoreNodeMatMatteTranslucent",
    "SuperLuxCoreNodeMatGlossyTranslucent",
))
_TRANSMISSIVE_CYCLES = frozenset((
    "ShaderNodeBsdfGlass",
    "ShaderNodeBsdfRefraction",
    "ShaderNodeBsdfTranslucent",
))
_CONTAINER_CYCLES = frozenset((
    "ShaderNodeMixShader",
    "ShaderNodeAddShader",
))
_VOLUME_LUX = frozenset((
    "SuperLuxCoreNodeVolHomogeneous",
    "SuperLuxCoreNodeVolHeterogeneous",
))
_VOLUME_CYCLES = frozenset((
    "ShaderNodeVolumeScatter",
    "ShaderNodeVolumeAbsorption",
    "ShaderNodeVolumePrincipled",
))
_EMISSIVE_NODES = frozenset((
    "SuperLuxCoreNodeMatEmission",
    "ShaderNodeEmission",
))
# Weight-parametrized nodes that may feed a container slot
_WEIGHT_NODES = frozenset((
    "SuperLuxCoreNodeMatOpenPBR",
    "ShaderNodeBsdfPrincipled",
))

# Emitters above this count make ReSTIR DI the better light strategy
# (kept in sync with the AUTO resolution in export/config.py)
AUTO_RESTIR_EMITTER_THRESHOLD = 16


def _weight_input(node, name):
    """Weight-style input counts as active when linked or > 0."""
    inp = node.inputs.get(name)
    if inp is None:
        return False
    if inp.is_linked:
        return True
    try:
        return inp.default_value > 0
    except (TypeError, AttributeError):
        return False


def _linked_node_ids(node):
    ids = set()
    for inp in node.inputs:
        for link in inp.links:
            ids.add(link.from_node.bl_idname)
    return ids


def _scan_nodes(nodes, flags):
    """Update feature flags in-place from one node list."""
    for node in nodes:
        t = node.bl_idname
        if t == "SuperLuxCoreNodeMatGlass":
            flags["transmissive"] = True
            # archglass/roughglass do not support dispersion
            if not getattr(node, "architectural", False) and \
                    not getattr(node, "rough", False) and \
                    _weight_input(node, "Dispersion"):
                flags["dispersion"] = True
        elif t == "SuperLuxCoreNodeMatOpenPBR":
            if _weight_input(node, "Transmission Weight"):
                flags["transmissive"] = True
            if _weight_input(node, "Subsurface Weight"):
                flags["sss"] = True
            if _weight_input(node, "Dispersion"):
                flags["dispersion"] = True
        elif t == "ShaderNodeBsdfPrincipled":
            if _weight_input(node, "Transmission Weight"):
                flags["transmissive"] = True
            if _weight_input(node, "Subsurface Weight"):
                flags["sss"] = True
            if _weight_input(node, "Emission Strength"):
                flags["emission"] = True
        elif t in _TRANSMISSIVE_LUX or t in _TRANSMISSIVE_CYCLES:
            flags["transmissive"] = True
            if t in _SSS_LUX:
                flags["sss"] = True
        elif t in _CONTAINER_LUX or t in _CONTAINER_CYCLES:
            linked = _linked_node_ids(node)
            if linked & (_TRANSMISSIVE_LUX | _TRANSMISSIVE_CYCLES |
                    _WEIGHT_NODES | {"SuperLuxCoreNodeMatGlass"}):
                flags["transmissive"] = True
            if linked & (_SSS_LUX | _WEIGHT_NODES |
                    {"ShaderNodeSubsurfaceScattering"}):
                flags["sss"] = True
        elif t == "ShaderNodeSubsurfaceScattering":
            flags["sss"] = True
        elif t in _VOLUME_LUX or t in _VOLUME_CYCLES:
            flags["volume"] = True
        elif t in _EMISSIVE_NODES:
            flags["emission"] = True
        elif t in ("SuperLuxCoreNodeMatOutput", "ShaderNodeOutputMaterial"):
            for inp in node.inputs:
                if "volume" in inp.name.lower() and inp.is_linked:
                    flags["volume"] = True
                    break


def material_is_emissive(mat):
    """Does this material emit light? (Cycles + SuperLuxCore trees.)"""
    if mat is None or not mat.use_nodes:
        return False
    flags = {"emission": False}
    _scan_nodes(mat.node_tree.nodes, flags)
    lux_nt = getattr(mat.superluxcore, "node_tree", None)
    if lux_nt is not None:
        _scan_nodes(lux_nt.nodes, flags)
    return flags["emission"]


def count_emitters(scene):
    """Estimate the number of distinct emitters for strategy selection.

    Light objects count once each; a mesh with an emissive material is
    weighted by polygon count because every triangle becomes a separate
    light in the engine; a lit world background counts once.
    """
    count = 0
    emissive_mats = set()
    for obj in scene.objects:
        if obj.type == "LIGHT":
            count += 1
        elif obj.type == "MESH" and obj.data is not None:
            mats = getattr(obj.data, "materials", None)
            if mats is None:
                continue
            for mat in mats:
                if mat is None:
                    continue
                if mat not in emissive_mats:
                    if not material_is_emissive(mat):
                        continue
                    emissive_mats.add(mat)
                # Polygons approximate the internal per-triangle light
                # count without needing a triangulation pass.
                count += max(1, len(obj.data.polygons))
                break

    world = scene.world
    if world is not None and getattr(world, "use_nodes", False):
        count += 1

    return count
