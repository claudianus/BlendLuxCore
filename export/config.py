import os
import errno
from math import degrees
import bpy
from collections import OrderedDict
import pyluxcore
from .. import utils
from . import aovs
from .imagepipeline import use_backgroundimage
from ..utils.errorlog import LuxCoreErrorLog
from ..utils import view_layer as utils_view_layer
from ..utils import get_addon_preferences


class SamplingOverlap:
    PROGRESSIVE = 1
    CACHE_FRIENDLY = 32
    OUT_OF_CORE = 32


# Emitters above this count switch the AUTO light strategy to ReSTIR DI:
# below it, the log-power distribution is cheaper per sample and equally
# accurate; reservoir resampling only pays off once plain light sampling
# keeps missing most of the emitters.
AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD = 16

# Engines supporting the ReSTIR DI light strategy (see RESTIR_DI_DESC in
# properties/config.py). AUTO falls back to LOG_POWER on any other engine.
_RESTIR_ENGINES = {
    "PATHCPU", "TILEPATHCPU", "RTPATHCPU",
    "PATHOCL", "TILEPATHOCL", "RTPATHOCL",
}

_EMISSIVE_NODE_TYPES = {"LuxCoreNodeMatEmission", "ShaderNodeEmission"}


def _material_is_emissive(mat):
    """Cheap heuristic: does this material emit light?

    Looks for an emission node (LuxCore or Cycles) or a Principled BSDF
    with emission enabled. Linked-ness of the emission node is not
    verified, so this may overcount emitters slightly — acceptable for a
    strategy heuristic that only needs the order of magnitude.
    """
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    for node in mat.node_tree.nodes:
        if node.bl_idname in _EMISSIVE_NODE_TYPES:
            return True
        if node.bl_idname == "ShaderNodeBsdfPrincipled":
            try:
                if node.inputs["Emission Strength"].default_value > 0:
                    return True
            except (KeyError, AttributeError):
                pass
    return False


def _count_emitters(scene):
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
                    if not _material_is_emissive(mat):
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


def _auto_light_strategy(scene, luxcore_engine):
    """Resolve AUTO light strategy from the scene's emitter count."""
    if luxcore_engine not in _RESTIR_ENGINES:
        return "LOG_POWER"
    if _count_emitters(scene) > AUTO_LIGHT_STRATEGY_EMITTER_THRESHOLD:
        return "RESTIR_DI"
    return "LOG_POWER"


def _collect_light_portals(scene):
    """Quad faces of objects flagged "Light Portal" as world-space rects.

    Returns a list of 12-float lists (4 corners, CCW around the face
    normal) for path.portal.<i>. Non-quad faces are skipped - the engine
    side only models planar rects.
    """
    rects = []
    skipped_tris = 0
    for obj in scene.objects:
        if obj.type != "MESH" or not getattr(
            obj.luxcore, "is_light_portal", False
        ):
            continue
        mesh = obj.data
        if mesh is None:
            continue
        mw = obj.matrix_world
        verts = mesh.vertices
        for poly in mesh.polygons:
            if len(poly.vertices) != 4:
                skipped_tris += 1
                continue
            corners = []
            for vi in poly.vertices:
                co = mw @ verts[vi].co
                corners.extend((co.x, co.y, co.z))
            rects.append(corners)
    if skipped_tris:
        LuxCoreErrorLog.add_warning(
            f"Light portal: skipped {skipped_tris} non-quad face(s) "
            "(portals must be planar quads)"
        )
    return rects


def convert(exporter, scene, context=None, engine=None):
    config = scene.luxcore.config
    simple_token = None
    try:
        prefix = ""
        # We collect the properties in this dictionary (ordered because we sometimes
        # need to read them for debugging).
        # The dictionary is converted to pyluxcore.Properties() in the return statement.
        definitions = OrderedDict()

        # See properties/config.py
        config = scene.luxcore.config
        is_viewport_render = context is not None

        # Compositor trap: rendering with compositing enabled but no
        # Composite node in the tree yields a black output file even though
        # the film itself is fine (measured on a production scene: lit film
        # 0.27, black PNG). Warn loudly instead of silently delivering black.
        try:
            if (scene.render.use_compositing and scene.use_nodes and
                    not is_viewport_render):
                tree = getattr(scene, "compositing_node_group", None)
                has_output = False
                if tree is not None:
                    for _n in tree.nodes:
                        if _n.bl_idname == "CompositorNodeComposite":
                            has_output = True
                            break
                if not has_output:
                    LuxCoreErrorLog.add_warning(
                        "Compositing is enabled but the node tree has no "
                        "Composite output node: the saved image will be black. "
                        "Disable compositing or add a Composite node")
        except Exception:
            pass

        # Quick Setup: map the single quality slider onto the underlying
        # settings before the regular conversion picks them up.
        # Snapshot/restore: the mapping writes into the live Blender
        # properties, so without this every (re-)export — viewports
        # re-export constantly — would silently eat the user's own values.
        simple_token = None
        if config.simple.enabled:
            simple_token = config.simple.snapshot(scene)
            config.simple.apply(config)
            config.simple.apply_halt(scene)
            # Caustics auto-detection (needs the scene, not just config)
            if not is_viewport_render:
                config.simple.apply_scene_scan(scene)

        width, height = utils.calc_filmsize(scene, context)
        in_material_shading_mode = utils.in_material_shading_mode(context)
        denoiser_enabled = (
            not is_viewport_render and scene.luxcore.denoiser.enabled
        ) or (
            is_viewport_render
            and scene.luxcore.viewport.use_denoiser
            and not in_material_shading_mode
        )
        preferences = get_addon_preferences(bpy.context)

        if is_viewport_render:
            # Viewport render
            luxcore_engine, sampler = convert_viewport_engine(
                context, scene, definitions, config
            )
        else:
            # Final render
            luxcore_engine, sampler = _convert_final_engine(
                scene, definitions, config
            )

        if (
            not config.filter_enabled
            or utils.is_pixel_filtering_forced_disabled(
                scene, denoiser_enabled
            )
            or in_material_shading_mode
        ):
            filter_type = "NONE"
        else:
            filter_type = config.filter

        if config.dls_cache.enabled:
            if is_viewport_render:
                # Avoid building DLS cache when rendering in viewport, fall back to log power
                light_strategy = "LOG_POWER"
            else:
                light_strategy = "DLS_CACHE"
        else:
            light_strategy = config.light_strategy
            if light_strategy == "AUTO":
                light_strategy = _auto_light_strategy(scene, luxcore_engine)

        # Common properties that should be set regardless of engine configuration.
        # NB: the engine's PMJ02 tag breaks the SOBOL/RANDOM convention
        # ("PMJ02SAMPLER", see Sampler::String2SamplerType).
        sampler_tag = "PMJ02SAMPLER" if sampler == "PMJ02" else sampler
        definitions.update(
            {
                "renderengine.type": luxcore_engine,
                "sampler.type": sampler_tag,
                "film.width": width,
                "film.height": height,
                "film.filter.type": filter_type,
                "film.filter.width": config.filter_width,
                "lightstrategy.type": light_strategy,
                "scene.epsilon.min": config.min_epsilon,
                "scene.epsilon.max": config.max_epsilon,
                "path.albedospecular.type": scene.luxcore.denoiser.albedo_specular_passthrough_mode,
                "path.albedospecular.glossinessthreshold": 0.05,
            }
        )

        if preferences.film_device not in {"", "none"}:
            definitions["film.opencl.enable"] = True
            definitions["film.opencl.device"] = int(preferences.film_device)
        else:
            definitions["film.opencl.enable"] = False

        if light_strategy == "DLS_CACHE":
            _convert_dlscache_settings(
                scene, definitions, config, is_viewport_render
            )

        if light_strategy == "RESTIR_DI":
            definitions["lightstrategy.restir.temporal.enable"] = (
                config.restir_temporal_enable
            )
            definitions["lightstrategy.restir.spatialreuse.enable"] = (
                config.restir_spatial_enable
            )
            definitions["lightstrategy.restir.visibility.enable"] = (
                config.restir_visibility_enable
            )
            if config.restir_candidates > 0:
                definitions["lightstrategy.restir.candidates"] = (
                    config.restir_candidates
                )

        # ReSTIR GI runs on PATHCPU and the pathoclbase GPU engines
        # (PATHOCL/TILEPATHOCL); RTPATHOCL and BIDIR* do not implement
        # the reservoir machinery, so exporting there would be a
        # silent no-op.
        if config.restir_gi_enable and luxcore_engine not in (
            "PATHCPU", "PATHOCL", "TILEPATHOCL"
        ):
            LuxCoreErrorLog.add_warning(
                f"ReSTIR GI is not supported by {luxcore_engine}, "
                "the setting is ignored"
            )
        if config.restir_gi_enable and luxcore_engine in (
            "PATHCPU", "PATHOCL", "TILEPATHOCL"
        ):
            definitions["path.restir.gi.enable"] = True
            definitions["path.restir.gi.temporal.enable"] = (
                config.restir_gi_temporal_enable
            )
            definitions["path.restir.gi.spatial.enable"] = (
                config.restir_gi_spatial_enable
            )
            if config.restir_gi_candidates > 0:
                definitions["path.restir.gi.candidates"] = (
                    config.restir_gi_candidates
                )

        if config.mnee_enable:
            definitions["path.mnee.enable"] = True
            if config.mnee_maxspecular > 1:
                definitions["path.mnee.maxspecular"] = config.mnee_maxspecular
            if config.mnee_maxiterations != 12:
                definitions["path.mnee.maxiterations"] = config.mnee_maxiterations
            if not config.mnee_seedcache:
                definitions["path.mnee.seedcache"] = False

        if config.guiding_enable and luxcore_engine in (
            "PATHCPU", "PATHOCL", "TILEPATHCPU", "TILEPATHOCL",
        ):
            definitions["path.guiding.enable"] = True
            if config.guiding_tablefile:
                definitions["path.guiding.tablefile"] = (
                    bpy.path.abspath(config.guiding_tablefile)
                )

        # Light portals (M5): quad faces of objects flagged
        # "Light Portal" become aperture rects for the portal bounce
        # proposal. The objects themselves are excluded from render
        # geometry (utils.is_obj_visible). CPU path engines only.
        if luxcore_engine in ("PATHCPU", "TILEPATHCPU", "RTPATHCPU"):
            portal_rects = _collect_light_portals(scene)
            if portal_rects:
                definitions["path.portal.count"] = len(portal_rects)
                definitions["path.portal.weight"] = config.portal_weight
                for i, rect in enumerate(portal_rects):
                    definitions[f"path.portal.{i}"] = rect

        if config.spectral_enable and luxcore_engine in (
            "PATHCPU", "PATHOCL", "TILEPATHCPU", "TILEPATHOCL",
            "RTPATHCPU", "RTPATHOCL",
        ):
            definitions["path.spectral.enable"] = True

        if config.photongi.enabled and not is_viewport_render:
            _convert_photongi_settings(context is not None, scene,
                                       definitions, config)

        # Manual clamping wins; otherwise auto-clamp applies the value
        # suggested by a previous unclamped render (see
        # utils/render.find_suggested_clamp_value) - but only while the
        # scene's light/emission content still matches the signature that
        # was stamped when the value was measured, so a stale suggestion
        # can never silently clamp a changed scene.
        use_clamping = config.path.use_clamping
        clamping_value = config.path.clamping
        if (
            not use_clamping
            and config.path.auto_clamping
            and config.path.suggested_clamping_value > 0
        ):
            from ..utils.render import compute_clamp_signature
            sig_now = compute_clamp_signature(scene)
            if sig_now and sig_now == config.path.suggested_clamping_sig:
                use_clamping = True
                clamping_value = config.path.suggested_clamping_value

        if (
            use_clamping
            and not in_material_shading_mode
            and not utils.using_photongi_debug_mode(is_viewport_render, scene)
        ):
            definitions["path.clamping.variance.maxvalue"] = clamping_value

        # Filter
        if config.filter == "GAUSSIAN":
            definitions["film.filter.gaussian.alpha"] = config.gaussian_alpha
        elif config.filter == "SINC":
            definitions["film.filter.sinc.tau"] = config.sinc_tau

        use_filesaver = utils.using_filesaver(context, scene)

        # Transparent film settings
        black_background = False
        if utils.is_valid_camera(scene.camera):
            pipeline = scene.camera.data.luxcore.imagepipeline

            if (
                pipeline.transparent_film
                or use_backgroundimage(context, scene)
            ) and not use_filesaver:
                # This avoids issues with transparent film in Blender
                black_background = True
        definitions["path.forceblackbackground.enable"] = black_background

        # FILESAVER engine (only in final render)
        if use_filesaver:
            _convert_filesaver(scene, definitions, luxcore_engine)

        # CPU thread settings (we use the properties from Blender here)
        if scene.render.threads_mode == "FIXED":
            definitions["native.threads.count"] = scene.render.threads

        _convert_seed(scene, definitions)

        # Create the properties
        config_props = utils.luxutils.create_props(prefix, definitions)

        # Convert AOVs
        aov_props = aovs.convert(exporter, scene, context, engine)
        config_props.Set(aov_props)

        if simple_token is not None:
            config.simple.restore(simple_token)
        return config_props
    except Exception as error:
        msg = "Config: %s" % error
        # Note: Exceptions in the config are critical, we can't render without a config
        LuxCoreErrorLog.add_error(msg)
        import traceback

        traceback.print_exc()
        try:
            if simple_token is not None:
                config.simple.restore(simple_token)
        except Exception:
            pass
        return pyluxcore.Properties()


def _convert_opencl_settings(scene, definitions, is_final_render):
    if scene.luxcore.debug.enabled and scene.luxcore.debug.use_opencl_cpu:
        # This is a mode for debugging OpenCL problems.
        # If the problem shows up in this mode, it is most
        # likely a bug in LuxCore and not an OpenCL compiler bug.
        definitions["opencl.cpu.use"] = True
        definitions["opencl.gpu.use"] = False
        definitions["opencl.native.threads.count"] = 0
    else:
        opencl = scene.luxcore.devices
        definitions["opencl.cpu.use"] = False
        definitions["opencl.gpu.use"] = True
        definitions["opencl.devices.select"] = (
            opencl.devices_to_selection_string()
        )

        # OpenCL CPU (hybrid render) thread settings. Only enabled in final render.
        if opencl.use_native_cpu and is_final_render:
            # We use the properties from Blender here
            if scene.render.threads_mode == "FIXED":
                # Explicitly set the number of threads
                definitions["opencl.native.threads.count"] = (
                    scene.render.threads
                )
            # If no thread count is specified, LuxCore automatically uses all available cores
        else:
            # Disable hybrid rendering
            definitions["opencl.native.threads.count"] = 0


def convert_viewport_engine(context, scene, definitions, config):
    if utils.in_material_shading_mode(context):
        definitions["path.pathdepth.total"] = 1
        definitions["path.pathdepth.diffuse"] = 1
        definitions["path.pathdepth.glossy"] = 1
        definitions["path.pathdepth.specular"] = 1

        definitions["rtpathcpu.zoomphase.size"] = 4
        definitions["rtpathcpu.zoomphase.weight"] = 0
        return "RTPATHCPU", "RTPATHCPUSAMPLER"

    viewport = scene.luxcore.viewport
    using_hybridbackforward = utils.using_hybridbackforward_in_viewport(scene)

    device = viewport.device
    if device == "OCL" and not (
        utils.luxutils.is_opencl_build() or utils.luxutils.is_cuda_build()
    ):
        msg = "Config: LuxCore was built without GPU support, can't use GPU engine in viewport"
        LuxCoreErrorLog.add_warning(msg)
        device = "CPU"

    _convert_path(
        config, definitions, using_hybridbackforward, device, True, scene
    )
    resolutionreduction = (
        viewport.resolution_reduction
        if viewport.reduce_resolution_on_edit
        else 1
    )

    if utils.using_bidir_in_viewport(scene):
        luxcore_engine = "BIDIRCPU"
        definitions["light.maxdepth"] = config.bidir_light_maxdepth
        definitions["path.maxdepth"] = config.bidir_path_maxdepth
        sampler = config.sampler
        definitions["sampler.sobol.adaptive.strength"] = 0
        definitions["sampler.random.adaptive.strength"] = 0
        _convert_metropolis_settings(definitions, config)
    elif device == "CPU":
        if using_hybridbackforward:
            luxcore_engine = "PATHCPU"
            sampler = "SOBOL"
            definitions["sampler.sobol.adaptive.strength"] = 0
        else:
            luxcore_engine = "RTPATHCPU"
            sampler = "RTPATHCPUSAMPLER"
            # Size of the blocks right after a scene edit (in pixels)
            definitions["rtpathcpu.zoomphase.size"] = resolutionreduction
            # How to blend new samples over old ones.
            # Set to 0 because otherwise bright pixels (e.g. meshlights) stay blocky for a long time.
            definitions["rtpathcpu.zoomphase.weight"] = 0
    else:
        assert device == "OCL"
        if using_hybridbackforward:
            luxcore_engine = "PATHOCL"
            sampler = "SOBOL"
            definitions["sampler.sobol.adaptive.strength"] = 0
        else:
            luxcore_engine = "RTPATHOCL"
            sampler = "TILEPATHSAMPLER"
            """
            # Render a sample every n x n pixels in the first passes.
            # For instance 4x4 then 2x2 and then always 1x1.
            definitions["rtpath.resolutionreduction.preview"] = resolutionreduction
            # Each preview step is rendered for n frames.
            definitions["rtpath.resolutionreduction.step"] = 1
            # Render a sample every n x n pixels, outside the preview phase,
            # in order to reduce the per frame rendering time.
            definitions["rtpath.resolutionreduction"] = 1
            """

            # First passes after a reset run at 1/(preview^2) of the film
            # resolution and splat into blocks (weight ~0) so a single
            # pass covers the whole frame. Camera edits reset the film at
            # every frame boundary - if the first preview pass takes
            # longer than the orbit edit rate (~16 ms/draw) the film stays
            # empty and the viewport renders black for the entire drag.
            # A coarse preview (1/64 res) is what makes the first frame
            # land within a few dozen ms even mid-orbit.
            definitions["rtpath.resolutionreduction.preview"] = (
                max(resolutionreduction, 8)
                if viewport.reduce_resolution_on_edit
                else 1
            )
            definitions["rtpath.resolutionreduction.preview.step"] = 2
            # Steady-state passes also render 1/(N^2) of the film per
            # pass; edits only apply at frame boundaries, so a pass
            # longer than ~50 ms makes every camera move wait that long
            # for its first samples. N=4 (upstream default) keeps
            # boundaries ~4x faster than 2 at identical throughput.
            definitions["rtpath.resolutionreduction"] = 4

        _convert_opencl_settings(scene, definitions, using_hybridbackforward)

    return luxcore_engine, sampler


def _convert_final_engine(scene, definitions, config):
    # AUTO resolves to the concrete backend here so every downstream
    # check (engine tag, hybrid split, OpenCL settings) sees the resolved
    # device, not the enum placeholder.
    device = config.effective_device()
    if config.engine == "PATH":
        # Specific settings for PATH and TILEPATH
        _convert_path(
            config,
            definitions,
            config.path.hybridbackforward_enable,
            device,
            False,
            scene,
        )

        if config.use_tiles:
            luxcore_engine = "TILEPATH"
            # Tile specific settings
            tile = config.tile

            definitions["tilepath.sampling.aa.size"] = (
                tile.path_sampling_aa_size
            )
            definitions["tile.size"] = tile.size
            definitions["tile.multipass.enable"] = (
                tile.multipass_enable or utils.use_two_tiled_passes(scene)
            )
            thresh = tile.multipass_convtest_threshold
            definitions["tile.multipass.convergencetest.threshold"] = thresh
            thresh_reduct = tile.multipass_convtest_threshold_reduction
            definitions[
                "tile.multipass.convergencetest.threshold.reduction"
            ] = thresh_reduct
            warmup = tile.multipass_convtest_warmup
            definitions["tile.multipass.convergencetest.warmup.count"] = warmup
        else:
            luxcore_engine = "PATH"

        # Add CPU/OCL suffix
        luxcore_engine += device

        if device == "OCL":
            # OpenCL specific settings
            _convert_opencl_settings(scene, definitions, True)
    else:
        # config.engine == BIDIR
        luxcore_engine = "BIDIRCPU"
        definitions["light.maxdepth"] = config.bidir_light_maxdepth
        definitions["path.maxdepth"] = config.bidir_path_maxdepth

    # Sampler
    if config.engine == "PATH" and config.use_tiles:
        # TILEPATH needs exactly this sampler
        sampler = "TILEPATHSAMPLER"
    else:
        sampler = config.get_sampler()

    # Sampler (SOBOL/RANDOM/PMJ02 share the stratified adaptive scheme;
    # PMJ02 has no blue-noise dithering switch)
    if sampler in {"SOBOL", "RANDOM", "PMJ02"}:
        sampler_type = sampler.lower()

        # Adaptive sampling
        adaptive_strength = config.sobol_adaptive_strength
        if adaptive_strength > 0:
            definitions["film.noiseestimation.warmup"] = (
                config.noise_estimation.warmup
            )
            definitions["film.noiseestimation.step"] = (
                config.noise_estimation.step
            )
        definitions[f"sampler.{sampler_type}.adaptive.strength"] = (
            adaptive_strength
        )

        if sampler == "SOBOL":
            definitions["sampler.sobol.bluenoise.enable"] = (
                config.sobol_bluenoise_enable
            )
            definitions["sampler.sobol.owen.enable"] = (
                config.sobol_owen_enable
            )
            definitions["sampler.sobol.owen.tile.enable"] = (
                config.sobol_owen_tile_enable
            )
            definitions["sampler.sobol.adaptive.moments.enable"] = (
                config.sobol_adaptive_moments_enable
            )
            definitions["sampler.sobol.adaptive.relerr"] = (
                config.sobol_adaptive_relerr
            )

        # Sampler pattern
        if config.using_out_of_core():
            bucketsize = 1
            tilesize = 16
            supersampling = int(config.out_of_core_supersampling)
            overlapping = SamplingOverlap.OUT_OF_CORE
        else:
            if config.sampler_pattern == "PROGRESSIVE":
                bucketsize = 16
                tilesize = 16
                supersampling = 1
                overlapping = SamplingOverlap.PROGRESSIVE
            elif config.sampler_pattern == "CACHE_FRIENDLY":
                bucketsize = 1
                tilesize = 16
                supersampling = 1
                overlapping = SamplingOverlap.CACHE_FRIENDLY
            else:
                raise Exception("Unknown sampler pattern")

        definitions[f"sampler.{sampler_type}.bucketsize"] = (
            bucketsize  # Must be power of 2
        )
        definitions[f"sampler.{sampler_type}.tilesize"] = (
            tilesize  # Must be power of 2
        )
        definitions[f"sampler.{sampler_type}.supersampling"] = supersampling
        definitions[f"sampler.{sampler_type}.overlapping"] = overlapping
    elif sampler == "METROPOLIS":
        _convert_metropolis_settings(definitions, config)

    if config.out_of_core:
        # Wether out_of_core mode is FILM or EVERYTHING, film is stored out of core
        definitions["opencl.outofcore.film.enable"] = True
    if config.using_out_of_core():
        definitions["opencl.outofcore.enable"] = True

    if config.low_vram():
        # Low-resource profile: shrink the GPU wavefront task count so
        # the per-task buffers (rays/hits, ReSTIR reservoirs, MNEE state,
        # visibility candidate rays) fit a small-VRAM GPU and leave
        # headroom for the driver and the OS compositor.
        definitions["opencl.task.count"] = config.LOW_RESOURCE_TASK_COUNT

    return luxcore_engine, sampler


def _convert_path(
    config,
    definitions,
    use_hybridbackforward,
    device,
    is_viewport_render,
    scene,
):
    path = config.path
    # Note that for non-specular paths +1 is added to the path depth in order to have behaviour
    # that feels intuitive for the user. LuxCore does only MIS on the last path bounce, but no
    # other shading, so depth 1 would be only direct light without MIS, depth 2 would be only
    # direct light with MIS, and depth 3 onwards would finally be direct + indirect light with MIS.
    definitions["path.pathdepth.total"] = path.depth_total + 1
    definitions["path.pathdepth.diffuse"] = path.depth_diffuse + 1
    definitions["path.pathdepth.glossy"] = path.depth_glossy + 1
    definitions["path.pathdepth.specular"] = path.depth_specular

    if not utils.using_photongi_debug_mode(is_viewport_render, scene):
        if device == "OCL":
            partition_raw = path.hybridbackforward_lightpartition_opencl
            # GPU light tracing (PATHOCL/RTPATHOCL): a fraction of the GPU
            # task population runs light subpaths and splats caustic-class
            # contributions into the film. The "Light Rays" percentage maps
            # directly onto the light-task fraction. The engine
            # automatically enables eye-side caustic suppression, so the
            # estimator stays unbiased without a CPU light pass.
            definitions["path.lighttracing.enable"] = use_hybridbackforward
            definitions["path.lighttracing.taskfraction"] = min(
                partition_raw / 100, 0.9
            )
            # Light-pass-only output (LIGHTCPU-style image produced by the
            # GPU light tasks; the eye pass is not run at all)
            definitions["path.lighttracing.only"] = (
                use_hybridbackforward and path.lighttracing_only
            )
            # Caustic focus cache (guided emission): learns productive
            # refraction entry points per light and steers a share of
            # emissions toward them (unbiased mixture pdf on device)
            definitions["path.lighttracing.focus.enable"] = path.lighttracing_focus
            definitions["path.lighttracing.focus.ratio"] = min(
                path.lighttracing_focus_ratio / 100, 0.9
            )
            definitions["path.lighttracing.focus.radius"] = path.lighttracing_focus_radius
        else:
            partition_raw = path.hybridbackforward_lightpartition
        # Note that our partition property is inverted compared to LuxCore's (it is the probability to
        # sample a light path, not the probability to sample a camera path)
        partition = 1 - partition_raw / 100
        definitions["path.hybridbackforward.enable"] = use_hybridbackforward
        definitions["path.hybridbackforward.partition"] = partition
        definitions["path.hybridbackforward.glossinessthreshold"] = (
            path.hybridbackforward_glossinessthresh
        )
        definitions["path.hybridbackforward.adaptivecaustic"] = (
            path.hybridbackforward_adaptivecaustic
        )
        definitions["path.hybridbackforward.terminalglossiness"] = (
            path.hybridbackforward_terminalglossiness
        )
        definitions["path.hybridbackforward.connectprob"] = (
            path.hybridbackforward_connectprob
        )


def _convert_filesaver(scene, definitions, luxcore_engine):
    config = scene.luxcore.config

    filesaver_path = config.filesaver_path
    output_path = utils.get_abspath(
        filesaver_path, must_exist=True, must_be_existing_dir=True
    )

    blend_name = utils.get_blendfile_name()
    if not blend_name:
        blend_name = "Untitled"

    dir_name = blend_name + "_LuxCore"
    frame_name = "%05d" % scene.frame_current

    # If we have multiple render layers, we append the layer name
    if len(scene.view_layers) > 1:
        # TODO 2.8
        render_layer = utils_view_layer.get_current_view_layer(scene)
        frame_name += "_" + render_layer.name

    if config.filesaver_format == "BIN":
        # For binary format, the frame number is used as file name instead of directory name
        frame_name += ".bcf"
        output_path = os.path.join(output_path, dir_name)
    else:
        # For text format, we use the frame number as name for a subfolder
        output_path = os.path.join(output_path, dir_name, frame_name)

    if not os.path.exists(output_path):
        # https://stackoverflow.com/a/273227
        try:
            os.makedirs(output_path)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

    if config.filesaver_format == "BIN":
        definitions["filesaver.filename"] = os.path.join(
            output_path, frame_name
        )
    else:
        # Text format
        definitions["filesaver.directory"] = output_path

    definitions["filesaver.format"] = config.filesaver_format
    definitions["renderengine.type"] = "FILESAVER"
    definitions["filesaver.renderengine.type"] = luxcore_engine


def _convert_seed(scene, definitions):
    config = scene.luxcore.config

    if config.use_animated_seed:
        # frame_current can be 0, but not negative, while LuxCore seed can only be > 1
        seed = scene.frame_current + 1
    else:
        seed = config.seed

    definitions["renderengine.seed"] = seed


def _convert_metropolis_settings(definitions, config):
    definitions["sampler.metropolis.largesteprate"] = (
        config.metropolis_largesteprate / 100
    )
    definitions["sampler.metropolis.maxconsecutivereject"] = (
        config.metropolis_maxconsecutivereject
    )
    definitions["sampler.metropolis.imagemutationrate"] = (
        config.metropolis_imagemutationrate / 100
    )


def _convert_dlscache_settings(scene, definitions, config, is_viewport_render):
    dls_cache = config.dls_cache
    file_path = utils.get_persistent_cache_file_path(
        dls_cache.file_path,
        dls_cache.save_or_overwrite,
        is_viewport_render,
        scene,
    )
    definitions.update(
        {
            "lightstrategy.entry.radius": (
                0 if dls_cache.entry_radius_auto else dls_cache.entry_radius
            ),
            "lightstrategy.entry.normalangle": degrees(
                dls_cache.entry_normalangle
            ),
            "lightstrategy.entry.maxpasses": dls_cache.entry_maxpasses,
            "lightstrategy.entry.convergencethreshold": dls_cache.entry_convergencethreshold
            / 100,
            "lightstrategy.entry.warmupsamples": dls_cache.entry_warmupsamples,
            "lightstrategy.entry.volumes.enable": dls_cache.entry_volumes_enable,
            "lightstrategy.lightthreshold": dls_cache.lightthreshold / 100,
            "lightstrategy.targetcachehitratio": dls_cache.targetcachehitratio
            / 100,
            "lightstrategy.maxdepth": dls_cache.maxdepth,
            "lightstrategy.maxsamplescount": dls_cache.maxsamplescount,
            "lightstrategy.persistent.file": file_path,
        }
    )


def _convert_photongi_settings(is_viewport_render, scene, definitions, config):
    photongi = config.photongi

    if photongi.indirect_lookup_radius_auto:
        indirect_radius = 0
    else:
        indirect_radius = photongi.indirect_lookup_radius

    if photongi.indirect_haltthreshold_preset == "final":
        indirect_haltthreshold = 0.05
    elif photongi.indirect_haltthreshold_preset == "preview":
        indirect_haltthreshold = 0.15
    elif photongi.indirect_haltthreshold_preset == "custom":
        indirect_haltthreshold = photongi.indirect_haltthreshold_custom / 100
    else:
        raise Exception("Unknown preset mode")

    caustic_radius = photongi.caustic_lookup_radius
    caustic_updatespp = (
        photongi.caustic_updatespp if photongi.caustic_periodic_update else 0
    )

    file_path = utils.get_persistent_cache_file_path(
        photongi.file_path, photongi.save_or_overwrite, is_viewport_render,
        scene
    )

    definitions.update(
        {
            "path.photongi.photon.maxcount": round(
                photongi.photon_maxcount * 1000000
            ),
            "path.photongi.photon.maxdepth": photongi.photon_maxdepth,
            "path.photongi.glossinessusagethreshold": photongi.glossinessusagethreshold,
            "path.photongi.indirect.enabled": photongi.indirect_enabled,
            "path.photongi.indirect.maxsize": 0,  # Set to 0 to use haltthreshold stop condition
            "path.photongi.indirect.haltthreshold": indirect_haltthreshold,
            "path.photongi.indirect.lookup.radius": indirect_radius,
            "path.photongi.indirect.lookup.normalangle": degrees(
                photongi.indirect_normalangle
            ),
            "path.photongi.indirect.usagethresholdscale": photongi.indirect_usagethresholdscale,
            "path.photongi.caustic.enabled": photongi.caustic_enabled,
            "path.photongi.caustic.maxsize": round(
                photongi.caustic_maxsize * 1000000
            ),
            "path.photongi.caustic.lookup.radius": caustic_radius,
            "path.photongi.caustic.lookup.normalangle": degrees(
                photongi.caustic_normalangle
            ),
            "path.photongi.caustic.updatespp": caustic_updatespp,
            "path.photongi.caustic.updatespp.radiusreduction": photongi.caustic_updatespp_radiusreduction
            / 100,
            "path.photongi.caustic.updatespp.minradius": photongi.caustic_updatespp_minradius,
            "path.photongi.persistent.file": file_path,
        }
    )

    if photongi.debug != "off":
        definitions["path.photongi.debug.type"] = photongi.debug
