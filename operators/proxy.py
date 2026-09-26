import bpy
import pyluxcore
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper

from ..export import mesh_converter


class LUXCORE_OT_bake_lxm_proxy(bpy.types.Operator, ExportHelper):
    """
    Bake the active object's evaluated mesh into an .lxm proxy file and
    set it as the object's mesh source. The proxy is memory-mapped at
    render time — the Blender mesh is never read on export, which is
    the point for heavy static assets.
    """

    bl_idname = "luxcore.bake_lxm_proxy"
    bl_label = "Bake .lxm Proxy"
    bl_options = {"UNDO"}

    filename_ext = ".lxm"
    filter_glob: StringProperty(default="*.lxm", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (
            obj is not None
            and obj.type == "MESH"
            and context.scene.render.engine == "LUXCORE"
        )

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = bpy.path.abspath("//" + context.object.name + ".lxm")
        # No file dialog in background mode — use the (defaulted) path.
        if bpy.app.background:
            return self.execute(context)
        return super().invoke(context, event)

    def execute(self, context):
        obj = context.object
        depsgraph = context.evaluated_depsgraph_get()

        # Single combined submesh (material slot 0): an .lxm proxy is a
        # single-material mesh. use_instancing=True keeps the mesh in
        # local space — the object's own transform still applies.
        scene = pyluxcore.Scene()
        key = "proxybake_" + obj.name
        exported = mesh_converter.convert(
            obj,
            key,
            depsgraph,
            scene,
            False,
            True,
            None,
            single_mesh=True,
        )
        if exported is None:
            self.report({"ERROR"}, f"'{obj.name}': no mesh to bake")
            return {"CANCELLED"}

        mesh_name = exported.mesh_definitions[0][0]
        path = bpy.path.abspath(self.filepath)
        try:
            scene.SaveMesh(mesh_name, path)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Proxy bake failed: {e}")
            return {"CANCELLED"}

        obj.luxcore.proxy_filepath = self.filepath
        self.report({"INFO"}, f"Baked {path} — object now renders from proxy")
        return {"FINISHED"}
