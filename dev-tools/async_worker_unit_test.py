"""
Unit tests for the async session worker architecture.

Loads `export/recorded_scene.py` and `engine/session_worker.py` directly by
file path (bypassing the package __init__, which needs bpy), with a fake
`pyluxcore` module injected into sys.modules. Runs under plain python3:

    python3 dev-tools/async_worker_unit_test.py
"""

import importlib.util
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("PASS" if ok else "FAIL"), name, detail)


# ---------------------------------------------------------------- fake pyluxcore


class FakeProperty:
    """Stands for luxrays::Property (a named value list)."""

    def __init__(self, name, values):
        self.name = name
        self.values = list(values)

    def Get(self, index=0):
        return self.values[index]

    def GetString(self, index=0):
        return str(self.values[index])


class FakeProperties:
    def __init__(self, *names):
        self._data = {}
        for name in names:
            self._data[name] = FakeProperty(name, [None])

    def __lshift__(self, values):
        vals = values if isinstance(values, (list, tuple)) else [values]
        for prop, val in zip(self._data.values(), vals):
            prop.values = [val]
        return self

    def Set(self, other, *a):
        # Mirrors the three real overloads: Set(Property),
        # Set(Properties) and Set(Properties, prefix) - the last one is
        # unused by the worker so it shares the merge path.
        if isinstance(other, FakeProperties):
            for name in other.GetAllNames(""):
                self._data[name] = FakeProperty(name, other.Get(name).values)
        elif isinstance(other, FakeProperty):
            self._data[other.name] = FakeProperty(other.name, other.values)
        else:
            for prop, val in zip(self._data.values(), other):
                prop.values = [val]
        return self

    def Get(self, name, index=0):
        return self._data.get(name, FakeProperty(name, [None]))

    def IsDefined(self, name):
        return name in self._data

    def GetAllNames(self, prefix=""):
        return [n for n in self._data if n.startswith(prefix)]

    def GetAllUniqueSubNames(self, prefix):
        """Real semantics: unique first path components after `prefix`
        (which may omit the trailing dot)."""
        seen = []
        for name in self._data:
            if not name.startswith(prefix):
                continue
            sub = name[len(prefix):].lstrip(".").split(".", 1)[0]
            if sub and sub not in seen:
                seen.append(sub)
        return seen


class FakeScene:
    def __init__(self):
        self.calls = []
        self.meshes = set()

    def Parse(self, props):
        self.calls.append(("Parse",))
        # Mirror the real Scene.Parse naming rules (parseobjects.cpp):
        # scene.shapes.<name> -> mesh named <name>;
        # scene.objects.<n>.ply -> mesh named by the .ply value;
        # scene.objects.<n>.vertices -> mesh named "InlinedMesh-<n>".
        for name in props.GetAllUniqueSubNames("scene.shapes"):
            self.meshes.add(name)
        for obj in props.GetAllUniqueSubNames("scene.objects"):
            prefix = "scene.objects." + obj + "."
            if props.IsDefined(prefix + "ply"):
                self.meshes.add(props.Get(prefix + "ply").GetString())
            elif props.IsDefined(prefix + "vertices"):
                self.meshes.add("InlinedMesh-" + obj)

    def DefineMesh(self, name, vs, tris):
        self.calls.append(("DefineMesh", name))
        self.meshes.add(name)

    def DefineMeshExt(self, name, *a):
        self.calls.append(("DefineMeshExt", name))
        self.meshes.add(name)

    def DefineBlenderStrands(self, name, *a):
        self.calls.append(("DefineBlenderStrands", name))
        self.meshes.add(name)

    def DefineBlenderCurveStrands(self, name, *a):
        self.calls.append(("DefineBlenderCurveStrands", name))
        self.meshes.add(name)

    def DuplicateObject(self, src, dst, obj_id, t):
        self.calls.append(("DuplicateObject", dst))

    def DeleteObject(self, name):
        self.calls.append(("DeleteObject", name))

    def DeleteLight(self, name):
        self.calls.append(("DeleteLight", name))

    def UpdateObjectTransformation(self, name, t):
        self.calls.append(("UpdateObjectTransformation", name))

    def RemoveUnusedMeshes(self):
        self.calls.append(("RemoveUnusedMeshes",))
        self.meshes.clear()

    def RemoveUnusedMaterials(self):
        self.calls.append(("RemoveUnusedMaterials",))

    def RemoveUnusedTextures(self):
        self.calls.append(("RemoveUnusedTextures",))

    def RemoveUnusedImageMaps(self):
        self.calls.append(("RemoveUnusedImageMaps",))

    def IsMeshDefined(self, name):
        return name in self.meshes

    def DefineImageMap(self, name, *a):
        self.calls.append(("DefineImageMap", name))

    def SetMeshVertexMotion(self, mesh, *a):
        self.calls.append(("SetMeshVertexMotion", mesh))


class FakeConfig:
    def __init__(self, props, scene=None):
        self.props = props
        self.scene = scene or FakeScene()

    def GetScene(self):
        return self.scene

    def HasCachedKernels(self):
        return False

    def IsDefined(self, name):
        return name in self.props.GetAllUniqueSubNames("")


class FakeSession:
    def __init__(self, config):
        self.config = config
        self.log = []
        self.started = False
        self.paused = False
        self.fail_parse = False
        self.start_delay = 0.0

    def Start(self):
        if self.start_delay:
            time.sleep(self.start_delay)
        self.log.append("Start")
        self.started = True

    def Stop(self):
        self.log.append("Stop")
        self.started = False

    def IsStarted(self):
        return self.started

    def BeginSceneEdit(self):
        self.log.append("BeginSceneEdit")

    def EndSceneEdit(self):
        self.log.append("EndSceneEdit")

    def IsInPause(self):
        return self.paused

    def Resume(self):
        self.log.append("Resume")
        self.paused = False

    def Pause(self):
        self.log.append("Pause")
        self.paused = True

    def Parse(self, props):
        self.log.append("Parse")
        if self.fail_parse:
            raise RuntimeError("boom parse")
        self.config.scene.Parse(props)

    def GetRenderConfig(self):
        return self.config


fake_pyluxcore = type(sys)("pyluxcore")
fake_pyluxcore.Properties = FakeProperties
fake_pyluxcore.Property = FakeProperty
fake_pyluxcore.RenderConfig = FakeConfig
fake_pyluxcore.RenderSession = FakeSession
fake_pyluxcore.KernelCacheFill = lambda config, cb=None: None
sys.modules["pyluxcore"] = fake_pyluxcore


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recorded_mod = load(
    "recorded_scene",
    os.path.join(ROOT, "export", "recorded_scene.py"))
RecordedScene = recorded_mod.RecordedScene

worker_mod = load(
    "session_worker",
    os.path.join(ROOT, "engine", "session_worker.py"))
SessionWorker = worker_mod.SessionWorker


def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


class FakeEngine:
    def __init__(self):
        self.session = None
        self.viewport_start_time = None
        self.redraws = 0
        self._lock = threading.Lock()

    def tag_redraw(self):
        with self._lock:
            self.redraws += 1


# ------------------------------------------------------------ RecordedScene


def test_recorded_scene():
    rec = RecordedScene()

    rec.Parse(FakeProperties("scene.objects.A.shape") << "meshA")
    rec.DefineMeshExt("meshB", FakeProperties("x"), 1, 2)
    rec.DefineMesh("meshC", "verts", "tris")
    rec.DeleteObject("obj1")
    rec.DeleteLight("light1")
    rec.UpdateObjectTransformation("obj2", "transform")
    rec.DuplicateObject("src", "dup", 0, None)
    rec.RemoveUnusedMeshes()

    ops = rec.drain()
    kinds = [op[0] for op in ops]
    check("record order", kinds == [
        "Parse", "DefineMeshExt", "DefineMesh", "DeleteObject",
        "DeleteLight", "UpdateObjectTransformation", "DuplicateObject",
        "RemoveUnusedMeshes"], kinds)
    check("drain clears", rec.drain() == [])

    # Shadow state
    rec = RecordedScene()
    rec.DefineMesh("m1", None, None)
    check("shadow IsMeshDefined", rec.IsMeshDefined("m1") is True)
    check("shadow unknown mesh", rec.IsMeshDefined("nope") is False)
    rec.DefineMeshExt("m2", FakeProperties("x"), 1, 2)
    check("shadow DefineMeshExt", rec.IsMeshDefined("m2") is True)
    rec.RemoveUnusedMeshes()
    check("shadow cleared by RemoveUnusedMeshes",
          rec.IsMeshDefined("m1") is False)

    # Parse-derived shadow state (real naming rules)
    rec = RecordedScene()
    props = FakeProperties(
        "scene.objects.D.shape",
        "scene.shapes.shapeD.file",
        "scene.materials.matD.type",
        "scene.lights.lightD.type")
    rec.Parse(props << ("shapeD", "f.ply", "matte", "point"))
    check("shadow Parse object shape",
          rec.IsMeshDefined("shapeD") is True)
    check("shadow Parse material", rec.IsMaterialDefined("matD") is True)
    check("shadow Parse light count", rec.GetLightCount() == 1)

    rec = RecordedScene()
    props = FakeProperties("scene.objects.E.ply")
    rec.Parse(props << "ext.ply")
    check("shadow Parse .ply value names mesh",
          rec.IsMeshDefined("ext.ply") is True)

    rec = RecordedScene()
    props = FakeProperties("scene.objects.F.vertices")
    rec.Parse(props << [0.0])
    check("shadow Parse inlined mesh",
          rec.IsMeshDefined("InlinedMesh-F") is True)


def test_replay():
    fake = FakeScene()
    rec = RecordedScene()
    rec.DefineMeshExt("meshX", FakeProperties("k"), "a", "b")
    rec.DuplicateObject("src", "d", 1, "t")
    ops = rec.drain()
    # session_worker replays via its own helper (same tuple format).
    worker_mod._replay_ops(fake, ops)

    check("replay applied", fake.calls == [
        ("DefineMeshExt", "meshX"),
        ("DuplicateObject", "d")], fake.calls)
    check("real scene mesh", fake.IsMeshDefined("meshX"))

    # Unknown mutators on the proxy must raise AttributeError, not silently
    # record (a typo'd method name would otherwise corrupt the update).
    rec = RecordedScene()
    try:
        rec.NoSuchMethod("x")
        check("unknown method raises", False)
    except AttributeError:
        check("unknown method raises", True)


# ------------------------------------------------------------ SessionWorker


def make_start_worker(start_delay=0.0):
    engine = FakeEngine()
    worker = SessionWorker(engine)
    return engine, worker, start_delay


def test_start():
    engine, worker, _ = make_start_worker()
    props = FakeProperties("renderengine.type") << "PATHCPU"
    worker.submit_start(FakeScene(), props)

    ok = wait_until(lambda: engine.session is not None and not worker.is_starting)
    check("start publishes session", ok and engine.session.started)
    worker.shutdown()


def test_edit_and_parse():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    wait_until(lambda: engine.session is not None)

    rec = RecordedScene()
    rec.DeleteObject("gone")
    rec.Parse(FakeProperties("scene.shapes.meshN.file") << "n.ply")
    worker.submit_edit(rec.drain())
    ok = wait_until(
        lambda: engine.session is not None
        and "EndSceneEdit" in engine.session.log
        and engine.session.config.scene.IsMeshDefined("meshN"))

    check("edit replayed in scene-edit block", ok)
    check("delete applied",
          ("DeleteObject", "gone") in engine.session.config.scene.calls)

    worker.submit_parse(FakeProperties("scene.shapes.meshP.file") << "p.ply")
    ok = wait_until(
        lambda: engine.session is not None
        and engine.session.config.scene.IsMeshDefined("meshP"))
    check("parse job applied", ok)
    worker.shutdown()


def test_config_coalescing():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    wait_until(lambda: engine.session is not None)

    worker.submit_config(FakeProperties("renderengine.type") << "P1")
    worker.submit_config(FakeProperties("renderengine.type") << "P2")
    worker.submit_config(FakeProperties("renderengine.type") << "P3")

    ok = wait_until(lambda: engine.session is not None and
                    engine.session.config.props.Get("renderengine.type").GetString() == "P3")
    check("config coalesced to latest", ok)
    worker.shutdown()


def test_stop_drops_pending():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    wait_until(lambda: engine.session is not None)
    session = engine.session

    rec = RecordedScene()
    rec.DeleteObject("x")
    for _ in range(5):
        worker.submit_edit(rec.drain())
    worker.submit_stop()

    ok = wait_until(lambda: engine.session is None and "Stop" in session.log)
    check("stop publishes None", ok)
    check("worker not starting", not worker.is_starting)
    worker.shutdown()


def test_newest_start_wins():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "X")
    # The newer export supersedes: the session must end up built from
    # the second start's props, and the first session must be stopped.
    ok = wait_until(lambda: engine.session is not None and
                    engine.session.config.props.Get(
                        "renderengine.type").GetString() == "X")
    check("newest start wins", ok)
    worker.shutdown()


def test_error_latch():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    wait_until(lambda: engine.session is not None)
    engine.session.fail_parse = True

    worker.submit_parse(FakeProperties("x.y") << 1)
    holder = []

    def _pop():
        err = worker.pop_error()
        if err is not None:
            holder.append(err)
        return bool(holder)

    wait_until(_pop)
    err = holder[0] if holder else None
    check("worker error surfaced", err is not None and err[0] == "parse")
    ok = wait_until(lambda: engine.session is None)
    check("session stopped on error", ok)
    worker.shutdown()


def test_pause_resume_edit():
    engine, worker, _ = make_start_worker()
    worker.submit_start(FakeScene(), FakeProperties("renderengine.type") << "PATHCPU")
    wait_until(lambda: engine.session is not None)
    engine.session.paused = True

    rec = RecordedScene()
    rec.DeleteObject("d")
    worker.submit_edit(rec.drain())
    ok = wait_until(lambda: "Resume" in engine.session.log)
    check("edit resumes paused session", ok)
    worker.shutdown()


def main():
    test_recorded_scene()
    test_replay()
    test_start()
    test_edit_and_parse()
    test_config_coalescing()
    test_stop_drops_pending()
    test_newest_start_wins()
    test_error_latch()
    test_pause_resume_edit()

    failed = [n for n, ok in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
