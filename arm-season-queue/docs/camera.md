# USB camera setup

The container uses Linux V4L2, with MJPEG requested at 3840×2160 / 15fps by default. This is a requested configuration, not a claim that the MX Brio has been exercised here. Preview reports the actual received resolution. Change the dimensions/FPS to a format the camera advertises.

On the TrueNAS host, inspect devices read-only:

```sh
ls -l /dev/v4l/by-id/ /dev/v4l/by-path/
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --list-formats-ext
stat -Lc '%n uid=%u gid=%g mode=%a' /dev/v4l/by-id/YOUR_CAPTURE_NODE
```

If `v4l2-ctl` is unavailable on the host, the image includes it; use a temporary container with just the discovered video node and numeric video group. Do not modify the TrueNAS appliance's base packages solely for this task. One webcam may expose multiple `/dev/video*` nodes; choose the image capture node, not a metadata-only node. Prefer a `/dev/v4l/by-id/...-video-index0` path after confirming its formats, or by-path if the camera lacks a stable unique ID. The real path is intentionally left blank in `.env.example`.

Set the host path in `CAMERA_HOST_DEVICE`; Compose maps it to `/dev/video0` in the companion. Add the host node's numeric group as `CAMERA_GID`, independently from application UID/GID 568. Use a dedicated USB 3 port/cable for 4K. If unplugging changes the device major/minor, recreate the queue container after reconnecting so Docker remaps it. Do not use privileged mode or map all of `/dev`.

## Positioning and capture

For testing, select **Manual test** in the web UI. Clear the scene and click
**View is empty**; this saves a background without running OCR. Place the disc,
wait for focus and click **Capture disc now**. This freezes one frame at the click.
Inspect the retained image before reading the OCR text. When recognition finishes,
capture again or swap discs and capture again without restarting the container.
Manual mode never triggers OCR from changes in the preview. Its snapshots cannot
pair with insertions or authorize ripping, and never change the masterlist.

Set `CAMERA_MODE=manual` or `auto` to select a startup mode. When omitted, an empty
`ARM_URL` defaults to manual; a configured ARM URL defaults to auto. Web UI mode
changes last until restart, invalidate unused evidence and require a new empty
baseline. Disconnected/stale frames and simultaneous captures are rejected.

In **Automatic** mode, if detection gets stuck after OCR finishes, clear the
actual view and click **Reset empty view**. This resets detection and invalidates
unused evidence without restarting or running OCR on the empty scene.

Manual tests retain one cropped JPEG per snapshot; automatic events retain three.
The description below applies to automatic detection.

Place the camera above a matte, contrasting, stationary surface. Keep the camera's view on the disc or packaging. Use diffuse side lighting; printed discs are reflective. Avoid aiming a lamp directly along the lens axis. Keep text large enough to read; for tiny labels, move the camera closer rather than expecting software to reconstruct missing detail. Autofocus must settle before capture. Capture detection uses a calibrated empty background, so changing lighting or moving the camera calls for recalibration.

`CAMERA_ROI=x1,y1,x2,y2` is a fractional crop of the incoming image, default `0.15,0.1,0.85,0.9`. Both preview and retained evidence use this crop. The default sharpness threshold is a Laplacian variance of 100; tune it with real frames, as it is camera/resolution-dependent. Background difference locates a newly presented object; image stability, sharpness and coarse glare checks gate three successive frames. This detector is not semantic disc classification: identification comes from visible text/barcodes.

The media must leave the crop and the empty background return for approximately 1.2 seconds before insertion can pair automatically. A continuously visible disc cannot generate repeated assignments. A new presentation invalidates old unpaired captures even if its label proves unreadable. Restart invalidates unused captures and requires recalibration. Present only one physical disc's evidence at a time and insert that same disc; one camera cannot cryptographically prove which object entered a drive.

## Local recognition and its limits

Tesseract reads German/English text with sparse-text segmentation, contrast enhancement and 0/90/180/270-degree rotations. At least two independent frames with high-confidence text and consistent numbers must match one approved title, season, disc and edition. The recogniser receives no masterlist, expected disc or online guess. Matching happens afterwards. Barcode decoding is optional: absent ZBar decoding must not silently substitute a guessed barcode.

This handles quarter-turn rotation and moderate contrast problems, not arbitrary perspective, severe glare, curved/occluded text or guaranteed 4K recognition. Reposition/re-present media when text is unreadable. Full arbitrary-angle deskew and learned visual label classification are not implemented. Episode/caption extraction is rule-based (e.g. `S02E01`, `Episodes 1–4`, `Folge 1`) and may miss unnumbered lists. These observations serve only as supporting evidence for comparison with your masterlist. They never generate or supplement the list; you make any corrections yourself.

Tesseract remains the default and sends no images over the network. Optional [Qwen image handoff](vision-handoff.md) sends selected captures to the explicitly configured vision endpoint, which can run on your LAN. It is disabled until configured and never authorizes ripping in this iteration. Never put provider keys or private deployment addresses in masterlists or source files.

The service stores three selected cropped JPEGs per camera event plus structured OCR results, never continuous video. Unreferenced evidence expires after `EVIDENCE_RETENTION_DAYS` (30 default); evidence referenced by reservations or masterlist provenance is protected. Retained OCR and identifiers stay in local SQLite. Preview/evidence require the same authentication as the interface.

