"""
Camera-view projection regression test, run inside real Blender:

    Blender -b --python dev-tools/camera_viewplane_test.py

Compares the add-on's camera-view math against an independent, faithful
port of Blender 5.2.2's projection code:

  * BKE_camera_params_from_view3d  (camzoom/camdx/camdy/shift handling)
  * BKE_camera_params_compute_viewplane (viewfac/pixsize/sensor fit)
  * view3d_camera_border           (camera frame mapped into the region)

Validated surfaces:
  * export.camera._view_camera() screenwindow + fieldofview
  * utils.calc_camera_frame_rect() (on-screen frame used by the draw quad)
  * utils.calc_filmsize() zoom independence for camera-view border
"""

import math
import sys
from types import SimpleNamespace

import bpy

import bl_ext.user_default.blendluxcore as blendluxcore  # noqa: F401
from bl_ext.user_default.blendluxcore import utils as blc_utils
from bl_ext.user_default.blendluxcore.export import camera as blc_camera

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    if not ok:
        print("FAIL", name, detail)


# ---------------------------------------------------------------------------
# Faithful port of the Blender 5.2.2 implementation (reference model)
# ---------------------------------------------------------------------------

M_SQRT2 = math.sqrt(2.0)


def zoom_to_fac(camzoom):
    """BKE_screen_view3d_zoom_to_fac (screen.cc)."""
    return (M_SQRT2 + camzoom / 50.0) ** 2 / 4.0


def sensor_size(fit, sx, sy):
    """BKE_camera_sensor_size - RAW fit: AUTO always means sensor_x."""
    return sy if fit == "VERTICAL" else sx


def sensor_fit(fit, sizex, sizey):
    """BKE_camera_sensor_fit - resolves AUTO on the given dims."""
    if fit == "AUTO":
        return "HORIZONTAL" if sizex >= sizey else "VERTICAL"
    return fit


def compute_viewplane(winx, winy, aspx, aspy, *, is_ortho, ortho_scale,
                      lens, clip_start, sx, sy, fit_raw,
                      shiftx, shifty, offsetx, offsety, zoom):
    """BKE_camera_params_compute_viewplane (camera.cc)."""
    ycor = aspy / aspx
    if is_ortho:
        pixsize = ortho_scale
    else:
        pixsize = sensor_size(fit_raw, sx, sy) * clip_start / lens
    fit = sensor_fit(fit_raw, aspx * winx, aspy * winy)
    viewfac = winx if fit == "HORIZONTAL" else ycor * winy
    pixsize /= viewfac
    pixsize *= zoom
    xmin, xmax = -0.5 * winx, 0.5 * winx
    ymin, ymax = -0.5 * ycor * winy, 0.5 * ycor * winy
    dx = shiftx * viewfac + winx * offsetx
    dy = shifty * viewfac + winy * offsety
    return ((xmin + dx) * pixsize, (xmax + dx) * pixsize,
            (ymin + dy) * pixsize, (ymax + dy) * pixsize)


def ref_camera_viewplane(cam, rw, rh, camzoom, camdx, camdy):
    """CAMOB branch of BKE_camera_params_from_view3d + compute_viewplane
    on the region dims with unit pixel aspect."""
    zf = zoom_to_fac(camzoom)
    return compute_viewplane(
        rw, rh, 1.0, 1.0,
        is_ortho=cam.type == "ORTHO",
        ortho_scale=cam.ortho_scale,
        lens=cam.lens, clip_start=cam.clip_start,
        sx=cam.sensor_width, sy=cam.sensor_height,
        fit_raw=cam.sensor_fit,
        shiftx=cam.shift_x * zf, shifty=cam.shift_y * zf,
        offsetx=2.0 * camdx * zf, offsety=2.0 * camdy * zf,
        zoom=1.0 / zf,
    )


def ref_camera_frame_vp(scene):
    """Camera's own viewplane on render resolution + pixel aspect
    (zoom=1, offset=0 - what the film image covers)."""
    cam = scene.camera.data
    r = scene.render
    return compute_viewplane(
        r.resolution_x, r.resolution_y, r.pixel_aspect_x,
        r.pixel_aspect_y,
        is_ortho=cam.type == "ORTHO",
        ortho_scale=cam.ortho_scale,
        lens=cam.lens, clip_start=cam.clip_start,
        sx=cam.sensor_width, sy=cam.sensor_height,
        fit_raw=cam.sensor_fit,
        shiftx=cam.shift_x, shifty=cam.shift_y,
        offsetx=0.0, offsety=0.0, zoom=1.0,
    )


def ref_frame_rect(scene, rw, rh, camzoom, camdx, camdy):
    """view3d_camera_border(): (left, bottom, w, h) in region px."""
    vp = ref_camera_viewplane(scene.camera.data, rw, rh,
                              camzoom, camdx, camdy)
    cp = ref_camera_frame_vp(scene)
    size_x = vp[1] - vp[0]
    size_y = vp[3] - vp[2]
    return (
        (cp[0] - vp[0]) / size_x * rw,
        (cp[2] - vp[2]) / size_y * rh,
        (cp[1] - cp[0]) / size_x * rw,
        (cp[3] - cp[2]) / size_y * rh,
    )


def ref_screenwindow(scene, rw, rh, camzoom, camdx, camdy):
    """The expected LuxCore screenwindow for camera view (no border).

    LuxCore's perspective camera maps raster -> sw -> ray with direction
    ~ sw * tan(fov/2); matching the viewplane means normalizing by
    sensor_size * clip / (2*lens). For ORTHO the sw is world units.
    """
    cam = scene.camera.data
    vp = ref_camera_viewplane(cam, rw, rh, camzoom, camdx, camdy)
    if cam.type == "ORTHO":
        return list(vp)
    unit = sensor_size(cam.sensor_fit, cam.sensor_width,
                       cam.sensor_height) * cam.clip_start / (2 * cam.lens)
    return [v / unit for v in vp]


def ref_screenwindow_border(scene):
    """Border render: camera's own viewplane lerped by the render border
    (crop_viewplane semantics), normalized like ref_screenwindow."""
    cam = scene.camera.data
    r = scene.render
    cp = ref_camera_frame_vp(scene)
    b = [r.border_min_x, r.border_max_x, r.border_min_y, r.border_max_y]
    lerped = [
        cp[0] + (cp[1] - cp[0]) * b[0],
        cp[0] + (cp[1] - cp[0]) * b[1],
        cp[2] + (cp[3] - cp[2]) * b[2],
        cp[2] + (cp[3] - cp[2]) * b[3],
    ]
    if cam.type == "ORTHO":
        return lerped
    unit = sensor_size(cam.sensor_fit, cam.sensor_width,
                       cam.sensor_height) * cam.clip_start / (2 * cam.lens)
    return [v / unit for v in lerped]


# ---------------------------------------------------------------------------

def fake_context(rw, rh, camzoom=0.0, camdx=0.0, camdy=0.0):
    return SimpleNamespace(
        region=SimpleNamespace(width=rw, height=rh),
        region_data=SimpleNamespace(
            view_perspective="CAMERA",
            view_camera_zoom=camzoom,
            view_camera_offset=(camdx, camdy),
        ),
        space_data=SimpleNamespace(
            use_render_border=False,
            render_border_min_x=0.0, render_border_max_x=1.0,
            render_border_min_y=0.0, render_border_max_y=1.0,
            lens=50.0,
        ),
    )


def close(a, b, tol=1e-4):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main():
    scene = bpy.context.scene
    cam_obj = scene.camera
    if cam_obj is None:
        cam_obj = bpy.data.objects.new(
            "Cam", bpy.data.cameras.new("Cam"))
        scene.collection.objects.link(cam_obj)
        scene.camera = cam_obj
    cam = cam_obj.data
    cam.lens = 50.0
    cam.sensor_width = 36.0
    cam.sensor_height = 24.0

    regions = [(1600, 900), (900, 1200), (1080, 1080)]
    zooms = [-50.0, -25.0, 0.0, 25.0, 50.0]
    pans = [(0.0, 0.0), (0.4, -0.3)]
    shifts = [(0.0, 0.0), (0.4, 0.3)]
    fits = ["AUTO", "HORIZONTAL", "VERTICAL"]
    reses = [(1920, 1080), (1080, 1920), (1080, 1080)]
    aspects = [(1.0, 1.0), (2.0, 1.0)]
    cam_types = ["PERSP", "ORTHO"]

    n_sw = n_frame = n_bad = 0
    worst = 0.0
    worst_desc = ""
    for cam_type in cam_types:
        cam.type = cam_type
        for fit in fits:
            cam.sensor_fit = fit
            for resx, resy in reses:
                scene.render.resolution_x = resx
                scene.render.resolution_y = resy
                for xasp, yasp in aspects:
                    scene.render.pixel_aspect_x = xasp
                    scene.render.pixel_aspect_y = yasp
                    for shx, shy in shifts:
                        cam.shift_x, cam.shift_y = shx, shy
                        for rw, rh in regions:
                            for cz in zooms:
                                for cdx, cdy in pans:
                                    ctx = fake_context(
                                        rw, rh, cz, cdx, cdy)
                                    # -- screenwindow (no border) --
                                    scene.render.use_border = False
                                    defs = {}
                                    blc_camera._view_camera(
                                        scene, ctx, defs)
                                    got = defs["screenwindow"]
                                    exp = ref_screenwindow(
                                        scene, rw, rh, cz, cdx, cdy)
                                    ok = all(
                                        close(g, e) for g, e in
                                        zip(got, exp))
                                    n_sw += 1
                                    if not ok:
                                        n_bad += 1
                                        err = max(
                                            abs(g - e) / max(1, abs(e))
                                            for g, e in zip(got, exp))
                                        if err > worst:
                                            worst = err
                                            worst_desc = (
                                                f"{cam_type}/{fit}/"
                                                f"{resx}x{resy}/"
                                                f"asp{xasp}/sh{shx}/"
                                                f"r{rw}x{rh}/z{cz}/"
                                                f"pan{cdx} "
                                                f"got={got} exp={exp}")
                                    # -- camera frame rect --
                                    exp_rect = ref_frame_rect(
                                        scene, rw, rh, cz, cdx, cdy)
                                    got_rect = (
                                        blc_utils.calc_camera_frame_rect(
                                            scene, ctx))
                                    ok2 = all(
                                        close(g, e) for g, e in
                                        zip(got_rect, exp_rect))
                                    n_frame += 1
                                    if not ok2:
                                        n_bad += 1
                                        err2 = max(
                                            abs(g - e) / max(1, abs(e))
                                            for g, e in
                                            zip(got_rect, exp_rect))
                                        if err2 > worst:
                                            worst = err2
                                            worst_desc = (
                                                f"frame {cam_type}/{fit}/"
                                                f"{resx}x{resy}/asp{xasp}/"
                                                f"sh{shx}/r{rw}x{rh}/z{cz}/"
                                                f"pan{cdx} got={got_rect} "
                                                f"exp={exp_rect}")

    check(f"screenwindow grid ({n_sw} cases)", n_bad == 0,
          f"worst rel err {worst:.4g}: {worst_desc}")

    # Border path: film sw = camera viewplane lerped by render border.
    scene.render.use_border = True
    scene.render.border_min_x = 0.1
    scene.render.border_max_x = 0.8
    scene.render.border_min_y = 0.2
    scene.render.border_max_y = 0.7
    n_b = n_bbad = 0
    for cam_type in cam_types:
        cam.type = cam_type
        for fit in fits:
            cam.sensor_fit = fit
            for resx, resy in reses:
                scene.render.resolution_x = resx
                scene.render.resolution_y = resy
                for xasp, yasp in aspects:
                    scene.render.pixel_aspect_x = xasp
                    scene.render.pixel_aspect_y = yasp
                    cam.shift_x, cam.shift_y = 0.4, 0.3
                    for cz in zooms:
                        ctx = fake_context(1600, 900, cz, 0.4, -0.3)
                        defs = {}
                        blc_camera._view_camera(scene, ctx, defs)
                        got = defs["screenwindow"]
                        exp = ref_screenwindow_border(scene)
                        n_b += 1
                        if not all(close(g, e)
                                   for g, e in zip(got, exp)):
                            n_bbad += 1
    check(f"border screenwindow grid ({n_b} cases)", n_bbad == 0)

    # FOV: AUTO always uses sensor_width; only explicit VERTICAL uses
    # sensor_height (BKE_camera_sensor_size takes the raw fit).
    cam.type = "PERSP"
    cam.sensor_fit = "AUTO"
    scene.render.resolution_x = 1080
    scene.render.resolution_y = 1920  # portrait: AUTO resolves VERT fit
    defs = {}
    blc_camera._view_camera(
        scene, fake_context(1600, 900), defs)
    exp_fov = math.degrees(2 * math.atan(36.0 / (2 * 50.0)))
    check("FOV AUTO+portrait uses sensor_width",
          close(defs["fieldofview"], exp_fov),
          f"got {defs['fieldofview']} exp {exp_fov}")
    cam.sensor_fit = "VERTICAL"
    defs = {}
    blc_camera._view_camera(
        scene, fake_context(1600, 900), defs)
    exp_fov = math.degrees(2 * math.atan(24.0 / (2 * 50.0)))
    check("FOV VERTICAL uses sensor_height",
          close(defs["fieldofview"], exp_fov),
          f"got {defs['fieldofview']} exp {exp_fov}")

    # Border filmsize must not depend on camzoom (no session restart on
    # camera-view zoom).
    cam.sensor_fit = "AUTO"
    scene.render.use_border = True
    sizes = set()
    for cz in zooms:
        sizes.add(blc_utils.calc_filmsize(
            scene, fake_context(1600, 900, cz)))
    check("border filmsize zoom-independent", len(sizes) == 1,
          str(sizes))

    scene.render.use_border = False
    print(f"{sum(1 for _, ok in results if ok)}/{len(results)} checks passed")
    sys.exit(0 if all(ok for _, ok in results) else 1)


main()
