import bpy
from bpy.props import BoolProperty
from ..base import LuxCoreNodeMaterial
from .glass import THIN_FILM_DESCRIPTION
from ... import utils
from ...utils.node import ThinFilmCoating


class LuxCoreNodeMatOpenPBR(LuxCoreNodeMaterial, bpy.types.Node):
    """ASWF OpenPBR Surface v1.1 material.

    Lobe stack (top to bottom): fuzz -> coat -> specular/diffuse/metal ->
    transmission/subsurface. Matches the parameterization of Blender's
    Principled BSDF so assets transfer directly.
    """
    bl_label = "OpenPBR Material"
    bl_width_default = 210

    def _set_enabled(self, names, enabled):
        # Blender 5.2: disabled sockets are excluded from inputs[name]
        # key lookups, so address them by index via find().
        for name in names:
            self.inputs[self.inputs.find(name)].enabled = enabled

    def update_use_thinfilmcoating(self, context):
        self._set_enabled(("Film Weight",), self.use_thinfilmcoating)
        ThinFilmCoating.toggle(self, context)

    def update_use_subsurface(self, context):
        self._set_enabled(("Subsurface Weight", "Subsurface Color",
                           "Subsurface Radius", "Subsurface Radius Scale",
                           "Subsurface Anisotropy"), self.use_subsurface)

    def update_use_transmission(self, context):
        self._set_enabled(("Transmission Weight", "Transmission Color",
                           "Transmission Depth", "Transmission Scatter",
                           "Transmission Scatter Anisotropy", "Dispersion"),
                          self.use_transmission)

    def update_use_coat(self, context):
        self._set_enabled(("Coat Weight", "Coat Color", "Coat Roughness",
                           "Coat Anisotropy", "Coat Rotation", "Coat IOR",
                           "Coat Darkening"), self.use_coat)

    def update_use_fuzz(self, context):
        self._set_enabled(("Fuzz Weight", "Fuzz Color", "Fuzz Roughness"),
                          self.use_fuzz)

    use_thinfilmcoating: BoolProperty(name="Thin Film Coating", default=False,
                                      description=THIN_FILM_DESCRIPTION,
                                      update=update_use_thinfilmcoating)
    use_subsurface: BoolProperty(name="Subsurface", default=False,
                                 description="Enable the subsurface scattering lobe "
                                             "(implicit interior volume from radius/color)",
                                 update=update_use_subsurface)
    use_transmission: BoolProperty(name="Transmission", default=False,
                                   description="Enable the specular transmission lobe",
                                   update=update_use_transmission)
    use_coat: BoolProperty(name="Coat", default=False,
                           description="Enable the dielectric clear-coat lobe",
                           update=update_use_coat)
    use_fuzz: BoolProperty(name="Fuzz", default=False,
                           description="Enable the retro-reflective fuzz (sheen) lobe",
                           update=update_use_fuzz)

    def init(self, context):
        # Base
        self.add_input("LuxCoreSocketColor", "Base Color", [0.8] * 3)
        self.add_input("LuxCoreSocketFloat0to1", "Base Weight", 1)
        self.add_input("LuxCoreSocketFloat0to1", "Base Metalness", 0)
        self.add_input("LuxCoreSocketFloat0to1", "Diffuse Roughness", 0)
        # Specular
        self.add_input("LuxCoreSocketFloat0to1", "Specular Weight", 1)
        self.add_input("LuxCoreSocketColor", "Specular Color", [1.0] * 3)
        self.add_input("LuxCoreSocketFloat0to1", "Specular Roughness", 0.3)
        self.add_input("LuxCoreSocketFloat0to1", "Specular Anisotropy", 0)
        self.add_input("LuxCoreSocketFloat0to1", "Specular Rotation", 0)
        self.add_input("LuxCoreSocketIOR", "Specular IOR", 1.5)
        # Transmission
        self.add_input("LuxCoreSocketFloat0to1", "Transmission Weight", 0, enabled=False)
        self.add_input("LuxCoreSocketColor", "Transmission Color", [1.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloatPositive", "Transmission Depth", 0, enabled=False)
        self.add_input("LuxCoreSocketColor", "Transmission Scatter", [0.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloatUnbounded", "Transmission Scatter Anisotropy", 0, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Dispersion", 0, enabled=False)
        # Subsurface
        self.add_input("LuxCoreSocketFloat0to1", "Subsurface Weight", 0, enabled=False)
        self.add_input("LuxCoreSocketColor", "Subsurface Color", [1.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloatPositive", "Subsurface Radius", 1, enabled=False)
        self.add_input("LuxCoreSocketColor", "Subsurface Radius Scale", [1.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloatUnbounded", "Subsurface Anisotropy", 0, enabled=False)
        # Coat
        self.add_input("LuxCoreSocketFloat0to1", "Coat Weight", 0, enabled=False)
        self.add_input("LuxCoreSocketColor", "Coat Color", [1.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Coat Roughness", 0, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Coat Anisotropy", 0, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Coat Rotation", 0, enabled=False)
        self.add_input("LuxCoreSocketIOR", "Coat IOR", 1.5, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Coat Darkening", 1, enabled=False)
        # Fuzz
        self.add_input("LuxCoreSocketFloat0to1", "Fuzz Weight", 0, enabled=False)
        self.add_input("LuxCoreSocketColor", "Fuzz Color", [1.0] * 3, enabled=False)
        self.add_input("LuxCoreSocketFloat0to1", "Fuzz Roughness", 0.5, enabled=False)
        # Thin film
        self.add_input("LuxCoreSocketFloat0to1", "Film Weight", 1, enabled=False)
        ThinFilmCoating.init(self)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.prop(self, "use_transmission")
        col.prop(self, "use_subsurface")
        col.prop(self, "use_coat")
        col.prop(self, "use_fuzz")
        col.prop(self, "use_thinfilmcoating")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        exp = lambda n: self.inputs[n].export(exporter, depsgraph, props)
        definitions = {
            "type": "openpbr",
            "basecolor": exp("Base Color"),
            "baseweight": exp("Base Weight"),
            "basemetalness": exp("Base Metalness"),
            "basediffuseroughness": exp("Diffuse Roughness"),
            "specularweight": exp("Specular Weight"),
            "specularcolor": exp("Specular Color"),
            "specularroughness": exp("Specular Roughness"),
            "specularanisotropy": exp("Specular Anisotropy"),
            "specularrotation": exp("Specular Rotation"),
            "specularior": exp("Specular IOR"),
        }

        if self.use_transmission:
            definitions["transmissionweight"] = exp("Transmission Weight")
            definitions["transmissioncolor"] = exp("Transmission Color")
            definitions["transmissiondepth"] = exp("Transmission Depth")
            definitions["transmissionscatter"] = exp("Transmission Scatter")
            definitions["transmissionscatteranisotropy"] = exp("Transmission Scatter Anisotropy")
            definitions["dispersion"] = exp("Dispersion")

        if self.use_subsurface:
            definitions["subsurfaceweight"] = exp("Subsurface Weight")
            definitions["subsurfacecolor"] = exp("Subsurface Color")
            definitions["subsurfaceradius"] = exp("Subsurface Radius")
            definitions["subsurfaceradiusscale"] = exp("Subsurface Radius Scale")
            definitions["subsurfaceanisotropy"] = exp("Subsurface Anisotropy")

        if self.use_coat:
            definitions["coatweight"] = exp("Coat Weight")
            definitions["coatcolor"] = exp("Coat Color")
            definitions["coatroughness"] = exp("Coat Roughness")
            definitions["coatanisotropy"] = exp("Coat Anisotropy")
            definitions["coatrotation"] = exp("Coat Rotation")
            definitions["coatior"] = exp("Coat IOR")
            definitions["coatdarkening"] = exp("Coat Darkening")

        if self.use_fuzz:
            definitions["fuzzweight"] = exp("Fuzz Weight")
            definitions["fuzzcolor"] = exp("Fuzz Color")
            definitions["fuzzroughness"] = exp("Fuzz Roughness")

        if self.use_thinfilmcoating:
            weight_socket = self.inputs["Film Weight"]
            weight = weight_socket.export(exporter, depsgraph, props)
            thickness_socket = self.inputs[ThinFilmCoating.THICKNESS_NAME]
            thickness = thickness_socket.export(exporter, depsgraph, props)
            if (weight_socket.is_linked or weight > 0) and \
                    (thickness_socket.is_linked or thickness > 0):
                definitions["filmweight"] = weight
                # The socket is in nm (artist convention, same as the Disney
                # node); the LuxCore OpenPBR property is in micrometers.
                if isinstance(thickness, str):
                    tex_name = utils.sanitize_luxcore_name(luxcore_name + "_film_nm_to_um")
                    props.Set(utils.luxutils.create_props("scene.textures." + tex_name + ".", {
                        "type": "scale",
                        "texture1": thickness,
                        "texture2": 0.001,
                    }))
                    thickness = tex_name
                else:
                    thickness *= 0.001
                definitions["filmthickness"] = thickness
                definitions["filmior"] = self.inputs[ThinFilmCoating.IOR_NAME].export(
                        exporter, depsgraph, props)

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
