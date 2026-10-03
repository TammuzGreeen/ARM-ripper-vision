# Changelog

## Manual camera testing

- Add explicit empty-view confirmation and one-frame manual capture; repeat captures without removal detection or container restarts.
- Default camera-only startup to manual testing; allow CAMERA_MODE and a runtime mode selector.
- Keep test snapshots separate from insertion pairing and ripping approval; reject stale/disconnected frames.
- Allow explicit empty-view reset to recover a stuck automatic detector. Failed recapture requests no longer invalidate evidence.
- Show retained images before a bounded OCR preview, with full diagnostic output in expandable details.
- Add nine regression tests for repeat capture, isolation, mode changes, stale frames and endpoint authentication.

## Camera companion integration (0.1.1)

- Make the prepared local USB-camera/OCR companion the default Docker runtime, retaining the previous helper under its existing package for reference/migration.
- Keep masterlists user-maintained. Camera/photo evidence only checks identity and printed contents; no recognition-to-masterlist generation or mutation exists.
- Add persistent associations, source-verified ARM integration, safe output validation and the separate FileFlows handover contract/helper.
- Add configurable deployment/camera examples, source verification, manual migration notes and the companion test suite to CI.
- Record 30 passing local companion tests; physical hardware, Docker deployment and actual ARM/FileFlows integration remain unverified.

## [0.1.0] - 2026-09-21
- Initial masterlist-driven controller skeleton.
- ARM API adapter, global hold, polling, queue state, Docker deployment, SQLite/Alembic, and v2 vision interface.


# Camera-only OCR fix

- Leave camera captures intact when ARM_URL is empty, instead of treating setup mode as an ARM outage.
- Retain late OCR results for inspection without restoring invalidated or expired insertion eligibility.
- Show detected text directly, including uncertain words for display only. Automatic matching still requires high-confidence text and an approved masterlist.
- Add regression coverage for camera-only polling, late results and uncertain-word separation.

