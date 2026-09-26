# SPDX-License-Identifier: Apache-2.0
import bpy
from bpy.app.handlers import persistent

from ..utils import render as utils_render


@persistent
def handler(scene, depsgraph):
    # Deferred RNA writes from the render callback (RNA writes are
    # forbidden while it runs, but legal here).
    utils_render.flush_suggested_clamp(scene)
