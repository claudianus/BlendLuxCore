"""
External-process rendering.

The exported SuperLuxCore scene is serialized to a binary .bcf and rendered
by a detached Python process. Blender's render() call returns right
after the spawn, so the dependency graph and all evaluated scene
memory are released while the heavy render runs outside — the only way
to actually get Blender-side scene memory back during a render.

When the detached process finishes, a timer copies the beauty output
to the render output path and shows it in an image editor.
"""

import glob
import os
import re
import subprocess
import sys
import tempfile

import bpy
import pysuperluxcore

RUNNER = os.path.join(os.path.dirname(__file__), "external_render_runner.py")
DONE_MARK = "__done__"


class _Job:
    """One in-flight external render."""

    def __init__(self, proc, workdir, log_path, target_path, frame):
        self.proc = proc
        self.workdir = workdir
        self.log_path = log_path
        self.target_path = target_path
        self.frame = frame


_jobs = []


def run(config_props, superluxcore_scene, scene):
    """Serialize the render config, spawn the detached renderer and
    return immediately. Called on the main thread during render()."""
    workdir = tempfile.mkdtemp(prefix="blc_extrender_")
    bcf_path = os.path.join(workdir, "scene.bcf")

    # The detached process enumerates devices itself; a selection string
    # sized for Blender's enumeration would not match (and crash on load)
    if "opencl.devices.select" in config_props.GetAllNames():
        config_props.Delete("opencl.devices.select")

    renderconfig = pysuperluxcore.RenderConfig(config_props, superluxcore_scene)
    renderconfig.Save(bcf_path)
    # Release the in-process scene + parsed config before spawning:
    # the external process rebuilds everything from the .bcf.
    del renderconfig
    del superluxcore_scene

    site_packages = os.path.dirname(os.path.dirname(pysuperluxcore.__file__))
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [site_packages, env.get("PYTHONPATH", "")]
    )

    # Vulkan is opt-in engine-side (never auto-selected by an empty
    # opencl.devices.select), so the child must re-derive the selection
    # string from its own enumeration — pass the wanted backend through.
    from . import get_addon_preferences  # utils/__init__.py
    gpu_backend = get_addon_preferences(bpy.context).gpu_backend
    if gpu_backend == "VULKAN":
        env["BLC_GPU_BACKEND"] = gpu_backend

    log_path = os.path.join(workdir, "render.log")
    log = open(log_path, "w")
    # Blender 5.x has no bpy.app.binary_path_python but sys.executable
    # already points at the bundled interpreter
    proc = subprocess.Popen(
        [sys.executable, RUNNER, bcf_path, workdir],
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=workdir,
        env=env,
    )

    print(f"[BLC] External render started (PID {proc.pid})")
    print(f"[BLC]   scene: {bcf_path}")
    print(f"[BLC]   log:   {log_path}")
    print(
        "[BLC] Blender-side scene memory is released now; the render "
        "cannot be cancelled from Blender — kill the process to abort."
    )

    target_path = bpy.path.abspath(scene.render.filepath)
    base = os.path.basename(target_path)
    if scene.render.use_file_extension and "." not in base:
        ext = scene.render.image_settings.file_format.lower()
        if ext == "open_exr":
            ext = "exr"
        target_path += "." + ext

    _jobs.append(
        _Job(proc, workdir, log_path, target_path, scene.frame_current)
    )
    bpy.app.timers.register(_poll, first_interval=2.0, persistent=True)


def _find_beauty(workdir):
    """The beauty output is the *IMAGEPIPELINE file with the highest
    pipeline index (the denoiser pipeline is appended last)."""
    candidates = glob.glob(os.path.join(workdir, "*IMAGEPIPELINE*.png"))
    if not candidates:
        return None

    def key(path):
        m = re.search(r"_(\d+)\.png$", path)
        # Prefer RGBA over RGB at the same pipeline index
        return (int(m.group(1)) if m else -1, "RGBA" in path)

    return max(candidates, key=key)


def _resolve_target(job):
    path = job.target_path
    if "#" in path:
        path = re.sub(
            r"#+", lambda m: str(job.frame).zfill(len(m.group())), path
        )
    return path


def _poll():
    for job in list(_jobs):
        done = os.path.exists(os.path.join(job.workdir, DONE_MARK))
        running = job.proc.poll() is None
        if running and not done:
            continue
        _jobs.remove(job)
        _finish(job)
    # Re-register while jobs are pending; the timer stops otherwise
    return 2.0 if _jobs else None


def _finish(job):
    # The done marker is written right before exit; wait briefly for the
    # process to actually terminate so the return code is available
    if job.proc.poll() is None:
        try:
            job.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    log_tail = ""
    try:
        with open(job.log_path) as f:
            log_tail = "".join(f.readlines()[-15:])
    except OSError:
        pass

    if job.proc.returncode != 0:
        print(
            f"[BLC] External render FAILED (exit {job.proc.returncode})"
            f" — log: {job.log_path}\n{log_tail}"
        )
        return

    beauty = _find_beauty(job.workdir)
    if beauty is None:
        print(f"[BLC] External render produced no image: {job.workdir}")
        return

    target = _resolve_target(job)
    if target:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        import shutil

        shutil.copyfile(beauty, target)
        print(f"[BLC] External render saved: {target}")

    try:
        image = bpy.data.images.load(target or beauty, check_existing=True)
        # Show it in an image editor if one is visible
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    area.spaces.active.image = image
                    return
    except Exception:
        pass  # background mode or no visible editor
