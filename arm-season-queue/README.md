# arm-season-queue

A local-first USB-camera companion for season-based ARM Neu ripping. You supply and supplement the masterlist. The camera adds confidence about the presented medium’s identity and printed contents by checking them against your list; it never generates or updates masterlist entries. Discrepancies require your review. Select an approved season, present a disc, remove it from the camera view, insert it, and repeat. ARM and FileFlows remain separate applications.

**Implementation status:** application, local webcam/OCR pipeline, browser interface, transactional queue, source-specific ARM adapter, Docker/TrueNAS example, and durable FileFlows handover helper are included. **This has not been deployed or tested against a physical webcam, ARM installation, or FileFlows installation.** No direct TrueNAS access was used. See [test report](docs/live-test-report.md).

The adapter currently targets inspected ARM Neu **19.1.0**, reference commit `f6ec2e3fd47cf951e89094e0a9d6999ab94d781f`. A version string alone is insufficient: startup of a batch requires a checked source profile and compatible live API/settings. An unknown deployment remains blocked from automatic starting. Other ARM versions need their own inspected adapter profile; changing an environment variable does not make an incompatible API supported.

## What v1 does

- Manual camera testing with explicit **View is empty** and **Capture disc now** controls. Repeat snapshots without restarting; test evidence never authorizes ripping. See [camera controls](docs/camera.md).

- Linux V4L2 USB camera capture, cropped live MJPEG preview, camera status, stable/sharp three-frame capture, local Tesseract German/English OCR, four rotations, local contrast enhancement and optional ZBar barcodes.
- Recognition extraction before matching. Multiple frames must agree on printed season/disc numbers and a unique approved title/edition. Unreadable, conflicting, unrelated or ambiguous evidence opens a concise review.
- User-maintained versioned JSON/YAML masterlists, manual import/editing and approval, explicit MakeMKV/DVD-title/ARM-track separation, out-of-order discs and separate editions.
- SQLite transactions, durable insertion associations, one active batch, retry/skip/cancel, independent rip and publication progress, protected evidence and restart recovery.
- Existing ARM APIs for per-job pause, metadata, track selection, names, preview and explicit start. No ARM database access or patches.
- Terminal-success checks, required-title and source-inventory validation, full video/audio decoding to a null sink, checksums, and atomic ready manifests. No media is transcoded or re-encoded for storage.
- A publication helper suitable for invocation from a FileFlows command/script node after that deployed node is verified. It checks media again, copies without overwriting, verifies SHA-256, retains sources and produces durable acknowledgements.

Scope is one camera, one optical drive and television season batches. `tv` is used for publication; existing `movies` and `music` directories are preserved. Movie/music automation, online metadata enrichment, cloud vision, visual-effects classification and arbitrary multi-camera queues are not implemented. The extraction results retain general title candidates, catalogue numbers, barcodes and language text, but do not claim general-purpose visual understanding.

## Setup on TrueNAS SCALE

Use a separate directory such as `/mnt/POOL/apps/arm-season-queue`. Keep the companion's state separate from existing ARM configuration.

1. Copy this project to that directory, then `cp .env.example .env`.
2. Fill `ARM_URL` with the ARM Neu **backend API origin**. Its Svelte UI may be a different service. Add `ARM_BEARER_TOKEN` only if your reverse proxy requires it. There are no built-in secrets.
3. Identify the actual ARM image/source and run the checks in [compatibility.md](docs/compatibility.md). Leave `ARM_VERIFIED_SOURCE_SHA` blank until the deployed source is verified.
4. Discover the Logitech MX Brio capture node and its group using [camera.md](docs/camera.md). Set `CAMERA_HOST_DEVICE` and numeric `CAMERA_GID`. The settings are configurable for other V4L2 webcams too.
5. Fill `ARM_MEDIA_HOST` with the existing ARM media host mount. `/media` in this container must correspond exactly to `ARM_MEDIA_PREFIX` inside ARM. Include ARM's finalised staging outputs, not just raw work files. Keep ARM's output outside the final Jellyfin library.
6. Create only the new state/handover directories and grant the configured UID/GID access through TrueNAS ACLs. Default is `568:568`. Do not recursively change ownership of existing datasets. SQLite state must be on a local filesystem, not an SMB/NFS mount.
7. Set `QUEUE_PASSWORD`. Configure `BIND_ADDRESS` to a chosen LAN address or leave loopback and use an HTTPS reverse proxy. HTTP Basic credentials need HTTPS on untrusted networks. The container does not expose ARM or bypass its network permissions.
8. Configure ARM's lossless settings and persistent global pause as described below. Start the companion before presenting the first test disc.

```sh
docker compose config --quiet
docker compose build queue
docker compose up -d queue
docker compose logs --tail=50 queue
```

This Compose example is for a directory-based Compose deployment on the NAS. A TrueNAS custom-app YAML editor cannot resolve a local build context unless that directory is available there; build/tag the image first, then use `image:` and the same mounts/devices in that editor. No privileged mode, optical device, Docker socket, or ARM database is mounted in the companion.

Set `LIBRARY_HOST` to your own media-library path; example paths contain placeholders. The queue has **no library write mount**; only the separately invoked publisher/FileFlows worker receives one.

## Prepare ARM once

On an idle designated test deployment, retain the current ARM configuration and effective MakeMKV preferences before editing. Existing optical mappings `/dev/sr0` and `/dev/sg2` stay in ARM.

```yaml
SKIP_TRANSCODE: true
RIPMETHOD: mkv
MAINFEATURE: false
MINLENGTH: 0
MAXLENGTH: 99998
DELRAWFILES: false
ALLOW_DUPLICATES: true
```

Use ARM **auto drive mode**, then enable ARM's persistent **global ripping pause** before inserting a disc. Do not use the manual-drive second wait as the controller's holding mechanism. Manual wait alone can expire; global pause prevents that normal timeout in the inspected source. Explicit per-job start bypasses global pause without releasing other jobs. The companion does not automatically toggle the global pause or edit global settings.

MakeMKV must retain all video/audio/subtitle streams (`+sel:all` in the applicable selection preference/profile). Confirm the effective preference in the actual deployment and validate its result against the source inventory. The public API does not prove the contents of that preference. Do not run a second optical scan while ARM is ripping.

`MAXLENGTH=99998` selects the source's per-title ripping path, which honours enabled flags; the normal `all` fast path does not. This is a path-selection requirement, not episode runtime matching. The companion verifies these values in both global and per-job configuration and never uses duration to reorder episodes.

Global pause applies to that ARM instance, including unrelated new jobs, so use a dedicated drive/test setup or coordinate this setting with other ARM users. The companion only assigns/starts jobs it has reserved on `ARM_DRIVE`. It never releases global pause on shutdown or cancellation. If someone disables it in ARM, or ARM's own pause-state database read fails, the companion cannot guarantee that ARM will keep unassigned jobs held. See the source limitation in compatibility notes.

## Normal operation

1. Open `http://<configured-address>:8080`, authenticate, and run **Check ARM connection**.
2. Import `examples/ds9-season-2-part-1.yaml` in the masterlist editor. Confirm the actual printed edition tokens; remove the masterlist-level unresolved question and set `approved: true`. Disc 1's mappings and source inventory are supplied. Discs 2/3 remain blocked until their own source mappings and inventories are established.
3. Clear the camera view and select **Calibrate empty view**. Do this after moving the camera or changing the background/light. Calibration is intentionally required after a camera reconnect or service restart.
4. Select the season once and start the batch. Existing ARM jobs are baselined and cannot consume a fresh capture automatically.
5. Present the printed disc face (or relevant cover/insert) with title, season, disc and edition visible. Wait for capture, remove it from view, then insert it into ARM. Captures expire after 180 seconds by default. OCR may finish after insertion; the association is already retained.
6. Watch rip and publication independently. A FileFlows delivery can finish while the next disc is being ripped. A different unprocessed disc in the same season uses its own entry, not the next position.

If an insertion cannot be paired, the job remains held. Present again, then use the event's **Confirm evidence** once, with the waiting ARM job ID and a short evidence note. Uploaded single photos require review; duplicating one photo never counts as independent-frame consensus. **Recapture** re-arms detection; first remove the previous item and wait for local OCR to finish.

Pause stops future configuration/start operations; ongoing ripping and validation continue. Cancel batch likewise preserves already-running work. Cancel waiting job only affects a queue-owned waiting job. Failed physical rips require re-presentation/reinsertion for a new ARM job; retrying a successful but unvalidated rip reruns output checks. An uncertain start response requires inspection and explicit retry, not a blind second start.

`BATCH_SWITCH_POLICY=review` is the default for a match in another loaded list. Select that batch and confirm its already-waiting job explicitly. `auto` switches to the matching approved list automatically and retains the previous batch's progress. Editing an imported masterlist does not mutate an existing batch snapshot; cancel that batch and start a new one for corrected mappings.

## FileFlows and formats

- [FileFlows integration and ready/ack contract](docs/fileflows.md)
- [Masterlist, recognition and association formats](docs/formats.md)
- [Machine-readable masterlist schema](docs/masterlist.schema.json)
- [ARM source/API compatibility](docs/compatibility.md)
- [USB webcam setup and practical recognition limits](docs/camera.md)
- [Verification results and remaining live tests](docs/live-test-report.md)

## Development and rollback

Python 3.12, `pip install -r requirements.txt`, then `python -m unittest discover -s tests -v`. FFmpeg/ffprobe enable the generated-media integration test; without them that test is reported skipped. Linux runtime also needs Tesseract `deu`/`eng`, ZBar and V4L2. `QUEUE_PASSWORD=... python -m app.main` starts one worker. Multiple web/controller workers are unsupported; do not increase Uvicorn's worker count.

Stop with `docker compose down` (without deleting volumes). Retain state and handover manifests for recovery. Restore only the ARM settings you deliberately changed, while idle; remove global pause only when you intend ARM to return to its ordinary behavior. Rollback does not delete camera evidence, staging media or library files. Keep evidence until successful publication has been checked. Unreferenced evidence expires after the configured retention period; reservations and imported-masterlist provenance protect their evidence indefinitely.

The Python dependency closure is pinned in `requirements.txt`; direct requirements are in `requirements.in`. The base image has a specific version tag. Debian OCR/media packages are resolved during image build, so record the built image digest and `dpkg-query -W` output when qualifying a deployment; the entire OS image is not claimed to be bit-reproducible.

