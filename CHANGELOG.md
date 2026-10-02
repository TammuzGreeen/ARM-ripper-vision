# Changelog

## Camera companion integration (0.1.1)

- Make the prepared local USB-camera/OCR companion the default Docker runtime, retaining the previous helper under its existing package for reference/migration.
- Keep masterlists user-maintained. Camera/photo evidence only checks identity and printed contents; no recognition-to-masterlist generation or mutation exists.
- Add persistent associations, source-verified ARM integration, safe output validation and the separate FileFlows handover contract/helper.
- Add configurable deployment/camera examples, source verification, manual migration notes and the companion test suite to CI.
- Record 30 passing local companion tests; physical hardware, Docker deployment and actual ARM/FileFlows integration remain unverified.

## [0.1.0] - 2026-09-21
- Initial masterlist-driven controller skeleton.
- ARM API adapter, global hold, polling, queue state, Docker deployment, SQLite/Alembic, and v2 vision interface.

