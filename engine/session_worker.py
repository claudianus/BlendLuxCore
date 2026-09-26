"""
Dedicated session worker thread for the viewport.

All mutating calls on the live pysuperluxcore session - Start/Stop/Parse,
BeginSceneEdit/EndSceneEdit, config restarts and kernel pre-compiles -
run here instead of on Blender's UI thread. The main thread submits
jobs; the worker executes them serially, so a slow Stop + RenderConfig +
RenderSession + Start cycle (or a 15-minute kernel compile inside it)
can no longer freeze Blender.

Job coalescing: rapid-fire edits (e.g. dragging a color picker or
resizing the viewport) produce many config/edit jobs; pending jobs of
the same kind merge so only the newest state is ever applied - the
render never plays catch-up with stale settings.

``session_lock`` is shared with the engine: the worker holds it around
mutating calls on the live session, and the framebuffer/denoiser hold it
around film reads, so a film reset in EndSceneEdit can never tear a
GetOutputFloat in half. The lock is never held across a session
construction, so readers never wait on compiles.

This module only imports pysuperluxcore (never bpy) so it stays unit-testable
and can never accidentally touch Blender state off the main thread.
"""

import threading
import time
import traceback
import weakref
from collections import deque

import pysuperluxcore


def _uses_vulkan(config_props):
    """True when opencl.devices.select picks a VULKAN_GPU device.

    Position i of the selection string addresses opencl.device.<i> of
    pysuperluxcore.GetOpenCLDeviceDescs() (same order the render engine sees:
    OpenCL, CUDA, Metal, Vulkan).
    """
    try:
        select = config_props.Get("opencl.devices.select").GetString()
        if not select:
            return False
        descs = pysuperluxcore.GetOpenCLDeviceDescs()
        for i, flag in enumerate(select):
            if flag == "1" and descs.Get(
                    "opencl.device." + str(i) + ".type"
            ).GetString() == "VULKAN_GPU":
                return True
    except Exception:
        pass
    return False


def precompile_kernels(config_props, renderconfig, progress_cb=None):
    """Pre-compile GPU kernels so RenderSession.Start() doesn't stall.

    Mirrors the pre-fill create_session used to do inline: RTPATHOCL and
    PATHOCL kernels are compiled up-front for OCL engines when the kernel
    cache is cold. Returns True when a fill ran.

    Pure pysuperluxcore - safe on any thread. ``progress_cb`` receives
    ``(index, count)`` per compiled kernel (0-based index).
    """
    renderengine_type = config_props.Get("renderengine.type").GetString()
    if not (
        renderengine_type.endswith("OCL")
        and not renderconfig.HasCachedKernels()
    ):
        return False
    # Copy config props so we can pass scene.epsilon.min,
    # scene.epsilon.max and opencl.devices.select to the kernel filler.
    props = pysuperluxcore.Properties(config_props)
    engines = ["PATHOCL", "RTPATHOCL"]
    if renderengine_type == "TILEPATHOCL":
        # Only pre-compile for tiled path if requested, since it's rarely
        # used
        engines.append("TILEPATHOCL")
    if _uses_vulkan(config_props):
        # A Vulkan cold compile is tens of minutes per engine variant
        # (clspv + MoltenVK pipeline specialization) - fill only the
        # engine the session will actually start instead of warming both.
        engines = [renderengine_type]
    props.Set(
        pysuperluxcore.Property("kernelcachefill.renderengine.types", engines)
    )
    if progress_cb is None:
        pysuperluxcore.KernelCacheFill(props)
    else:
        pysuperluxcore.KernelCacheFill(props, progress_cb)
    return True


class SessionWorker:
    """Single worker thread owning the live pysuperluxcore session."""

    def __init__(self, engine=None):
        self._cond = threading.Condition()
        self._queue = deque()
        self._shutdown = False

        # Live session, owned by the worker. Mirrored to engine.session
        # on every publish so main-thread readers (stats, film fetch)
        # always see the current object.
        self.session = None
        # Shared with the engine; guards mutating calls on the live
        # session against concurrent film reads. Never held across a
        # RenderConfig/RenderSession construction or a kernel compile.
        self.session_lock = getattr(
            engine, "session_lock", None
        ) or threading.Lock()

        # Status surfaced in the viewport stats line.
        self.phase = ""
        self.progress = None  # (index, count) while compiling kernels

        # Weak reference: engine -> worker -> engine would otherwise be a
        # reference cycle, keeping the engine (and its live session) alive
        # until cyclic GC runs after Blender drops the RenderEngine.
        try:
            self._engine = weakref.ref(engine) if engine is not None else None
        except TypeError:
            self._engine = lambda: engine
        self._error = None
        # Create/start jobs queued or executing: while >0 and no session
        # is live, the viewport shows "starting" instead of polling.
        self._starting = 0
        # A start/config/edit job is executing: edits that arrive while
        # the session object is momentarily gone requeue instead of
        # being dropped.
        self._lifecycle_busy = False
        # Latest config submitted while no session was live; folded into
        # the next start job so a config edit during startup never starts
        # a session with stale settings.
        self._pending_config = None
        # Generation counter for start jobs: a superseded start (a newer
        # export landed while it was queued) skips its build.
        self._start_seq = 0
        # Incremented after every applied mutation (edit/config/parse/
        # start): film reads record it at kickoff so the framebuffer can
        # tell post-edit pixels from pre-edit ones (hold-last-frame).
        self.mutation_seq = 0

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="SuperLuxCoreSessionWorker"
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Main-thread API (job submission)
    # ------------------------------------------------------------------

    def submit_start(self, superluxcore_scene, config_props):
        """Build RenderConfig + RenderSession + Start off-thread."""
        with self._cond:
            self._start_seq += 1
            self._starting += 1
            # The export that produced this job already reflects any
            # config stashed while session-less: don't let it override.
            self._pending_config = None
            self._queue.append(
                ["start", (superluxcore_scene, config_props, self._start_seq)]
            )
            self._cond.notify()

    def submit_config(self, config_props):
        """Restart the session on the reused scene with new config props.
        Coalesces: only the newest pending config is applied."""
        with self._cond:
            if self.session is None:
                # No live session: fold into the pending start (or stash
                # for when one arrives - e.g. mid-fatal-restart).
                self._pending_config = config_props
                return
            for job in self._queue:
                if job[0] == "config":
                    job[1] = config_props
                    break
            else:
                self._queue.append(["config", config_props])
            self._cond.notify()

    def submit_edit(self, ops):
        """Replay recorded scene ops inside one Begin/EndSceneEdit."""
        if not ops:
            return
        with self._cond:
            if self._queue and self._queue[-1][0] == "edit":
                self._queue[-1][1].extend(ops)
            else:
                self._queue.append(["edit", list(ops)])
            self._cond.notify()

    def submit_parse(self, props):
        """session.Parse (imagepipeline/halt props). Merges with a pending
        parse so a burst of edits costs one call."""
        with self._cond:
            if self._queue and self._queue[-1][0] == "parse":
                self._queue[-1][1].Set(props)
            else:
                self._queue.append(["parse", props])
            self._cond.notify()

    def submit_resolution_reduction(self, value):
        """Runtime RTPATHOCL/RTPATHCPU resolution-reduction override.

        ``value`` 0 restores the configured reduction. Coalesces like a
        config job: only the newest pending value matters. Applies at
        the next frame boundary without a film reset, so it is safe to
        flip per interaction burst (drag = sparse fast passes, settle =
        full-quality passes).
        """
        with self._cond:
            for job in self._queue:
                if job[0] == "res_reduction":
                    job[1] = value
                    break
            else:
                self._queue.append(["res_reduction", value])
            self._cond.notify()

    def submit_stop(self):
        """Stop the live session and drop queued jobs (they were computed
        against a scene the restart is about to discard or the engine is
        being torn down; the follow-up full export re-covers them)."""
        with self._cond:
            for job in self._queue:
                if job[0] == "start":
                    self._starting -= 1
            self._queue.clear()
            self._pending_config = None
            self._queue.append(("stop", None))
            self._cond.notify()

    def shutdown(self):
        """Worker teardown (engine deleted): stop the session and exit."""
        with self._cond:
            self._shutdown = True
            self._cond.notify()

    # ------------------------------------------------------------------
    # Main-thread polling
    # ------------------------------------------------------------------

    @property
    def is_starting(self):
        """True while a create/start job is queued or executing and no
        live session exists yet."""
        return self.session is None and self._starting > 0

    def pop_error(self):
        """Return and clear the last failed job as (kind, error)."""
        err, self._error = self._error, None
        return err

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _get_engine(self):
        return self._engine() if self._engine is not None else None

    def _publish(self, session):
        """Make ``session`` the engine-visible current session."""
        self.session = session
        engine = self._get_engine()
        if engine is not None:
            try:
                engine.session = session
                engine.viewport_start_time = time.time()
                engine.tag_redraw()
            except ReferenceError:
                # RenderEngine struct deleted underneath us
                pass

    def _run(self):
        while True:
            with self._cond:
                while not self._queue and not self._shutdown:
                    self._cond.wait()
                if self._shutdown:
                    break
                job = self._queue.popleft()
            try:
                self._execute(job)
            except Exception as error:
                traceback.print_exc()
                self._error = (job[0], error)
                self.phase = ""
                self.progress = None
                if job[0] in ("start", "config", "edit", "parse"):
                    # The session is unusable (stopped or half-edited):
                    # drop it so the next view_update re-exports fresh.
                    self._stop_session()
                    self._publish(None)
        # Shutdown: stop whatever session is still live.
        self._stop_session()

    def _execute(self, job):
        kind, payload = job
        if kind == "start":
            self._do_start(*payload)
        elif kind == "config":
            self._do_config(payload)
        elif kind == "edit":
            self._do_edit(payload)
        elif kind == "parse":
            self._do_parse(payload)
        elif kind == "res_reduction":
            self._do_res_reduction(payload)
        elif kind == "stop":
            self._do_stop()

    def _stop_session(self):
        session, self.session = self.session, None
        if session is not None:
            try:
                with self.session_lock:
                    session.Stop()
            except Exception:
                traceback.print_exc()

    def _do_stop(self):
        self._stop_session()
        self._publish(None)

    def _do_start(self, superluxcore_scene, config_props, seq):
        try:
            if seq != self._start_seq:
                # A newer export landed while this job sat in the queue.
                return
            self._lifecycle_busy = True
            # A config change that queued behind this start wins over
            # the props captured during export.
            with self._cond:
                if self._pending_config is not None:
                    config_props = self._pending_config
                    self._pending_config = None

            # A previous session must not keep rendering alongside the
            # new one (a superseded start leaves a live session behind).
            self._stop_session()
            self.phase = "Creating render config"
            renderconfig = pysuperluxcore.RenderConfig(config_props, superluxcore_scene)

            def progress_cb(index, count):
                self.progress = (index + 1, count)
                self.phase = f"Compiling kernels ({index + 1}/{count})"

            precompile_kernels(config_props, renderconfig, progress_cb)
            self.progress = None

            self.phase = "Starting session"
            session = pysuperluxcore.RenderSession(renderconfig)
            session.Start()
            self.phase = ""
            self.mutation_seq += 1
            self._publish(session)
        finally:
            self._lifecycle_busy = False
            with self._cond:
                self._starting -= 1

    def _do_config(self, config_props):
        """https://github.com/LuxCoreRender/BlendLuxCore/issues/577

        The old code mutated the RenderConfig of the running session,
        which leaked (every stopped session kept its scene copy) and
        crashed on some SuperLuxCore versions. We build a fresh RenderConfig
        on the *same* SuperLuxCore scene instead - meshes, materials and
        lights stay defined, so no Blender re-export is needed - then
        swap in a fresh session. Everything runs on the worker so the
        Stop + rebuild + Start cycle never touches the UI thread.
        """
        old = self.session
        if old is None:
            # Session died between submit and execution: stash the props
            # so the next start uses them.
            with self._cond:
                self._pending_config = config_props
            return

        self._lifecycle_busy = True
        try:
            self.phase = "Reconfiguring session"
            # GetScene() returns a smart_holder wrapper that co-owns the
            # native scene, so it stays valid after `old` is dropped
            # (verified: RenderConfig(props, scene) is a non-owning
            # ctor - the scene must not die with the old session).
            scene = old.GetRenderConfig().GetScene()
            with self.session_lock:
                old.Stop()
            # engine.session still points at the old (stopped) session
            # until the replacement publishes: readers keep seeing its
            # last film instead of a black frame.
            self.session = None

            renderconfig = pysuperluxcore.RenderConfig(config_props, scene)

            def progress_cb(index, count):
                self.progress = (index + 1, count)
                self.phase = f"Compiling kernels ({index + 1}/{count})"

            precompile_kernels(config_props, renderconfig, progress_cb)
            self.progress = None

            self.phase = "Starting session"
            session = pysuperluxcore.RenderSession(renderconfig)
            session.Start()
            self.phase = ""
            self.mutation_seq += 1
            self._publish(session)
        finally:
            self._lifecycle_busy = False

    def _do_edit(self, ops):
        session = self.session
        if session is None:
            # A start/config is in flight and momentarily owns no
            # session: requeue behind it so the edit lands on the new
            # session (the scene is shared, order is preserved).
            with self._cond:
                if self._lifecycle_busy or any(
                    j[0] in ("start", "config") for j in self._queue
                ):
                    self._queue.append(["edit", ops])
                # else: truly session-less (startup failed); the
                # follow-up full export rebuilds the state anyway.
            return
        self.phase = "Applying scene edits"
        scene = session.GetRenderConfig().GetScene()
        with self.session_lock:
            session.BeginSceneEdit()
            try:
                _replay_ops(scene, ops)
            except Exception:
                # Partial replay leaves the scene in an unknown state;
                # bail out to a full rebuild rather than render garbage.
                try:
                    session.EndSceneEdit()
                except Exception:
                    pass
                raise
            session.EndSceneEdit()
            if session.IsInPause():
                session.Resume()
            # Bump inside the lock so a film read can never observe the
            # post-edit film while still carrying a pre-edit mut_seq.
            self.mutation_seq += 1
        # The edit restarted rendering: re-anchor the viewport halt timer
        # at the actual resume point. Without this a queued edit that runs
        # after halt_time elapsed gets re-paused instantly by view_draw
        # (resume -> pause loop = black/frozen viewport).
        engine = self._get_engine()
        if engine is not None:
            try:
                engine.viewport_start_time = time.time()
            except ReferenceError:
                pass
        self.phase = ""

    def _do_res_reduction(self, value):
        session = self.session
        if session is None:
            return
        # Atomic store into the engine - no film reset, no mutation_seq
        # bump (coverage pattern changes, accumulated samples stay valid).
        # getattr: tolerate a stale pysuperluxcore lacking the binding.
        set_reduction = getattr(
            session, "SetRuntimeResolutionReduction", None
        )
        if set_reduction is not None:
            with self.session_lock:
                set_reduction(value)

    def _do_parse(self, props):
        session = self.session
        if session is None:
            return
        with self.session_lock:
            session.Parse(props)
            self.mutation_seq += 1


def _replay_ops(scene, ops):
    """Apply RecordedScene ops on the real scene. Free function so tests
    can drive it without a worker."""
    for name, args, kwargs in ops:
        getattr(scene, name)(*args, **kwargs)
