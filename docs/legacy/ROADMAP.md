# Roadmap

ARM Ripper Vision is being developed in stages. The early versions deliberately keep ARM Neu responsible for ripping and progressively add stronger identification and deployment features around it.

## v1.0 — Deterministic masterlist controller

Goal: reliably automate a single-drive TV box-set ripping session when the operator already knows which masterlist and disc position is being used.

### Controller

- [x] ARM HTTP API adapter.
- [x] Configurable ARM base URL and authentication.
- [x] Poll ARM for jobs.
- [x] Acquire the global ARM Auto-Start/ripping hold for controlled batches.
- [x] Snapshot existing ARM jobs so a newly created job can be associated with the expected disc.
- [x] Persist batch state in SQLite.
- [x] Require operator review after helper restart instead of silently resuming.
- [ ] Complete live ARM API validation against a standard ARM Neu installation.
- [ ] Verify and lock down the per-track update API route/payload.
- [ ] Complete operator Retry / Skip disc / Stop controls for failed jobs.
- [ ] Complete hold ownership/audit behavior and live failure testing.

### Masterlists

- [x] Versioned YAML schema.
- [x] Series-level metadata including optional IMDb ID.
- [x] Multiple seasons and discs in one series masterlist.
- [x] Episode number/title metadata.
- [x] Explicit MakeMKV source-title mappings.
- [x] Optional edition labels.
- [x] Optional known extras.
- [x] Example Deep Space Nine masterlist.
- [ ] Expand schema validation and actionable error messages.
- [ ] Add further real-world example masterlists.

### Track behavior

- [x] Prefer explicit source-title mapping.
- [x] Fall back to eligible ARM tracks in ascending source-title order in v1.
- [x] Set known episode number/title/custom filename.
- [x] Leave unrelated tracks untouched.
- [x] Allow ARM to rip/name unspecified extras according to ARM configuration.
- [ ] Complete explicit known-extra naming behavior.
- [ ] Read back edited ARM job/track data and validate it before starting the rip.

### Web UI and API

- [x] Basic trusted-LAN web UI.
- [x] Masterlist and starting-position selection.
- [x] FastAPI/OpenAPI foundation.
- [ ] Current batch/expected disc detail.
- [ ] ARM connectivity and hold status.
- [ ] Stop batch control in the UI.
- [ ] Restart/recovery decision UI.
- [ ] Failed-job Retry / Skip / Stop UI.
- [ ] Manual masterlist reload/status reporting.

### Deployment and quality

- [x] Dockerfile.
- [x] Docker Compose.
- [x] Non-root application user.
- [x] Container health check.
- [x] SQLite/Alembic persistence.
- [x] CI test workflow.
- [ ] Broaden mocked ARM API coverage.
- [ ] State-machine and restart/failure tests.
- [ ] Deployment and troubleshooting documentation.
- [ ] First tagged v1.0 release after live validation.

## v1.1 — Safer disc identification

Goal: reduce the risk of assigning metadata to the wrong physical disc without requiring a camera.

Planned evidence includes:

- disc CRC/fingerprint where available;
- disc label;
- title count;
- title durations;
- structural fingerprints;
- expected MakeMKV title layout;
- reinsertion detection;
- mismatch/ambiguity handling.

Unlike v1's insertion-order fallback, v1.1 should fail closed when available structural evidence conflicts with the expected masterlist disc.

The masterlist and persistence architecture should evolve without making this evidence mandatory for existing v1 masterlists.

## v2 — Vision and multi-drive

Goal: use physical-disc visual evidence as another independent signal and remove the one-drive architectural restriction.

Planned work:

- `DiscVisionProvider` implementation;
- webcam capture;
- OCR of disc/label text;
- recognition of visible series, season, disc, release, and catalog information;
- confidence/evidence reporting rather than opaque guesses;
- vision-assisted masterlist/position verification;
- multiple optical drives and independent per-drive state machines.

Multi-drive support will require reworking the v1 global ARM hold strategy because a single ARM-wide hold is too coarse for independent concurrent drive control.

Vision should strengthen deterministic masterlist control, not replace it.

## v3 — TrueNAS App

Goal: package the stable controller as a proper TrueNAS App.

Planned work:

- TrueNAS application metadata and templates;
- dataset configuration for persistent state and masterlists;
- ARM network/service configuration;
- optional camera/device configuration for v2 features;
- upgrade/migration path;
- health/status integration;
- installation and recovery documentation.

Generic Docker Compose remains the primary deployment path before this stage.

## Explicit non-goals

Unless the project scope changes, ARM Ripper Vision will not become a transcoder or media organizer. ARM/MakeMKV owns ripping and stream selection, and downstream tools such as FileFlows may handle later media processing independently.

The helper should not directly modify the ARM database, mount ARM's completed-media library merely to infer state, or silently change ARM ripping configuration.

