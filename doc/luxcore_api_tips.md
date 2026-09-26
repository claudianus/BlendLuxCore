When one property of a key is set, all other (previously defined) properties of the key will be deleted.
Example:
Let's say we have set the following properties:
```
props.Set(pyluxcore.Property("scene.materials.test.type", "matte"))
props.Set(pyluxcore.Property("scene.materials.test.kd", [0.7, 0.7, 0.7]))
```
Now we want to set the material color to red. If we try the following, LuxCore will complain:
```
props.Set(pyluxcore.Property("scene.materials.test.kd", [0.8, 0, 0]))
```
This is because the line `"scene.materials.test.type", "matte"` will be deleted and the material definition is missing the material type information.
You have to explicitly set lines you want to keep, even if they have not changed.

## Threading / GIL (pyluxcore)

- Long native calls release the GIL (`call_guard<gil_scoped_release>` on
  pure-C++ signatures, or an inner `py::gil_scoped_release` after Python
  argument conversion). NEVER put `call_guard` on a wrapper that reads a
  `py::object`/`py::str` argument inside the function body: the guard
  releases the GIL *before* the body runs, so the conversion touches
  Python objects without the GIL and crashes. Convert first, then release
  inside the body around the native call only.
- `pyluxcore.KernelCacheFill(props, cb)` accepts a progress callback
  `cb(index, count)`. It fires from a native worker thread — do not touch
  bpy in it; capture needed values up-front (see
  `Exporter.create_session`). Only one callback can be active at a time.
- Ownership: `RenderConfig(props, scene)` is NON-OWNING (the scene must
  outlive it; `py::keep_alive` on the binding does that for the Python
  object you pass). `RenderConfig.GetScene()` returns a smart_holder
  wrapper that CO-OWNS the native scene — a session rebuilt on
  `old.GetRenderConfig().GetScene()` stays valid after the old session is
  dropped (verified empirically).
- `Scene.Parse` on an object def with `.ply` defines a mesh named by the
  ply path; `.vertices` defines `"InlinedMesh-<objname>"`; `.shape`
  references an existing mesh. `RecordedScene._track_props` mirrors this
  — keep them in sync if the rules change.
- `Film::AddChannel` on an initialized film is a no-op when the channel
  already exists (required: OIDN component-mode image pipelines re-Parse
  into a live film), but still throws for genuinely new channels.
- At interpreter shutdown a stopped `RenderSession` can segfault in
  static teardown (pre-existing pyluxcore issue) — unrelated to addon
  code.