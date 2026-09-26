# SPDX-License-Identifier: Apache-2.0
"""Cycles/Eevee scene-level compatibility helpers.

`cycles_node_reader` converts shader node trees; this module covers the
Cycles features that live *outside* the node tree:

- per-object ray visibility / shadow catcher / holdout flags
- Cycles light portals (``light.cycles.is_portal`` on area lights)
- world ray visibility (``world.cycles_visibility``)
- Cycles light flags (bounce limit, cast shadow) and object flags
  (light linking, camera/distance culling, caustics tags) that have no
  SuperLuxCore equivalent -> one warning per object, never silent.
"""

import math
import bpy
import pysuperluxcore
from mathutils import Matrix

from .. import utils
from ..utils.errorlog import SuperLuxCoreErrorLog


# ---------------------------------------------------------------------------
# Warning dedup — one warning per (object, flag) per exporter session.
# ---------------------------------------------------------------------------

def _warned_set(exporter):
    warned = getattr(exporter, "_cycles_compat_warned", None)
    if warned is None:
        warned = set()
        exporter._cycles_compat_warned = warned
    return warned


def _warn_once(warned, key, msg, obj_name=""):
    if key in warned:
        return
    warned.add(key)
    SuperLuxCoreErrorLog.add_warning(msg, obj_name=obj_name)


# ---------------------------------------------------------------------------
# Per-object shading flags -> material variants
#
# Cycles stores visibility / shadow-catcher / holdout on the *object*;
# SuperLuxCore stores the equivalent switches on the *material*. When an
# object's flags differ from the defaults we clone the already-exported
# material property block under a flag-derived name so shared materials
# stay untouched. Flags that have no per-ray-type equivalent in the
# engine produce warnings instead of silently wrong output.
# ---------------------------------------------------------------------------

_FLAG_BITS = (
    "visible_diffuse",
    "visible_glossy",
    "visible_transmission",
    "visible_volume_scatter",
    "visible_shadow",
    "is_shadow_catcher",
    "is_holdout",
)


def object_shading_overrides(obj, warned, view_layer=None):
    """Material-property overrides for a depsgraph object.

    Returns a dict of ``scene.materials.X.<key>`` suffix -> value. The
    caller clones the material block and applies these on top.
    """
    overrides = {}
    name = obj.name

    if not obj.visible_diffuse:
        overrides["visibility.indirect.diffuse.enable"] = 0
    if not obj.visible_glossy:
        overrides["visibility.indirect.glossy.enable"] = 0
        # Cycles "Glossy" visibility also covers mirror reflections;
        # SuperLuxCore's specular flag additionally covers refraction,
        # so it is not applied here - mirror rays will still see it.
        _warn_once(
            warned, (name, "glossy"),
            "Ray visibility 'Glossy' only maps to rough-glossy rays in "
            "SuperLuxCore - perfect mirror reflections still show the "
            "object (engine granularity)", name)
    if not obj.visible_transmission:
        _warn_once(
            warned, (name, "transmission"),
            "Ray visibility 'Transmission' has no per-ray-type "
            "equivalent in SuperLuxCore (specular rays cover reflection "
            "and refraction together) - ignored", name)
    if not obj.visible_volume_scatter:
        _warn_once(
            warned, (name, "scatter"),
            "Ray visibility 'Volume Scatter' is not supported by "
            "SuperLuxCore - ignored", name)
    if not obj.visible_shadow:
        # Cycles 'Shadow' off = object casts no shadow. A fully white
        # shadow transparency lets every shadow ray pass through -
        # the material-level equivalent.
        overrides["transparency.shadow"] = [1.0, 1.0, 1.0]

    if obj.is_shadow_catcher:
        overrides["shadowcatcher.enable"] = 1
    # Object flag OR view-layer collection holdout (holdout_get resolves
    # layer_collection.holdout for this object).
    holdout = obj.is_holdout
    if view_layer is not None and not holdout:
        try:
            holdout = obj.holdout_get(view_layer=view_layer)
        except TypeError:
            pass
    if holdout:
        overrides["holdout.enable"] = 1

    return overrides


def apply_object_shading_flags(obj, lux_mat_name, mat_props, exporter,
                               view_layer=None):
    """Return the material name to bind on this object.

    If the object's Cycles shading flags need it, a cloned material
    variant is appended to ``mat_props`` and its name is returned;
    otherwise the original name passes through.
    """
    warned = _warned_set(exporter)
    warn_object_scene_flags(obj, warned)
    overrides = object_shading_overrides(obj, warned, view_layer)
    if not overrides:
        return lux_mat_name

    bits = "".join(
        "0" if getattr(obj, flag, True) else "1" for flag in _FLAG_BITS
    )
    variant = f"{lux_mat_name}__ov{bits}"

    src_prefix = "scene.materials." + lux_mat_name + "."
    dst_prefix = "scene.materials." + variant + "."
    cloned = pysuperluxcore.Properties()
    for name in mat_props.GetAllNames():
        if name.startswith(src_prefix):
            cloned.Set(pysuperluxcore.Property(
                dst_prefix + name[len(src_prefix):],
                mat_props.Get(name).GetStrings()))
    for key, value in overrides.items():
        cloned.Set(pysuperluxcore.Property(dst_prefix + key, value))
    mat_props.Set(cloned)
    return variant


# ---------------------------------------------------------------------------
# Material-datablock Cycles/Eevee settings -> material props / warnings.
# ---------------------------------------------------------------------------

def apply_material_scene_flags(material, props, warned, superluxcore_name):
    """Fold material-datablock Cycles/Eevee settings into the exported
    material props; unsupported raster-era settings warn once."""
    name = material.name
    prefix = "scene.materials." + superluxcore_name + "."

    # 'Use Transparent Shadow' off = fully opaque shadows regardless of
    # alpha/transmission (black shadow transparency).
    if not getattr(material, "use_transparent_shadow", True):
        props.Set(pysuperluxcore.Property(
            prefix + "transparency.shadow", [0.0, 0.0, 0.0]))

    if getattr(material, "use_backface_culling", False):
        _warn_once(warned, (name, "bfc"),
                   "Backface culling is not supported by SuperLuxCore",
                   name)
    srm = getattr(material, "surface_render_method", None)
    if srm is not None and srm != "DITHERED":
        _warn_once(warned, (name, "rendermethod"),
                   f'"{srm.title()}" surface render method is an '
                   "Eevee concept - use a transparent BSDF or alpha "
                   "in the node tree", name)
    if getattr(material, "use_backface_culling_shadow", False):
        _warn_once(warned, (name, "bfcshadow"),
                   "Shadow backface culling is Eevee-only - ignored",
                   name)
    if getattr(material, "use_backface_culling_lightprobe_volume", False):
        _warn_once(warned, (name, "bfclpv"),
                   "Light-probe-volume backface culling is Eevee-only - "
                   "ignored", name)
    if getattr(material, "use_thickness_from_shadow", False):
        _warn_once(warned, (name, "thicknessshadow"),
                   "'Thickness from Shadow' is Eevee-only - ignored",
                   name)
    dm = getattr(material, "displacement_method", "BUMP")
    if dm != "BUMP":
        _warn_once(warned, (name, "displacement"),
                   f'Cycles displacement method "{dm}" - displacement '
                   "is driven by the material Displacement output "
                   "only (no adaptive subdivision/cage)", name)
    vim = getattr(material, "volume_intersection_method", None)
    if vim is not None and vim != "FAST":
        _warn_once(warned, (name, "volintersect"),
                   "Volume intersection method is Eevee-only - "
                   "ignored", name)


# ---------------------------------------------------------------------------
# Object-level Cycles features with no engine equivalent - warn once.
# ---------------------------------------------------------------------------

def warn_object_scene_flags(obj, warned):
    cycles = getattr(obj, "cycles", None)
    name = obj.name
    if cycles is not None:
        if getattr(cycles, "use_camera_cull", False):
            _warn_once(warned, (name, "camcull"),
                       "Cycles camera culling is not supported - the "
                       "object is always exported", name)
        if getattr(cycles, "use_distance_cull", False):
            _warn_once(warned, (name, "distcull"),
                       "Cycles distance culling is not supported - the "
                       "object is always exported", name)
        if getattr(cycles, "is_caustics_caster", False) \
                or getattr(cycles, "is_caustics_receiver", False):
            _warn_once(warned, (name, "caustics"),
                       "Cycles caustics caster/receiver tags are not "
                       "supported - caustic handling is global "
                       "(light tracing / PhotonGI)", name)
        # Deformation blur veto is honoured in motion_blur.py; nothing
        # to warn about - the flag works.

    # Light linking on light objects IS supported (linkgroups); the
    # per-scene plan in light_link_plan() warns for the rest.


# ---------------------------------------------------------------------------
# Cycles light / shadow linking -> linkgroups
#
# Cycles stores the relation on the EMITTER: `receiver_collection` lists the
# objects this emitter illuminates (and `blocker_collection` the shadow
# casters); members carry an INCLUDE/EXCLUDE `link_state` on the
# collection_objects / collection_children records (parallel arrays of
# Collection.objects / .children). "Included minus excluded" wins; a
# collection with only excludes means "everything except".
#
# SuperLuxCore stores the inverse: `scene.lights.X.linkgroups` gives the
# light a group mask L and `scene.objects.Y.linkgroups`/`linkmode=include`
# the receiver's accept mask - the light illuminates the object iff
# (L & acceptMask) != 0, and a light with no groups illuminates everything.
# Shadow (blocker) linking has no engine equivalent and is warned about.
# ---------------------------------------------------------------------------

_LINK_GROUP_PREFIX = "ll_"
_LINK_GROUP_MAX = 64  # Scene::GetLinkGroupBit throws beyond 64 names

# Per-(scene, depsgraph) cached plan - membership edits produce a new
# depsgraph pointer, so the plan rebuilds exactly when relations change.
_link_plan = None
_link_plan_key = None


def light_link_group_name(obj):
    return _LINK_GROUP_PREFIX + utils.sanitize_superluxcore_name(
        utils.get_name_with_lib(obj))


def _link_member_sets(coll):
    """Resolve a linking collection to (included_keys, excluded_keys).

    Blender's effective state is "included minus excluded": an EXCLUDE
    anywhere along the membership path (object edge or enclosing child
    collection) excludes the object even if another path includes it.
    """
    inc, exc = set(), set()

    def visit(c, inherited):
        for co, obj in zip(getattr(c, "collection_objects", ()), c.objects):
            state = co.light_linking.link_state
            # EXCLUDE is sticky: a member inside an excluded child (or
            # itself excluded) lands in the exclude set.
            (inc if state == "INCLUDE" and inherited == "INCLUDE"
             else exc).add(utils.make_key(obj.original))
        for cc, child in zip(
                getattr(c, "collection_children", ()), c.children):
            state = cc.light_linking.link_state
            visit(child, "INCLUDE"
                  if state == "INCLUDE" and inherited == "INCLUDE"
                  else "EXCLUDE")

    visit(coll, "INCLUDE")
    return inc, exc


def invalidate_link_plan():
    """Drop the cached plan; called once per export_scene() so collection
    membership edits between renders are always picked up (the depsgraph
    pointer is not a reliable invalidation token for those)."""
    global _link_plan, _link_plan_key
    _link_plan = None
    _link_plan_key = None


def light_link_plan(scene, dg, warned):
    """(obj_groups, emitter_groups) for a (scene, depsgraph) evaluation.

    obj_groups:     {make_key(obj): set(group_name)} - receivers that
                    accept a restricted set of emitter groups.
    emitter_groups: {make_key(obj): group_name} - emitters (light objects
                    and emissive meshes) carrying a link mask; absent =
                    global emitter.
    """
    global _link_plan, _link_plan_key
    key = (scene.as_pointer(), dg.as_pointer())
    if _link_plan_key != key:
        _link_plan_key = key
        _link_plan = _build_light_link_plan(scene, warned)
    return _link_plan


def _object_is_emissive(obj):
    """Cheap emission check for the mesh-emitter branch of linking."""
    from ..utils import scene_analysis
    mats = getattr(obj.data, "materials", None) if obj.data else None
    return mats is not None and any(
        scene_analysis.material_is_emissive(m) for m in mats)


def _build_light_link_plan(scene, warned):
    obj_groups = {}
    emitter_groups = {}
    for emitter in scene.objects:
        ll = getattr(emitter, "light_linking", None)
        if ll is None:
            continue
        rc = ll.receiver_collection
        bc = ll.blocker_collection
        is_light = emitter.type == "LIGHT"
        if not is_light:
            # Emissive meshes are emitters too: triangle lights inherit
            # scene.objects.X.linkgroups (SceneObjectDefs copies
            # linkGroupMask onto each TriangleLight's linkMask). A
            # non-emissive object cannot be an emitter at all.
            if (rc is not None or bc is not None) \
                    and not _object_is_emissive(emitter):
                _warn_once(warned, ("link", emitter.name),
                           "Light linking on non-emissive objects has no "
                           "effect - ignored", emitter.name)
                continue
        if bc is not None:
            _warn_once(warned, ("linkblocker", emitter.name),
                       "Shadow linking (blocker collection) is not "
                       "supported - all objects cast shadows",
                       emitter.name)
        if rc is None:
            continue
        inc, exc = _link_member_sets(rc)
        if not inc and not exc:
            continue  # empty collection: emitter stays global
        group = light_link_group_name(emitter)
        emitter_groups[utils.make_key(emitter)] = group
        if inc:
            receivers = inc - exc
        else:
            # Exclude-only: every exportable receiver minus the excludes.
            receivers = {
                utils.make_key(o) for o in scene.objects
                if o.type != "LIGHT" and utils.is_obj_visible_in_cycles(o)
            } - exc
        for k in receivers:
            obj_groups.setdefault(k, set()).add(group)

    if len(set(emitter_groups.values())) > _LINK_GROUP_MAX:
        _warn_once(warned, ("linkoverflow",),
                   "%d light-linking groups exceed the engine limit of "
                   "%d - extra links are ignored"
                   % (len(set(emitter_groups.values())), _LINK_GROUP_MAX))
        keep = set(sorted(set(emitter_groups.values()))[:_LINK_GROUP_MAX])
        emitter_groups = {k: g for k, g in emitter_groups.items()
                          if g in keep}
        obj_groups = {k: (gs & keep) for k, gs in obj_groups.items()}
    return obj_groups, emitter_groups


def light_linking_link_group(obj, depsgraph, warned):
    """Link-group name for a light object, or None when it is global."""
    plan = light_link_plan(depsgraph.scene, depsgraph, warned)
    return plan[1].get(utils.make_key(obj))


def object_link_groups(obj, depsgraph, warned):
    """Sorted group names for a scene object (or None).

    A mesh emitter carries its OWN group (scene.objects.X.linkgroups is
    both emitter membership and receiver accept mask - membership wins
    because unioning the accept set would leak the emitter into foreign
    groups); a pure receiver carries its accept set.
    """
    plan = light_link_plan(depsgraph.scene, depsgraph, warned)
    key = utils.make_key(obj)
    emitter_group = plan[1].get(key)
    if emitter_group:
        return [emitter_group]
    groups = plan[0].get(key)
    return sorted(groups) if groups else None


# ---------------------------------------------------------------------------
# Cycles light portals -> path.portal.* rects
#
# An area light flagged "is_portal" in Cycles does not emit light - it
# marks an aperture that guides environment light sampling, which is
# exactly what SuperLuxCore's path.portal quads do. The quad spans the
# light's size x size_y rectangle, wound CCW around the local -Z
# emission normal (same winding as the mesh-light quad).
# ---------------------------------------------------------------------------

def cycles_portal_rects(scene):
    """12-float rects (4 world-space corners) for Cycles portal lights."""
    rects = []
    for obj in scene.objects:
        if obj.type != "LIGHT" or obj.data is None:
            continue
        light = obj.data
        # Only Cycles-mode lights become portals - in SuperLuxCore light
        # mode the object is an actual emitter and is_portal is ignored.
        if light.type != "AREA" \
                or not getattr(light.superluxcore, "use_cycles_settings", False) \
                or not getattr(light.cycles, "is_portal", False):
            continue
        if light.shape not in {"SQUARE", "RECTANGLE"}:
            SuperLuxCoreErrorLog.add_warning(
                f"Light portal '{obj.name}': shape {light.shape.title()} "
                "is not supported (portals must be rectangular)",
                obj_name=obj.name)
            continue
        m = _portal_transform(light, obj.matrix_world)
        corners = []
        for co in ((1, 1, 0), (1, -1, 0), (-1, -1, 0), (-1, 1, 0)):
            p = m @ co
            corners.extend((p.x, p.y, p.z))
        rects.append(corners)
    return rects


def _portal_transform(light, matrix_world):
    """World matrix mapping the unit portal quad (|x|,|y| <= 1, z=0)."""
    from mathutils import Matrix as M
    m = matrix_world.copy()
    m @= M.Scale(light.size / 2, 4, (1, 0, 0))
    if light.shape in {"RECTANGLE", "ELLIPSE"}:
        m @= M.Scale(light.size_y / 2, 4, (0, 1, 0))
    else:
        m @= M.Scale(light.size / 2, 4, (0, 1, 0))
    return m


# ---------------------------------------------------------------------------
# World ray visibility -> world light visibility.* flags
# ---------------------------------------------------------------------------

def apply_world_cycles_visibility(world, definitions, warned):
    cv = getattr(world, "cycles_visibility", None)
    if cv is None:
        return
    if not cv.diffuse:
        definitions["visibility.indirect.diffuse.enable"] = 0
    if not cv.glossy:
        definitions["visibility.indirect.glossy.enable"] = 0
        _warn_once(warned, (world.name, "world-glossy"),
                   "World ray visibility 'Glossy' does not hide the "
                   "environment from perfect mirror rays in "
                   "SuperLuxCore", world.name)
    if not cv.transmission:
        _warn_once(warned, (world.name, "world-transmission"),
                   "World ray visibility 'Transmission' is not "
                   "supported - ignored", world.name)
    if not cv.scatter:
        _warn_once(warned, (world.name, "world-scatter"),
                   "World ray visibility 'Volume Scatter' is not "
                   "supported - ignored", world.name)
    if not cv.shadow:
        _warn_once(warned, (world.name, "world-shadow"),
                   "World ray visibility 'Shadow' is not supported - "
                   "ignored", world.name)
    # cv.camera is handled separately via transparent film in aovs.py


def world_camera_invisible(world):
    """True when the Cycles world should not be visible to camera rays
    (-> transparent film background)."""
    cv = getattr(world, "cycles_visibility", None)
    return cv is not None and not cv.camera


# ---------------------------------------------------------------------------
# Cycles light datablock flags - warnings only.
# ---------------------------------------------------------------------------

def warn_cycles_light_flags(light, warned, obj_name):
    cycles = getattr(light, "cycles", None)
    if cycles is None:
        return
    if getattr(cycles, "max_bounces", 1024) < 1024:
        _warn_once(warned, (obj_name, "maxbounces"),
                   "Cycles 'Max Bounces' per-light limit is not "
                   "supported - global path depth applies", obj_name)
    if getattr(cycles, "is_caustics_light", False):
        _warn_once(warned, (obj_name, "causticslight"),
                   "Cycles 'Caustics Light' tag is not supported - "
                   "caustics are handled globally (light tracing / "
                   "PhotonGI / MNEE)", obj_name)
    if getattr(light, "use_shadow", True) is False:
        _warn_once(warned, (obj_name, "noshadow"),
                   "'Use Shadow' off is not supported - light shadows "
                   "are physical in a path tracer", obj_name)
    if not getattr(cycles, "use_multiple_importance_sampling", True):
        _warn_once(warned, (obj_name, "mis"),
                   "Per-light 'Multiple Importance Sampling' off is not "
                   "supported - light sampling strategy is global",
                   obj_name)
    if getattr(light, "use_nodes", False):
        _warn_once(warned, (obj_name, "lightnodes"),
                   "Cycles light node trees are not supported - the "
                   "light datablock settings are used", obj_name)
    if (light.type == "AREA"
            and getattr(light, "spread", math.pi) < math.pi - 1e-3):
        _warn_once(warned, (obj_name, "spread"),
                   "Area light 'Spread' directional limit is not "
                   "supported - the light emits into the full "
                   "hemisphere", obj_name)


# ---------------------------------------------------------------------------
# Blender view-layer passes -> film.outputs
#
# The SuperLuxCore AOV panel is native; a Cycles-authored scene instead
# enables Blender's use_pass_* flags. Flags with a real film output are
# mapped onto the output set (defaults are all off except combined /
# cryptomatte_accurate, so nothing extra is emitted unless the user
# enabled a pass); the rest produce one warning per view layer.
# ---------------------------------------------------------------------------

_PASS_OUTPUTS = (
    ("use_pass_z", ("DEPTH",)),
    ("use_pass_normal", ("SHADING_NORMAL",)),
    ("use_pass_position", ("POSITION",)),
    ("use_pass_vector", ("MOTION_VECTOR",)),
    ("use_pass_uv", ("UV",)),
    ("use_pass_object_index", ("OBJECT_ID",)),
    ("use_pass_material_index", ("MATERIAL_ID",)),
    ("use_pass_emit", ("EMISSION",)),
    ("use_pass_shadow", ("DIRECT_SHADOW_MASK", "INDIRECT_SHADOW_MASK")),
    ("use_pass_diffuse_direct", ("DIRECT_DIFFUSE",)),
    ("use_pass_diffuse_indirect", ("INDIRECT_DIFFUSE",)),
    ("use_pass_diffuse_color", ("ALBEDO",)),
    ("use_pass_glossy_direct", ("DIRECT_GLOSSY",)),
    ("use_pass_glossy_indirect", ("INDIRECT_GLOSSY",)),
    ("use_pass_glossy_color", ("ALBEDO",)),
    # Cycles transmission covers glossy + specular refraction; the
    # specular part only exists on the indirect side in the engine.
    ("use_pass_transmission_direct", ("DIRECT_GLOSSY_TRANSMIT",)),
    ("use_pass_transmission_indirect",
     ("INDIRECT_GLOSSY_TRANSMIT", "INDIRECT_SPECULAR_TRANSMIT")),
    ("use_pass_transmission_color", ("ALBEDO",)),
    ("use_pass_cryptomatte_object", ("CRYPTOMATTE_OBJECT",)),
    ("use_pass_cryptomatte_material", ("CRYPTOMATTE_MATERIAL",)),
)


def cycles_pass_outputs(view_layer, warned):
    """Film output names implied by the layer's Blender pass flags."""
    outputs = set()
    lname = view_layer.name
    for flag, names in _PASS_OUTPUTS:
        if getattr(view_layer, flag, False):
            outputs.update(names)

    if getattr(view_layer, "use_pass_cryptomatte_asset", False):
        _warn_once(warned, (lname, "cryptoasset"),
                   "Cryptomatte 'Asset' level is not supported - only "
                   "object/material cryptomatte outputs exist", lname)
    if getattr(view_layer, "use_pass_cryptomatte_accurate", False) and (
            getattr(view_layer, "use_pass_cryptomatte_object", False)
            or getattr(view_layer, "use_pass_cryptomatte_material", False)):
        _warn_once(warned, (lname, "cryptoaccurate"),
                   "Cryptomatte 'Accurate' mode is not supported - "
                   "standard cryptomatte is emitted", lname)
    if getattr(view_layer, "use_pass_environment", False):
        _warn_once(warned, (lname, "envpass"),
                   "The Environment pass has no film output - the "
                   "environment is part of every radiance pass", lname)
    if getattr(view_layer, "use_pass_ambient_occlusion", False):
        _warn_once(warned, (lname, "aopass"),
                   "The AO pass has no film output - ambient occlusion "
                   "is a shader-level effect in SuperLuxCore", lname)
    if getattr(view_layer, "use_pass_mist", False):
        _warn_once(warned, (lname, "mistpass"),
                   "The Mist pass is an image-pipeline effect in "
                   "SuperLuxCore - enable Mist under Image Pipeline, "
                   "or use the Depth pass", lname)
    if any(getattr(view_layer, f, False) for f in (
            "use_pass_subsurface_direct", "use_pass_subsurface_indirect",
            "use_pass_subsurface_color")):
        _warn_once(warned, (lname, "ssspass"),
                   "Subsurface passes have no dedicated output - SSS "
                   "energy lands in the diffuse passes", lname)
    if getattr(view_layer, "use_pass_grease_pencil", False):
        _warn_once(warned, (lname, "gppass"),
                   "Grease Pencil / Freestyle passes are not "
                   "supported", lname)
    return outputs


# ---------------------------------------------------------------------------
# World HDRI Mapping node -> infinite light transformation
#
# LuxCore samples the env map at inv(lightToWorld) * -dir, so folding a
# Cycles Mapping node (inverse TRS lookup in 'point' mode) into the
# light transform means composing the *forward* TRS onto the mirror fix.
# ---------------------------------------------------------------------------

def mapping_node_matrix(node, obj_name):
    """Forward TRS matrix of a ShaderNodeMapping, or None when the node
    chain cannot be folded into the infinite-light transformation."""
    if node.bl_idname == "ShaderNodeTexCoord":
        # Generated/Normal on a world shader is the direction itself.
        return Matrix.Identity(4)
    if node.bl_idname != "ShaderNodeMapping":
        SuperLuxCoreErrorLog.add_warning(
            f'World: node "{node.name}" ({node.bl_idname}) driving the '
            "environment Vector input is not supported - the map is "
            "used un-transformed", obj_name=obj_name)
        return None

    if getattr(node, "vector_type", "POINT") != "POINT":
        SuperLuxCoreErrorLog.add_warning(
            f'World: Mapping node "{node.name}" type '
            f'"{node.vector_type}" is approximated as "Point"',
            obj_name=obj_name)

    def _sock(name, fallback):
        sock = node.inputs.get(name)
        if sock is None:
            return fallback
        if sock.is_linked:
            SuperLuxCoreErrorLog.add_warning(
                f'World: linked Mapping "{name}" input is not '
                "supported - default used", obj_name=obj_name)
            return fallback
        return sock.default_value

    loc = _sock("Location", (0.0, 0.0, 0.0))
    rot = _sock("Rotation", (0.0, 0.0, 0.0))
    scale = _sock("Scale", (1.0, 1.0, 1.0))
    return Matrix.LocRotScale(loc, rot, scale)
