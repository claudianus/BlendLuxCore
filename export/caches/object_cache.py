import bpy
from array import array
from contextlib import contextmanager
from functools import lru_cache
from time import time

from ... import utils
import pyluxcore
from .. import mesh_converter
from .. import named_attributes
from ..hair import (
    convert_hair,
    warn_about_missing_uvs,
    set_hair_props,
    make_hair_shape_name,
    get_hair_material_index,
    convert_hair_curves,
)
from .exported_data import ExportedObject, ExportedPart
from .. import light, material, pointcloud, volume, cycles_node_reader
from ...utils.errorlog import LuxCoreErrorLog
from ...utils import node as utils_node
from ...utils import MESH_OBJECTS
from ...utils.node import get_active_output


class TriAOVDataIndices:
    RANDOM_PER_ISLAND_INT = 0
    RANDOM_PER_ISLAND_FLOAT = 1


MAX_PARTICLES_FOR_LIVE_TRANSFORM = 2000


def _instance_key(dg_obj_instance):
    # Stable inter-frame identity for a dupli/particle instance
    # (A5 motion blur). persistent_id is only unique per instancer, so
    # the instancer pointer is included: two emitters sharing one dupli
    # object would otherwise alias their particle ids. parent is None
    # for some instance types, hence the 0 fallback.
    parent = dg_obj_instance.parent
    parent_ptr = parent.original.as_pointer() if parent else 0
    return (parent_ptr, tuple(dg_obj_instance.persistent_id))


def _dupli_motion_enabled(dg_obj_instance):
    # Opt-in for instance transform motion blur (A5): enable_motion_blur
    # on the instanced object OR on the instancer (emitter). Checking
    # both keeps the first instance's object-level motion props and the
    # duplicated instances consistent — flagging either side blurs all
    # copies instead of a subset.
    if dg_obj_instance.object.luxcore.enable_motion_blur:
        return True
    parent = dg_obj_instance.parent
    return bool(parent and parent.luxcore.enable_motion_blur)


@contextmanager
def _timed(exporter, stat_name):
    # Accumulates elapsed seconds into exporter.stats.<stat_name> when
    # stats collection is active; zero-cost no-op otherwise (A6 stage
    # instrumentation).
    if exporter and exporter.stats:
        start = time()
        yield
        getattr(exporter.stats, stat_name).value += time() - start
    else:
        yield


def uses_pointiness(node_tree):
    # TODO better check would be if the node is linked to the output and actually used
    return utils_node.has_nodes(node_tree, "LuxCoreNodeTexPointiness", True)


def uses_random_per_island_uniform_float(node_tree):
    # TODO better check would be if the node is linked to the output and actually used
    return utils_node.has_nodes(
        node_tree, "LuxCoreNodeTexRandomPerIsland", True
    )


def uses_random_per_island_int(node_tree):
    # TODO better check would be if the node is linked to the output and actually used
    for node in utils_node.find_nodes_multi(
        node_tree, {"LuxCoreNodeTexMapping2D", "LuxCoreNodeTexMapping3D"}, True
    ):
        if (
            node.mapping_type in {"uvrandommapping2d", "localrandommapping3d"}
            and node.seed_type == "mesh_islands"
        ):
            return True
    return False


def needs_edge_detector_shape(node_tree):
    # TODO better check would be if the node is linked to the output and actually used
    for node in utils_node.find_nodes(
        node_tree, "LuxCoreNodeTexWireframe", True
    ):
        if node.hide_planar_edges:
            return True
    # The bevel texture reads per-edge angles written by edgedetectoraov
    if utils_node.find_nodes(node_tree, "ShaderNodeBevel", True):
        return True
    return False


def uses_displacement(obj):
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if not mat:
            continue
        if (
            mat.luxcore.node_tree
            and utils_node.has_nodes_multi(
                mat.luxcore.node_tree,
                {
                    "LuxCoreNodeShapeHeightDisplacement",
                    "LuxCoreNodeShapeVectorDisplacement",
                },
                True,
            )
        ):
            return True
        # Cycles-routed material with a Displacement output link
        if (
            not mat.luxcore.node_tree
            and cycles_node_reader.get_displacement_link(mat.original) is not None
        ):
            return True
    return False


def _apply_cycles_displacement(shape, obj, mat_index, depsgraph, scene_props):
    """
    Wraps the shape in a LuxCore "displacement" shape when the material on
    mat_index is a Cycles-routed material whose output Displacement socket
    is driven by a Displacement/Vector Displacement node.
    """
    mat = get_material(obj, mat_index, depsgraph)
    if mat is None:
        return shape
    link = cycles_node_reader.get_displacement_link(mat.original)
    if link is None:
        return shape

    disp = cycles_node_reader.export_displacement(
        link, scene_props, mat.original, obj.name
    )
    if disp is None:
        LuxCoreErrorLog.add_warning(
            "Material output Displacement is only supported through "
            "Displacement/Vector Displacement nodes",
            obj_name=obj.name,
        )
        return shape

    disp_shape = "%s_disp%d" % (shape, mat_index)
    prefix = "scene.shapes." + disp_shape + "."
    scene_props.Set(pyluxcore.Property(prefix + "type", "displacement"))
    scene_props.Set(pyluxcore.Property(prefix + "source", shape))
    scene_props.Set(pyluxcore.Property(prefix + "map", disp["map"]))
    scene_props.Set(pyluxcore.Property(prefix + "map.type", disp["map.type"]))
    scene_props.Set(pyluxcore.Property(prefix + "scale", disp["scale"]))
    scene_props.Set(pyluxcore.Property(prefix + "offset", disp["offset"]))
    scene_props.Set(pyluxcore.Property(prefix + "normalsmooth", True))
    return disp_shape


def define_shapes(input_shape, node_tree, exporter, depsgraph, scene_props):
    shape = input_shape

    output_node = get_active_output(node_tree)
    if output_node:
        # Convert the whole shape stack
        shape = output_node.inputs["Shape"].export_shape(
            exporter, depsgraph, scene_props, shape
        )

    # Add some shapes at the end that are required by some nodes in the node tree

    if uses_pointiness(node_tree):
        # Note: Since Blender still does not make use of the vertex alpha channel
        # as of 2.82, we use it to store the pointiness information.
        pointiness_shape = input_shape + "_pointiness"
        prefix = "scene.shapes." + pointiness_shape + "."
        scene_props.Set(pyluxcore.Property(prefix + "type", "pointiness"))
        scene_props.Set(pyluxcore.Property(prefix + "source", shape))
        shape = pointiness_shape

    _uses_random_per_island_uniform_float = (
        uses_random_per_island_uniform_float(node_tree)
    )
    _uses_random_per_island_int = uses_random_per_island_int(node_tree)
    if _uses_random_per_island_uniform_float or _uses_random_per_island_int:
        island_aov_index = TriAOVDataIndices.RANDOM_PER_ISLAND_INT

        if not _uses_random_per_island_int:
            # We don't need the int result, so use the float index for it so it gets overwrittten to save memory
            island_aov_index = TriAOVDataIndices.RANDOM_PER_ISLAND_FLOAT

        island_aov_shape = input_shape + "_island_aov"
        prefix = "scene.shapes." + island_aov_shape + "."
        scene_props.Set(pyluxcore.Property(prefix + "type", "islandaov"))
        scene_props.Set(pyluxcore.Property(prefix + "source", shape))
        scene_props.Set(
            pyluxcore.Property(prefix + "dataindex", island_aov_index)
        )
        shape = island_aov_shape

        if _uses_random_per_island_uniform_float:
            # Used to normalize the island indices from ints to floats in 0..1 range
            random_tri_aov_shape = input_shape + "_random_tri_aov_shape"
            prefix = "scene.shapes." + random_tri_aov_shape + "."
            scene_props.Set(
                pyluxcore.Property(prefix + "type", "randomtriangleaov")
            )
            scene_props.Set(pyluxcore.Property(prefix + "source", shape))
            scene_props.Set(
                pyluxcore.Property(prefix + "srcdataindex", island_aov_index)
            )
            scene_props.Set(
                pyluxcore.Property(
                    prefix + "dstdataindex",
                    TriAOVDataIndices.RANDOM_PER_ISLAND_FLOAT,
                )
            )
            shape = random_tri_aov_shape

    if needs_edge_detector_shape(node_tree):
        edge_detector_shape = input_shape + "_edge_detector"
        prefix = "scene.shapes." + edge_detector_shape + "."
        scene_props.Set(pyluxcore.Property(prefix + "type", "edgedetectoraov"))
        scene_props.Set(pyluxcore.Property(prefix + "source", shape))
        shape = edge_detector_shape

    return shape


def warn_about_subdivision_levels(obj):
    for modifier in obj.modifiers:
        if modifier.type == "SUBSURF" and modifier.show_viewport:
            if not modifier.show_render:
                LuxCoreErrorLog.add_warning(
                    "Subdivision modifier enabled in viewport, but not in final render",
                    obj_name=obj.name,
                )
            elif modifier.render_levels < modifier.levels:
                LuxCoreErrorLog.add_warning(
                    f"Final render subdivision level ({modifier.render_levels}) smaller than viewport subdivision level ({modifier.levels})",
                    obj_name=obj.name,
                )


def get_material(obj, material_index, depsgraph):
    material_override = (
        depsgraph.view_layer_eval.material_override
    )  # the view layer override material
    # Evaluate if the override_exclude checkbox is ticked
    override_exclude = False
    material = None
    if material_index < len(obj.material_slots):
        material = obj.material_slots[material_index].material
    if material is not None:
        node_tree = material.luxcore.node_tree
        if (
            node_tree is not None
        ):  # happens e.g. in default cube scene when only cycles nodes are defined
            output_node = get_active_output(node_tree)
            try:
                override_exclude = output_node.override_exclude
            except AttributeError:
                override_exclude = True

    if material_override and material is None:
        mat = material_override
    elif material_override and not override_exclude:
        mat = material_override
    elif material_index < len(obj.material_slots):
        mat = material

        if mat is None:
            # Note: material.convert returns the fallback material in this case
            msg = "No material attached to slot %d" % (material_index + 1)
            LuxCoreErrorLog.add_warning(msg, obj_name=obj.name)
    else:
        # The object has no material slots
        LuxCoreErrorLog.add_warning("No material defined", obj_name=obj.name)
        # Use fallback material
        mat = None

    return mat


def export_material(
    obj, material_index, exporter, depsgraph, is_viewport_render
):
    mat = get_material(obj, material_index, depsgraph)

    if mat:
        # We need the original material, not the evaluated one, otherwise
        # Blender gives us "NodeTreeUndefined" as mat.node_tree.bl_idname
        mat = mat.original

        lux_mat_name, mat_props = material.convert(
            exporter, depsgraph, mat, is_viewport_render, obj.name
        )
        node_tree = mat.luxcore.node_tree
        return lux_mat_name, mat_props, node_tree
    else:
        lux_mat_name, mat_props = material.fallback()
        return lux_mat_name, mat_props, None


def make_psys_key(obj, psys, is_instance):
    psys_lib_name = psys.settings.library.name if psys.settings.library else ""
    return obj.name_full + psys.name + psys_lib_name + str(is_instance)


def get_total_particle_count(particle_system, is_viewport_render):
    """
    Note: this function does not return the amount of particles that are actually visible in a given
    frame (because it's hard to find that number), but the maximum number the particle system will ever create.
    """
    settings = particle_system.settings

    if (
        settings.render_type in {"NONE", "HALO", "LINE"}
        or (settings.type == "HAIR" and settings.render_type == "PATH")
        or (is_viewport_render and settings.display_method != "RENDER")
    ):
        return 0

    particle_count = settings.count
    if is_viewport_render:
        particle_count *= settings.display_percentage / 100
    if settings.child_type != "NONE":
        particle_count *= (
            settings.child_percent
            if is_viewport_render
            else settings.rendered_child_count
        )
    return particle_count


@lru_cache(maxsize=32)
def supports_live_transform(particle_system):
    if not particle_system:
        return True
    total_particles = get_total_particle_count(particle_system, True)
    return total_particles <= MAX_PARTICLES_FOR_LIVE_TRANSFORM


def _update_stats(
    engine, current_obj_name, extra_obj_info, current_index, total_object_count
):
    engine.update_stats(
        "Export",
        f"Object: {current_obj_name}{extra_obj_info} ({current_index}/{total_object_count})",
    )
    engine.update_progress(current_index / total_object_count)


def get_obj_count_estimate(depsgraph):
    # This is faster than len(depsgraph.object_instances)
    # TODO: count dupliverts and dupliframes
    obj_count = len(depsgraph.objects)
    for obj in depsgraph.objects:
        try:
            for psys in obj.particle_systems:
                obj_count += get_total_particle_count(psys, False)
        except AttributeError:
            pass
    return obj_count


class Duplis:
    def __init__(self, exported_obj):
        self.exported_obj = exported_obj
        self.matrices = array("f", [])
        self.object_ids = array("I", [])
        # Transform motion blur for instances (A5). `keys` is allocated
        # only when object blur is enabled and the instanced object opts
        # in via luxcore.enable_motion_blur; it stores one
        # (instancer_ptr, persistent_id) key per instance, parallel to
        # object_ids. motion_blur.convert() then fills motion/motion_times
        # as [instance][step]-major buffers for Scene.DuplicateObject's
        # motion-multi overload. `motion_missing` counts steps where an
        # instance had no evaluated transform and fell back to its
        # center-frame matrix (particle born/died mid-shutter).
        self.keys = None
        self.motion = None
        self.motion_times = None
        self.motion_steps = 0
        self.motion_missing = 0
        # Source object's luxcore.id, cached at Duplis creation: it is a
        # per-object constant, so reading original.luxcore.id per instance
        # would be a wasted 4-level RNA traversal in the hot loop.
        self.luxcore_id = -1
        # Compound instance key of the first (base) instance — lets the
        # persistent-scene delta find this source's geo_meta entry
        # (A6-III instancer refresh).
        self.obj_key = None

    def get_count(self):
        return len(self.object_ids)


class ObjectCache2:
    def __init__(self):
        self.exported_objects = {}
        self.exported_meshes = {}
        self.exported_hair = {}
        self.pending_pointcloud_duplicates = []
        # {obj_key: (baked matrix_world, delta-safe)} used by the
        # persistent-scene cache for transform-only deltas (A6-II).
        self.bake_matrices = {}
        # {obj_key: (mesh src ptr, mesh_key, use_instancing, base shape
        # names, has wrapper shapes)} — geometry-delta eligibility
        # metadata for the persistent-scene cache (A6-III).
        self.obj_geo_meta = {}
        # {instancer obj_key: set(source original ptr)} — which objects
        # each instancer emitted duplis of, and
        # {instancer obj_key} whose instances took the singular
        # (per-instance ExportedObject) path instead of a dupli set —
        # populated by first_run for the persistent-scene delta.
        self.instancer_srcs = {}
        self.instancer_singular = set()

    def first_run(
        self,
        exporter,
        depsgraph,
        view_layer,
        engine,
        luxcore_scene,
        scene_props,
        context,
    ):
        is_viewport_render = bool(context)
        instances = {}
        # Fresh export: drop any generic-attribute name→index maps a
        # previous session registered.
        named_attributes.clear()
        # Persistent-scene delta bookkeeping: for every instancer, the
        # set of source objects it spawned duplis of (fast path) or a
        # marker that some of its instances were exported individually
        # (singular path — such instancers cannot be delta-refreshed).
        self.instancer_srcs = {}
        self.instancer_singular = set()

        if engine:
            obj_count_estimate = max(1, get_obj_count_estimate(depsgraph))
        else:
            obj_count_estimate = 0

        # Particle system counts might have changed
        supports_live_transform.cache_clear()

        # Hoisted out of the per-instance fast path below: one global
        # lookup instead of an attribute chain per instance.
        blender_mat_to_list = pyluxcore.BlenderMatrix4x4ToList

        for index, dg_obj_instance in enumerate(depsgraph.object_instances):
            obj = dg_obj_instance.object

            if (
                dg_obj_instance.is_instance
                and not (
                    is_viewport_render
                    and supports_live_transform(
                        dg_obj_instance.particle_system
                    )
                )
                and obj.type in MESH_OBJECTS
            ):
                # This code is optimized for large amounts of duplis. Drawback is that objects generated from this
                # code can't be transformed later in a viewport render session (due to BlendLuxCore implementation
                # reasons, not because of LuxCore)
                if dg_obj_instance.parent is not None:
                    # Record unconditionally (even for instances skipped
                    # below): the refresh path compares this source set
                    # against the instancer's current depsgraph output.
                    self.instancer_srcs.setdefault(
                        utils.make_key(dg_obj_instance.parent), set()
                    ).add(obj.original.as_pointer())
                if engine and index % 5000 == 0:
                    if engine.test_break():
                        return None
                    _update_stats(
                        engine, obj.name, " (dupli)", index, obj_count_estimate
                    )

                try:
                    # The code in this try block is performance-critical, as it is
                    # executed most often when exporting millions of instances.
                    duplis = instances[obj.original.as_pointer()]
                    # If duplis is None, then a non-exportable object (e.g. a curve
                    # with zero faces or an object excluded from the render) is
                    # being duplicated
                    if duplis:
                        # The per-object part of utils.is_instance_visible() was
                        # already checked when the duplis entry was created in the
                        # except branch below. The remaining checks are
                        # per-instance and have to be done every time.
                        if not (
                            dg_obj_instance.show_self
                            or dg_obj_instance.show_particles
                        ):
                            continue
                        if context:
                            viewport_vis_obj = (
                                dg_obj_instance.parent
                                if dg_obj_instance.parent
                                else obj
                            )
                            if not viewport_vis_obj.visible_in_viewport_get(
                                context.space_data
                            ):
                                continue
                        obj_id = duplis.luxcore_id
                        if obj_id == -1:
                            obj_id = dg_obj_instance.random_id & 0xFFFFFFFE
                        duplis.object_ids.append(obj_id)
                        if duplis.keys is not None:
                            duplis.keys.append(
                                _instance_key(dg_obj_instance)
                            )
                        # We need a copy of matrix_world here, not sure why, but if we don't
                        # make a copy, we only get an identity matrix in C++
                        duplis.matrices.extend(
                            blender_mat_to_list(
                                dg_obj_instance.matrix_world.copy()
                            )
                        )
                except KeyError:
                    if engine:
                        if engine.test_break():
                            return None
                        _update_stats(
                            engine,
                            obj.name,
                            " (dupli)",
                            index,
                            obj_count_estimate,
                        )
                    # Same checks as utils.is_instance_visible() in the non-fast
                    # path below. Objects that are not renderable at all
                    # (exclude_from_render, disabled Cycles ray visibility etc.)
                    # get a None entry so all their remaining duplis are
                    # skipped cheaply in the try block above.
                    if not utils.is_obj_visible(obj):
                        instances[obj.original.as_pointer()] = None
                        continue
                    if not (
                        dg_obj_instance.show_self
                        or dg_obj_instance.show_particles
                    ):
                        continue
                    if context:
                        viewport_vis_obj = (
                            dg_obj_instance.parent
                            if dg_obj_instance.parent
                            else obj
                        )
                        if not viewport_vis_obj.visible_in_viewport_get(
                            context.space_data
                        ):
                            continue
                    exported_obj = self._convert_obj(
                        exporter,
                        dg_obj_instance,
                        obj,
                        depsgraph,
                        luxcore_scene,
                        scene_props,
                        is_viewport_render,
                        view_layer,
                        engine,
                    )
                    if exported_obj:
                        # Note, the transformation matrix and object ID of this first instance is not added
                        # to the duplication list, since it already exists in the scene
                        new_duplis = Duplis(exported_obj)
                        new_duplis.obj_key = utils.make_key_from_instance(
                            dg_obj_instance
                        )
                        new_duplis.luxcore_id = obj.original.luxcore.id
                        if (
                            exporter.object_blur_enabled
                            and _dupli_motion_enabled(dg_obj_instance)
                        ):
                            new_duplis.keys = []
                        instances[obj.original.as_pointer()] = new_duplis
                    else:
                        # Could not export the object, happens e.g. with curve objects with zero faces
                        instances[obj.original.as_pointer()] = None
            else:
                # This code is for singular objects and for duplis that should be movable later in a viewport render
                if (
                    dg_obj_instance.is_instance
                    and dg_obj_instance.parent is not None
                ):
                    # Instances converted one-by-one (non-mesh sources,
                    # viewport live-transform particles): their matrices
                    # live on per-instance ExportedObjects a dupli
                    # re-flush cannot reach — flag the instancer.
                    self.instancer_singular.add(
                        utils.make_key(dg_obj_instance.parent)
                    )
                if not utils.is_instance_visible(
                    dg_obj_instance, obj, context
                ):
                    continue

                if engine:
                    if engine.test_break():
                        return None
                    _update_stats(
                        engine, obj.name, "", index, obj_count_estimate
                    )

                self._convert_obj(
                    exporter,
                    dg_obj_instance,
                    obj,
                    depsgraph,
                    luxcore_scene,
                    scene_props,
                    is_viewport_render,
                    view_layer,
                    engine,
                )

        if exporter.stats:
            exporter.stats.exported_object_count.value = len(
                self.exported_objects
            )
        # self._debug_info()
        return instances

    def duplicate_instances(self, instances, luxcore_scene, stats):
        """
        We can only duplicate the instances *after* the scene_props were parsed so the base
        objects are available for luxcore_scene. Needs to happen before this method is called.
        """
        start_time = time()

        # Point clouds: one icosphere instance per point beyond the base object
        instance_count = self._flush_pointcloud_duplicates(luxcore_scene)
        for duplis in instances.values():
            if duplis is None:
                # If duplis is None, then a non-exportable object like a curve with zero faces is being duplicated
                continue

            if duplis.get_count() == 0:
                # Only one instance was created (and is already present in the luxcore_scene), nothing to duplicate
                continue

            instance_count += duplis.get_count()

            for part in duplis.exported_obj.parts:
                src_name = part.lux_obj
                dst_name = src_name + "dupli"
                if duplis.motion is not None and duplis.motion_steps > 1:
                    # Transform motion blur for instances (A5): per-instance
                    # [step] time series collected by motion_blur.convert().
                    luxcore_scene.DuplicateObject(
                        src_name,
                        dst_name,
                        duplis.get_count(),
                        duplis.motion_steps,
                        duplis.motion_times,
                        duplis.motion,
                        duplis.object_ids,
                    )
                else:
                    luxcore_scene.DuplicateObject(
                        src_name,
                        dst_name,
                        duplis.get_count(),
                        duplis.matrices,
                        duplis.object_ids,
                    )

        if stats:
            stats.export_time_instancing.value = time() - start_time
            stats.instance_count.value = instance_count

    def _flush_pointcloud_duplicates(self, luxcore_scene):
        count = 0
        for (
            src_name, matrices, count_, object_ids, obj_key
        ) in self.pending_pointcloud_duplicates:
            exported = self.exported_objects.get(obj_key)
            if exported is not None and exported.pc_motion is not None:
                # Per-point motion blur: [instance][step] buffers built by
                # motion_blur.convert() from re-evaluated point positions.
                luxcore_scene.DuplicateObject(
                    src_name,
                    src_name + "dupli",
                    count_,
                    exported.pc_steps_n,
                    exported.pc_motion_times,
                    exported.pc_motion,
                    object_ids,
                )
            else:
                luxcore_scene.DuplicateObject(
                    src_name, src_name + "dupli", count_, matrices, object_ids
                )
            count += count_
        self.pending_pointcloud_duplicates.clear()
        return count

    def _debug_info(self):
        print("Objects in cache:", len(self.exported_objects))
        print("Meshes in cache:", len(self.exported_meshes))
        # for key, exported_mesh in self.exported_meshes.items():
        #     if exported_mesh:
        #         print(key, exported_mesh.mesh_definitions)
        #     else:
        #         print(key, "mesh is None")

    def _get_mesh_key(self, obj, use_instancing, is_viewport_render=True):
        # Important: we need the data of the original object, not the evaluated one.
        # The instancing state has to be part of the key because a non-instanced mesh
        # has its transformation baked-in and can't be used by other instances.
        modified = utils.has_deforming_modifiers(obj.original)
        source = (
            obj.original.data
            if (use_instancing and not (modified or obj.type == "META"))
            else obj.original
        )
        key = utils.get_luxcore_name(source, is_viewport_render)
        if use_instancing:
            key += "_instance"
        return key

    def _convert_obj(
        self,
        exporter,
        dg_obj_instance,
        obj,
        depsgraph,
        luxcore_scene,
        scene_props,
        is_viewport_render,
        view_layer=None,
        engine=None,
    ):
        """Convert one DepsgraphObjectInstance amd keep track of it with self.exported_objects"""

        if obj.data is None:
            return None
        warn_about_subdivision_levels(obj)

        obj_key = utils.make_key_from_instance(dg_obj_instance)
        exported_stuff = None
        props = pyluxcore.Properties()

        if dg_obj_instance.show_self:
            if obj.type in MESH_OBJECTS:
                if obj.type == "CURVES" and not obj.data == None:
                    if obj.data.rna_type.name == "Hair Curves":
                        visible_to_cam = utils.visible_to_camera(
                            dg_obj_instance, is_viewport_render, view_layer
                        )
                        # Same rule as use_instancing in _convert_mesh_obj:
                        # objects with motion blur need a transformation on
                        # the LuxCore object, it may not be baked into the
                        # strand points
                        is_for_duplication = (
                            is_viewport_render
                            or dg_obj_instance.is_instance
                            or (
                                exporter.motion_blur_enabled
                                and obj.luxcore.enable_motion_blur
                            )
                        )
                        curve_res = convert_hair_curves(
                            exporter,
                            depsgraph,
                            obj,
                            obj_key,
                            luxcore_scene,
                            is_for_duplication,
                            dg_obj_instance.matrix_world,
                        )
                        lux_shape, strand_sig = (
                            curve_res if curve_res else (None, None)
                        )
                        if lux_shape:
                            # Curves data may have no material slots at all
                            mat = (
                                obj.data.materials[0]
                                if len(obj.data.materials)
                                else None
                            )
                            if mat:
                                node_tree = mat.luxcore.node_tree
                                if node_tree:
                                    lux_shape = define_shapes(
                                        lux_shape,
                                        node_tree,
                                        exporter,
                                        depsgraph,
                                        scene_props,
                                    )

                            self.exported_hair[obj_key] = (
                                lux_shape,
                                strand_sig,
                            )

                            lux_mat, mat_props, node_tree = export_material(
                                obj, 0, exporter, depsgraph, is_viewport_render
                            )
                            scene_props.Set(mat_props)

                            # Hair curves objects have no mesh parts, so their
                            # ExportedObject is built manually. Registering it
                            # enables instancing via DuplicateObject and lets
                            # update() and motion blur track it like any other
                            # object.
                            exported_stuff = ExportedObject(
                                obj_key,
                                [],
                                [],
                                dg_obj_instance.matrix_world.copy()
                                if is_for_duplication
                                else None,
                                visible_to_cam,
                                utils.make_object_id(dg_obj_instance),
                            )
                            exported_stuff.parts.append(
                                ExportedPart(lux_shape, lux_shape, lux_mat)
                            )
                            # Strand motion blur (E9): record the strand
                            # mesh and its raw layout so the per-step
                            # sampler can feed SetStrandsVertexMotion.
                            # Shape wrappers (subdiv etc.) build a new
                            # mesh off the base strands and would
                            # silently drop the motion series.
                            exported_stuff.strand_recs.append(
                                {
                                    "mesh": obj_key,
                                    "kind": strand_sig["kind"],
                                    "sig": strand_sig,
                                    "space_matrix": strand_sig[
                                        "space_matrix"
                                    ],
                                    "wrapped": lux_shape != obj_key,
                                }
                            )
                else:

                    exported_stuff = self._convert_mesh_obj(
                        exporter,
                        dg_obj_instance,
                        obj,
                        obj_key,
                        depsgraph,
                        luxcore_scene,
                        scene_props,
                        is_viewport_render,
                        view_layer,
                    )
                if exported_stuff:
                    props = exported_stuff.get_props()
            elif obj.type == "POINTCLOUD":
                with _timed(exporter, "export_time_pointcloud"):
                    exported_stuff = pointcloud.convert_pointcloud_obj(
                        exporter,
                        dg_obj_instance,
                        obj,
                        obj_key,
                        depsgraph,
                        luxcore_scene,
                        scene_props,
                        is_viewport_render,
                        view_layer,
                        self.pending_pointcloud_duplicates,
                    )
                if exported_stuff:
                    props = exported_stuff.get_props()
            elif obj.type == "VOLUME":
                with _timed(exporter, "export_time_volumes"):
                    exported_stuff = volume.convert_volume_obj(
                        exporter,
                        dg_obj_instance,
                        obj,
                        obj_key,
                        depsgraph,
                        luxcore_scene,
                        scene_props,
                        is_viewport_render,
                        view_layer,
                    )
                if exported_stuff:
                    props = exported_stuff.get_props()
            elif obj.type == "LIGHT":
                with _timed(exporter, "export_time_lights"):
                    props, exported_stuff = light.convert_light(
                        exporter,
                        obj,
                        obj_key,
                        depsgraph,
                        luxcore_scene,
                        dg_obj_instance.matrix_world.copy(),
                        is_viewport_render,
                    )

        # Convert hair
        for psys in obj.particle_systems:
            settings = psys.settings

            if (
                psys.particles
                and settings.type == "HAIR"
                and settings.render_type == "PATH"
            ):
                # Can't use the memory address of the psys as key because it changes
                # when the psys is updated (e.g. because some hair moves)
                # Motion-blur opt-in needs the transform on the LuxCore
                # object (not baked into the strand points) so object
                # motion and strand deformation compose correctly.
                is_for_duplication = (
                    is_viewport_render
                    or dg_obj_instance.is_instance
                    or (
                        exporter.motion_blur_enabled
                        and obj.luxcore.enable_motion_blur
                    )
                )
                psys_key = make_psys_key(obj, psys, is_for_duplication)
                lux_obj = make_hair_shape_name(obj_key, psys)
                visible_to_cam = utils.visible_to_camera(
                    dg_obj_instance, is_viewport_render, view_layer
                )
                mat_index = get_hair_material_index(psys)

                strand_sig = None
                try:
                    lux_shape, strand_sig = self.exported_hair[psys_key]
                except KeyError:
                    hair_res = convert_hair(
                        exporter,
                        obj,
                        obj_key,
                        psys,
                        depsgraph,
                        luxcore_scene,
                        scene_props,
                        is_viewport_render,
                        is_for_duplication,
                        dg_obj_instance.matrix_world,
                        visible_to_cam,
                        engine,
                    )
                    lux_shape, strand_sig = (
                        hair_res if hair_res else (None, None)
                    )
                    if lux_shape:
                        mat = get_material(obj, mat_index, depsgraph)
                        if mat:
                            node_tree = mat.luxcore.node_tree
                            if node_tree:
                                lux_shape = define_shapes(
                                    lux_shape,
                                    node_tree,
                                    exporter,
                                    depsgraph,
                                    scene_props,
                                )

                        self.exported_hair[psys_key] = (
                            lux_shape,
                            strand_sig,
                        )

                if lux_shape:
                    lux_mat, mat_props, node_tree = export_material(
                        obj, mat_index, exporter, depsgraph, is_viewport_render
                    )
                    scene_props.Set(mat_props)
                    set_hair_props(
                        scene_props,
                        lux_obj,
                        lux_shape,
                        lux_mat,
                        visible_to_cam,
                        is_for_duplication,
                        dg_obj_instance.matrix_world,
                        settings.luxcore.hair.instancing == "enabled",
                    )

                # TODO handle case when exported_stuff is None
                #  (we'll have to create a new ExportedObject just for the hair mesh)
                if exported_stuff and lux_shape:
                    # Should always be the case because lights can't have particle systems
                    assert isinstance(exported_stuff, ExportedObject)
                    exported_stuff.parts.append(
                        ExportedPart(lux_obj, lux_shape, lux_mat)
                    )
                    # Strand motion blur (E9): record the raw strand
                    # layout for the per-step sampler.
                    if strand_sig is not None:
                        exported_stuff.strand_recs.append(
                            {
                                "mesh": lux_obj,
                                "kind": strand_sig["kind"],
                                "sig": strand_sig,
                                "space_matrix": strand_sig[
                                    "space_matrix"
                                ],
                                "wrapped": lux_shape != lux_obj,
                            }
                        )

        if exported_stuff:
            scene_props.Set(props)
            self.exported_objects[obj_key] = exported_stuff
            # Transform deltas are only safe where the transform either
            # sits on the LuxCore object or is world-baked into mesh
            # verts. Volumes bake it into their grid mapping and
            # pointclouds into per-point instance matrices, so those
            # require a full re-export on any transform change.
            self.bake_matrices[obj_key] = (
                dg_obj_instance.matrix_world.copy(),
                obj.type in MESH_OBJECTS,
            )

        return exported_stuff

    def _convert_mesh_obj(
        self,
        exporter,
        dg_obj_instance,
        obj,
        obj_key,
        depsgraph,
        luxcore_scene,
        scene_props,
        is_viewport_render,
        view_layer,
    ):
        transform = dg_obj_instance.matrix_world

        # Objects with displacement in the node tree are instanced to avoid discrepancies between viewport and final render
        use_instancing = (
            is_viewport_render
            or dg_obj_instance.is_instance
            or utils.can_share_mesh(obj.original)
            or (
                exporter.motion_blur_enabled and obj.luxcore.enable_motion_blur
            )
            or uses_displacement(obj)
        )

        mesh_key = self._get_mesh_key(obj, use_instancing, is_viewport_render)

        if use_instancing and mesh_key in self.exported_meshes:
            exported_mesh = self.exported_meshes[mesh_key]
            loaded_from_cache = True
        else:
            exported_mesh = mesh_converter.convert(
                obj,
                mesh_key,
                depsgraph,
                luxcore_scene,
                is_viewport_render,
                use_instancing,
                transform,
                exporter,
            )
            self.exported_meshes[mesh_key] = exported_mesh
            loaded_from_cache = False

        if exported_mesh:
            mat_names = []
            # Local working copy (see the ExportedObject construction below:
            # the cached list must not be mutated per object).
            mesh_definitions = [list(entry) for entry in exported_mesh.mesh_definitions]
            for idx, (shape_name, mat_index) in enumerate(
                mesh_definitions
            ):
                shape = shape_name
                lux_mat_name, mat_props, node_tree = export_material(
                    obj, mat_index, exporter, depsgraph, is_viewport_render
                )
                scene_props.Set(mat_props)
                mat_names.append(lux_mat_name)

                # Meshes in the cache already have the shapes added.
                # (This assumes that the instances use the same materials as the original mesh)
                if node_tree and not loaded_from_cache:
                    warn_about_missing_uvs(obj, node_tree)
                    shape = define_shapes(
                        shape, node_tree, exporter, depsgraph, scene_props
                    )
                elif not loaded_from_cache:
                    # Cycles-routed material: the Displacement output is a
                    # mesh-level effect — wrap the shape if the material's
                    # Blender node tree drives it with a displacement node.
                    shape = _apply_cycles_displacement(
                        shape, obj, mat_index, depsgraph, scene_props
                    )

                mesh_definitions[idx] = [shape, mat_index]

            obj_transform = transform.copy() if use_instancing else None
            obj_id = utils.make_object_id(dg_obj_instance)

            # mesh_definitions here is the local working copy (the mesh
            # cache keeps the pristine shapes for the next object).
            exported_obj = ExportedObject(
                obj_key,
                mesh_definitions,
                mat_names,
                obj_transform,
                utils.visible_to_camera(
                    dg_obj_instance, is_viewport_render, view_layer
                ),
                obj_id,
            )
            # Geometry-delta metadata (A6-III): the ordered base shape
            # list lets the persistent-scene delta re-DefineMesh in
            # place and the shape signature replay the wrapper chain,
            # while the wrapper flag excludes objects whose final
            # shape is a derived wrapper (displacement/pointiness/...) —
            # those hold a raw pointer to the base mesh that DefineMesh
            # replacement cannot rewire.
            base_list = list(exported_mesh.mesh_definitions)
            base_names = {name for name, _m in base_list}
            # Deformation motion blur (E9): let motion_blur.convert()
            # re-evaluate this object's mesh per shutter step and attach
            # a vertex series to the base shapes. Objects whose final
            # shape is a wrapper (subdiv etc.) are excluded — the wrapper
            # mesh is a new mesh that would drop the base series anyway.
            exported_obj.exported_mesh = exported_mesh
            exported_obj.vert_mesh_key = mesh_key
            exported_obj.has_shape_wrapper = any(
                part.lux_shape not in base_names
                for part in exported_obj.parts
            )
            self.obj_geo_meta[obj_key] = (
                obj.original.data.as_pointer() if obj.original.data else 0,
                mesh_key,
                use_instancing,
                base_list,
                any(
                    part.lux_shape not in base_names
                    for part in exported_obj.parts
                ),
            )
            return exported_obj

    def diff(self, depsgraph):
        only_scene = len(depsgraph.updates) == 1 and isinstance(
            depsgraph.updates[0].id, bpy.types.Scene
        )
        # MESH data-block edits (e.g. mesh data tweaks that only flag the
        # datablock, material-driven geometry) don't always flag the OBJECT.
        # Same for (hair) curve data-blocks.
        return (
            depsgraph.id_type_updated("OBJECT")
            or depsgraph.id_type_updated("MESH")
            or depsgraph.id_type_updated("CURVE")
            or depsgraph.id_type_updated("CURVES")
            or depsgraph.id_type_updated("VOLUME")
            or depsgraph.id_type_updated("POINTCLOUD")
        ) and not only_scene

    def update(self, exporter, depsgraph, luxcore_scene, scene_props, context):
        is_viewport_render = bool(context)
        redefine_objs_with_these_mesh_keys = []
        # Always instance in viewport so we can move objects around
        use_instancing = True

        # Geometry updates (mesh edit, modifier edit etc.)
        # MESH datablocks updated without an OBJECT geometry flag (see diff):
        # collect their names so objects using them are refreshed below.
        # The same applies to CURVE and CURVES (hair curves) datablocks.
        mesh_updated_names = {
            u.id.name
            for u in depsgraph.updates
            if isinstance(
                u.id,
                (
                    bpy.types.Mesh,
                    bpy.types.Curve,
                    bpy.types.Curves,
                    bpy.types.Volume,
                    bpy.types.PointCloud,
                ),
            )
        }
        if depsgraph.id_type_updated("OBJECT") or mesh_updated_names:
            for dg_update in depsgraph.updates:
                obj = None
                if dg_update.is_updated_geometry and isinstance(
                    dg_update.id, bpy.types.Object
                ):
                    obj = dg_update.id
                elif (
                    isinstance(dg_update.id, bpy.types.Object)
                    and dg_update.id.data is not None
                    and getattr(dg_update.id.data, "name", "") in mesh_updated_names
                ):
                    # Object itself not flagged, but its mesh datablock was.
                    obj = dg_update.id
                if obj is None:
                    continue
                if True:
                    if not utils.is_obj_visible(
                        obj
                    ) or not obj.visible_in_viewport_get(context.space_data):
                        continue

                    if obj.type in MESH_OBJECTS:
                        if obj.type == "CURVES" and not obj.data == None:
                            if obj.data.rna_type.name == "Hair Curves":
                                obj_key = utils.make_key(obj)
                                # The hair may not have been exported yet
                                # (e.g. object was hidden during first_run).
                                # Instance keys have the object key as prefix
                                # (see utils.make_key_from_instance).
                                for key in [
                                    k
                                    for k in self.exported_hair
                                    if k == obj_key
                                    or k.startswith(obj_key + "_")
                                ]:
                                    del self.exported_hair[key]
                                # Remove all entries of this object and its
                                # instances so it is fully re-exported below
                                # instead of only getting a transform update
                                for key in [
                                    k
                                    for k in self.exported_objects
                                    if k == obj_key
                                    or k.startswith(obj_key + "_")
                                ]:
                                    del self.exported_objects[key]
                        else:
                            mesh_key = self._get_mesh_key(obj, use_instancing)

                            # if mesh_key not in self.exported_meshes:
                            # TODO this can happen if a deforming modifier is added
                            #  to an already-exported object. how to handle this case?

                            transform = None  # In viewport render, everything is instanced
                            exported_mesh = mesh_converter.convert(
                                obj,
                                mesh_key,
                                depsgraph,
                                luxcore_scene,
                                is_viewport_render,
                                use_instancing,
                                transform,
                            )

                            if exported_mesh:
                                for i in range(
                                    len(exported_mesh.mesh_definitions)
                                ):
                                    shape, mat_index = (
                                        exported_mesh.mesh_definitions[i]
                                    )
                                    mat = get_material(
                                        obj, mat_index, depsgraph
                                    )

                                    if mat:
                                        node_tree = mat.luxcore.node_tree
                                        if node_tree:
                                            shape = define_shapes(
                                                shape,
                                                node_tree,
                                                exporter,
                                                depsgraph,
                                                scene_props,
                                            )

                                    exported_mesh.mesh_definitions[i] = (
                                        shape,
                                        mat_index,
                                    )

                            self.exported_meshes[mesh_key] = exported_mesh

                            # We arrive here not only when the mesh is edited, but also when the material
                            # of the object is changed in Blender. In this case we have to re-define all
                            # objects using this mesh (just the properties, the mesh is not re-exported).
                            redefine_objs_with_these_mesh_keys.append(mesh_key)

                        # Re-export hair systems of objects with updated geometry
                        for psys in obj.particle_systems:
                            settings = psys.settings

                            if (
                                psys.particles
                                and settings.type == "HAIR"
                                and settings.render_type == "PATH"
                            ):
                                # Can't use the memory address of the psys as key because it changes
                                # when the psys is updated (e.g. because some hair moves)
                                psys_key = make_psys_key(obj, psys, True)
                                # The hair may not have been exported yet
                                self.exported_hair.pop(psys_key, None)
                    elif obj.type == "VOLUME":
                        obj_key = utils.make_key(obj)
                        # Drop the exported object (and its instances) so it is
                        # fully re-exported below; a new VDB frame or grid change
                        # can alter every part of the volume definition.
                        for key in [
                            k
                            for k in self.exported_objects
                            if k == obj_key or k.startswith(obj_key + "_")
                        ]:
                            del self.exported_objects[key]
                    elif obj.type == "POINTCLOUD":
                        obj_key = utils.make_key(obj)
                        # Remove the base object and all point duplicates so
                        # the cloud is fully re-exported below.
                        for key in [
                            k
                            for k in self.exported_objects
                            if k == obj_key or k.startswith(obj_key + "_")
                        ]:
                            self.exported_objects[key].delete(luxcore_scene)
                            del self.exported_objects[key]
                    elif obj.type == "LIGHT":
                        obj_key = utils.make_key(obj)
                        props, exported_stuff = light.convert_light(
                            exporter,
                            obj,
                            obj_key,
                            depsgraph,
                            luxcore_scene,
                            obj.matrix_world.copy(),
                            is_viewport_render,
                        )
                        if exported_stuff:
                            self.exported_objects[obj_key] = exported_stuff
                            scene_props.Set(props)

        # TODO maybe not loop over all instances, instead only loop over updated
        #  objects and check if they have a particle system that needs to be updated?
        #  Would be better for performance with many particles, however I'm not sure
        #  we can find all instances corresponding to one particle system?

        # Currently, every update that doesn't require a mesh re-export happens here
        for dg_obj_instance in depsgraph.object_instances:
            if not supports_live_transform(dg_obj_instance.particle_system):
                continue

            obj = dg_obj_instance.object
            if not utils.is_instance_visible(dg_obj_instance, obj, context):
                continue

            obj_key = utils.make_key_from_instance(dg_obj_instance)
            mesh_key = self._get_mesh_key(obj, use_instancing)

            if (
                obj_key in self.exported_objects and obj.type != "LIGHT"
            ) and not mesh_key in redefine_objs_with_these_mesh_keys:
                exported_obj = self.exported_objects[obj_key]
                updated = False

                if exported_obj.transform != dg_obj_instance.matrix_world:
                    exported_obj.transform = (
                        dg_obj_instance.matrix_world.copy()
                    )
                    updated = True

                obj_id = utils.make_object_id(dg_obj_instance)
                if exported_obj.obj_id != obj_id:
                    exported_obj.obj_id = obj_id
                    updated = True

                if exported_obj.visible_to_camera != utils.visible_to_camera(
                    dg_obj_instance, is_viewport_render
                ):
                    exported_obj.visible_to_camera = utils.visible_to_camera(
                        dg_obj_instance, is_viewport_render
                    )
                    updated = True

                if updated:
                    scene_props.Set(exported_obj.get_props())
            else:
                # Object is new and not in LuxCore yet, or it is a light, do a full export
                self._convert_obj(
                    exporter,
                    dg_obj_instance,
                    obj,
                    depsgraph,
                    luxcore_scene,
                    scene_props,
                    is_viewport_render,
                )

        # Newly re-exported point clouds queued their instances during
        # _convert_obj; realize them on the live scene now.
        self._flush_pointcloud_duplicates(luxcore_scene)

        # self._debug_info()
