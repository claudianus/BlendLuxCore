import bpy
import os

# Poll .lxm proxy files: the depsgraph cannot see external file
# changes, so a re-baked/overwritten proxy would render stale geometry
# until the object itself is touched. A 2s timer stats every object's
# proxy_filepath and marks the object updated on mtime/size change —
# the viewport update path then re-exports the object (and re-maps the
# file). Baseline is populated on first sighting, so merely assigning
# a path never triggers a spurious reload.
_proxy_files = {}
_INTERVAL = 2.0


def _scan():
    for obj in bpy.data.objects:
        path = getattr(obj.superluxcore, "proxy_filepath", "")
        if not path:
            continue
        apath = bpy.path.abspath(path)
        try:
            st = os.stat(apath)
        except OSError:
            _proxy_files.pop(apath, None)
            continue
        sig = (st.st_mtime_ns, st.st_size)
        old = _proxy_files.get(apath)
        if old is None:
            _proxy_files[apath] = sig
        elif old != sig:
            _proxy_files[apath] = sig
            obj.update_tag()
            for win in bpy.context.window_manager.windows:
                for area in win.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()


def _poll():
    try:
        if not bpy.app.background:
            _scan()
    except Exception:
        pass
    return _INTERVAL


def register():
    bpy.app.timers.register(_poll, first_interval=_INTERVAL,
                            persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    _proxy_files.clear()
