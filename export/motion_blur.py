import hashlib
import math
from array import array
import mathutils
import numpy as np
import pyluxcore
from .. import utils
from .caches.exported_data import ExportedObject, ExportedLight
from .caches.object_cache import _instance_key, _dupli_motion_enabled
from .mesh_converter import get_ndarray
from .pointcloud import _read_pointcloud_data, _point_matrices
from .hair import _read_curves_points


# TODO fix motion blur of area lights, they get a wrong transformation

def convert(context, engine, scene, depsgraph, exported_objects,
            luxcore_scene=None, instances=None):
    assert scene.camera
    motion_blur = scene.camera.data.luxcore.motion_blur
    assert motion_blur.enable and (motion_blur.object_blur or motion_blur.camera_blur)

    steps = motion_blur.steps
    assert steps >= 2 and isinstance(steps, int)

    frame_offsets = _calc_frame_offsets(motion_blur.shutter, steps)
    # Per-step {instance_key: matrix_world} maps for dupli motion blur (A5).
    # Collected during the same frame stepping loop that samples object
    # matrices, so no extra depsgraph evaluations are needed.
    dupli_steps = (
        [dict() for _ in range(steps)] if instances is not None else None
    )
    # Per-step deforming-mesh vertex samples for vertex motion blur (E9):
    # {mesh_key: {"mesh": ExportedMesh, "data": [arr|None]*steps,
    #             "ok": bool}}
    vert_steps = {} if luxcore_scene is not None else None
    # Per-step strand control-point samples for hair/curve motion blur
    # (E9): {strand_mesh_name: {"rec": strand_rec, "data": {...},
    # "ok": bool}}
    strand_steps = {} if luxcore_scene is not None else None
    matrices = _get_matrices(context, engine, scene, steps, frame_offsets,
                             depsgraph, exported_objects, instances,
                             dupli_steps, vert_steps, strand_steps)

    if dupli_steps is not None:
        _build_dupli_motion(instances, dupli_steps, frame_offsets, steps)

    _build_pointcloud_motion(exported_objects, matrices, frame_offsets, steps)

    if vert_steps is not None:
        _build_vertex_motion(vert_steps, frame_offsets, luxcore_scene)

    if strand_steps is not None:
        _build_strand_motion(strand_steps, frame_offsets, luxcore_scene)

    # Find and delete entries of non-moving objects (where all matrices are equal)
    for prefix, matrix_steps in list(matrices.items()):
        matrices_equal = utils.all_elems_equal(matrix_steps)

        if matrices_equal:
            # This object does not need motion blur because it does not move
            del matrices[prefix]

    # Export the properties for moving objects
    props = pyluxcore.Properties()

    for prefix, matrix_steps in matrices.items():
        for step in range(steps):
            time = frame_offsets[step]
            matrix = matrix_steps[step]
            transformation = utils.luxutils.matrix_to_list(matrix)
            definitions = {
                "motion.%d.time" % step: time,
                "motion.%d.transformation" % step: transformation,
            }
            props.Set(utils.luxutils.create_props(prefix, definitions))

    # We need this information outside
    is_camera_moving = "scene.camera." in matrices
    return props, is_camera_moving


def _calc_frame_offsets(shutter, steps):
    """ Return a list of offsets (unit: frame) to step through in _get_matrices() """
    step_interval = shutter / (steps - 1)
    return [step_interval * step - shutter / 2 for step in range(steps)]


def _get_matrices(context, engine, scene, steps, frame_offsets, depsgraph,
                  exported_objects, instances=None, dupli_steps=None,
                  vert_steps=None, strand_steps=None):
    motion_blur = scene.camera.data.luxcore.motion_blur
    matrices = {}  # {prefix: [matrix1, matrix2, ...]}

    frame_center = scene.frame_current
    subframe_center = scene.frame_subframe
    for step in range(steps):
        offset = frame_offsets[step]
        frame = frame_center + subframe_center + offset
        frame_int = math.floor(frame)
        subframe = frame - frame_int
        engine.frame_set(frame_int, subframe)
        # frame_set() alone does not re-evaluate the depsgraph: without an
        # explicit update every step would read the center-frame matrices
        # and motion blur would silently render static.
        try:
            depsgraph.update()
        except Exception:
            pass
        if motion_blur.object_blur:
            _append_object_matrices(
                depsgraph, exported_objects, matrices, step,
                instances, dupli_steps, vert_steps, strand_steps,
            )

        if motion_blur.camera_blur and not context:
            # Evaluated camera, not the original: original matrix_world
            # does not follow frame animation.
            camera_eval = depsgraph.objects.get(scene.camera.name)
            matrix = (camera_eval.matrix_world if camera_eval is not None
                      else scene.camera.matrix_world)

            prefix = "scene.camera."
            _append_matrix(matrices, prefix, matrix, step)

    # Restore original frame
    engine.frame_set(frame_center, subframe_center)
    return matrices


def _append_object_matrices(depsgraph, exported_objects, matrices, step,
                            instances=None, dupli_steps=None,
                            vert_steps=None, strand_steps=None):
    for dg_obj_instance in depsgraph.object_instances:
        obj = dg_obj_instance.parent if dg_obj_instance.is_instance else dg_obj_instance.object
        # A5: opt-in is enable_motion_blur on the instanced object OR the
        # instancer — same rule as Duplis key allocation in first_run.
        dupli_mb = (
            dg_obj_instance.is_instance
            and _dupli_motion_enabled(dg_obj_instance)
        )

        # Dupli/particle transform motion blur (A5): record this instance's
        # matrix under its stable key when its source object opted in.
        if dupli_steps is not None and dupli_mb:
            duplis = instances.get(
                dg_obj_instance.object.original.as_pointer()
            )
            if duplis is not None and duplis.keys is not None:
                dupli_steps[step][_instance_key(dg_obj_instance)] = (
                    dg_obj_instance.matrix_world.copy()
                )

        # The first dupli instance exists as a real scene object and is
        # covered by the object-level motion props below — extend the gate
        # with the A5 rule so flagging the instanced object blurs it too.
        if not (obj.luxcore.enable_motion_blur or dupli_mb):
            continue

        obj_key = utils.make_key_from_instance(dg_obj_instance)
        matrix = dg_obj_instance.matrix_world.copy()

        try:
            exported_thing = exported_objects[obj_key]
            if isinstance(exported_thing, ExportedObject):
                if exported_thing.is_pointcloud:
                    matrix = _collect_pointcloud_step(
                        exported_thing, dg_obj_instance, step
                    )
                _collect_vertex_step(
                    vert_steps, exported_thing, dg_obj_instance.object,
                    depsgraph, step,
                )
                _collect_strand_step(
                    strand_steps, exported_thing, dg_obj_instance.object,
                    depsgraph, step,
                )
                for part in exported_thing.parts:
                    prefix = "scene.objects." + part.lux_obj + "."
                    _append_matrix(matrices, prefix, matrix, step)
            # else:
            #     assert isinstance(exported_thing, ExportedLight)
            #     prefix = "scene.lights." + exported_thing.lux_light_name + "."
            #     _append_matrix(matrices, prefix, matrix, step)
        except KeyError:
            # This is not a problem, objects are skipped during export for various reasons
            # E.g. if the object is not visible, or if it's a camera
            pass


def _collect_pointcloud_step(exported_thing, dg_obj_instance, step):
    """Point-transform motion blur: re-evaluate the point data at this
    shutter step and rebuild world-space point matrices. Returns the
    matrix to store in the motion props — point 0's matrix (the base
    object IS point 0), or the object transform as fallback when the
    step evaluation fails.
    """
    if step == 0:
        # ExportedObject records can persist across viewport updates —
        # start a fresh sample set for each stepping pass.
        exported_thing.pc_step_data.clear()
        exported_thing.pc_failed = False
    positions, radii = _read_pointcloud_data(dg_obj_instance.object)
    expected = exported_thing.duplicate_count + 1
    if positions is None or len(positions) != expected:
        exported_thing.pc_failed = True
        exported_thing.pc_step_data.append(None)
        return dg_obj_instance.matrix_world.copy()

    step_flat = np.ascontiguousarray(
        _point_matrices(positions, radii, dg_obj_instance.matrix_world)
        .transpose(0, 2, 1)
        .reshape(-1),
        dtype=np.float32,
    )
    exported_thing.pc_step_data.append(step_flat)
    exported_thing.pc_prefix = (
        "scene.objects." + exported_thing.parts[0].lux_obj + "."
    )
    # Base object = point 0: the first 16 floats are its transposed matrix
    m = step_flat[:16]
    return mathutils.Matrix(
        [[m[0], m[4], m[8], m[12]],
         [m[1], m[5], m[9], m[13]],
         [m[2], m[6], m[10], m[14]],
         [m[3], m[7], m[11], m[15]]]
    )


def _build_pointcloud_motion(exported_objects, matrices, frame_offsets, steps):
    """Flatten per-step point matrices into the [instance][step]-major
    buffers expected by Scene.DuplicateObject's motion-multi overload.
    Point 0 is the base object and rides the regular motion.N property
    path. If the point count differs at any step (topology change) the
    whole cloud falls back to static — including the base object props.
    """
    for exported_thing in exported_objects.values():
        if not getattr(exported_thing, "is_pointcloud", False):
            continue
        if exported_thing.pc_prefix is None:
            continue  # motion gate off or no step data was collected

        data = exported_thing.pc_step_data
        count = exported_thing.duplicate_count
        if (
            exported_thing.pc_failed
            or len(data) != steps
            or any(s is None for s in data)
        ):
            matrices.pop(exported_thing.pc_prefix, None)
            continue

        if all(np.array_equal(s, data[0]) for s in data[1:]):
            # Cloud does not move — drop base props as well
            matrices.pop(exported_thing.pc_prefix, None)
            continue

        if count == 0:
            # Single point: no duplicates, base props carry its motion
            continue

        exported_thing.pc_steps_n = steps
        exported_thing.pc_motion = array("f", [])
        exported_thing.pc_motion_times = array("f", [])
        for inst in range(count):
            for s in range(steps):
                exported_thing.pc_motion.extend(
                    data[s][inst * 16 + 16 : inst * 16 + 32]
                )
                exported_thing.pc_motion_times.append(frame_offsets[s])


def _append_matrix(matrices, prefix, matrix, step):
    if step == 0:
        matrices[prefix] = [matrix]
    else:
        matrices[prefix].append(matrix)


def _build_dupli_motion(instances, dupli_steps, frame_offsets, steps):
    """Flatten the per-step key->matrix maps into the [instance][step]-major
    buffers expected by Scene.DuplicateObject's motion-multi overload:
    times = count*steps floats, motion = count*steps*16 floats. Instances
    missing at a step (particle born/died mid-shutter) reuse their
    center-frame matrix, matching the static transform at that step.
    """
    total_missing = 0
    for duplis in instances.values():
        if duplis is None or duplis.keys is None:
            continue
        count = duplis.get_count()
        # keys were appended in lockstep with object_ids in first_run.
        # They must also be unique: an empty/colliding persistent_id set
        # would alias several instances to one matrix, so motion is
        # dropped for that dupli object entirely (static fallback).
        if (
            count == 0
            or len(duplis.keys) != count
            or len(set(duplis.keys)) != count
        ):
            continue

        duplis.motion_steps = steps
        duplis.motion = array("f", [])
        duplis.motion_times = array("f", [])
        for j in range(count):
            key = duplis.keys[j]
            center = duplis.matrices[j * 16 : j * 16 + 16]
            for s in range(steps):
                matrix = dupli_steps[s].get(key)
                if matrix is None:
                    duplis.motion.extend(center)
                    duplis.motion_missing += 1
                else:
                    duplis.motion.extend(
                        pyluxcore.BlenderMatrix4x4ToList(matrix)
                    )
                duplis.motion_times.append(frame_offsets[s])
        total_missing += duplis.motion_missing

    if total_missing:
        print(
            "Motion blur: %d instance-step samples had no evaluated "
            "transform (particle born/died mid-shutter); center-frame "
            "transform was used for those steps." % total_missing
        )


def _collect_vertex_step(vert_steps, exported_thing, eval_obj, depsgraph, step):
    """Deformation motion blur (E9): sample this object's evaluated mesh
    at the current shutter step and keep the loop-expanded vertex
    positions if the topology still matches the exported mesh. Samples
    are deduplicated per mesh_key so objects sharing an instanced mesh
    evaluate it only once per step.
    """
    if vert_steps is None:
        return
    exported_mesh = exported_thing.exported_mesh
    mesh_key = exported_thing.vert_mesh_key
    if (
        exported_mesh is None
        or exported_mesh.vert_sig is None
        or exported_thing.has_shape_wrapper
    ):
        return

    rec = vert_steps.get(mesh_key)
    if rec is None:
        rec = {"mesh": exported_mesh, "data": {}, "ok": True}
        vert_steps[mesh_key] = rec
    if step in rec["data"] or not rec["ok"]:
        return

    positions = _sample_loop_points(eval_obj, depsgraph, exported_mesh.vert_sig)
    if positions is None:
        rec["ok"] = False
        print(
            "Motion blur: topology of mesh '%s' changed mid-shutter; "
            "vertex motion disabled for it (rendering static)." % mesh_key
        )
    else:
        rec["data"][step] = positions


def _sample_loop_points(eval_obj, depsgraph, vert_sig):
    """Re-run the mesh_converter vertex pipeline on the evaluated object
    and return loop-expanded (N,3) float32 positions, or None when the
    topology differs from the export-time signature (vert_count,
    loop_count, loop_indices digest).
    """
    vert_count, loop_count, loop_digest = vert_sig
    object_eval = None
    mesh = None
    try:
        object_eval = eval_obj.evaluated_get(depsgraph)
        mesh = object_eval.to_mesh()
        if mesh is None:
            return None
        mesh.calc_loop_triangles()
        mesh.split_faces()
        if len(mesh.vertices) != vert_count or len(mesh.loops) != loop_count:
            return None
        loop_vertices = get_ndarray(mesh.loops, "vertex_index", 0, np.uint32)
        if hashlib.blake2b(
            loop_vertices.tobytes(), digest_size=16
        ).digest() != loop_digest:
            return None
        vertex_points = get_ndarray(mesh.vertices, "co", 3, np.float32)
        return np.ascontiguousarray(vertex_points[loop_vertices])
    except Exception:
        return None
    finally:
        if object_eval is not None and mesh is not None:
            object_eval.to_mesh_clear()


def _build_vertex_motion(vert_steps, frame_offsets, luxcore_scene):
    """Attach the collected per-step vertex buffers to every base shape
    of each sampled mesh via Scene.SetMeshVertexMotion. The shutter
    times are the same frame_offsets used by the transform motion props,
    so a single schedule drives both. Meshes that failed a topology
    check or never moved stay static.
    """
    times = np.asarray(frame_offsets, dtype=np.float32)
    for mesh_key, rec in vert_steps.items():
        data = rec["data"]
        if not rec["ok"] or len(data) != len(frame_offsets):
            continue
        steps_data = [data[s] for s in range(len(frame_offsets))]
        if all(np.array_equal(s, steps_data[0]) for s in steps_data[1:]):
            # Mesh does not deform — no vertex series needed
            continue
        for shape_name, _mat in rec["mesh"].mesh_definitions:
            # Submeshes are exported with locally compacted vertices —
            # apply the same loop remap to every step so the series
            # matches the shape's vertex count.
            uniq = rec["mesh"].submesh_maps.get(shape_name)
            if uniq is None:
                luxcore_scene.SetMeshVertexMotion(shape_name, times, steps_data)
            else:
                sub_steps = [d[uniq] for d in steps_data]
                luxcore_scene.SetMeshVertexMotion(shape_name, times, sub_steps)


def _collect_strand_step(strand_steps, exported_thing, eval_obj, depsgraph, step):
    """Strand deformation motion blur (E9): sample the object's strand
    control points at the current shutter step. The sample must match
    the raw strand layout recorded at export time (per-strand point
    counts for hair curves, particle range/counts for particle hair);
    LuxCore re-filters the raw points through the stored source map, so
    this returns raw (unfiltered) positions in the same space the base
    export stored them.
    """
    if strand_steps is None:
        return
    for rec in exported_thing.strand_recs:
        if rec["wrapped"]:
            # A shape wrapper (subdiv, pointiness, ...) created a new
            # mesh off the base strands; motion set on the base mesh
            # would not propagate to it.
            continue
        key = rec["mesh"]
        entry = strand_steps.get(key)
        if entry is None:
            entry = {"rec": rec, "data": {}, "ok": True}
            strand_steps[key] = entry
        if step in entry["data"] or not entry["ok"]:
            continue

        points = _sample_strand_points(rec, eval_obj, depsgraph)
        if points is None:
            entry["ok"] = False
            print(
                "Motion blur: strand layout of '%s' changed mid-shutter; "
                "strand motion disabled for it (rendering static)." % key
            )
        else:
            entry["data"][step] = points


def _sample_strand_points(rec, eval_obj, depsgraph):
    """Re-read the object's strand control points on the evaluated
    object and return a (P,3) float32 array in the stored strand space,
    or None when the strand layout differs from the export-time
    signature.
    """
    kind, sig = rec["kind"], rec["sig"]
    object_eval = None
    try:
        object_eval = eval_obj.evaluated_get(depsgraph)
        if kind == "curves":
            points_per_strand, points = _read_curves_points(
                object_eval.data.curves
            )
            if tuple(int(c) for c in points_per_strand) != sig["pps"]:
                return None
        else:
            # Particle hair: identical to convert_hair's co_hair loop.
            psys = object_eval.particle_systems.get(sig["psys_name"])
            if (
                psys is None
                or len(psys.particles) != sig["num_parents"]
                or len(psys.child_particles) != sig["num_children"]
            ):
                return None
            co_hair = psys.co_hair
            points = np.fromiter(
                (
                    elem
                    for pindex in range(sig["start"], sig["dupli_count"])
                    for s in range(sig["pps"])
                    for elem in co_hair(
                        object=object_eval, particle_no=pindex, step=s
                    )
                ),
                dtype=np.float32,
            )
        points = points.reshape(-1, 3)
        space_matrix = rec["space_matrix"]
        if space_matrix is not None:
            # The binding applied this transform to the stored strand
            # points (world -> object space); step samples must match.
            m = np.asarray(space_matrix, dtype=np.float32)
            points = points @ m[:3, :3].T + m[:3, 3]
        return np.ascontiguousarray(points, dtype=np.float32)
    except Exception:
        return None


def _build_strand_motion(strand_steps, frame_offsets, luxcore_scene):
    """Attach the collected per-step strand control-point buffers to
    each strand mesh via Scene.SetStrandsVertexMotion. LuxCore
    re-tessellates every step through the strand motion recipe stored
    at definition time, so the exported strand layout only needs to
    match in raw input space. Strands that failed a layout check or
    never moved stay static.
    """
    times = np.asarray(frame_offsets, dtype=np.float32)
    for mesh_name, entry in strand_steps.items():
        data = entry["data"]
        if not entry["ok"] or len(data) != len(frame_offsets):
            continue
        steps_data = [data[s] for s in range(len(frame_offsets))]
        if all(np.array_equal(s, steps_data[0]) for s in steps_data[1:]):
            # Strands do not deform — no point series needed
            continue
        luxcore_scene.SetStrandsVertexMotion(mesh_name, times, steps_data)
