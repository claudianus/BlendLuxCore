# Async viewport session (SessionWorker)

## Why

`view_update`/`view_draw` run on Blender's UI thread. Session creation
(RenderConfig + kernel pre-compile + RenderSession.Start) can take
minutes on a cold kernel cache, and every synchronous call froze the
whole window. All live-session work now runs on a dedicated worker
thread; the main thread only does what it must — depsgraph access.

## Threads and ownership

```
main thread (bpy/depsgraph)          worker thread (pyluxcore only)
--------------------------           -----------------------------
Exporter.export_scene()        ->    RenderConfig + KernelCacheFill
  produces (scene, config_props)     + RenderSession.Start
Exporter.update()              ->    BeginSceneEdit + replay ops +
  produces [("edit", ops),           EndSceneEdit, session.Parse,
            ("parse", props)]        Stop, config rebuilds
```

- `engine.session_worker` owns the live `pyluxcore.RenderSession`.
  `_publish()` mirrors it into `engine.session` so readers (stats, film
  readback) always see the current object.
- `session_lock` (created in `engine.reset()`, shared with the worker)
  guards live-session mutations vs. film reads. It is never held across
  RenderConfig/RenderSession construction or kernel compiles, so readers
  never stall behind a rebuild.
- The worker holds `weakref(engine)` — `engine -> worker -> engine`
  would otherwise be a reference cycle delaying `__del__` cleanup.

## Job coalescing

- `submit_config` merges pending config jobs (newest wins) or stashes
  into `_pending_config` when no session is live (folded into the next
  start — a config change during startup never boots stale settings).
- `submit_edit` appends/merges op lists; edits arriving mid-restart are
  requeued so they land on the new session (the scene is shared).
- `submit_start` carries a seq counter: a superseded start skips its
  build. Before building, the old session is stopped.
- `submit_stop` clears the queue and stops the session; the follow-up
  full export re-covers any dropped edits.

## Errors

- `_error` latches `(kind, error)`; `view_update`/`view_draw` drain it
  via `pop_error()` -> `LuxCoreErrorLog` + stats line.
- `start`/`config` failures set `engine.viewport_fatal_error` so the
  viewport stops retrying (persistent errors like a bad engine name).
- `edit`/`parse` failures drop the session (`_publish(None)`) — the next
  `view_update` sees `engine.session is None` and re-exports fresh.
- `engine.reset()` clears `viewport_fatal_error` on every fresh start —
  recovery beats a permanently dead viewport.

## RecordedScene contract

`Exporter.update()` never touches the live scene. It runs the normal
conversion code against `export/recorded_scene.RecordedScene`, which
records `(name, args, kwargs)` and answers `Is*Defined` queries from a
conservative shadow state (unknown -> False -> caller re-defines, which
is safe; a false positive would corrupt the scene). The worker replays
the ops inside one `BeginSceneEdit`/`EndSceneEdit`. When adding a scene
call to the update path, it must be in `RecordedScene._MUTATORS` (or a
shadow query) — otherwise it raises AttributeError on the proxy.

## Framebuffer (draw/viewport.py)

- Film readback (`GetOutputFloat` = device drain + imagepipeline) runs
  on a readback thread (`update_async`); results are consumed on the
  main thread and stale results (session swapped meanwhile) are dropped.
- `begin_reset(hold_s)` holds the last visible frame while the new
  render has no samples yet — no black flash on camera orbit, resize,
  or session restart. `HOLD_LAST_FRAME_MAX_S` (0.5s) bounds generic
  edits; camera moves pass `HOLD_LAST_FRAME_CAMERA_S` (0.15s) because
  the old viewpoint is wrong — the deadline is anchored at the FIRST
  reset of a burst so a sustained orbit can't pin a stale view forever.
- Stale-read rejection uses `worker.mutation_seq` (bumped under
  `session_lock` after every applied edit/config/parse/start): a read
  records the counter at kickoff and, because reads and edits are
  mutually exclusive under the lock, `box.mut_seq > _reset_mut_seq`
  (captured at `begin_reset`) proves the read ran after the pending
  edit landed. Only post-reset *non-empty* reads satisfy the pending
  hold; newer-but-pre-reset reads still update the held frame, so a
  sustained orbit tracks live instead of freezing. Do NOT key this on
  a per-reset counter — `begin_reset` runs every draw during a drag and
  would reject every in-flight read (the viewport froze on the stale
  frame: the "sluggish orbit" bug).
- Measured on M5 Pro (default cube, RTPATHOCL): camera edit submit→apply
  ~1ms, apply→first post-reset sample ~56ms — so ~100-150ms end-to-end
  tracking during orbit once the stale-read bug is gone.
- Edit pacing (`_submit_jobs` in `engine/viewport.py`): every applied
  scene edit resets the film at the next render-thread frame boundary,
  and the film is empty until the first preview pass splats. Orbit
  fires one edit per draw (~16 ms) which would leave the film empty
  for the whole drag. While `_pending_reset` is still waiting for
  content, edits are deferred on `engine._deferred_edit_jobs` (newest
  wins) and flushed when the pending frame lands - the effective edit
  rate adapts to scene speed. The flush is bounded by the pending
  deadline and keeps `tag_redraw` alive so a paused session can't
  starve the deferred final position.
- RTPATHOCL tuning (export/config.py): `resolutionreduction.preview=8`
  (1/64-res first pass after each reset, splats blocks at weight 0.001)
  and `resolutionreduction=4` (steady passes cover 1/16 of the film per
  pass). Edits apply only at frame boundaries, so pass duration IS the
  edit latency floor: ~180 ms/reset at reduction 2 → ~56 ms at 4.
- Readback cadence: 20 Hz for `FAST_READ_WINDOW_S` (1.5s) after each
  reset/first start so the first recognizable frame lands early, then
  10 Hz steady state.
- Readback/denoiser workers deliberately never reference the
  FrameBuffer or engine objects (module-level `run_denoiser`/`_fetch_pixels`
  take plain args + a box dict): a thread outliving teardown would
  otherwise free GL objects off the main thread = crash.
- All main-thread session calls go through `_locked_session_call`
  (non-blocking `session_lock`): a worker mid-`Stop`/`BeginSceneEdit`
  makes view_draw skip the call that frame instead of racing a torn
  session (the Solid<->Rendered switch crash) or freezing behind it.
- OIDN denoise runs off-thread (`run_denoiser`); GL upload stays on the
  main thread (`consume_denoise_result`). Results pinned to the paused
  session are ignored if the session was replaced.
- `view_draw` snapshots `engine.session` once per draw: the worker may
  publish None mid-draw, and a stale-but-alive session degrades to a
  caught RuntimeError instead of AttributeError.

## Camera view math (utils, export/camera.py)

Verified against the Blender 5.2.2 source clone (`blender-5.2`,
`source/blender/blenkernel/intern/camera.cc`, `view3d_draw.cc`):

- `zoom` = `CAMERA_PARAM_ZOOM_INIT_CAMOB / BKE_screen_view3d_zoom_to_fac(camzoom)`
  = `4 / (sqrt(2) + camzoom/50)^2`; `shiftx *= zoomfac`,
  `offset = 2*camd*zoomfac` before `compute_viewplane`.
- `BKE_camera_sensor_size` uses the RAW fit: AUTO always reads
  `sensor_width` (only explicit VERTICAL reads `sensor_height`) — while
  `BKE_camera_sensor_fit` resolves the fit AXIS on the frame/region
  aspect. `_fieldofview_deg` therefore always uses sensor_width for AUTO
  even on portrait frames.
- `calc_screenwindow`: no-border camera view normalizes by the region
  dims along the resolved fit (unit pixel aspect, matching
  `compute_viewplane(winx, winy, 1, 1)`); border view uses the camera's
  own viewplane (render dims + pixel aspect) lerped by the border —
  camzoom/view_camera_offset do NOT enter it (they only move the drawn
  quad), so zooming the camera view no longer restarts the render.
- `calc_filmsize` for camera+border anchors on the fitted frame size —
  zoom-independent, so no session restart on camzoom.
- `calc_camera_frame_rect` replicates `view3d_camera_border()`: maps the
  camera viewplane into the viewport viewplane; the draw quad tracks
  camzoom/pan instantly via `FrameBuffer.sync_view_transform` (6-vertex
  batch rebuild only).
- Regression: `dev-tools/camera_viewplane_test.py` (Blender -b) compares
  all of the above against an independent faithful port over ~6.5k
  cases: sensor fits, persp+ortho, pixel aspects, shifts, pans, zooms.

## Halt timer / pause lockup

`viewport_start_time` must be re-anchored whenever rendering resumes:

- at submit time in `view_update`/`view_draw` (covers the common case),
- and inside `SessionWorker._do_edit` at the actual resume point —
  a queued edit that lands after `halt_time` elapsed would otherwise be
  re-paused by the very next `view_draw` (resume->pause loop = black,
  unresponsive viewport).

## Dynamic resolution switching (edit latency floor)

`rtpath.resolutionreduction` only selects which pixels a pass covers -
every splat still carries weight 1.0, so it can be changed mid-accumulation
with zero bias. LuxCore exposes this as
`session.SetRuntimeResolutionReduction(n)`: an atomic store the render
threads pick up at the next frame boundary and re-upload into their
per-device `taskConfigBuff` (values below the configured reduction are
clamped to it: task buffers were sized `pixels / configured^2`, so denser
sampling at runtime would leave part of the film unsampled).

The viewport drives it automatically: every scene-edit submission raises
the override to `_DYN_RES_VALUE = 16`, and 0.4 s after edits stop the
worker restores 0 (= configured value). During a drag each pass covers
1/256 of the film (~2-5 ms instead of ~40 ms), so both the
frame-boundary wait before an edit applies and the first-post-reset
sample latency shrink. Measured on M5 Pro (1920x1080, camera edits):
submit->first-sample ~55 ms baseline -> ~26 ms with override=16.

Gotcha found by lldb: `EnqueueWriteBuffer` on Metal is a host memcpy;
the blocking variant drains the queue, and the upload must not race a
kernel reading `taskConfigBuff` - the apply point sits between the
frameBarrier and thread-0 edit handling, after `FinishQueue`.

At override 32 an intermittent Metal queue wedge was observed
(render thread parked on `-[MTLCommandBuffer initWithQueue]` semaphore
mid-pass; film frozen at 0, engine not done/paused). 16 is both stable
(60+ edits, zero wedges) and measurably faster than 32 - sparse coverage
below ~2k live tasks underutilizes the GPU while per-pass fixed cost
dominates.

## Status line

`worker.phase` carries "Exporting scene" / "Creating render config" /
"Compiling kernels (i/n)" / "Starting session" / "Applying scene edits"
/ "Reconfiguring session" into `update_stats`, so a long compile shows
progress instead of a frozen frame.

## Tests

- `dev-tools/async_worker_unit_test.py` — worker + RecordedScene, plain
  python3 (no Blender), fake pyluxcore.
- `dev-tools/async_session_e2e_test.py` — real Blender -b + real
  pyluxcore: start/edit/config/burst/bad-config/stop.
- `dev-tools/viewport_latency_bench.py` — edit->first-sample latency;
  `-- RTPATHOCL dyn` exercises the runtime resolution override.
- LuxCore `pyunittests/.../testrtpathocldynres.py` — override keeps the
  film accumulating, clamps below configured, recovers after mid-burst
  edits.
