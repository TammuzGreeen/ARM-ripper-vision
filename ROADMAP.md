# Roadmap

## Current: camera-assisted verification of user-maintained masterlists

The active implementation is `arm-season-queue/`. Live USB camera capture, local OCR, evidence review, persistent associations, an ARM API adapter and FileFlows handover helper are included. The camera does not generate or supplement masterlists.

## Required deployment qualification

- Inspect the actual ARM source/API and qualify persistent holding, metadata readback, naming preview and explicit starts.
- Configure and test the MX Brio (or another V4L2 camera), including rotation, glare and unreadable labels.
- Complete user-supplied mappings/inventories for additional discs; do not infer them from photos.
- Run a designated complete lossless rip, verify all source tracks, and exercise failure/restart/reinsertion behavior without unrelated jobs.
- Inspect FileFlows version/nodes, configure the manifest flow, validate delivery and then export that tested flow.
- Build/run the Docker image and qualify TrueNAS permissions and actual Jellyfin version grouping.

## Later work

Add inspected ARM compatibility profiles, improve difficult-label recognition from real evidence, and evaluate multi-drive or native TrueNAS packaging only when requested. A future cloud backend requires explicit agreement before image uploads. Source deletion remains outside the current default; media is retained.

Historical milestones are preserved in `docs/legacy/ROADMAP.md`; they are not the current scope.
