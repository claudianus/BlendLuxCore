# SPDX-License-Identifier: Apache-2.0
#
# E2E: Cycles-node compatibility layer -> real SuperLuxCore renders.
#
# Builds a suite of scenes that use ONLY native Blender/Cycles node trees
# (no SuperLuxCore node trees, no SuperLuxCore materials), renders each with the
# SuperLuxCore engine and asserts the output is finite and non-black with a
# plausible luminance band. Where the result is physically comparable the
# same scene is also rendered with Cycles (CPU) and the mean luminance /
# normalised-luminance RMSE are compared with a loose tolerance.
#
# Renders are written as EXR and read back via bpy.data.images.load() so
# all checks run on scene-linear radiance — this gives a genuine finite
# check (Render Result pixels are not readable in background mode) and
# keeps the Cycles comparison free of view-transform bias.
#
# Expected divergences (loose tolerances, see per-scene comments):
#   * glass / sun   — SuperLuxCore PATH handles refraction+caustics differently
#                     than Cycles at low sample counts; IOR handling differs
#   * noise ramp    — blender_noise is an approx mapping; distribution mean
#                     differs from the Cycles noise
#   * bump          — bump distance/strength semantics differ
#   * world sky     — TexSky(Hosek-Wilkie) -> sky2 with an eyeballed gain
#                     factor, so absolute brightness is only approximate
#   * volume        — Principled Volume color*density is an approximation of
#                     Cycles' absorption/scattering model
#   * auto-clamping — the default auto-clamp applies the previous render's
#                     suggested variance clamp, silently crushing bright
#                     emitters (~13x on s04); disabled here for parity
#   * light path    — mapped to the SuperLuxCore rayinfo texture (HitPoint ray
#                     context filled by Scene::Intersect); only physically
#                     different integrator behaviour should remain
#
# Run:
#   /Applications/Blender.app/Contents/MacOS/Blender --background \
#       --factory-startup --python dev-tools/e23_cycles_compat_e2e_test.py
#
# Rendered images land in $E23_TEST_OUT (default /tmp). Exits 0 on PASS,
# 1 on FAIL.

import importlib
import os
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Vector

_EXT_DIR = os.path.expanduser(
    "~/Library/Application Support/Blender/5.2/extensions/user_default")
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

OUT_DIR = os.environ.get("E23_TEST_OUT", "/tmp")
RES = 128
LUMA = np.array([0.2126, 0.7152, 0.0722])

RESULTS = []
SUMMARY = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"[E23-TEST] {'ok' if ok else 'FAIL'}: {name} {detail}", flush=True)


def ensure_superluxcore():
    """Register the extension's render engine when running under
    --factory-startup (extensions are not auto-enabled there)."""
    try:
        bpy.context.scene.render.engine = "SUPERLUXCORE"
        return "already-registered"
    except TypeError:
        bpy.ops.preferences.addon_enable(module="superluxcore")
        bpy.context.scene.render.engine = "SUPERLUXCORE"
        return "enabled"


def find_addon_key():
    return next(
        a.module for a in bpy.context.preferences.addons
        if "superluxcore" in a.module.lower()
    )


def superluxcore_warnings():
    """Warning messages logged by the *engine's own* errorlog module."""
    try:
        mod = importlib.import_module(find_addon_key() + ".utils.errorlog")
        return [w.message for w in mod.SuperLuxCoreErrorLog.warnings]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# EXR readback — renders are written as float EXR so the checks run on
# scene-linear radiance (the Render Result image buffer is not readable in
# background mode, and PNG would conflate the AgX view transform with the
# actual luminance).
# ---------------------------------------------------------------------------

def read_render(path):
    """Load a rendered EXR back as an (h, w, 3) float array."""
    img = bpy.data.images.load(path)
    w, h = int(img.size[0]), int(img.size[1])
    arr = np.asarray(img.pixels[:], dtype=np.float64)
    bpy.data.images.remove(img)
    return arr.reshape(h, w, 4)[:, :, :3]


def img_stats(rgb):
    lum = rgb @ LUMA
    return {
        "mean": float(lum.mean()),
        "std": float(lum.std()),
        "max": float(lum.max()),
        "coverage": float((lum > 0.02).mean()),
        "finite": bool(np.isfinite(rgb).all()),
        "rgb": rgb.reshape(-1, 3).mean(axis=0),
        "lum": lum,
    }


def norm_rmse(stats_a, stats_b):
    """RMSE of mean-normalised luminance (scale-free shape comparison)."""
    a = stats_a["lum"] / max(stats_a["mean"], 1e-9)
    b = stats_b["lum"] / max(stats_b["mean"], 1e-9)
    return float(np.sqrt(np.mean((a - b) ** 2)))


# ---------------------------------------------------------------------------
# Scene construction helpers (native Cycles trees only)
# ---------------------------------------------------------------------------

def reset_scene():
    scene = bpy.context.scene
    scene.world = None
    scene.camera = None
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                 bpy.data.lights, bpy.data.cameras, bpy.data.worlds,
                 bpy.data.images):
        for item in list(coll):
            if item.name == "Render Result":
                continue
            try:
                coll.remove(item)
            except Exception:
                pass
    return scene


def black_world(scene):
    """A Cycles world with zero strength -> no environment light in either
    engine (SuperLuxCore returns no world light when gain == 0)."""
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.superluxcore.use_cycles_settings = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Strength"].default_value = 0.0
    return world


def new_mat(name):
    """Material with an empty native shader tree; returns (mat, nt, out)."""
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree  # Blender 5.x materials always have a node tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    return mat, nt, out


def add_floor(scene, size=14.0, color=(0.5, 0.5, 0.5, 1.0)):
    bpy.ops.mesh.primitive_plane_add(size=size)
    floor = bpy.context.active_object
    mat, nt, out = new_mat("floor")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = color
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    floor.data.materials.append(mat)
    return floor


def add_subject(scene, mat, kind="cube", location=(0, 0, 0.75), size=1.4):
    if kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(
            radius=size / 2, location=location, segments=48, ring_count=24)
    else:
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def add_camera(scene, location=(3.4, -3.4, 2.5), target=(0, 0, 0.6),
               lens=50):
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    scene.collection.objects.link(cam)
    cam.location = location
    direction = Vector(target) - Vector(location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = lens
    scene.camera = cam
    return cam


def add_light(scene, light_type, location=(0, 0, 5), target=None,
              energy=60.0, rotation=None, **attrs):
    ld = bpy.data.lights.new("lt", type=light_type)
    ld.energy = energy
    for key, value in attrs.items():
        setattr(ld, key, value)
    ld.superluxcore.use_cycles_settings = True
    obj = bpy.data.objects.new("lt", ld)
    scene.collection.objects.link(obj)
    obj.location = location
    if rotation is not None:
        obj.rotation_euler = rotation
    elif target is not None:
        direction = Vector(target) - Vector(location)
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def render(scene, engine, tag, samples, denoise=True):
    scene.render.engine = engine
    scene.render.resolution_x = RES
    scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    scene.render.film_transparent = False

    if engine == "SUPERLUXCORE":
        scene.superluxcore.config.engine = "PATH"
        scene.superluxcore.config.device = "CPU"
        halt = scene.superluxcore.halt
        halt.enable = True
        halt.use_time = False
        halt.use_samples = True
        halt.samples = samples
        halt.use_noise_thresh = False
        halt.use_light_samples = False
        # Auto-clamping (on by default) stores a suggested clamp after each
        # unclamped render and applies it to the NEXT render — with a shared
        # scene that silently crushes bright emitters (s04 measured ~13x
        # dimmer until disabled). Disable for a fair transport comparison.
        path_cfg = scene.superluxcore.config.path
        path_cfg.use_clamping = False
        path_cfg.auto_clamping = False
        path_cfg.suggested_clamping_value = -1
        scene.superluxcore.denoiser.enabled = denoise
    else:
        scene.cycles.samples = samples
        scene.cycles.device = "CPU"
        try:
            scene.cycles.use_denoising = denoise
        except Exception:
            pass
        try:
            scene.view_layers[0].cycles.use_denoising = denoise
        except Exception:
            pass

    path = os.path.join(OUT_DIR, f"e23_{tag}_{engine.lower()}.exr")
    scene.render.filepath = path
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    elapsed = time.time() - t0
    return read_render(path), elapsed


# ---------------------------------------------------------------------------
# Scene builders — each uses only native Cycles node types
# ---------------------------------------------------------------------------

def build_s01_diffuse_point(scene):
    """BsdfDiffuse cube, Cycles point light."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("diff")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.75, 0.3, 0.15, 1.0)
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_light(scene, "POINT", location=(2.5, -1.5, 4.0), energy=80)
    add_camera(scene)


def build_s02_principled_area(scene):
    """ShaderNodeBsdfPrincipled sphere, Cycles area light."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("principled")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = (0.15, 0.45, 0.8, 1.0)
    bsdf.inputs["Metallic"].default_value = 0.2
    bsdf.inputs["Roughness"].default_value = 0.45
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat, kind="sphere", size=1.6)
    add_light(scene, "AREA", location=(3, -2, 5), target=(0, 0, 0.8),
              energy=400, shape="SQUARE", size=2.0)
    add_camera(scene)


def build_s03_glass_sun(scene):
    """BsdfGlass sphere on a floor, Cycles sun. Caustics/IOR diverge."""
    black_world(scene)
    add_floor(scene, color=(0.6, 0.55, 0.45, 1.0))
    mat, nt, out = new_mat("glass")
    glass = nt.nodes.new("ShaderNodeBsdfGlass")
    glass.inputs["Color"].default_value = (0.9, 0.95, 1.0, 1.0)
    glass.inputs["Roughness"].default_value = 0.02
    glass.inputs["IOR"].default_value = 1.45
    nt.links.new(glass.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat, kind="sphere", size=1.7)
    add_light(scene, "SUN", energy=3.0, rotation=(0.45, 0.15, 0.4))
    add_camera(scene)


def build_s04_emission(scene):
    """Emission-shaded cube seen directly + lighting the floor."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("emit")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (1.0, 0.55, 0.15, 1.0)
    em.inputs["Strength"].default_value = 6.0
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    add_subject(scene, mat, size=1.3)
    add_camera(scene)


def build_s05_noise_ramp(scene):
    """TexNoise -> ValToRGB colorramp -> Diffuse, top-down fill frame."""
    black_world(scene)
    bpy.ops.mesh.primitive_plane_add(size=6)
    plane = bpy.context.active_object
    mat, nt, out = new_mat("noise")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.5
    noise.inputs["Detail"].default_value = 4.0
    noise.inputs["Roughness"].default_value = 0.7
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.15, 0.8, 1.0)
    ramp.color_ramp.elements[1].color = (0.9, 0.15, 0.1, 1.0)
    mid = ramp.color_ramp.elements.new(0.5)
    mid.color = (0.9, 0.8, 0.1, 1.0)
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], diff.inputs["Color"])
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    plane.data.materials.append(mat)
    add_light(scene, "POINT", location=(1.5, -1.0, 3.5), energy=70)
    add_camera(scene, location=(0, -0.3, 5.0), target=(0, 0, 0))


def build_s06_image_texture(scene):
    """Generated + packed image via TexImage on a floor plane."""
    black_world(scene)
    # 2x2 quadrant test image: red / green / blue / white
    size = 64
    img = bpy.data.images.new("quadtex", size, size, alpha=False)
    px = np.zeros((size, size, 4), dtype=np.float32)
    half = size // 2
    px[half:, :half] = (0.9, 0.08, 0.08, 1.0)     # bottom-left: red
    px[half:, half:] = (0.1, 0.8, 0.12, 1.0)      # bottom-right: green
    px[:half, :half] = (0.1, 0.2, 0.9, 1.0)       # top-left: blue
    px[:half, half:] = (0.85, 0.85, 0.85, 1.0)    # top-right: white
    img.pixels.foreach_set(px.ravel())
    img.file_format = "PNG"
    img.pack()  # generated -> packed file so the exporter can spill it

    bpy.ops.mesh.primitive_plane_add(size=5)
    plane = bpy.context.active_object
    mat, nt, out = new_mat("imgtex")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    nt.links.new(tex.outputs["Color"], diff.inputs["Color"])
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    plane.data.materials.append(mat)
    add_light(scene, "SUN", energy=2.5, rotation=(0.15, 0.1, 0.0))
    add_camera(scene, location=(0, -0.4, 4.6), target=(0, 0, 0))


def build_s07_bump(scene):
    """Noise -> Bump -> Diffuse Normal on a sphere; grazing point light."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("bump")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 7.0
    noise.inputs["Detail"].default_value = 3.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    bump.inputs["Distance"].default_value = 0.15
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.55, 0.5, 0.45, 1.0)
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], diff.inputs["Normal"])
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat, kind="sphere", size=1.7)
    add_light(scene, "POINT", location=(3.0, -0.5, 1.8), energy=80)
    add_camera(scene)


def build_s08_mix_transparent(scene):
    """Checkerboard -> MixShader(Transparent, Diffuse) on a cube."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("mixt")
    checker = nt.nodes.new("ShaderNodeTexChecker")
    checker.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)
    checker.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1.0)
    checker.inputs["Scale"].default_value = 5.0
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.85, 0.2, 0.15, 1.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(checker.outputs["Color"], mix.inputs["Fac"])
    nt.links.new(transp.outputs["BSDF"], mix.inputs[1])
    nt.links.new(diff.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_light(scene, "POINT", location=(2.0, -2.0, 4.0), energy=80)
    add_camera(scene)


def build_s09_world_background(scene):
    """World Background node (flat color) lights a cube — no lights."""
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.superluxcore.use_cycles_settings = True
    bg = world.node_tree.nodes.get("Background")
    bg.inputs["Color"].default_value = (0.3, 0.4, 0.55, 1.0)
    bg.inputs["Strength"].default_value = 0.9
    add_floor(scene)
    mat, nt, out = new_mat("amb")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.7, 0.65, 0.6, 1.0)
    nt.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_camera(scene)


def build_s10_world_sky(scene):
    """World Background <- TexSky (Hosek-Wilkie) -> SuperLuxCore sky2."""
    world = bpy.data.worlds.new("w")
    scene.world = world
    world.superluxcore.use_cycles_settings = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    bg.inputs["Strength"].default_value = 1.0
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "HOSEK_WILKIE"
    sky.turbidity = 3.0
    sky.sun_direction = Vector((0.25, 0.15, 0.95)).normalized()
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    add_floor(scene)
    mat, nt2, out = new_mat("skymat")
    diff = nt2.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    nt2.links.new(diff.outputs["BSDF"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_camera(scene)


def build_s11_volume_principled(scene):
    """Cube whose only output is a Principled Volume (interior fog)."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("vol")
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.inputs["Density"].default_value = 0.8
    vol.inputs["Color"].default_value = (0.75, 0.8, 0.9, 1.0)
    vol.inputs["Anisotropy"].default_value = 0.3
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    add_subject(scene, mat, location=(0, 0, 1.0), size=1.9)
    add_light(scene, "POINT", location=(1.8, -1.8, 4.0), energy=160)
    add_camera(scene)


def build_s12_lightpath(scene):
    """Light Path IsCameraRay drives a MixShader (camera: emission,
    indirect: diffuse)."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("lp")
    lp = nt.nodes.new("ShaderNodeLightPath")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.15, 0.15, 0.15, 1.0)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Color"].default_value = (0.2, 1.0, 0.25, 1.0)
    em.inputs["Strength"].default_value = 4.0
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(diff.outputs["BSDF"], mix.inputs[1])
    nt.links.new(em.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_camera(scene)


def build_s14_lightpath_mirror(scene):
    """Light Path across a mirror bounce: the subject shows the
    'camera ray' branch in the direct view and the 'secondary ray'
    branch inside the reflection."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("lpmirror")
    lp = nt.nodes.new("ShaderNodeLightPath")
    red = nt.nodes.new("ShaderNodeBsdfDiffuse")
    red.inputs["Color"].default_value = (0.9, 0.1, 0.1, 1.0)
    green = nt.nodes.new("ShaderNodeBsdfDiffuse")
    green.inputs["Color"].default_value = (0.1, 0.9, 0.2, 1.0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(green.outputs["BSDF"], mix.inputs[1])
    nt.links.new(red.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    add_subject(scene, mat)

    # Sharp mirror wall behind the subject -> the reflected view of the
    # cube uses the secondary-ray (green) branch
    bpy.ops.mesh.primitive_plane_add(
        size=8.0, location=(0, 3.2, 2.2), rotation=(1.5708, 0, 0))
    mirror = bpy.context.active_object
    mmat, mnt, mout = new_mat("mirror")
    glossy = mnt.nodes.new("ShaderNodeBsdfGlossy")
    glossy.inputs["Roughness"].default_value = 0.0
    glossy.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    mnt.links.new(glossy.outputs["BSDF"], mout.inputs["Surface"])
    mirror.data.materials.append(mmat)

    add_light(scene, "POINT", location=(1.8, -1.8, 4.0), energy=160)
    add_camera(scene)


def build_s13_shadertorgb_fallback(scene):
    """Warn-tier: ShaderToRGB (Eevee-only) feeding an Emission color."""
    black_world(scene)
    add_floor(scene)
    mat, nt, out = new_mat("s2rgb")
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (0.6, 0.4, 0.8, 1.0)
    s2rgb = nt.nodes.new("ShaderNodeShaderToRGB")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 3.0
    nt.links.new(diff.outputs["BSDF"], s2rgb.inputs["Shader"])
    nt.links.new(s2rgb.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    add_subject(scene, mat)
    add_camera(scene)


# ---------------------------------------------------------------------------
# Per-scene image assertions (evaluated on the SuperLuxCore render)
# ---------------------------------------------------------------------------

def check_noise_structure(rgb):
    flat = rgb.reshape(-1, 3)
    reddish = float((flat[:, 0] > flat[:, 2] * 1.4).mean())
    bluish = float((flat[:, 2] > flat[:, 0] * 1.4).mean())
    return [
        ("red+blue ramp regions present", reddish > 0.05 and bluish > 0.05,
         f"red={reddish:.2f} blue={bluish:.2f}"),
    ]


def check_image_quadrants(rgb):
    """Every quadrant hue must appear as some patch's dominant channel."""
    h, w = rgb.shape[:2]
    doms = set()
    for fy, fx in ((0.25, 0.25), (0.25, 0.75), (0.75, 0.25), (0.75, 0.75)):
        y0, x0 = int(h * fy) - 8, int(w * fx) - 8
        patch = rgb[y0:y0 + 16, x0:x0 + 16].reshape(-1, 3).mean(axis=0)
        spread = patch.max() - patch.min()
        doms.add("W" if spread < 0.3 * patch.mean() else
                 "RGB"[int(patch.argmax())])
    return [
        ("all 4 quadrant hues rendered", doms == {"R", "G", "B", "W"},
         f"dominants={sorted(doms)}"),
    ]


def check_high_variance(rgb):
    s = img_stats(rgb)
    return [("shading structure visible", s["std"] > 0.15 * s["mean"],
             f"std={s['std']:.3f} mean={s['mean']:.3f}")]


def check_camera_vs_mirror_colors(rgb):
    """s14: the direct view must contain red pixels (camera ray) and the
    mirror reflection green ones (secondary ray)."""
    flat = rgb.reshape(-1, 3)
    reddish = float((flat[:, 0] > 2.0 * flat[:, 1]).mean())
    greenish = float((flat[:, 1] > 2.0 * flat[:, 0]).mean())
    return [
        ("camera-ray branch (red) visible", reddish > 0.01,
         f"red={reddish:.3f}"),
        ("secondary-ray branch (green) visible in reflection",
         greenish > 0.005, f"green={greenish:.3f}"),
    ]


# ---------------------------------------------------------------------------
# Scene table
# ---------------------------------------------------------------------------
# parity: max allowed luminance factor max(lux,cyc)/min(lux,cyc);
#         "record" renders Cycles too but only logs the divergence;
#         None renders SuperLuxCore only.
SCENES = [
    dict(id="s01", name="s01_diffuse_point", build=build_s01_diffuse_point,
         parity=1.6, rmse=0.65),
    dict(id="s02", name="s02_principled_area", build=build_s02_principled_area,
         # disney approx + area-light fudge gain factor
         parity=2.5, rmse=0.8),
    dict(id="s03", name="s03_glass_sun", build=build_s03_glass_sun,
         # glass IOR/caustic handling differs; loose bound only
         parity=2.0, rmse=0.9),
    dict(id="s04", name="s04_emission", build=build_s04_emission,
         parity=2.0, rmse=0.9),
    dict(id="s05", name="s05_noise_ramp", build=build_s05_noise_ramp,
         # blender_noise is an approx mapping: distribution mean differs
         parity=2.5, rmse=0.9, verify=check_noise_structure),
    dict(id="s06", name="s06_image_texture", build=build_s06_image_texture,
         parity=1.8, rmse=0.7, verify=check_image_quadrants),
    dict(id="s07", name="s07_bump_noise", build=build_s07_bump,
         # bump distance/strength semantics differ -> loose bound
         parity=1.8, rmse=0.8, verify=check_high_variance),
    dict(id="s08", name="s08_mix_transparent", build=build_s08_mix_transparent,
         parity=2.0, rmse=0.9, verify=check_high_variance),
    dict(id="s09", name="s09_world_background", build=build_s09_world_background,
         parity=1.6, rmse=0.6),
    dict(id="s10", name="s10_world_sky", build=build_s10_world_sky,
         # sky2 gain uses an eyeballed factor -> keep tolerance loose
         parity=1.8, rmse=0.8),
    dict(id="s11", name="s11_volume_principled", build=build_s11_volume_principled,
         # color*density -> sigma_s approximation; stochastic
         parity=1.8, rmse=0.9, samples=64),
    dict(id="s12", name="s12_lightpath", build=build_s12_lightpath,
         # real rayinfo mapping: emission fires on camera rays only
         parity=1.8, rmse=0.8),
    dict(id="s13", name="s13_shadertorgb_fallback",
         build=build_s13_shadertorgb_fallback, parity=None,
         warn="ShaderNodeShaderToRGB"),
    dict(id="s14", name="s14_lightpath_mirror", build=build_s14_lightpath_mirror,
         # glossy2@0 roughness is a delta mirror; reflected cube must be green
         parity=2.0, rmse=0.9, verify=check_camera_vs_mirror_colors),
]


def run_scene(spec):
    name = spec["name"]
    scene = reset_scene()
    try:
        spec["build"](scene)
    except Exception:
        check(f"{name} scene build", False, traceback.format_exc(limit=3))
        return

    samples = spec.get("samples", 32)
    denoise = spec.get("denoise", True)

    # --- SuperLuxCore render -------------------------------------------------
    try:
        rgb, dt = render(scene, "SUPERLUXCORE", name, samples, denoise)
    except Exception:
        check(f"{name} SUPERLUXCORE render", False, traceback.format_exc(limit=3))
        return

    st = img_stats(rgb)
    check(f"{name} finite output", st["finite"])
    check(f"{name} non-black", st["mean"] > 0.008 and st["coverage"] > 0.2,
          f"mean={st['mean']:.3f} cov={st['coverage']:.2f}")
    check(f"{name} plausible luminance", st["mean"] < 300,
          f"mean={st['mean']:.3f} max={st['max']:.1f}")

    warns = superluxcore_warnings()
    if spec.get("warn"):
        needle = spec["warn"]
        hit = any(needle in m for m in warns)
        check(f"{name} fallback warning logged", hit,
              f"warnings={len(warns)}"
              + ("" if hit else f" missing '{needle}'"))

    if spec.get("verify"):
        for label, ok, detail in spec["verify"](rgb):
            check(f"{name} {label}", ok, detail)

    lux_stats = st
    cyc_stats = None

    # --- Cycles parity render -------------------------------------------
    if spec.get("parity") is not None:
        try:
            rgb2, dt2 = render(scene, "CYCLES", name, samples, denoise)
            cyc_stats = img_stats(rgb2)
        except Exception:
            check(f"{name} CYCLES render", False,
                  traceback.format_exc(limit=3))
            cyc_stats = None

    factor = float("nan")
    nrmse = float("nan")
    if cyc_stats is not None:
        lo = max(0.0, min(lux_stats["mean"], cyc_stats["mean"]))
        hi = max(lux_stats["mean"], cyc_stats["mean"])
        factor = hi / max(lo, 1e-9)
        nrmse = norm_rmse(lux_stats, cyc_stats)
        check(f"{name} CYCLES non-black",
              cyc_stats["mean"] > 0.008 and cyc_stats["coverage"] > 0.2,
              f"mean={cyc_stats['mean']:.3f}")
        if spec["parity"] != "record":
            check(f"{name} luminance parity",
                  factor <= spec["parity"],
                  f"lux={lux_stats['mean']:.3f} cyc={cyc_stats['mean']:.3f} "
                  f"factor={factor:.2f} tol={spec['parity']}")
            check(f"{name} structure rmse",
                  nrmse <= spec["rmse"],
                  f"nrmse={nrmse:.2f} tol={spec['rmse']}")

    SUMMARY.append((name, lux_stats["mean"],
                    cyc_stats["mean"] if cyc_stats else float("nan"),
                    factor, nrmse, dt))


def check_stale_clamp():
    """Regression for the auto-clamp staleness bug: a suggested clamp value
    measured on one scene must NOT be applied to a differently-lit scene.

    The fix stamps the suggestion with a lighting-content signature
    (suggested_clamping_sig); export only applies auto-clamp while the
    signature still matches. Here we render a bright emission scene once
    (produces the suggestion), boost the emitter 10x, and render again -
    if the stale clamp leaked, the second render would be crushed to the
    old clamp value instead of tracking the brighter emitter.
    """
    scene = reset_scene()
    build_s04_emission(scene)

    scene.render.engine = "SUPERLUXCORE"
    scene.render.resolution_x = scene.render.resolution_y = RES
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    halt = scene.superluxcore.halt
    halt.enable = True
    halt.use_time = False
    halt.use_samples = True
    halt.samples = 16
    halt.use_noise_thresh = False
    path_cfg = scene.superluxcore.config.path
    path_cfg.use_clamping = False
    path_cfg.auto_clamping = True
    path_cfg.suggested_clamping_value = -1
    path_cfg.suggested_clamping_sig = ""

    scene.render.filepath = os.path.join(OUT_DIR, "e23_clamp_a.exr")
    bpy.ops.render.render(write_still=True)
    rgb_a = read_render(scene.render.filepath)
    st_a = img_stats(rgb_a)
    check("clamp_a suggestion recorded",
          path_cfg.suggested_clamping_value > 0
          and len(path_cfg.suggested_clamping_sig) > 0,
          f"val={path_cfg.suggested_clamping_value:.3g} "
          f"sig={path_cfg.suggested_clamping_sig[:8]}")

    # Drastically brighten the emitter: the signature must now mismatch so
    # the stale clamp is skipped and output tracks the new brightness.
    for ob in scene.objects:
        if ob.type != 'MESH' or not ob.material_slots:
            continue
        mat = ob.material_slots[0].material
        if not (mat and mat.node_tree):
            continue
        for n in mat.node_tree.nodes:
            if n.type == 'EMISSION':
                n.inputs["Strength"].default_value = 60.0

    scene.render.filepath = os.path.join(OUT_DIR, "e23_clamp_b.exr")
    bpy.ops.render.render(write_still=True)
    rgb_b = read_render(scene.render.filepath)
    st_b = img_stats(rgb_b)

    ratio = st_b["mean"] / max(st_a["mean"], 1e-9)
    check("clamp_b tracks brighter emitter (no stale clamp)",
          ratio > 2.0,
          f"mean_a={st_a['mean']:.3f} mean_b={st_b['mean']:.3f} "
          f"ratio={ratio:.2f}")


def main():
    t_start = time.time()
    print("[E23-TEST] addon:", ensure_superluxcore())

    for spec in SCENES:
        run_scene(spec)

    check_stale_clamp()

    print()
    print(f"{'scene':<28} {'lux_mean':>9} {'cyc_mean':>9} "
          f"{'factor':>7} {'nRMSE':>6} {'lux_s':>6}")
    for name, lm, cm, fac, nrmse, dt in SUMMARY:
        cm_s = f"{cm:9.3f}" if cm == cm else "      n/a"
        fac_s = f"{fac:7.2f}" if fac == fac else "    n/a"
        nr_s = f"{nrmse:6.2f}" if nrmse == nrmse else "   n/a"
        print(f"{name:<28} {lm:9.3f} {cm_s} {fac_s} {nr_s} {dt:6.1f}")
    print(f"\n[E23-TEST] total runtime {time.time() - t_start:.1f}s")

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[E23-TEST] {len(RESULTS) - len(fails)}/{len(RESULTS)} "
          f"checks passed")
    if fails:
        print(f"[E23-TEST] FAILURES: {fails}")
        sys.exit(1)


main()
