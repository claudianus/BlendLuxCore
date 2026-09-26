"""
Recording proxy for pysuperluxcore.Scene used by the async session worker.

The viewport update path (Exporter._update_scene) runs on Blender's main
thread because it needs the depsgraph, but the real pysuperluxcore scene is
owned by the session worker thread, which performs
BeginSceneEdit/EndSceneEdit and session restarts. Calling into the real
scene from the main thread would race the worker (and a long call there
would freeze the UI).

RecordedScene records every mutation as ``(name, args, kwargs)`` tuples.
The worker replays them on the real scene inside a single scene-edit
block (SessionWorker). Definition queries the export code performs
mid-update (IsMeshDefined and friends) are answered from a shadow state
updated while recording.

The shadow state is deliberately conservative: a name the proxy has not
seen defined reports False. A false negative only makes the caller
define the resource again — SuperLuxCore re-defines an existing mesh,
material, texture or image map in place — while a false positive would
skip a needed definition and corrupt the scene, so the proxy never
claims a name it did not record.

This module must not import bpy: it is exercised by worker-adjacent unit
tests without a Blender runtime.
"""


# Scene methods that mutate state and are replayed on the real scene.
_MUTATORS = (
    "Parse",
    "DefineImageMap",
    "DefineMesh",
    "DefineMeshExt",
    "DefineStrands",
    "DefineBlenderStrands",
    "DefineBlenderCurveStrands",
    "SetMeshVertexAOV",
    "SetMeshTriangleAOV",
    "SetMeshVertexMotion",
    "SetStrandsVertexMotion",
    "SetMeshAppliedTransformation",
    "DuplicateObject",
    "DeleteObjects",
    "DeleteLights",
    "UpdateObjectTransformation",
    "UpdateObjectMaterial",
    "DeleteObject",
    "DeleteLight",
    "RemoveUnusedImageMaps",
    "RemoveUnusedTextures",
    "RemoveUnusedMaterials",
    "RemoveUnusedMeshes",
    "Save",
    "SaveMesh",
)

# Mutators whose first argument is a defined mesh/shape name.
_MESH_DEFINERS = (
    "DefineMesh",
    "DefineMeshExt",
    "DefineStrands",
    "DefineBlenderStrands",
    "DefineBlenderCurveStrands",
)

# Mutators that return a boolean success value to the caller.
_BOOL_RETURNS = (
    "DefineMesh",
    "DefineMeshExt",
    "DefineStrands",
    "DefineBlenderStrands",
    "DefineBlenderCurveStrands",
)


def _first_arg(args, kwargs, name="name"):
    """First positional arg, or the ``name`` kwarg (DefineMeshExt style)."""
    if args:
        return args[0]
    return kwargs.get(name)


class RecordedScene:
    def __init__(self):
        self._ops = []
        # Shadow sets of names that are *known* to be defined. See the
        # module docstring: under-approximation is safe, over- is not.
        self._meshes = set()
        self._objects = set()
        self._lights = set()
        self._materials = set()
        self._textures = set()
        self._imagemaps = set()

    # ------------------------------------------------------------------
    # Recording + shadow bookkeeping
    # ------------------------------------------------------------------

    def _record(self, name, args, kwargs):
        self._ops.append((name, args, kwargs))
        self._track(name, args, kwargs)
        return True if name in _BOOL_RETURNS else None

    def _track(self, name, args, kwargs):
        if name == "Parse":
            self._track_props(args[0])
        elif name in _MESH_DEFINERS:
            mesh = _first_arg(args, kwargs)
            if mesh is not None:
                self._meshes.add(mesh)
        elif name == "DefineImageMap":
            self._imagemaps.add(_first_arg(args, kwargs))
        elif name == "DuplicateObject":
            # (srcName, dstName, ...) — the destination is a new object
            self._objects.add(args[1])
        elif name == "DeleteObject":
            # The mesh stays alive (it may be shared); only the object goes.
            self._objects.discard(args[0])
        elif name == "DeleteObjects":
            self._objects -= set(args[0])
        elif name == "DeleteLight":
            self._lights.discard(args[0])
        elif name == "DeleteLights":
            self._lights -= set(args[0])
        elif name == "RemoveUnusedMeshes":
            # Survivors depend on live reference counts; conservatively
            # forget everything (callers re-define, which is safe).
            self._meshes.clear()
        elif name == "RemoveUnusedMaterials":
            self._materials.clear()
        elif name == "RemoveUnusedTextures":
            self._textures.clear()
        elif name == "RemoveUnusedImageMaps":
            self._imagemaps.clear()

    def _track_props(self, props):
        """Extract the names a ``Scene.Parse`` call would define."""
        self._objects |= set(props.GetAllUniqueSubNames("scene.objects"))
        self._lights |= set(props.GetAllUniqueSubNames("scene.lights"))
        self._materials |= set(
            props.GetAllUniqueSubNames("scene.materials")
        )
        self._textures |= set(props.GetAllUniqueSubNames("scene.textures"))
        self._meshes |= set(props.GetAllUniqueSubNames("scene.shapes"))
        # Implicit meshes created by object definitions.
        for obj_name in props.GetAllUniqueSubNames("scene.objects"):
            prefix = "scene.objects." + obj_name + "."
            if props.IsDefined(prefix + "ply"):
                self._meshes.add(props.Get(prefix + "ply").GetString())
            elif props.IsDefined(prefix + "vertices"):
                self._meshes.add("InlinedMesh-" + obj_name)

    # ------------------------------------------------------------------
    # Worker interface
    # ------------------------------------------------------------------

    def drain(self):
        """Return the recorded op list and clear it for the next batch."""
        ops, self._ops = self._ops, []
        return ops

    @staticmethod
    def replay(scene, ops):
        """Apply recorded ops on the real scene (worker thread, inside
        BeginSceneEdit/EndSceneEdit)."""
        for name, args, kwargs in ops:
            getattr(scene, name)(*args, **kwargs)

    # ------------------------------------------------------------------
    # Recorded mutators (installed below)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Shadowed queries
    # ------------------------------------------------------------------

    def IsMeshDefined(self, name):
        return name in self._meshes

    def IsTextureDefined(self, name):
        return name in self._textures

    def IsMaterialDefined(self, name):
        return name in self._materials

    def IsImageMapDefined(self, name):
        return name in self._imagemaps

    def GetLightCount(self):
        return len(self._lights)

    def GetObjectCount(self):
        return len(self._objects)


def _make_mutator(name):
    def method(self, *args, **kwargs):
        return self._record(name, args, kwargs)

    method.__name__ = name
    return method


for _name in _MUTATORS:
    setattr(RecordedScene, _name, _make_mutator(_name))
