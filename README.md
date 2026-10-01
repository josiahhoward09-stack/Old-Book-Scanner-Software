# Book Scanner Software

This repository contains the Book Scanner Universal Node Controller V6.

## Local HTML preview

From this directory, run:

```bash
python3 preview_server.py
```

Open [http://127.0.0.1:8093](http://127.0.0.1:8093) in a browser. When the HTML is updated, save the file and refresh that page. The local preview shows the interface but does not connect to Raspberry Pi hardware.

## Raspberry Pi installation

The original deployment files are included in `server.py` and `install.sh`. See `README.txt` for the Pi installation and network requirements.

## Camera service

### Capture Studio controls

- Click a gallery photo to open the full-screen inspector. Scroll or use +/−
  to zoom, drag to pan, use Fit or 1:1, and use the arrows to browse photos.
- Move to Trash removes a photo from the gallery without permanently deleting
  it. Open Trash and choose Restore to bring it back. This affects the node
  editor's copy; older control interfaces may retain their own capture copies.
- Camera nodes default to **64 MP / Maximum (9248 × 6944)**. Existing saved
  graphs retain their selected resolution. The sensor stays in its native full
  resolution instead of restarting for each photo.
- **Fast scanning · focus once** is the default focus mode. Autofocus runs once
  and holds its result between shots so page turns don't restart a full focus
  search. Use **Refocus & hold** after changing camera/book height or distance.
  This is a speed/position-stability trade-off: it does not track moving subjects.
  **Continuous · refocus as needed** remains available, but may hunt between shots.
- Watch for **64 MP ready · focus held for repeat shots**. A manual click and a node trigger
  use the same path: accept the next focused exposure after the request, then
  save it. An already-focused shot tested at roughly one second including JPEG
  saving. This is not a zero-latency guarantee: startup, a moved subject, low
  light, and an explicitly requested focus sweep can take substantially longer.
- Rapid manual clicks queue up to eight pending photos, processed one at a time.
  A queued photo is exposed when its turn arrives, not when it was clicked.
  Stop cancels waiting shots; an active exposure may finish. A capture error
  cancels waiting shots so the UI does not keep retrying unexpectedly. After
  completion you can keep adding shots; the queue never retains image buffers.
  Camera requests from competing clients are rejected as busy rather than
  overlapping. A disk-space guard stops capture before storage is exhausted.
- 1080p and 4K are cropped/resized from that same focused native exposure.
  Preview snapshot (1280 × 720) saves the current preview without checking focus.
- Keeping 64 MP ready reduces live-preview frame rate (about 2 fps on this
  OV64A40/Pi 5), but the preview continues during capture. Autofocus timeouts
  produce an error instead of silently delivering an unfocused photo.
- Capture feedback separates waiting for focus, photo captured/saving, and
  saved. The notification reports shutter latency and total saved time. Inspect
  fine text in the gallery; autofocus metadata is not a visual sharpness guarantee.
- Gold nodes are executing; green checkmarks show completed work; red labels
  show errors. The bottom bar lists all active nodes, including parallel work.
  Camera nodes and a capture notification show when capture starts and saves.
- Stop prevents new sequence actions and cancels delayed transitions. An
  already-started hardware operation may finish; manual capture still works.
- Drag empty canvas to pan; scroll empty canvas to zoom; Fit frames all nodes.

`index.html`, `studio.js`, and `studio.css` must be deployed together.

The node editor shares Camera 0 with the existing `bookscanner-pi-control.service`
on `127.0.0.1:8090`. The editor must not start its own `rpicam` process while
that service owns the camera. The service's maintained source is included in
`camera_service/pi_control_server.py`; its installed location on the Pi is
`/home/copycatbrain/pi-control/pi_control_server.py`.

`camera_service/camera_engine.py` is also installed in `/home/copycatbrain/pi-control`.
The Flask service supervises this persistent engine using `/usr/bin/python3`,
which has the Pi's Picamera2, libcamera, Pillow, and simplejpeg packages. It talks
over `camera-engine.sock` (0600); no additional public TCP port is opened. The
engine is the sole camera owner and uses three full-resolution YUV buffers,
not full-size RGB or raw copies. Successful captures log focus and timing data.

Preview requests reuse low-resolution frames from the same running camera.
Full-resolution photos use fresh, focused main-stream buffers; there are no
per-shot camera process launches or mode switches. Captures are copied into the node editor's
`captures` directory for Camera Gallery. Camera indices other than 0 currently
return an explicit unsupported-camera error.

The older `bookscanner-litegraph.service` also shares this camera owner. Its
source is `camera_service/pi_litegraph_server.py`, installed at
`/home/copycatbrain/pi-litegraph/pi_litegraph_server.py`. Do not reinstate its
old independent camera worker: it would compete for the camera again.

After changing the camera service, deploy both Python files to the installed location
and restart `bookscanner-pi-control.service`. After changing `server.py`, deploy
it to `/home/copycatbrain/universal-nodes/server.py` and restart
`bookscanner-universal-nodes.service`. `install.sh` installs the node editor;
it does not install the separate Flask camera service.
# Timelapse studio

Open **Timelapse** in the top toolbar. Choose camera 1, camera 2, or both;
photo interval in seconds; recording duration in minutes (up to 24 hours);
1080p, 4K, or 64 MP original photos; focus once, focus every shot, or autofocus
off (current lens position); and MP4 playback FPS (1–60).

Recording runs in the Pi server, not the browser. Closing the page does not
stop it. **Stop & build video** finishes in-flight exposures and exports early.
The duration controls when new exposures stop, not when encoding finishes.
Both cameras are scheduled concurrently, without hardware shutter synchronization.
If capture takes longer than the interval, missed slots are skipped, not queued.
Three consecutive failures disable the affected camera for that session.

Each camera produces a separate H.264 MP4, capped at 4K for playback compatibility.
64 MP originals remain in `timelapses/<session>/camera-N/` on the Pi. The panel
offers video playback and download. Interrupted sessions keep their photos and
can use **Build videos from saved photos** after a reboot; recording does not
automatically resume. Storage checks stop recording below 512 MiB free.
Manual capture, focus changes, and camera restart from this UI are blocked while
the recorder owns the cameras. The node graph Stop button does not stop timelapse.

The second OwlSight connector requires a matching boot overlay. For the confirmed
two-OV64A40 setup, `camera_service/enable_second_camera.py` validates the existing
configuration, backs it up, and adds CAM1 at the existing 360 MHz link frequency.
Run it with sudo on the Pi, then reboot. Never move camera ribbons while powered.
