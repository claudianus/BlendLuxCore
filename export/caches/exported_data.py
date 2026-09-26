from ... import utils

class ExportedPart:
    def __init__(self, lux_obj, lux_shape, lux_mat):
        self.lux_obj = lux_obj
        self.lux_shape = lux_shape
        self.lux_mat = lux_mat


class ExportedMesh:
    def __init__(self, mesh_definitions, vert_sig=None, submesh_maps=None):
        self.mesh_definitions = mesh_definitions
        # Deformation motion blur (E9): export-time topology signature
        # (vertex_count, loop_count, loop_indices blake2b digest). The
        # per-step sampler in motion_blur.py re-checks it so a
        # mid-shutter topology change falls back to static instead of
        # corrupting the vertex series.
        self.vert_sig = vert_sig
        # {shape_name: compacted loop index array} — set only when a
        # submesh's triangles don't reference all loops (see the
        # per-material compaction in mesh_converter.convert). Missing
        # entries mean the submesh covers the full loop domain.
        self.submesh_maps = submesh_maps or {}
        # .lxm proxy meshes carry no Blender-side buffers: a
        # {material_slot_index: absolute .lxm path} dict, or None for
        # converted meshes. ExportedObject reads this to emit
        # "scene.objects.X.ply" instead of ".shape".
        self.proxy_paths = None


class ExportedData:
    def delete(self, luxcore_scene):
        raise NotImplementedError()


class ExportedObject(ExportedData):
    def __init__(self, lux_name_base, mesh_definitions, mat_names, transform, visible_to_camera, obj_id=-1):
        self.transform = transform
        self.parts = []
        self.visible_to_camera = visible_to_camera
        self.obj_id = obj_id
        # Number of "dupli" objects spawned per part (point cloud instancing)
        self.duplicate_count = 0
        # Point cloud records: per-step matrices/motion buffers collected by
        # motion_blur.convert() when the object opted into motion blur.
        self.is_pointcloud = False
        self.pc_step_data = []
        self.pc_failed = False
        self.pc_motion = None
        self.pc_motion_times = None
        self.pc_steps_n = 0
        self.pc_prefix = None
        # Deformation motion blur (E9): the ExportedMesh this object was
        # built from plus the mesh_key used for per-step deduplication,
        # and whether any part's final shape is a wrapper (subdiv etc.)
        # around the base mesh — wrappers create new meshes that would
        # silently drop a vertex series set on the base shape.
        self.exported_mesh = None
        self.vert_mesh_key = None
        self.has_shape_wrapper = False
        # {part.lux_obj: absolute .lxm path}: proxied parts are emitted
        # as "scene.objects.X.ply" file references instead of ".shape".
        self.proxy_paths = None
        # ((path, mtime_ns, size), ...) of the proxy files at export
        # time — the viewport update path re-stats these to detect an
        # externally re-baked .lxm.
        self.proxy_sig = ()
        # Strand deformation motion blur (E9): records of strand meshes
        # (hair curves or particle hair) exported by this object. Each
        # entry is a dict {"mesh", "kind", "sig", "space_matrix",
        # "wrapped"} consumed by motion_blur's per-step strand sampler.
        self.strand_recs = []

        for (shape_name, mat_index), mat_name in zip(mesh_definitions, mat_names):
            obj_name = lux_name_base + str(mat_index)

            self.parts.append(ExportedPart(obj_name, shape_name, mat_name))

    def get_props(self):
        prefix = "scene.objects."
        definitions = {}

        for part in self.parts:
            proxy_path = (
                self.proxy_paths.get(part.lux_obj) if self.proxy_paths else None
            )
            if proxy_path:
                definitions[part.lux_obj + ".ply"] = proxy_path
            else:
                definitions[part.lux_obj + ".shape"] = part.lux_shape
            definitions[part.lux_obj + ".material"] = part.lux_mat
            definitions[part.lux_obj + ".camerainvisible"] = not self.visible_to_camera
            if self.obj_id != -1:
                definitions[part.lux_obj + ".id"] = self.obj_id

            if self.transform:
                definitions[part.lux_obj + ".transformation"] = utils.luxutils.matrix_to_list(self.transform)

        return utils.luxutils.create_props(prefix, definitions)

    def delete(self, luxcore_scene):
        for part in self.parts:
            for i in range(self.duplicate_count):
                luxcore_scene.DeleteObject(part.lux_obj + "dupli" + str(i))
            luxcore_scene.DeleteObject(part.lux_obj)


class ExportedLight(ExportedData):
    def __init__(self, lux_light_name):
        self.lux_light_name = lux_light_name

    def delete(self, luxcore_scene):
        luxcore_scene.DeleteLight(self.lux_light_name)
