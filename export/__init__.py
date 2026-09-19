from time import time

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
)
from .light import WORLD_BACKGROUND_LIGHT_NAME
from .caches.object_cache import supports_live_transform
from .caches import persistent_scene

if _needs_reload:
    import importlib

    modules = (
        caches,
        persistent_scene,
        camera,
        config,
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
                    o.visible_camera,
                    o.visible_diffuse,
                    o.visible_glossy,
                    o.visible_transmission,
                    o.visible_volume_scatter,
                    o.visible_shadow,
                )
                for o in scene.objects
            }
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
                    else:
                        _mode, transform_deltas = persistent_scene.classify(
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
                            eval_by_key = {
                                utils.make_key(o): o
                                for o in depsgraph.objects
                            }
                            _camera_key = (
                                utils.make_key(scene.camera)
                                if scene.camera
                                else None
                            )
                            _rebuild, _moved = (
                                persistent_scene.frame_change(
                                    pentry, eval_by_key, _camera_key
                                )
                            )
                            if _rebuild:
                                pentry = None
                            else:
                                transform_deltas |= _moved

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
                self._apply_transform_deltas(
                    pentry, transform_deltas, depsgraph, luxcore_scene
                )
                pentry["frame"] = depsgraph.scene.frame_current
                instances = {}
                print(
                    "[Exporter] Persistent scene reuse:"
                    f" {len(transform_deltas)} transform delta(s),"
                    f" {len(pentry['objects'])} objects kept"
                )
            except Exception:
                # A delta that fails mid-way leaves the cached scene in
                # an unknown state: discard it and rebuild from scratch.
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
            _member_mats = {
                utils.make_key(o): o.matrix_world.copy()
                for o in depsgraph.objects
                if utils.make_key(o) in vis_sig
                and utils.make_key(o)
                not in self.object_cache2.bake_matrices
            }
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
        renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)

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

        # Pre-compile CUDA or OpenCL kernels for viewport and final.
        renderengine_type = config_props.Get("renderengine.type").GetString()
        if (
            renderengine_type.endswith("OCL")
            and not renderconfig.HasCachedKernels()
        ):
            if engine:
                gpu_backend = utils.get_addon_preferences(
                    bpy.context
                ).gpu_backend
                message = (
                    f"Compiling {gpu_backend} kernels (just once, "
                    "usually takes 15-30 minutes)"
                )
                engine.report({"INFO"}, message)
                engine.update_stats(message, "")

            # Copy config props so we can pass scene.epsilon.min,
            # scene.epsilon.max and opencl.devices.select to the kernel
            config_props_copy = pyluxcore.Properties(config_props)
            engines = ["PATHOCL", "RTPATHOCL"]
            if renderengine_type == "TILEPATHOCL":
                # Only pre-compile for tiled path if requested, since it's
                # rarely used
                engines.append("TILEPATHOCL")
            config_props_copy.Set(
                pyluxcore.Property(
                    "kernelcachefill.renderengine.types", engines
                )
            )
            pyluxcore.KernelCacheFill(config_props_copy)

        # Inform about pre-computations that can take a long time to complete,
        # like caches
        if engine:
            message = "Creating RenderSession"

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
                "Export Finished (%.1f s)" % export_time, message
            )

        # Do not hold reference to temporary data
        self.scene = None
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
            if (
                eval_obj.instance_type != "NONE"
                or eval_obj.particle_systems
            ):
                # An instancer's transform also moves its dupli/particle
                # instance set, which a per-object delta cannot update.
                raise RuntimeError(
                    f"instancer '{eval_obj.name}' needs full export"
                )
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

    def update(self, depsgraph, context, session, changes):
        self.scene = depsgraph.scene_eval
        print("[Exporter] Update because of:", Change.to_string(changes))
        # Invalidate node cache
        self.node_cache.clear()

        if changes & Change.CONFIG:
            # We already converted the new config settings during
            # get_changes(), re-use them
            session = self._update_config(session, self.config_cache.props)

        if changes & Change.REQUIRES_SCENE_EDIT:
            luxcore_scene = session.GetRenderConfig().GetScene()
            session.BeginSceneEdit()

            try:
                props = self._update_scene(
                    depsgraph, context, changes, luxcore_scene
                )
                luxcore_scene.Parse(props)
            except Exception as error:
                LuxCoreErrorLog.add_error(error)
                import traceback

                traceback.print_exc()

            try:
                session.EndSceneEdit()
            except RuntimeError as error:
                import traceback

                traceback.print_exc()
                LuxCoreErrorLog.add_error(error)
                print("Fatal error, stopping session.")
                session.Stop()  # TODO not sure if this works
                raise

            if session.IsInPause():
                session.Resume()

        if changes & Change.REQUIRES_SESSION_PARSE:
            self.update_session(changes, session)

        # Do not hold reference to temporary data
        self.scene = None

        # We have to return and re-assign the session in the RenderEngine,
        # because it might have been replaced in _update_config()
        return session

    def update_session(self, changes, session):
        if changes & Change.IMAGEPIPELINE:
            session.Parse(self.imagepipeline_cache.props)
        if changes & Change.HALT:
            session.Parse(self.halt_cache.props)

    def _update_config(self, session, config_props):
        # https://github.com/LuxCoreRender/BlendLuxCore/issues/577
        # The historical implementations of this method mutated the existing
        # RenderConfig via Parse() after stopping the session. That path leaks
        # (each stopped session keeps its copy of the scene alive) and in some
        # LuxCore versions crashed Blender.
        #
        # Instead of mutating the old config, we build a fresh RenderConfig
        # from the new props while REUSING the LuxCore scene of the running
        # session. Re-exporting the whole Blender scene is therefore not
        # necessary (meshes, materials and lights stay defined in the reused
        # scene) - this is what makes viewport config changes fast.
        #
        # Note: renderengine.type changes and film size changes are handled
        # fine by this too (a new session is started with the new config).
        renderconfig = session.GetRenderConfig()
        luxcore_scene = renderconfig.GetScene()

        session.Stop()
        # Explicitly drop our reference to the old session so the scene copy
        # it owns is freed before we create the replacement
        del session

        new_renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)
        new_session = pyluxcore.RenderSession(new_renderconfig)
        new_session.Start()
        return new_session

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
