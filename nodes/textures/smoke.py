import bpy
import mathutils
from time import time
from bpy.props import EnumProperty, PointerProperty, StringProperty
from ..base import SuperLuxCoreNodeTexture
from ... import utils
import pysuperluxcore
from ...export import smoke
from ...utils import node as utils_node
from ... import icons
from ...utils.errorlog import SuperLuxCoreErrorLog

class SuperLuxCoreNodeTexSmoke(SuperLuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Smoke"
    bl_width_default = 200

    def poll_domain(self, obj):
        # Only allow objects with a smoke modifier in domain mode to be picked
        return utils.find_smoke_domain_modifier(obj)
    
    domain: PointerProperty(update=utils_node.force_viewport_update, name="Domain", type=bpy.types.Object, poll=poll_domain)

    # NOTE: The source property is no longer used, just here for backwards compatibility (utils/compatibility.py)
    source_items = [
        ("density", "Density", "Smoke density grid, 1 value per voxel", 0),
        ("fire", "Fire", "Fire grid, 1 value per voxel", 1),
        ("heat", "Heat", "Smoke heat grid, 1 value per voxel", 2),
        ("color", "Color", "Smoke color grid, 3 values per voxel (RGB)", 3),
        ("velocity", "Velocity", "Smoke velocity grid, 3 values per voxel", 4),
    ]
    source: EnumProperty(name="Grid Type", items=source_items, default="density")

    precision_items = [
        ("byte", "Byte", "Only 1 byte per value. Required memory is 1/2 of Half and 1/4 of Float", 0),
        ("half", "Half", "2 bytes per value. Required memory is 1/2 of Float, but 2 times the size of Byte", 1),
        ("float", "Float", "4 bytes per value. Required memory is 2 times the size of half and 4 times the size of Byte", 2),
    ]
    precision: EnumProperty(update=utils_node.force_viewport_update, name="Precision", items=precision_items, default="half",
                             description="How many bytes to use per value. The floating point precision "
                                         "increases/decreases when more/less bytes are used. Low floating "
                                         "point precision can lead to artifacts when the smoke resolution is low")

    def init(self, context):
        self.outputs.new("SuperLuxCoreSocketFloatPositive", "density")
        self.outputs.new("SuperLuxCoreSocketFloatPositive", "flame")
        self.outputs.new("SuperLuxCoreSocketFloatPositive", "heat")
        self.outputs.new("SuperLuxCoreSocketFloatPositive", "temperature")
        self.outputs.new("SuperLuxCoreSocketColor", "color")
        self.outputs.new("SuperLuxCoreSocketColor", "velocity")


    def draw_buttons(self, context, layout):
        layout.prop(self, "domain")

        if self.domain and not utils.find_smoke_domain_modifier(self.domain):
            layout.label(text="Not a smoke domain!", icon=icons.WARNING)
        elif self.domain is None:
            layout.label(text="Select the smoke domain object", icon=icons.WARNING)

        col = layout.column()
        col.prop(self, "precision")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        start_time = time()
        print("[Node Tree: %s][Smoke Domain: %s] Beginning smoke export of channel %s"
              % (self.id_data.name, self.domain.name, output_socket.name))

        if not self.domain:
            error = "No Domain object selected."
            msg = 'Node "%s" in tree "%s": %s' % (self.name, self.id_data.name, error)
            SuperLuxCoreErrorLog.add_warning(msg)

            definitions = {
                "type": "constfloat3",
                "value": [0, 0, 0],
            }
            return self.create_props(props, definitions, superluxcore_name)

        domain_eval = self.domain.evaluated_get(depsgraph)

        scale = domain_eval.dimensions
        translate = domain_eval.matrix_world @ mathutils.Vector(domain_eval.bound_box[0][:])
        rotate = domain_eval.rotation_euler

        # create a location matrix
        tex_loc = mathutils.Matrix.Translation(translate)

        # create an identitiy matrix
        tex_sca = mathutils.Matrix()
        tex_sca[0][0] = scale[0]  # X
        tex_sca[1][1] = scale[1]  # Y
        tex_sca[2][2] = scale[2]  # Z

        # create a rotation matrix
        tex_rot0 = mathutils.Matrix.Rotation(rotate[0], 4, 'X')
        tex_rot1 = mathutils.Matrix.Rotation(rotate[1], 4, 'Y')
        tex_rot2 = mathutils.Matrix.Rotation(rotate[2], 4, 'Z')
        tex_rot = tex_rot2 @ tex_rot1 @ tex_rot0

        resolution, grid = smoke.convert(domain_eval, output_socket.name, depsgraph)
        nx, ny, nz = resolution

        smoke_domain_mod = utils.find_smoke_domain_modifier(domain_eval)
        grid_name = output_socket.name
        cell_size = mathutils.Vector((0, 0, 0))
        amplify = 1

        use_high_resolution = False

        for i in range(3):
            cell_size[i] = smoke_domain_mod.domain_settings.cell_size[i] * 1/amplify

        # combine transformations
        mapping_type = 'globalmapping3d'
        matrix_transformation = utils.luxutils.matrix_to_list(mathutils.Matrix.Translation(0.5*mathutils.Vector(cell_size)) @ tex_loc @ tex_rot @ tex_sca,
                                                     invert=True)

        definitions = {
            "type": "densitygrid",
            "wrap": "black",
            "storage": self.precision,
            "nx": nx,
            "ny": ny,
            "nz": nz,
            # Mapping
            "mapping.type": mapping_type,
            "mapping.transformation": matrix_transformation,
        }

        superluxcore_name = self.create_props(props, definitions, superluxcore_name)
        prefix = self.prefix + superluxcore_name + "."
        # We use a fast path (AddAllFloat method) here to transfer the grid data to the properties


        if output_socket.name == "color":
            prop = pysuperluxcore.Property(prefix + "data3", [])
            # Omit every 4th element because the color_grid contains 4 values per cell
            # but SuperLuxCore expects 3 values per cell (r, g, b)
            prop.AddAllFloat(grid, 3, 1)
        elif output_socket.name == "velocity":
            prop = pysuperluxcore.Property(prefix + "data3", [])
            prop.AddAllFloat(grid)
        else:
            prop = pysuperluxcore.Property(prefix + "data", [])
            prop.AddAllFloat(grid)

        # We have to free the memory manually because the grid can be VERY large
        # and if we don't invoke the garbage collection, we use way more RAM than necessary
        del grid
        import gc
        gc.collect()

        props.Set(prop)

        elapsed_time = time() - start_time
        print("[Node Tree: %s][Smoke Domain: %s] Smoke export of channel %s took %.3f s"
              % (self.id_data.name, self.domain.name, output_socket.name, elapsed_time))

        return superluxcore_name
