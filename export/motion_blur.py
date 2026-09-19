import math
from array import array
import pyluxcore
from .. import utils
from .caches.exported_data import ExportedObject, ExportedLight
from .caches.object_cache import _instance_key, _dupli_motion_enabled


# TODO fix motion blur of area lights, they get a wrong transformation

def convert(context, engine, scene, depsgraph, exported_objects, instances=None):
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
    matrices = _get_matrices(context, engine, scene, steps, frame_offsets,
                             depsgraph, exported_objects, instances, dupli_steps)

    if dupli_steps is not None:
        _build_dupli_motion(instances, dupli_steps, frame_offsets, steps)

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
                  exported_objects, instances=None, dupli_steps=None):
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
                instances, dupli_steps,
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
                            instances=None, dupli_steps=None):
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
