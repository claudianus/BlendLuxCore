import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, IntProperty
from ..base import SuperLuxCoreNodeMaterial
from ...utils import node as utils_node


ORIENTATION_ITEMS = [
    ("radial", "Radial (CD)",
     "Concentric circular grooves around a world-space center (compact discs, vinyl)", 0),
    ("radialuv", "Radial (UV)",
     "Concentric circular grooves around a UV-space center", 1),
    ("u", "Straight along U",
     "Parallel grooves along the U direction (linear gratings, holographic foils)", 2),
    ("v", "Straight along V",
     "Parallel grooves along the V direction", 3),
]


class SuperLuxCoreNodeMatDiffraction(SuperLuxCoreNodeMaterial, bpy.types.Node):
    """diffraction grating material node (CD rainbow, holographic foil)"""
    bl_label = "Diffraction Material"
    bl_width_default = 200

    orientation: EnumProperty(
        name="Groove Orientation", items=ORIENTATION_ITEMS, default="radial",
        description="Direction of the grating grooves on the surface",
        update=utils_node.force_viewport_update)
    center: FloatVectorProperty(
        name="Center", subtype="XYZ", default=(0.0, 0.0, 0.0),
        description="Groove circle center in object space (Radial (CD) mode)",
        update=utils_node.force_viewport_update)
    center_u: FloatProperty(
        name="Center U", default=0.5, min=0.0, max=1.0,
        description="Groove circle center U coordinate (Radial (UV) mode)",
        update=utils_node.force_viewport_update)
    center_v: FloatProperty(
        name="Center V", default=0.5, min=0.0, max=1.0,
        description="Groove circle center V coordinate (Radial (UV) mode)",
        update=utils_node.force_viewport_update)
    blaze: FloatProperty(
        name="Blaze Angle", subtype="ANGLE", default=0.0, min=-1.39626, max=1.39626,
        description="Groove facet tilt (blazed grating, holographic foils). "
                    "0 deg = symmetric lamellar grating",
        update=utils_node.force_viewport_update)
    orders: IntProperty(
        name="Max Orders", default=8, min=1, max=32,
        description="Cap on searched diffraction orders "
                    "(higher orders have negligible energy for typical spacings)",
        update=utils_node.force_viewport_update)

    def init(self, context):
        self.add_input("SuperLuxCoreSocketColor", "Reflection Color", (0.95, 0.95, 0.95))
        self.add_input("SuperLuxCoreSocketFloatPositive", "Spacing (nm)", 1600.0)
        self.add_input("SuperLuxCoreSocketFloat0to1", "Roughness", 0.05)
        self.add_input("SuperLuxCoreSocketFloat0to1", "Fill Factor", 0.5)
        self.add_common_inputs()

        self.outputs.new("SuperLuxCoreSocketMaterial", "Material")

    def draw_buttons(self, context, layout):
        layout.prop(self, "orientation")
        if self.orientation == "radial":
            layout.prop(self, "center")
        elif self.orientation == "radialuv":
            col = layout.column(align=True)
            col.prop(self, "center_u")
            col.prop(self, "center_v")

        spacing_socket = self.inputs["Spacing (nm)"]
        if not spacing_socket.is_linked:
            spacing = spacing_socket.default_value
            if spacing > 0:
                layout.label(text=f"= {1e6 / spacing:.0f} lines/mm", icon="DRIVER_DISTANCE")

        layout.prop(self, "blaze")
        layout.prop(self, "orders")

        config = getattr(context.scene.superluxcore, "config", None)
        if config is not None and not config.spectral_enable:
            layout.label(text="Enable Spectral Rendering for physical colors",
                         icon="INFO")

    def sub_export(self, exporter, depsgraph, props, superluxcore_name=None, output_socket=None):
        import math
        definitions = {
            "type": "diffraction",
            "kr": self.inputs["Reflection Color"].export(exporter, depsgraph, props),
            "spacing": self.inputs["Spacing (nm)"].export(exporter, depsgraph, props),
            "roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
            "fillfactor": self.inputs["Fill Factor"].export(exporter, depsgraph, props),
            "orientation": self.orientation,
            "center": list(self.center),
            "centeru": self.center_u,
            "centerv": self.center_v,
            # Scene property is in degrees (converted to radians internally)
            "blaze": math.degrees(self.blaze),
            "orders": self.orders,
        }
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, superluxcore_name)
