# ARM Ripper Vision

A Docker companion for ARM Neu with local USB-camera recognition and user-maintained season masterlists.

Optional [Qwen image handoff](arm-season-queue/docs/vision-handoff.md) sends selected
captures to your configured llama.cpp/Ollama/image API and displays printed identity
fields for review. Menu capture, subtitle analysis and fingerprint lookup have
explicit future-extension stubs; they do not run yet. No reference-photo library
is required, and model observations never modify masterlists or authorize ripping.

**You supply and supplement the masterlist.** The camera adds confidence about the presented medium’s identity and printed contents by checking them against that list. It never creates, supplements or rewrites masterlist entries. Conflicts or uncertain recognition require review.

## Current implementation

The active Docker application is [arm-season-queue](arm-season-queue/README.md), version 0.1.1. It provides live webcam preview/capture, local OCR/barcode evidence, manual masterlist import/editing, persistent disc/job associations, ARM API metadata assignment and naming previews, and a verified-file handover contract/helper for a separate FileFlows application.

The previous insertion-order-only implementation remains under `arm_ripper_vision/` for migration/reference. Root Compose and Dockerfile now launch the camera companion. The old Python package entry point and Alembic database are legacy; they are not the current Docker runtime. Historical design documents are preserved in [docs/legacy](docs/legacy/).

## Start here

**Prebuilt containers:** the [Publish container workflow](.github/workflows/publish-image.yml)
builds, tests and publishes `ghcr.io/tammuzgreeen/arm-season-queue:main` (also
`latest` and immutable-by-convention `sha-<commit>` tags) for Linux amd64.
See [TrueNAS GUI installation](docs/truenas-first-test.md)
and the [camera-test Compose template](deploy/truenas-camera-test.yaml).
Wait for a successful publishing run before pulling a new image.

**Media scope:** TV matching/processing is implemented. Movies, music and
audiobooks have [development contracts and explicit stubs](docs/media-recognition-roadmap.md),
not working identification or queue support. Camera evidence never writes masterlists.

1. Read the [setup and operation guide](arm-season-queue/README.md).
2. Copy `.env.example` to `.env`; provide your ARM API address, queue password, storage paths and camera device/group. All are configurable; no credentials are supplied.
3. Verify the actual deployed ARM source/API with the [compatibility guide](arm-season-queue/docs/compatibility.md). Do not bypass the compatibility check based only on a version string.
4. Review the [migration notes](docs/migration-camera-companion.md) before replacing an older deployment.
5. With ARM idle, configure its lossless per-title path and persistent global pause, then run:

```sh
docker compose config --quiet
docker compose build queue
docker compose up -d queue
```

Default listener: `127.0.0.1:8080`. Set the bind address or use an HTTPS reverse proxy for remote access. Default UID/GID: `568:568`. The camera device is configurable, including a Logitech MX Brio; no optical drive, Docker socket or ARM database is mounted in the companion.

## Workflow

Supply and approve your masterlist → select a season → present its disc to the camera → remove it from view → insert it into ARM → repeat. Recognition checks the supplied list, and progress follows the recognised disc rather than assumed insertion order. Supplement or correct masterlists yourself; camera observations remain separate evidence.

[DS9 example](arm-season-queue/examples/ds9-season-2-part-1.yaml): Disc 1 mappings use the explicitly supplied evidence. Disc 2/3 source mappings remain unresolved. Photos cannot invent MakeMKV/DVD-title or angle mappings.

[Borgia and Voyager masterlist drafts](arm-season-queue/examples/masterlist-drafts/README.md) provide ten season examples with edition and numbering notes. They are **unapproved and not import-ready**: technical disc scans and review are still required.

## Verification and limits

32 companion tests pass, including a generated-media validation/copy/acknowledgement round trip. GitHub Actions builds and smoke-tests the container before publishing. **Physical webcam OCR, real ARM ripping and deployed FileFlows integration have not been qualified.** No direct TrueNAS access was used. No mock ARM service was built. See the [test report](arm-season-queue/docs/live-test-report.md).

The inspected ARM profile normally holds unassigned jobs through companion outage, but its pause-state database-error handling is not strictly fail-closed. That source limitation and ARM restart recovery are documented in the compatibility guide. Do not treat this as a production-qualified deployment.

FileFlows remains a separate application. The ready-manifest contract and publisher are supplied; a version-specific flow export awaits inspection and testing of the installed FileFlows version/nodes.

## Development

```sh
cd arm-season-queue
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

FFmpeg/ffprobe enable the real local generated-media test. The Docker image also installs Tesseract and ZBar. CI retains the legacy tests and adds the companion suite. See [formats](arm-season-queue/docs/formats.md), [camera setup](arm-season-queue/docs/camera.md), and [FileFlows contract](arm-season-queue/docs/fileflows.md).

License: [MIT](LICENSE).

