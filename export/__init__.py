from time import time
from array import array
import types

_needs_reload = "bpy" in locals()

import bpy
import pyluxcore
from .. import utils
from ..utils import render as utils_render
from ..utils import compatibility as utils_compatibility
from ..utils.errorlog import LuxCoreErrorLog
from . import (
    caches,
    camera,
    config,
    imagepipeline,
    light,
    material,
    motion_blur,
    hair,
    halt,
    world,
    mesh_converter,
    recorded_scene,
)
from .light import WORLD_BACKGROUND_LIGHT_NAME
from .caches.object_cache import (
    supports_live_transform,
    uses_displacement,
    get_material,
    define_shapes,
    make_psys_key,
    _apply_cycles_displacement,
)
from .caches import persistent_scene
from . import image
from .recorded_scene import RecordedScene  # noqa: F811 keep name for reload

if _needs_reload:
    import importlib

    modules = (
        caches,
        persistent_scene,
        recorded_scene,
        camera,
        config,
        image,
        imagepipeline,
        light,
        material,
        motion_blur,
        hair,
        halt,
        world,
        utils,
        mesh_converter,
    )
    for module in modules:
        importlib.reload(module)


def _camera_spec(camera_props):
    """
    Camera signature for persistent-scene reuse: the full property
    string minus keys that legitimately change every render (camera
    position/direction, motion-blur steps, position-derived focus
    distance). Anything else that differs (type, DoF, bokeh, clipping,
    camera volume...) forces a scene rebuild because Parse cannot
    delete properties that existed in the cached scene.
    """
    volatile = (
        "scene.camera.lookat.",
        "scene.camera.up ",
        "scene.camera.motion.",
        "scene.camera.focaldistance",
        "scene.camera.shutteropen",
        "scene.camera.shutterclose",
    )
    return "\n".join(
        line
        for line in str(camera_props).splitlines()
        if not line.startswith(volatile)
    )


def _eval_object_map(depsgraph, scene, wanted):
    """
    {obj_key: evaluated object} for depsgraph.objects plus every
    wanted key it omits.

    A render-mode depsgraph skips objects that exist only as instance
    sources (e.g. a VERTS-dupli child Blender never emits as its own
    scene object): ``Object.evaluated_get`` still resolves them, and
    the persistent-scene signatures (data_ptrs, shape_sig,
    member_mats, frame_change matrix compares) depend on coverage of
    every member.
    """
    by_key = {utils.make_key(o): o for o in depsgraph.objects}
    for o in scene.objects:
        key = utils.make_key(o)
        if key not in by_key and key in wanted:
            try:
                by_key[key] = o.evaluated_get(depsgraph)
            except Exception:
                pass
    return by_key


class Change:
    NONE = 0

    CONFIG = 1 << 0
    CAMERA = 1 << 1
    OBJECT = 1 << 2
    MATERIAL = 1 << 3
    VISIBILITY = 1 << 4
    WORLD = 1 << 5
    IMAGEPIPELINE = 1 << 6
    HALT = 1 << 7

    REQUIRES_SCENE_EDIT = CAMERA | OBJECT | MATERIAL | VISIBILITY | WORLD
    REQUIRES_VIEW_UPDATE = CONFIG
    REQUIRES_SESSION_PARSE = IMAGEPIPELINE | HALT

    @staticmethod
    def to_string(changes):
        s = ""
        members = [
            attr
            for attr in dir(Change)
            if not callable(getattr(Change, attr))
            and not attr.startswith("__")
        ]
        for changetype in members:
            if changes & getattr(Change, changetype):
                if s:
                    s += " | "
                s += changetype

        return s if changes else "NONE"


class Exporter(object):
    def __init__(self, stats=None):
        self.scene = None  # TODO I would like to remove this, the evaluated scene is temporary
        self.stats = stats

        self.config_cache = caches.StringCache()
        self.camera_cache = caches.CameraCache()
        # self.object_cache = caches.ObjectCache()
        self.object_cache2 = caches.ObjectCache2()
        self.material_cache = caches.MaterialCache()
        self.visibility_cache = caches.VisibilityCache()
        self.world_cache = caches.WorldCache()
        self.imagepipeline_cache = caches.StringCache()
        self.halt_cache = caches.StringCache()
        self.motion_blur_enabled = False
        self.object_blur_enabled = False

        # A dictionary with the following mapping:
        # {node_key: luxcore_name}
        # Most of the time node_key == luxcore_name, but some nodes have to insert
        # implicit textures n front of themselves which changes their luxcore_name.
        # Avoids re-exporting the same node multiple times.
        # TODO: currently the node cache has to be cleared when an output node starts
        # to export, because we don't have one global properties object.
        self.node_cache = {}

        # If a light/material uses a lightgroup, the id is stored here during export
        self.lightgroup_cache = set()

    def create_session(
        self, depsgraph, context=None, engine=None, view_layer=None
    ):
        """Synchronous session creation (final render, preview).

        The viewport uses the split path instead: export_scene() on the
        main thread, then create_render_session() on the session worker.
        """
        export_start = time()
        result = self.export_scene(depsgraph, context, engine, view_layer)
        if result is None:
            return None
        luxcore_scene, config_props = result

        scene = depsgraph.scene_eval
        renderengine_type = config_props.Get("renderengine.type").GetString()

        # Inform about pre-computations that can take a long time to
        # complete, like caches
        if engine:
            message = "Creating RenderSession"
            is_viewport_render = context is not None
            # Caches are never used in viewport render
            if not is_viewport_render:
                # The second argument of Get() is used as fallback if the
                # property is not set
                cache_indirect = config_props.Get(
                    "path.photongi.indirect.enabled", [False]
                ).GetBool()
                cache_caustics = config_props.Get(
                    "path.photongi.caustic.enabled", [False]
                ).GetBool()
                cache_envlight = scene.luxcore.config.envlight_cache.enabled
                cache_dls = (
                    config_props.Get("lightstrategy.type", [""]).GetString()
                    == "DLS_CACHE"
                )
                stats = self.stats
                if stats:
                    stats.cache_indirect.value = cache_indirect
                    stats.cache_caustics.value = cache_caustics
                    stats.cache_envlight.value = cache_envlight
                    stats.cache_dls.value = cache_dls

                cache_state = {
                    "Indirect Light": cache_indirect,
                    "Caustics": cache_caustics,
                    "Env. Light": cache_envlight,
                    "DLSC": cache_dls,
                }
                enabled_caches = [
                    key for key, value in cache_state.items() if value
                ]
                if any(enabled_caches):
                    message += (
                        ", computing caches ("
                        + ", ".join(enabled_caches)
                        + ")"
                    )
            message += " ..."
            engine.update_stats(
                "Export Finished (%.1f s)" % (time() - export_start),
                message,
            )

        progress_cb = None
        if engine and renderengine_type.endswith("OCL"):
            # Reported once compilation actually starts (the callback only
            # fires when the kernel cache is cold). The backend name is
            # captured now: the callback may fire on a native worker
            # thread where bpy.context access is unsafe.
            gpu_backend = utils.get_addon_preferences(
                bpy.context
            ).gpu_backend

            def progress_cb(index, count):
                if index == 0:
                    engine.report(
                        {"INFO"},
                        f"Compiling {gpu_backend} kernels (just once, "
                        "usually takes 15-30 minutes)",
                    )
                engine.update_stats(
                    f"Compiling {gpu_backend} kernels "
                    f"({index + 1}/{count})",
                    "just once, usually takes 15-30 minutes"
                    if index == 0
                    else "",
                )

        return self.create_render_session(
            config_props, luxcore_scene, progress_cb
        )

    def export_scene(
        self, depsgraph, context=None, engine=None, view_layer=None
    ):
        """Convert the Blender scene to a pyluxcore scene + config props.

        Returns ``(luxcore_scene, config_props)`` or None when the user
        cancelled. Runs on the main thread (depsgraph access); the worker
        takes over from create_render_session() onward.
        """
        # Notes:
        # In final render, context is None

        print("[Exporter] Creating session")
        start = time()
        # TODO 2.8 I'm not too happy about this, we shouldn't keep any
        # reference to temporary data, even if only for a while
        self.scene = depsgraph.scene_eval
        scene = self.scene
        stats = self.stats
        if stats:
            stats.reset()

        # We have to run the compatibility code before export because it could
        # be that the user has linked/appended assets with node trees from
        # previous versions of the addon since opening the .blend file.
        utils_compatibility.run()

        # Fresh lightgroup set per session: the exporter (and its cache)
        # outlives single renders in the viewport, and stale groups would
        # otherwise export extra pipelines forever.
        self.lightgroup_cache = set()

        # Scene
        image_resize_policy_props = (
            scene.luxcore.config.image_resize_policy.convert()
        )
        scene_props = pyluxcore.Properties()
        is_viewport_render = context is not None

        # Camera and world are converted up-front: their signatures are
        # part of the persistent-scene reuse decision below, and the
        # properties themselves are still parsed into whichever scene
        # ends up being used (Parse is required first because hair
        # tesselation needs the camera).
        camera_start = time()
        self.camera_cache.diff(
            self, scene, depsgraph, context
        )  # Init camera cache
        camera_props = self.camera_cache.props
        if stats:
            stats.export_time_camera.value += time() - camera_start

        world_start = time()
        world_props = world.convert(self, depsgraph, scene, is_viewport_render)
        if stats:
            stats.export_time_world.value += time() - world_start
        # Inititalize the world_cache
        self.world_cache.world_name = scene.world.name_full if scene.world else None

        # Persistent-scene reuse (A6-II): a final render can reuse the
        # pyluxcore.Scene cached from the previous render of the same
        # scene + view layer when the accumulated depsgraph dirty set
        # allows it (see doc/incremental_export_design.md). The decision
        # is made before scene creation because the camera has to be
        # parsed into whichever scene ends up being used.
        pkey = None
        pentry = None
        transform_deltas = set()
        geometry_keys = set()
        material_dirty = False
        mb_sig = (False, 0)
        camera_sig = None
        world_sig = None
        if not is_viewport_render and utils.is_valid_camera(scene.camera):
            _blur = scene.camera.data.luxcore.motion_blur
            _mb_enabled = (
                _blur.enable
                and (_blur.object_blur or _blur.camera_blur)
                and _blur.shutter > 0
            )
            mb_sig = (_mb_enabled, _blur.steps if _mb_enabled else 0)
            camera_sig = _camera_spec(camera_props)
            world_sig = str(world_props)
            # Per-object visibility snapshot: toggles that do not
            # reliably dirty the depsgraph (hide_render etc.) are
            # caught by comparing it at reuse time.
            vis_sig = {
                utils.make_key(o): (
                    o.hide_render,
                    o.luxcore.exclude_from_render,
                    getattr(o.luxcore, "is_light_portal", False),
                    o.visible_camera,
                    o.visible_diffuse,
                    o.visible_glossy,
                    o.visible_transmission,
                    o.visible_volume_scatter,
                    o.visible_shadow,
                )
                for o in scene.objects
            }
            # Material identity (rename changes the LuxCore name) and
            # slot layout per member object — both force a rebuild
            # because they change object-side definitions a material
            # delta cannot reach.
            mat_sig = {}
            slot_sig = {}
            for o in scene.objects:
                slots = []
                for slot in o.material_slots:
                    mat = slot.material
                    if mat is None:
                        slots.append((None, slot.link))
                        continue
                    mptr = str(mat.original.as_pointer())
                    slots.append((mptr, slot.link))
                    mat_sig[mptr] = utils.get_luxcore_name(
                        mat.original, is_viewport_render
                    )
                slot_sig[utils.make_key(o)] = tuple(slots)
            if not (
                _blur.enable and _blur.object_blur and _blur.shutter > 0
            ):
                # Object motion blur needs the per-frame instance data
                # collected in first_run, so it always re-exports.
                pkey = (
                    depsgraph.scene.as_pointer(),
                    view_layer.name if view_layer else "",
                )
                dirty = persistent_scene.take_dirty(
                    depsgraph.scene.as_pointer()
                )
                pentry = persistent_scene.get(pkey)
                if pentry is not None:
                    # Replay the wrapper-shape chain on the current
                    # node trees: material edits can add/remove shape
                    # wrappers (e.g. displacement), which a material
                    # delta alone cannot create in the cached scene.
                    _eval_shape = _eval_object_map(
                        depsgraph,
                        scene,
                        set(pentry["members"])
                        | set(pentry["dupli_srcs"]),
                    )
                    # Compound keys of dupli sources resolve their
                    # signature against the source's evaluated object.
                    _dupli_eval = {
                        ck: _eval_shape[sk]
                        for sk, (_e, ck) in pentry[
                            "dupli_srcs"
                        ].items()
                        if ck and sk in _eval_shape
                    }
                    _shape_now = {
                        k: self._shape_stack_sig(
                            _eval_shape.get(k) or _dupli_eval.get(k),
                            depsgraph,
                            meta[3],
                        )
                        for k, meta in pentry["geo_meta"].items()
                    }
                    if (
                        pentry["mb_sig"] != mb_sig
                        or pentry["camera_sig"] != camera_sig
                        or pentry["world_sig"] != world_sig
                    ):
                        # Parse cannot remove properties once set, so a
                        # changed camera spec (e.g. DoF toggled), world
                        # (e.g. env light removed) or motion-blur
                        # signature would leave stale definitions in the
                        # cached scene: rebuild instead.
                        pentry = None
                    elif vis_sig != pentry["vis"] or set(
                        vis_sig
                    ) != pentry["members"]:
                        # Visibility toggled or an object was
                        # added/removed
                        pentry = None
                    elif (
                        mat_sig != pentry["mat_sig"]
                        or slot_sig != pentry["slot_sig"]
                    ):
                        # A material was renamed or slot layout/link
                        # changed — object-side definitions a material
                        # delta cannot reach
                        pentry = None
                    elif _shape_now != pentry["shape_sig"]:
                        # A material edit changed the required wrapper
                        # shape stack (e.g. displacement added) — a
                        # material delta cannot create the missing
                        # wrapper shapes, so rebuild.
                        pentry = None
                    else:
                        (
                            _mode,
                            transform_deltas,
                            material_dirty,
                            geometry_keys,
                            instancer_keys,
                        ) = persistent_scene.classify(
                            dirty, pentry, scene.camera
                        )
                        if _mode == "full":
                            pentry = None
                        elif (
                            depsgraph.scene.frame_current
                            != pentry["frame"]
                        ):
                            # frame_set() moves animated objects without
                            # leaving depsgraph updates — re-check every
                            # member against its stored transform and
                            # animation kind.
                            eval_by_key = _eval_object_map(
                                depsgraph,
                                scene,
                                set(pentry["members"])
                                | set(pentry["dupli_srcs"]),
                            )
                            _camera_key = (
                                utils.make_key(scene.camera)
                                if scene.camera
                                else None
                            )
                            (
                                _rebuild,
                                _moved,
                                _geo_keys,
                                _inst_keys,
                                _mat_dirty,
                            ) = persistent_scene.frame_change(
                                pentry, eval_by_key, _camera_key
                            )
                            if _rebuild:
                                pentry = None
                            else:
                                transform_deltas |= _moved
                                geometry_keys |= _geo_keys
                                instancer_keys |= _inst_keys
                                material_dirty |= _mat_dirty

        luxcore_scene = (
            pentry["scene"]
            if pentry is not None
            else pyluxcore.Scene(
                pyluxcore.Properties(), image_resize_policy_props
            )
        )
        luxcore_scene.Parse(camera_props)

        if utils.is_valid_camera(scene.camera):
            blur_settings = scene.camera.data.luxcore.motion_blur
            # Don't export camera blur in viewport
            camera_blur = blur_settings.camera_blur and not context
            self.motion_blur_enabled = (
                blur_settings.enable
                and (blur_settings.object_blur or camera_blur)
                and (blur_settings.shutter > 0)
            )
            # Object blur including dupli/particle instances (A5). Kept
            # separate from motion_blur_enabled so a camera-blur-only
            # render does not pay for per-instance key collection.
            self.object_blur_enabled = (
                blur_settings.enable
                and blur_settings.object_blur
                and (blur_settings.shutter > 0)
                and context is None
            )

        # Objects and lights
        objects_start = time()
        if pentry is not None:
            try:
                if geometry_keys:
                    # In-place mesh re-definition or delete + re-export;
                    # both carry the current transform, so those keys
                    # leave the transform-delta set.
                    _subsumed = self._apply_geometry_deltas(
                        pentry, geometry_keys, depsgraph,
                        luxcore_scene, view_layer, engine
                    )
                    transform_deltas -= _subsumed
                self._apply_transform_deltas(
                    pentry, transform_deltas, depsgraph, luxcore_scene
                )
                if material_dirty:
                    self._reexport_scene_materials(
                        depsgraph, luxcore_scene
                    )
                # Instancer sets are flushed last: refreshed dupli
                # bases must already exist for DuplicateObject to bind.
                if instancer_keys:
                    self._refresh_dupli_sets(
                        pentry, instancer_keys, depsgraph,
                        luxcore_scene
                    )
                pentry["frame"] = depsgraph.scene.frame_current
                instances = {}
                print(
                    "[Exporter] Persistent scene reuse:"
                    f" {len(transform_deltas)} transform delta(s),"
                    f" {len(geometry_keys)} geometry delta(s),"
                    f" {len(instancer_keys)} instancer flush(es),"
                    f" materials {'refreshed' if material_dirty else 'kept'},"
                    f" {len(pentry['objects'])} objects kept"
                )
            except Exception:
                # A delta that fails mid-way leaves the cached scene in
                # an unknown state: discard it and rebuild from scratch.
                import traceback

                traceback.print_exc()
                print(
                    "[Exporter] Persistent scene delta failed,"
                    " falling back to full export"
                )
                pentry = None
                luxcore_scene = pyluxcore.Scene(
                    pyluxcore.Properties(), image_resize_policy_props
                )
                luxcore_scene.Parse(self.camera_cache.props)

        if pentry is None:
            instances = self.object_cache2.first_run(
                self,
                depsgraph,
                view_layer,
                engine,
                luxcore_scene,
                scene_props,
                context,
            )
        if stats:
            stats.export_time_objects.value += time() - objects_start
        if instances is None:
            # Export was cancelled by user
            return None

        if is_viewport_render:
            self.visibility_cache.init(depsgraph, context)

        # Motion blur
        # Motion blur seems not to work in viewport render, i.e. matrix_world
        # is the same on every frame
        if not context and utils.is_valid_camera(scene.camera):
            if self.motion_blur_enabled:
                motion_blur_start = time()
                motion_blur_props, cam_moving = motion_blur.convert(
                    context,
                    engine,
                    scene,
                    depsgraph,
                    self.object_cache2.exported_objects,
                    luxcore_scene,
                    instances,
                )

                if cam_moving:
                    # Re-export the camera with motion blur enabled
                    # (This is fast and we only have to step through the scene once in total, not twice)
                    camera_props = camera.convert(
                        self, scene, depsgraph, context, cam_moving
                    )
                    motion_blur_props.Set(camera_props)

                scene_props.Set(motion_blur_props)
                if stats:
                    stats.export_time_motionblur.value += (
                        time() - motion_blur_start
                    )

        # World (converted above the persistent-scene decision)
        scene_props.Set(world_props)

        # Out-of-core geometry spilling (LuxCore scene.spill.*): mesh
        # buffers over the threshold are file-backed before the BVH is
        # built, so the kernel can evict cold pages under pressure.
        if scene.luxcore.config.spill_geometry:
            scene_props.Set(pyluxcore.Property("scene.spill.enable", True))
            scene_props.Set(pyluxcore.Property(
                "scene.spill.minbytes",
                scene.luxcore.config.spill_geometry_minmb * 1024 * 1024,
            ))
            scene_props.Set(pyluxcore.Property(
                "scene.spill.images", scene.luxcore.config.spill_images
            ))

        if (
            scene.luxcore.debug.enabled
            and scene.luxcore.debug.print_properties
        ):
            print("-" * 50)
            print("DEBUG: Scene Properties:\n")
            print(
                "(Note: does not contain dupli props, only the props of the base object)\n"
            )
            print(scene_props)
            print("-" * 50)
        parse_start = time()
        luxcore_scene.Parse(scene_props)
        if stats:
            stats.export_time_scene_parse.value += time() - parse_start
        # We can only duplicate the instances *after* the scene_props were
        # parsed so the base objects are available for luxcore_scene
        self.object_cache2.duplicate_instances(instances, luxcore_scene, stats)
        # Dupli source objects need their "src+dupli" set re-flushed
        # when an instancer changes — keep the source->(ExportedObject,
        # compound obj_key) map for the persistent-scene delta path.
        # None entries mark sources that were instanced but not
        # exportable (first_run stores them as None).
        _dupli_srcs = {
            str(ptr): (d.exported_obj, d.obj_key) if d else (None, None)
            for ptr, d in instances.items()
        }
        # The instances dict can be quite large, delete explicitely (TODO maybe
        # even call gc.collect()?)
        del instances

        # Regularly check if we should abort the export (important in heavy scenes)
        if engine and engine.test_break():
            return None

        # Store the fully exported scene for reuse by the next final
        # render (skipped when this render already reused it).
        if pkey is not None and pentry is None:
            print(
                "[Exporter] Caching scene for persistent reuse:"
                f" {len(self.object_cache2.exported_objects)} objects"
            )
            _eval_shape = _eval_object_map(
                depsgraph, scene, set(vis_sig) | set(_dupli_srcs)
            )
            _member_mats = {
                k: o.matrix_world.copy()
                for k, o in _eval_shape.items()
                if k in vis_sig
                and k not in self.object_cache2.bake_matrices
            }
            # Dupli sources keep their instanced mesh under a compound
            # obj_key — include their geo_meta so the delta path can
            # redefine those meshes in place too.
            _dupli_eval = {
                ck: _eval_shape[sk]
                for sk, (_e, ck) in _dupli_srcs.items()
                if ck and sk in _eval_shape
            }
            _geo_meta = {
                k: v
                for k, v in self.object_cache2.obj_geo_meta.items()
                if k in vis_sig or k in _dupli_eval
            }
            _shape_sig = {
                k: self._shape_stack_sig(
                    _eval_shape.get(k) or _dupli_eval.get(k),
                    depsgraph,
                    meta[3],
                )
                for k, meta in _geo_meta.items()
            }
            # Original data pointer per member object — dirty data
            # datablocks (Mesh, Curves, Volume, ...) resolve to member
            # objects through this map at reuse time. Built from the
            # original objects because depsgraph.objects can skip
            # instanced-only members.
            _data_ptrs = {
                utils.make_key(o): o.data.as_pointer()
                for o in scene.objects
                if utils.make_key(o) in vis_sig
                and getattr(o, "data", None) is not None
            }
            # Member instancers (dupli/particle emitters): their dirty
            # flags re-flush affected dupli sets instead of rebuilding.
            _instancers = {
                k
                for k, o in _eval_shape.items()
                if k in vis_sig
                and (
                    o.instance_type != "NONE" or o.particle_systems
                )
            }
            # ParticleSettings ptr -> instancer key: a settings edit
            # re-flushes the dupli sets of every instancer using it.
            _psys_map = {}
            for _o in scene.objects:
                _ok = utils.make_key(_o)
                if _ok in vis_sig:
                    for _psys in _o.particle_systems:
                        _s = getattr(_psys.settings, "original", None)
                        _psys_map[
                            (_s or _psys.settings).as_pointer()
                        ] = _ok
            persistent_scene.store(
                pkey,
                luxcore_scene,
                self.object_cache2.exported_objects,
                set(vis_sig),
                self.object_cache2.bake_matrices,
                _member_mats,
                mb_sig,
                camera_sig,
                world_sig,
                vis_sig,
                depsgraph.scene.frame_current,
                mat_sig,
                slot_sig,
                _geo_meta,
                _shape_sig,
                _data_ptrs,
                _instancers,
                _dupli_srcs,
                _psys_map,
                self.object_cache2.instancer_srcs,
                self.object_cache2.instancer_singular,
            )

        # Convert config at last because all lightgroups and passes have to be
        # already defined
        config_start = time()
        config_props = config.convert(self, scene, context, engine)
        if str(config_props) == "":
            # Config props are empty: there was a critical error in config
            # export, we can't render
            raise Exception("Errors in config, check error log")

        # Init config cache (convert to string here because config_props gets
        # changed below)
        self.config_cache.diff(str(config_props))

        # Imagepipeline
        imagepipeline_props = imagepipeline.convert(scene, context)
        self.imagepipeline_cache.diff(
            imagepipeline_props
        )  # Init imagepipeline cache
        # Add imagepipeline to config props
        config_props.Set(imagepipeline_props)

        # Halt conditions
        halt_props = halt.convert(scene)
        self.halt_cache.diff(halt_props)
        config_props.Set(halt_props)
        if stats:
            stats.export_time_config.value += time() - config_start

        light_count = luxcore_scene.GetLightCount()
        if light_count > 1000:
            msg = (
                f"The scene contains a lot of light sources ({light_count}), "
                "performance might suffer "
                f"(each triangle of a meshlight counts as a separate light)"
            )
            LuxCoreErrorLog.add_warning(msg)
        if stats:
            stats.light_count.value = light_count

        # Create the renderconfig
        if (
            scene.luxcore.debug.enabled
            and scene.luxcore.debug.print_properties
        ):
            print("-" * 50)
            print("DEBUG: Config Properties:\n")
            print(config_props)
            print("-" * 50)

        # Regularly check if we should abort the export (important in heavy
        # scenes)
        if engine and engine.test_break():
            return None

        export_time = time() - start
        print("Export took %.1f s" % export_time)
        if stats:
            stats.export_time.value = export_time
            # Stage breakdown so export bottlenecks are visible in the log
            # instead of guessed (A6).
            stages = [
                ("camera", stats.export_time_camera),
                ("objects", stats.export_time_objects),
                ("  pointcloud", stats.export_time_pointcloud),
                ("  volumes", stats.export_time_volumes),
                ("  lights", stats.export_time_lights),
                ("  meshes", stats.export_time_meshes),
                ("  hair", stats.export_time_hair),
                ("motion_blur", stats.export_time_motionblur),
                ("world", stats.export_time_world),
                ("scene_parse", stats.export_time_scene_parse),
                ("instancing", stats.export_time_instancing),
                ("config", stats.export_time_config),
            ]
            breakdown = " | ".join(
                "%s=%.2fs" % (name, stat.value)
                for name, stat in stages
                if stat.value > 0.001
            )
            if breakdown:
                print("Export stages:", breakdown)
            if stats.instance_count.value:
                print(
                    "Export counts: objects=%d instances=%d"
                    % (
                        stats.exported_object_count.value,
                        stats.instance_count.value,
                    )
                )
            self._init_stats(stats, config_props, scene)

        # Final renders can release Blender's decoded image buffers:
        # LuxCore reads images from files and never touches ImBuf, so
        # the same texture data otherwise sits in RAM twice for the
        # whole render. Only file-backed, unmodified images are freed.
        if not is_viewport_render and getattr(
            scene.luxcore.config, "free_blender_image_buffers", True
        ):
            image.ImageExporter.free_blender_buffers()

        # Do not hold reference to temporary data
        self.scene = None
        return luxcore_scene, config_props

    def create_render_session(
        self, config_props, luxcore_scene, progress_cb=None
    ):
        """RenderConfig + kernel pre-compile + RenderSession.

        Pure pyluxcore - no depsgraph/bpy-scene access, so it is safe on
        the session worker thread (that is where the viewport runs it).
        ``progress_cb`` receives ``(index, count)`` while GPU kernels
        compile; pass None for a silent fill.
        """
        from ..engine.session_worker import precompile_kernels

        renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)
        precompile_kernels(config_props, renderconfig, progress_cb)
        return pyluxcore.RenderSession(renderconfig)

    def _apply_transform_deltas(
        self, pentry, transform_deltas, depsgraph, luxcore_scene
    ):
        """
        Apply transform-only updates to a reused persistent scene.

        For objects exported with a transformation on the LuxCore object
        (instanced/shared/motion-blur exports) the new absolute matrix
        replaces the old one. For objects with the transform baked into
        the mesh vertices, UpdateObjectTransformation applies a relative
        delta (new @ old.inverted()) to the world-space geometry.
        """
        if not transform_deltas:
            return
        eval_by_key = {
            utils.make_key(o): o for o in depsgraph.objects
        }
        matrix_to_list = utils.luxutils.matrix_to_list
        for key in transform_deltas:
            exported = pentry["objects"][key]
            eval_obj = eval_by_key.get(key)
            if eval_obj is None:
                # Should not happen: membership was checked against the
                # same scene. Skip rather than corrupt the entry.
                continue
            new_matrix = eval_obj.matrix_world
            # An instancer's own transform is patchable here; its dupli
            # set is flushed separately via _refresh_dupli_sets.
            if exported.transform is None:
                delta = new_matrix @ pentry["bake"][key].inverted()
            else:
                delta = new_matrix
            mat_list = matrix_to_list(delta)
            for part in exported.parts:
                luxcore_scene.UpdateObjectTransformation(
                    part.lux_obj, mat_list
                )
            pentry["bake"][key] = new_matrix.copy()

    def _mesh_inplace_safe(self, pentry, key, geo_meta, eval_by_key):
        """
        Eligibility for the in-place ``DefineMesh`` geometry delta: a
        mesh object whose exported mesh can be re-defined under the
        same name without leaving stale definitions behind.
        """
        meta = geo_meta.get(key)
        exported = pentry["objects"].get(key)
        if meta is None or exported is None:
            return False
        (
            _src_ptr,
            mesh_key,
            use_instancing,
            base_list,
            wrapped,
        ) = meta
        if (
            wrapped
            or getattr(exported, "duplicate_count", 0)
            or key not in pentry["delta_safe"]
        ):
            return False
        # Every object sharing this mesh_key must be wrapper-free,
        # or its wrapper keeps a dangling source-mesh pointer.
        for key2, meta2 in geo_meta.items():
            if key2 != key and meta2[1] == mesh_key and meta2[4]:
                return False
        obj = eval_by_key.get(key)
        # All mesh-convertible types (MESH/CURVE/FONT/...) re-define
        # through the same DefineMesh path; hair-curves objects carry
        # no geo_meta entry and are excluded above.
        if obj is None or obj.type not in utils.MESH_OBJECTS:
            return False
        # The instancing decision must match export time, or the
        # recomputed mesh_key/shape names would not line up.
        cur_instancing = (
            utils.can_share_mesh(obj.original)
            or uses_displacement(obj)
            or (
                self.motion_blur_enabled
                and obj.luxcore.enable_motion_blur
            )
        )
        if cur_instancing != use_instancing:
            return False
        # Auto mesh proxy: heavy meshes baked to .lxm files are not
        # DefineMesh-able — force the delete+re-export path, where
        # _convert_mesh_obj bakes or reuses the proxy files.
        return not self.object_cache2._auto_proxy_applies(
            obj, self.scene, self.motion_blur_enabled
        )

    def _apply_geometry_deltas(
        self,
        pentry,
        geometry_keys,
        depsgraph,
        luxcore_scene,
        view_layer,
        engine,
    ):
        """
        Re-export the geometry of member objects whose data changed.

        Mesh objects that pass the eligibility check take the cheap
        path: ``Scene.DefineMesh`` replaces a named mesh in place and
        rewires every scene object referencing it — including triangle
        lights — so object definitions and material bindings stay
        untouched. Everything else (hair curves, volumes, pointclouds,
        objects whose shape stack changed) is deleted and re-exported
        through the normal conversion path, which also picks up the
        current transform.

        Returns the set of keys whose transform delta is subsumed (all
        re-exports carry the current matrix). Raises on any failure,
        which the caller turns into a full rebuild.
        """
        eval_by_key = _eval_object_map(
            depsgraph,
            depsgraph.scene,
            set(pentry["members"]) | set(pentry["dupli_srcs"]),
        )
        geo_meta = pentry["geo_meta"]
        subsumed = set()
        reexport_keys = []
        for key in sorted(geometry_keys):
            if key in pentry["dupli_srcs"]:
                # A dupli source's instanced copy lives under a
                # different mesh_key ("_instance" suffix) than its
                # standalone export — redefine it in place as well so
                # the whole dupli set follows the edit (DefineMesh
                # rewires the dupli base and all duplicates).
                if not self._dupli_src_inplace(
                    pentry, key, eval_by_key, depsgraph, luxcore_scene
                ):
                    raise ValueError(
                        f"{key}: dupli-source mesh cannot be redefined"
                    )
                if key not in pentry["objects"]:
                    # Instancer-only source (e.g. a vert-dupli child
                    # Blender never exports standalone): the instanced
                    # mesh redefine covered everything.
                    continue
            inplace = self._mesh_inplace_safe(
                pentry, key, geo_meta, eval_by_key
            )
            if inplace:
                (
                    _src_ptr,
                    mesh_key,
                    use_instancing,
                    base_list,
                    _wrapped,
                ) = geo_meta[key]
                obj = eval_by_key[key]
                try:
                    new_mesh = mesh_converter.convert(
                        obj,
                        mesh_key,
                        depsgraph,
                        luxcore_scene,
                        False,
                        use_instancing,
                        obj.matrix_world,
                        self,
                    )
                    if {n for n, _m in new_mesh.mesh_definitions} != {
                        n for n, _m in base_list
                    }:
                        # A slot became (un)used — part names shifted,
                        # so the scene holds stale object definitions;
                        # fall through to the delete + re-export path.
                        raise ValueError(f"{key}: submesh set changed")
                except Exception:
                    inplace = False
                else:
                    self.object_cache2.exported_meshes[
                        mesh_key
                    ] = new_mesh
                    if not use_instancing:
                        # The re-export already baked the current
                        # matrix into the mesh verts — no separate
                        # transform delta needed.
                        pentry["bake"][key] = obj.matrix_world.copy()
                        subsumed.add(key)
            if not inplace:
                # Delete + re-export carries the current transform too.
                reexport_keys.append(key)
                subsumed.add(key)
        if reexport_keys:
            self._reexport_objects(
                pentry, reexport_keys, depsgraph, luxcore_scene,
                view_layer, engine
            )
        return subsumed

    def _dupli_src_inplace(
        self, pentry, key, eval_by_key, depsgraph, luxcore_scene
    ):
        """
        In-place ``DefineMesh`` for a dupli source's *instanced* mesh.

        The dupli set references the mesh under the compound key's
        ``_instance`` mesh_key, which a plain-object re-definition does
        not touch. Redefining it here rewires the dupli base object and
        every duplicate sharing the shape name. Returns False when the
        instanced mesh cannot be safely redefined (the caller rebuilds).
        """
        exported, compound_key = pentry["dupli_srcs"][key]
        geo_meta = pentry["geo_meta"]
        meta = geo_meta.get(compound_key)
        obj = eval_by_key.get(key)
        if (
            exported is None
            or meta is None
            or obj is None
            # All mesh-convertible types take the same DefineMesh path;
            # hair-curves sources have no geo_meta entry and fail above.
            or obj.type not in utils.MESH_OBJECTS
        ):
            return False
        (
            _src_ptr,
            mesh_key,
            use_instancing,
            base_list,
            wrapped,
        ) = meta
        if wrapped or not use_instancing:
            return False
        # Every object sharing this instanced mesh_key must be
        # wrapper-free, or its wrapper keeps a dangling source-mesh
        # pointer.
        for k2, meta2 in geo_meta.items():
            if k2 != compound_key and meta2[1] == mesh_key and meta2[4]:
                return False
        try:
            new_mesh = mesh_converter.convert(
                obj,
                mesh_key,
                depsgraph,
                luxcore_scene,
                False,
                use_instancing,
                obj.matrix_world,
                self,
            )
            if {n for n, _m in new_mesh.mesh_definitions} != {
                n for n, _m in base_list
            }:
                return False
        except Exception:
            return False
        self.object_cache2.exported_meshes[mesh_key] = new_mesh
        return True

    def _reexport_objects(
        self,
        pentry,
        keys,
        depsgraph,
        luxcore_scene,
        view_layer,
        engine,
    ):
        """
        Delete + re-export member objects whose geometry changed but
        cannot be patched by a bare ``DefineMesh`` (hair curves,
        volumes, pointclouds, shifted submesh sets, wrapped meshes).

        ``ExportedObject.delete`` removes the object's parts, its
        pointcloud dupis and any triangle lights; the normal
        ``_convert_obj`` path then re-defines shapes (in-place mesh /
        strand replacement under the same names) and object props,
        which ``Scene.Parse`` applies as a fresh definition. Per-entry
        records are refreshed so later deltas see the new state.
        """
        # DepsgraphObjectInstance wrappers are only valid while the
        # instance iterator is alive — holding them in a map turns them
        # into dangling StructRNA references. Snapshot the fields
        # _convert_obj needs (evaluated object, show_self, matrix) as
        # plain values and rebuild a shim below.
        inst_info = {}
        for dg_inst in depsgraph.object_instances:
            if not dg_inst.is_instance:
                inst_info[utils.make_key_from_instance(dg_inst)] = (
                    dg_inst.object,
                    dg_inst.show_self,
                    dg_inst.matrix_world.copy(),
                )
        cache = self.object_cache2
        for key in keys:
            info = inst_info.get(key)
            if info is None:
                raise ValueError(
                    f"{key}: no base instance in depsgraph"
                )
            eval_obj, show_self, matrix = info
            dg_inst = types.SimpleNamespace(
                object=eval_obj,
                is_instance=False,
                show_self=show_self,
                parent=None,
                persistent_id=None,
                random_id=0,
                matrix_world=matrix,
            )
            if key in pentry["dupli_srcs"]:
                # A dupli source's ExportedObject is registered under a
                # compound instance key and carries the first instance's
                # transform — the plain-object re-export would corrupt
                # both. Dupli sources rebuild conservatively.
                raise ValueError(f"{key}: dupli source re-export unsafe")
            exported = pentry["objects"].get(key)
            if exported is not None:
                exported.delete(luxcore_scene)
                # The instancer/pointcloud "dupli" object is a single
                # scene object, not covered by the indexed names in
                # delete(); drop it so the re-export can redefine it.
                for part in getattr(exported, "parts", []):
                    luxcore_scene.DeleteObject(part.lux_obj + "dupli")
            # Drop stale cache entries so the re-export is fresh:
            # mesh cache by mesh_key, hair cache by key prefix.
            meta = pentry["geo_meta"].pop(key, None)
            if meta is not None and meta[1]:
                cache.exported_meshes.pop(meta[1], None)
            for hkey in (
                k
                for k in cache.exported_hair
                if k == key or k.startswith(key + "_")
            ):
                cache.exported_hair.pop(hkey, None)
            # Particle hair is cached under a name-based psys key —
            # drop the entries of this object's particle systems.
            for _psys in eval_obj.particle_systems:
                for _inst in (False, True):
                    cache.exported_hair.pop(
                        make_psys_key(eval_obj, _psys, _inst), None
                    )
            scratch = pyluxcore.Properties()
            new_exported = cache._convert_obj(
                self,
                dg_inst,
                dg_inst.object,
                depsgraph,
                luxcore_scene,
                scratch,
                False,
                view_layer,
                engine,
            )
            if new_exported is None:
                raise ValueError(f"{key}: re-export produced nothing")
            luxcore_scene.Parse(scratch)
            # Pointcloud dupis are staged for the post-Parse flush.
            cache.duplicate_instances({}, luxcore_scene, None)
            # Refresh the entry's per-object records. _convert_obj
            # already wrote exported_objects and bake_matrices — those
            # dicts are shared with the cache, while the entry keeps
            # its own snapshots of the rest.
            pentry["objects"][key] = new_exported
            bake = cache.bake_matrices.get(key)
            if bake is not None:
                pentry["bake"][key] = bake[0]
                if bake[1]:
                    pentry["delta_safe"].add(key)
                else:
                    pentry["delta_safe"].discard(key)
            pentry["member_mats"].pop(key, None)
            new_meta = cache.obj_geo_meta.get(key)
            if new_meta is not None:
                pentry["geo_meta"][key] = new_meta
                pentry["shape_sig"][key] = self._shape_stack_sig(
                    dg_inst.object, depsgraph, new_meta[3]
                )
            else:
                pentry["shape_sig"].pop(key, None)
            obj_orig = dg_inst.object.original
            pentry["data_ptrs"][key] = (
                obj_orig.data.as_pointer()
                if getattr(obj_orig, "data", None) is not None
                else 0
            )
            # Slot bindings can shift when the re-export resolves a
            # different material set.
            slots = []
            for slot in obj_orig.material_slots:
                mat = slot.material
                if mat is None:
                    slots.append((None, slot.link))
                    continue
                mptr = str(mat.original.as_pointer())
                slots.append((mptr, slot.link))
                pentry["mat_sig"][mptr] = utils.get_luxcore_name(
                    mat.original, False
                )
            pentry["slot_sig"][key] = tuple(slots)

    def _refresh_dupli_sets(
        self, pentry, instancer_keys, depsgraph, luxcore_scene
    ):
        """
        Re-flush the dupli objects of every source instanced by a dirty
        or moved instancer.

        LuxCore stores a source's dupli instances as a single scene
        object per part (``src+dupli``) holding the flattened transform
        list of *all* its instances — so the set is rebuilt wholesale:
        collect every current instance of the source, update the base
        object's transform to the first instance's matrix, delete the
        old ``dupli`` object and re-``DuplicateObject`` the rest.
        Sources re-exported by the geometry path are also refreshed
        (their dupli objects were deleted with them).
        """
        if self.object_blur_enabled:
            # Per-step motion buffers are built by motion_blur.convert's
            # re-evaluation — a plain matrix re-flush would lose blur.
            raise ValueError(
                "instancer delta unsupported with object motion blur"
            )
        mat_to_list = pyluxcore.BlenderMatrix4x4ToList
        # One pass over the instance list: collect each dirty
        # instancer's current source set plus, per source, the flat
        # matrix list and object IDs of its visible instances — the
        # same data first_run feeds DuplicateObject (the first
        # instance's transform rides on the base object).
        cur_srcs = {}
        inst_mats = {}
        inst_ids = {}
        for dg_inst in depsgraph.object_instances:
            if not dg_inst.is_instance or dg_inst.parent is None:
                continue
            pkey = utils.make_key(dg_inst.parent)
            if pkey not in instancer_keys:
                continue
            sptr = dg_inst.object.original.as_pointer()
            cur_srcs.setdefault(pkey, set()).add(sptr)
            if not (dg_inst.show_self or dg_inst.show_particles):
                continue
            obj_id = dg_inst.object.original.luxcore.id
            if obj_id == -1:
                obj_id = dg_inst.random_id & 0xFFFFFFFE
            inst_ids.setdefault(sptr, []).append(obj_id)
            inst_mats.setdefault(sptr, []).extend(
                mat_to_list(dg_inst.matrix_world.copy())
            )
        for key in instancer_keys:
            if cur_srcs.get(key, set()) != pentry["instancer_srcs"].get(
                key, set()
            ):
                # A source was added or dropped entirely (e.g. the
                # particle count hit zero): dropped sources would leave
                # orphaned dupli objects behind and new ones have no
                # base export to duplicate — rebuild.
                raise ValueError(
                    f"{key}: instancer source set changed"
                )
        for key in instancer_keys:
            for sptr in cur_srcs.get(key, ()):
                src_key = str(sptr)
                if src_key not in pentry["dupli_srcs"]:
                    raise ValueError(f"{key}: unexported dupli source")
                exported, _compound_key = pentry["dupli_srcs"][src_key]
                if exported is None:
                    # Instanced but not exportable at export time —
                    # first_run skipped it the same way.
                    continue
                mats = inst_mats.get(sptr, [])
                ids = inst_ids.get(sptr, [])
                count = len(ids)
                if count == 0 or exported.transform is None:
                    # An emptied set cannot leave a stale dupli object,
                    # and a non-instancing (baked) base cannot take an
                    # absolute transform — rebuild.
                    raise ValueError(
                        f"{key}: dupli set {src_key} not refreshable"
                    )
                for part in exported.parts:
                    luxcore_scene.DeleteObject(part.lux_obj + "dupli")
                    luxcore_scene.UpdateObjectTransformation(
                        part.lux_obj, mats[:16]
                    )
                    if count > 1:
                        # DuplicateObject wants typed buffers, same as
                        # Duplis.matrices/object_ids in first_run.
                        luxcore_scene.DuplicateObject(
                            part.lux_obj,
                            part.lux_obj + "dupli",
                            count - 1,
                            array("f", mats[16:]),
                            array("I", ids[1:]),
                        )

    def _shape_stack_sig(self, obj, depsgraph, base_list):
        """
        Recompute the wrapper-shape chain a member object's materials
        would produce today.

        Material edits can change the shape stack — adding a
        displacement link or a luxcore shape node means a *new* wrapper
        shape is required, which a material re-export alone cannot
        create. Replaying the same functions the export path uses into
        a scratch Properties catches both name-level (added/removed
        wrappers) and value-level (displacement scale) changes; on any
        failure the caller must rebuild, so ``None`` is a never-match
        sentinel rather than an error.
        """
        try:
            scratch = pyluxcore.Properties()
            shapes = []
            for base_name, mat_index in base_list:
                mat = get_material(obj, mat_index, depsgraph)
                node_tree = (
                    mat.original.luxcore.node_tree
                    if mat is not None
                    else None
                )
                if node_tree:
                    shape = define_shapes(
                        base_name, node_tree, self, depsgraph, scratch
                    )
                else:
                    shape = _apply_cycles_displacement(
                        base_name, obj, mat_index, depsgraph, scratch
                    )
                shapes.append(shape)
            return (tuple(shapes), str(scratch))
        except Exception:
            return None

    def _reexport_scene_materials(self, depsgraph, luxcore_scene):
        """
        Re-export every member material into the cached scene.

        Material (and texture/volume) re-definition via Scene.Parse is
        a first-class engine operation: the new properties rebuild the
        named material in place, including its light-source
        associations. Only the material's *content* is refreshed —
        identity and slot bindings are guarded by the mat_sig/slot_sig
        signatures checked before reuse.
        """
        done = set()
        for obj in depsgraph.scene.objects:
            for slot in obj.material_slots:
                mat = slot.material
                if mat is None:
                    continue
                ptr = str(mat.original.as_pointer())
                if ptr in done:
                    continue
                done.add(ptr)
                _lux_name, mat_props = material.convert(
                    self, depsgraph, mat.original, False, obj.name
                )
                luxcore_scene.Parse(mat_props)
        print(
            "[Exporter] Re-exported"
            f" {len(done)} material(s) into cached scene"
        )

    def get_viewport_changes(self, depsgraph, context=None):
        self.scene = depsgraph.scene_eval
        changes = Change.NONE

        config_props = config.convert(self, self.scene, context)
        if self.config_cache.diff(config_props):
            changes |= Change.CONFIG

        if self.camera_cache.diff(self, self.scene, depsgraph, context):
            changes |= Change.CAMERA

        # Do not hold reference to temporary data
        self.scene = None
        return changes

    def get_changes(self, depsgraph, context=None, changes=None):
        self.scene = depsgraph.scene_eval
        final = context is None

        # Particle system counts might have changed
        supports_live_transform.cache_clear()

        if not final:
            if changes is None:
                changes = self.get_viewport_changes(depsgraph, context)

            if self.object_cache2.diff(depsgraph):
                changes |= Change.OBJECT

            if self.material_cache.diff(depsgraph):
                changes |= Change.MATERIAL

            if self.visibility_cache.diff(depsgraph, context):
                changes |= Change.VISIBILITY

                if self.visibility_cache.has_new_objects:
                    changes |= Change.OBJECT

            if self.world_cache.diff(depsgraph):
                changes |= Change.WORLD

        if changes is None:
            changes = Change.NONE

        # Relevant during final render
        imagepipeline_props = imagepipeline.convert(depsgraph.scene, context)
        if self.imagepipeline_cache.diff(imagepipeline_props):
            changes |= Change.IMAGEPIPELINE

        if final:
            # Halt conditions are only used during final render
            halt_props = halt.convert(depsgraph.scene)
            if self.halt_cache.diff(halt_props):
                changes |= Change.HALT

        # Do not hold reference to temporary data
        self.scene = None
        return changes

    def update(self, depsgraph, context, changes):
        """Prepare deferred session work for the session worker.

        Runs on the main thread (depsgraph access) but never touches the
        live pyluxcore session: scene mutations are recorded on a
        RecordedScene and session-level parses become props payloads.
        Returns a list of ``(kind, payload)`` jobs - ``("edit", ops)``
        and/or ``("parse", props)`` - for the caller to submit.

        Raises on export failure: a half-recorded edit must not be
        replayed, so the caller falls back to a full session restart.
        """
        self.scene = depsgraph.scene_eval
        print("[Exporter] Update because of:", Change.to_string(changes))
        # Invalidate node cache
        self.node_cache.clear()

        jobs = []
        try:
            if changes & Change.REQUIRES_SCENE_EDIT:
                recorded = RecordedScene()
                props = self._update_scene(
                    depsgraph, context, changes, recorded
                )
                recorded.Parse(props)
                jobs.append(("edit", recorded.drain()))

            if changes & Change.REQUIRES_SESSION_PARSE:
                props = pyluxcore.Properties()
                if changes & Change.IMAGEPIPELINE:
                    props.Set(self.imagepipeline_cache.props)
                if changes & Change.HALT:
                    props.Set(self.halt_cache.props)
                jobs.append(("parse", props))
        finally:
            # Do not hold reference to temporary data
            self.scene = None

        return jobs

    def update_session(self, changes, session):
        if changes & Change.IMAGEPIPELINE:
            session.Parse(self.imagepipeline_cache.props)
        if changes & Change.HALT:
            session.Parse(self.halt_cache.props)

    def _update_scene(self, depsgraph, context, changes, luxcore_scene):
        props = pyluxcore.Properties()

        if changes & Change.CAMERA:
            # We already converted the new camera settings during
            # get_changes(), re-use them
            props.Set(self.camera_cache.props)

        if changes & Change.OBJECT:
            self.object_cache2.update(
                self, depsgraph, luxcore_scene, props, context
            )

        if changes & Change.MATERIAL:
            self.material_cache.update(self, depsgraph, context, props)

        if changes & Change.VISIBILITY:
            for key in self.visibility_cache.objects_to_remove:
                print("Removing object with key", key)

                try:
                    exported_obj = self.object_cache2.exported_objects.pop(key)
                    exported_obj.delete(luxcore_scene)
                except KeyError:
                    # This is ok, not every exportable object is added to exported_objects
                    pass

            if self.visibility_cache.objects_to_remove:
                # luxcore_scene.RemoveUnusedMeshes()  # TODO for some reason this deletes even some meshes that are still in use
                luxcore_scene.RemoveUnusedMaterials()
                luxcore_scene.RemoveUnusedTextures()
                luxcore_scene.RemoveUnusedImageMaps()

        if changes & Change.WORLD:
            if (
                not context.scene.world
                or context.scene.world.luxcore.light == "none"
            ):
                luxcore_scene.DeleteLight(WORLD_BACKGROUND_LIGHT_NAME)

            world_props = world.convert(
                self, depsgraph, context.scene, is_viewport_render=True
            )
            props.Set(world_props)

        return props

    def _init_stats(self, stats, config_props, scene):
        render_engine = config_props.Get("renderengine.type").GetString()
        stats.render_engine.value = utils_render.engine_to_str(render_engine)
        # Tiled engines always run a convergence test. On PATH/PATHOCL the
        # film-level test (batch.haltnoisethreshold, exported via the legacy
        # batch.haltthreshold alias) is opt-in through the noise-threshold
        # halt condition; -1 renders as "n/a" and is never overwritten by
        # update_from_luxcore_stats.
        has_noise_test = (
            "TILE" in render_engine
            or config_props.Get("batch.haltnoisethreshold", [-1]).GetFloat() > 0
            or config_props.Get("batch.haltthreshold", [-1]).GetFloat() > 0
        )
        stats.convergence.value = 0.0 if has_noise_test else -1.0
        sampler = config_props.Get("sampler.type").GetString()
        stats.sampler.value = utils_render.sampler_to_str(sampler)

        config_settings = scene.luxcore.config
        path_settings = config_settings.path

        if render_engine == "BIDIRCPU":
            path_depths = (
                config_settings.bidir_path_maxdepth,
                config_settings.bidir_light_maxdepth,
            )
        else:
            path_depths = (
                path_settings.depth_total,
                path_settings.depth_diffuse,
                path_settings.depth_glossy,
                path_settings.depth_specular,
            )
        stats.path_depths.value = path_depths

        if path_settings.use_clamping:
            stats.clamping.value = path_settings.clamping
        else:
            stats.clamping.value = 0

        stats.use_hybridbackforward.value = (
            config_props.Get(
                "path.hybridbackforward.enable", [False]
            ).GetBool()
            and render_engine != "BIDIRCPU"
        )
