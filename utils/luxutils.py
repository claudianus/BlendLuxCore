"""Various utilities requiring pysuperluxcore."""

import pysuperluxcore


def create_props(prefix, definitions):
    """
    :param prefix: string, will be prepended to each key part of the definitions.
                   Example: "scene.camera." (note the trailing dot)
    :param definitions: dictionary of definition pairs. Example: {"fieldofview", 45}
    :return: pysuperluxcore.Properties() object, initialized with the given definitions.
    """
    props = pysuperluxcore.Properties()

    for key, value in definitions.items():
        props.Set(pysuperluxcore.Property(prefix + key, value))

    return props


def matrix_to_list(matrix, invert=False):
    """Flatten a 4x4 matrix into a list

    Returns list[16]
    """
    # Copy required for BlenderMatrix4x4ToList(), not sure why, but if we don't
    # make a copy, we only get an identity matrix in C++
    matrix = matrix.copy()

    if invert:
        matrix.invert_safe()

    return pysuperluxcore.BlenderMatrix4x4ToList(matrix)


def is_opencl_build():
    """Check if pysuperluxcore has been built with OpenCL support."""
    return (
        pysuperluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_OPENCL")
        .GetBool()
    )


def is_cuda_build():
    """Check if pysuperluxcore has been built with Cuda support."""
    return (
        pysuperluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_CUDA")
        .GetBool()
    )

def is_metal_build():
    """Check if pysuperluxcore has been built with Metal support."""
    return (
        pysuperluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_METAL")
        .GetBool()
    )


def is_vulkan_build():
    """Check if pysuperluxcore has been built with Vulkan support."""
    return (
        pysuperluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_VULKAN")
        .GetBool()
    )
