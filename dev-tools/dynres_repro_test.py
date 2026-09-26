"""Minimal repro: does RTPATHOCL keep accumulating samples after
SetRuntimeResolutionReduction(32) with NO scene edits?

    Blender -b --python dev-tools/dynres_repro_test.py
"""

import sys
import time

import bpy
import pysuperluxcore

import bl_ext.user_default.superluxcore as superluxcore  # noqa: F401
from bl_ext.user_default.superluxcore import export as blc_export


def main():
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()

    exporter = blc_export.Exporter()
    result = exporter.export_scene(depsgraph, None)
    superluxcore_scene, config_props = result

    config_props.Set(pysuperluxcore.Property("renderengine.type", ["RTPATHOCL"]))
    config_props.Set(pysuperluxcore.Property("sampler.type", ["TILEPATHSAMPLER"]))
    config_props.Set(
        pysuperluxcore.Property("rtpath.resolutionreduction.preview", [8]))
    config_props.Set(
        pysuperluxcore.Property("rtpath.resolutionreduction.preview.step", [2]))
    config_props.Set(
        pysuperluxcore.Property("rtpath.resolutionreduction", [4]))

    renderconfig = pysuperluxcore.RenderConfig(config_props, superluxcore_scene)
    session = pysuperluxcore.RenderSession(renderconfig)
    session.Start()

    def sc():
        return session.GetFilm().GetStats().Get(
            "stats.film.total.samplecount").GetInt()

    for i in range(20):
        time.sleep(0.1)
        print(f"pre-override  t+{i * 100:4}ms  samples={sc()}")

    session.SetRuntimeResolutionReduction(32)
    print(">>> override 32 applied")

    zeros = 0
    for i in range(50):
        time.sleep(0.1)
        s = sc()
        if s == 0:
            zeros += 1
        print(f"post-override t+{i * 100:4}ms  samples={s}")

    print("zero-reads:", zeros)

    # Visual sanity: save the film output while the override is active -
    # sparse coverage must still produce a coherent (noisy but correct)
    # image, not garbage.
    film = session.GetFilm()
    w = film.GetWidth()
    h = film.GetHeight()
    from array import array
    buf = array("f", bytes(
        film.GetOutputSize(pysuperluxcore.FilmOutputType.RGB_IMAGEPIPELINE) * 4))
    film.GetOutputFloat(pysuperluxcore.FilmOutputType.RGB_IMAGEPIPELINE, buf)
    rgba = array("f", [0.0] * (w * h * 4))
    rgba[0::4] = buf[0::3]
    rgba[1::4] = buf[1::3]
    rgba[2::4] = buf[2::3]
    rgba[3::4] = array("f", [1.0] * (w * h))
    img = bpy.data.images.new("dynres", width=w, height=h)
    img.pixels[:] = rgba
    img.filepath_raw = "/tmp/dynres_override.png"
    img.file_format = "PNG"
    img.save()
    print("saved /tmp/dynres_override.png")

    session.Stop()
    print("DONE")
    sys.exit(0)


main()
