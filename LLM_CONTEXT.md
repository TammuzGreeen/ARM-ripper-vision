# Current project context

## Current scope

The user’s current requirements supersede the historical insertion-order-only plan, which is retained in `docs/legacy/LLM_CONTEXT.md` for reference.

- The user supplies and supplements masterlists. Camera/photo observations are evidence for media identity/content checks only. Never generate or edit masterlist entries from recognition.
- USB camera capture and local OCR are part of the active implementation; they are not deferred to a later vision release.
- The active Docker application is `arm-season-queue/` (0.1.1). Root Compose/Dockerfile run it. `arm_ripper_vision/`, its old CLI and Alembic schema remain legacy reference code.
- ARM owns optical ripping and source stream preservation. The companion does not transcode, use ARM’s database, mount the optical drive, or access the Docker socket.
- Use the existing ARM API; verify the actual deployed source/API before enabling automatic starts. The inspected source profile is not proof of any deployed version.
- Connections, credentials, device paths, UID/GID and storage are configurable. Never commit secrets. Do not access an operator's NAS without explicit authorization.
- One camera, one configured drive, one active season batch. Camera evidence takes priority over expected insertion order; uncertainties stay held for review.
- An explicit source mapping and complete evidenced source inventory are required before processing a disc. Do not invent mappings from photos, runtime or title index arithmetic.
- The user expanded scope to a separate FileFlows handover contract/helper. Do not claim a tested FileFlows flow export without examining its deployed version and nodes.
- Keep live/hardware validation limitations explicit. No ARM API simulator is included. Local tests use parsing/state operations and generated media.

## Storage and migration

Current batches snapshot the approved user-supplied list for reproducibility; recognition never changes it. User edits apply to a new batch. State is separate from ARM and from the older helper’s database. Do not auto-convert old source-title IDs into DVD-title numbers or reuse its runtime database.

The new controller requires ARM global pause to be established in advance and does not release it on shutdown. Recovery, per-title selection, and the inspected source’s pause-database-error limitation are documented. These differ from the earlier automatic global-hold restoration design.

Read `arm-season-queue/README.md`, `arm-season-queue/docs/compatibility.md`, `docs/migration-camera-companion.md` and the current test report before changing integration behavior. Test the relevant change and distinguish local checks from physical-disc/camera/deployment evidence.
