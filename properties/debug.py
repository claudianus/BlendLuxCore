import bpy
from bpy.props import IntProperty, BoolProperty


class SuperLuxCoreDebugSettings(bpy.types.PropertyGroup):
    show: BoolProperty(default=False)
    enabled: BoolProperty(name="Enabled", default=False)
    use_opencl_cpu: BoolProperty(name="Use only OpenCL CPU device", default=False,
                                  description="This is a mode for debugging OpenCL problems. "
                                              "If the problem shows up in this mode, it is most "
                                              "likely a bug in SuperLuxCore and not an OpenCL compiler bug")
    print_properties: BoolProperty(name="Print Properties", default=False)
