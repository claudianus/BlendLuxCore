"""
Detached LuxCore render runner — executed by Blender's bundled Python
as a separate process (see utils/external_render.py). Loads a binary
serialized RenderConfig, renders until the halt condition, and writes
every film output to the working directory.

    python3 external_render_runner.py <scene.bcf> <workdir>

It can also be launched through a Blender binary (there is no
bpy.app.binary_path_python in Blender 5.x):

    Blender -b --python external_render_runner.py -- <scene.bcf> <workdir>
"""

import os
import sys


def main():
    # Args after "--" when invoked via "Blender -b --python ... -- args"
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = argv[1:]
    bcf_path, workdir = argv[0], argv[1]

    import pyluxcore

    print(f"[extrender] loading {bcf_path}", flush=True)
    config = pyluxcore.RenderConfig(bcf_path)
    session = pyluxcore.RenderSession(config)

    session.Start()
    print("[extrender] session started", flush=True)

    # Halt conditions are evaluated inside Film::RunTests(), which only
    # runs when the host asks for a film update — so poll HasDone while
    # periodically calling UpdateStats (this also keeps stats fresh).
    import time
    while not session.HasDone():
        session.UpdateStats()
        time.sleep(1.0)

    session.Stop()
    print("[extrender] render done, saving outputs", flush=True)

    # Writes every film.outputs.* file relative to the working dir
    session.GetFilm().SaveOutputs()

    # Completion marker for the polling timer in Blender
    open(os.path.join(workdir, "__done__"), "w").close()
    print("[extrender] finished", flush=True)


main()
