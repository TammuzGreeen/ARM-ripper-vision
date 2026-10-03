# TrueNAS camera test

## Container image
The publishing workflow builds Linux amd64 images at
`ghcr.io/tammuzgreeen/arm-season-queue` with `main`, `latest` and
`sha-<full commit SHA>` tags after container tests and an HTTP startup check.
For public packages no registry token is needed. For a private package, use your
own GitHub account and a classic token with read:packages via TrueNAS registry
credentials or Docker's password-stdin login. Never put tokens in Git or YAML.

## Discover the camera
Run on the TrueNAS host:
```sh
ls -l /dev/video*
ls -l /dev/v4l/by-id/
```
Select an actual video-capture node, not a metadata-only node. Set CAMERA to the
observed device path and print its numeric group:
```sh
CAMERA='/dev/v4l/by-id/REPLACE_WITH_CAPTURE_DEVICE'
stat -Lc 'Camera GID: %g' "$CAMERA"
```
After pulling the image, inspect its supported modes:
```sh
sudo docker pull ghcr.io/tammuzgreeen/arm-season-queue:main
sudo docker run --rm --pull=never --user 568:568 \
  --group-add "$(stat -Lc '%g' "$CAMERA")" \
  --device "$CAMERA:/dev/video0:rw" \
  ghcr.io/tammuzgreeen/arm-season-queue:main \
  v4l2-ctl --device=/dev/video0 --list-formats-ext
```
The application requests MJPG. Select a listed width/height/FPS combination.
No camera model or serial number is assumed.

## Storage and GUI deployment
Copy [the template](../deploy/truenas-camera-test.yaml) into Apps > Discover Apps
> menu > Install via YAML. Replace the password, camera path, camera numeric GID
and all POOL/storage placeholders. Set the port to an available host port.
Create dedicated writable state/handover folders with access for UID/GID 568.
Do not change permissions on existing media recursively.

An ixVolume is also suitable for private application state when using TrueNAS's
guided Custom App installer; grant user 568 write access. Compose named volumes
are Docker-managed volumes, not the wizard's ixVolumes. A handover directory must
be accessible to the separate FileFlows worker when that integration is enabled.

The template pulls the public image (`pull_policy: always`). For a specific build,
replace `main` with its `sha-<full commit SHA>` tag.
Open `http://NAS_IP:8099`, log in as `operator`, and use **Manual test** mode:

1. Remove the disc and click **View is empty**. This saves the background without OCR.
2. Place the disc, wait for focus, and click **Capture disc now**.
3. Inspect the retained image and OCR preview. After OCR finishes, capture again
   or swap discs and capture again. No container restart is needed between tests.

Set `CAMERA_MODE=manual` in the GUI to keep this mode across restarts. If omitted,
an empty `ARM_URL` defaults to manual; a configured ARM URL defaults to automatic.
The web UI mode selector lasts until restart and requires a new empty baseline.
Manual snapshots cannot authorize ripping. No masterlist is needed for preview/OCR.
ARM-not-configured and unmatched recognition are expected. The first port in
`8099:8080` is configurable.

## Later ARM connection
Leave ARM_URL empty for the first camera test. Before ripping, identify the
deployed ARM version/source and follow the
[compatibility guide](../arm-season-queue/docs/compatibility.md). Do not copy a
source SHA just to bypass the attestation gate. The `latest` image tag does not
prove compatibility. An approved release-specific TV masterlist is required.

Set ARM_URL to the backend origin, not the separate UI. For separate Compose
applications, use the NAS address and published backend port, or the template's
host.docker.internal alias with that published port. On a shared Docker network,
use the backend service name and its internal port. Callback URLs follow the same
rule: service-to-service traffic uses the internal port.

Mount ARM's existing media directory read-only at /media, and set ARM_MEDIA_PREFIX
to the corresponding path inside ARM (commonly /home/arm/media). The camera-only
test does not need this mount; add it when integrating ARM. Optical drives and
the ARM database are not mounted into the companion.

Keep device serials, hostnames, private addresses, logs and live test reports in
your own private deployment notes, not in contributions to this repository.

References: [GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[TrueNAS custom apps](https://apps.truenas.com/managing-apps/installing-custom-apps/).

