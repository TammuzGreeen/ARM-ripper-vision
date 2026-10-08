# Migrating the earlier helper

This update makes the prepared camera companion the default Docker runtime. It deliberately preserves the old source/history, but does not reuse its insertion-order assumptions, SQLite schema or masterlist format.

1. Stop the old helper while ARM is idle. Preserve its environment file, database, masterlists and prior ARM settings. Never run both controllers against the same ARM drive.
2. Create a fresh `.env` from the new example; old `ARV_*` settings do not configure this runtime. Set the backend `ARM_URL`, private queue password, device/group and paths. The listener changes from the earlier 8099 to configurable 8080 by default, bound to loopback.
3. Use a separate new state directory. Do not point it at the old helper’s SQLite database. Root Compose now uses service `queue`; the optional `publisher` tool is not a replacement FileFlows server.
4. Manually translate each desired series/season/edition into the documented schema. The old multi-season YAML remains preserved in `examples/masterlists/`. The current sample is under `arm-season-queue/examples/`. Do not infer DVD title numbers from old `source_title` values. Supply the actual mappings and source inventory yourself.
5. The camera only checks your list. Recognition cannot create entries or fill missing mappings. Import/review your edited masterlist through the new UI; discrepancies remain held.
6. Verify deployed ARM source/API, configure its per-title lossless settings and persistent global pause, and qualify the camera and one staging job. The new controller never automatically releases global pause on completion/shutdown.

Other deliberate behavior changes: selected titles outside the approved mapping are disabled for that queue-owned job; the new application does not inherit the old unrestricted-extra selection policy. No audio/subtitle streams are pruned by the companion. If extras are needed, establish explicit mappings or process them separately in ARM. The queue reads ARM staging media through a read-only mount for output validation; FileFlows/library publication has a separate write boundary.

## Rejected-disc review and intentional retry

A rejected capture remains audit-only when its correction is saved or resolved. To retry, capture the re-presented disc as a new event, save the correction against that fresh event, inspect its evidence, and explicitly authorize that exact capture against one approved masterlist disc before insertion. This human-confirmed path is separate from model agreement; no prior correction is silently transferred. The correction must match the selected approved disc and all normal ARM pause, insertion identity, mapping and validation checks still apply. See the active application's rejected-rip section for the operator sequence.

Rollback: stop the queue, retain its state/manifests/media, restore the previous Compose/environment from Git history and the old helper’s separate data. Restore ARM settings only while idle and deliberately manage global pause. Never delete media or replace state files as part of rollback.

Source/API checking, normal restart recovery and the ARM pause-read failure caveat remain mandatory reading in the current setup guide.
