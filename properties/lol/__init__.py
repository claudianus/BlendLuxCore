from ... import utils
from . import SuperLuxCoreOLScene

classes = (
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryAssetBar,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryUI,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryModel,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryMaterial,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryScene,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryAsset,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibraryUpload,
    SuperLuxCoreOLScene.SuperLuxCoreOnlineLibrary,
)

def register():
    utils.register_module("Properties.Lol", classes)

def unregister():
    utils.unregister_module("Properties.Lol", classes)
