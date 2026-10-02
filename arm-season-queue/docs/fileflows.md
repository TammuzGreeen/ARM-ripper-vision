# FileFlows integration

No deployed FileFlows address/version/node inventory was provided. `FILEFLOWS_URL` and `FILEFLOWS_VERSION` are optional deployment-record variables; the controller does not call an undocumented FileFlows endpoint. No flow export is supplied or claimed tested. ARM, the queue and FileFlows remain separate applications.

The integration boundary is a mounted directory contract. FileFlows must discover completed `handover/ready/*.json` files, invoke the supplied publisher on the **manifest**, and treat exit code 0 plus its acknowledgement as completion. Configure the actual flow only after checking the installed version's available command/script nodes, executable environment and library filters. A JSON manifest must not enter a default video transcoding flow.

## Required worker environment

Provide Python 3.12 and FFmpeg/ffprobe on the FileFlows processing node. Copy `app/__init__.py`, `app/handover.py` and `app/publish.py` into `/opt/arm-season-queue/app/`; these modules use only the Python standard library plus the external FFmpeg tools. The full camera/recognition dependencies are not needed there.

Mount the same ARM media tree read-only as `/media`, handover as `/handover` read/write, and the final library as `/library` read/write. Map host paths according to the actual node; remote FileFlows nodes need equivalent accessible mounts. Grant the worker's UID/GID precise dataset access. It requires no optical device, camera, ARM DB, privileged mode or Docker socket.

For each discovered ready manifest, invoke with separately quoted arguments in the verified node:

```sh
cd /opt/arm-season-queue
python -m app.publish /handover/ready/123.json \
  --media-root /media --library /library --handover /handover
```

Replace `123.json` using that FileFlows version's real input-path variable; this project intentionally does not invent its expression syntax. A node that supports executable and argument fields is preferable to composing a shell string from paths. Flow failure must retain the JSON/source for retry. On success, mark the input processed in FileFlows rather than moving/deleting the queue's ready manifest, because the queue uses it to verify acknowledgements. Ignore `.pending-*`, `.partial`, `acks/` and media files in this manifest library.

For a controlled standalone delivery test, the optional Compose tool can execute the same helper:

```sh
docker compose --profile tools run --rm publisher /handover/ready/123.json
```

This tests the helper, **not** FileFlows integration. Do not label it a successful FileFlows run. A real flow run and its logs are still required.

## Ready manifest v1

Files are written in the handover filesystem to a temporary name, fsynced, then exposed with a no-replacement atomic hardlink. A differing existing manifest is an error. Use a local filesystem supporting hardlinks and fsync (e.g. the intended ZFS dataset); network shares with weaker semantics are not qualified.

Key fields:

```json
{
  "schema_version": 1,
  "status": "ready",
  "errors": [],
  "arm_status": "success",
  "batch_id": "batch-uuid",
  "arm_job_id": 123,
  "disc_id": "disc-1",
  "masterlist": {"...": "complete approved snapshot"},
  "masterlist_sha256": "canonical snapshot digest",
  "recognition_event": "event-uuid",
  "recognition": {"...": "frames, confirmed identity, matches and any human review"},
  "observed_label": "EU_103539",
  "structure_signature": "source-title structure digest",
  "source_titles": [],
  "source_inventory": {"complete": true, "evidence": "source scan", "...": "expected streams"},
  "outputs": [{
    "arm_track_id": 800,
    "makemkv_id": 0,
    "dvd_title": 1,
    "angle": null,
    "episode": {"printed": "1", "number": 1, "title": "Die Heimkehr"},
    "arm_output_path": "/home/arm/media/.../episode.mkv",
    "media_relative_path": ".../episode.mkv",
    "destination": "tv/Star Trek Deep Space Nine/Season 02/...mkv",
    "bytes": 123456,
    "sha256": "content digest",
    "scan_duration": 2614,
    "probe": {"...": "actual ffprobe result"},
    "validation": {"stream_inventory": "passed", "decode": "passed", "duration": 2618}
  }],
  "preserve_sources": true
}
```

This is an abbreviated shape illustration, not a runnable ready manifest. Actual records also include mapping evidence, scan snapshot, staging basename and tolerated duration difference. ARM staging filenames use short ASCII names such as `ASQ-S02-D01-T000.mkv`; FileFlows applies the full authoritative destination names. This avoids punctuation differences in ARM's sanitizer changing filenames unexpectedly. Paths are derived from terminal `job.path` plus the saved naming preview, not by guessing file order or trusting old ARM filename fields after finalisation. The prefix map is explicit: `ARM_MEDIA_PREFIX` in ARM corresponds to `/media` in the queue/worker.

Before ready publication, the controller requires successful ARM terminal state, every selected title marked ripped without a track error, existing nonempty actual outputs, full evidenced stream inventory, tolerant duration/chapters/video-format checks, a complete video/audio decode to a null sink, hashes and a final ARM completion recheck. Unchanged size by itself is never a completion signal. No compressed media is re-encoded for delivery.

## Worker behavior and acknowledgement

1. Read a v1 ready manifest with ARM `success` and no manifest errors.
2. Restrict source/destination paths to configured roots; reject traversal and escaping symlinks.
3. Re-run probing, required-track checks and video/audio decoding. Verify source sizes and SHA-256 against the manifest.
4. Copy into a temporary file **on the destination filesystem**, fsync and verify SHA-256.
5. Atomically link the complete temporary file to the final name without replacement. Existing identical content is an idempotent success; different content fails. Verify the final file again.
6. After all outputs succeed, atomically publish `handover/acks/<job>.json`:

```json
{
  "schema_version": 1,
  "manifest_sha256": "SHA-256 of canonical ready JSON",
  "job": 123,
  "batch": "batch-uuid",
  "status": "published",
  "outputs": [{"destination": "tv/...mkv", "sha256": "content digest"}],
  "sources_deleted": false
}
```

Canonical JSON uses sorted keys, UTF-8, unescaped Unicode, and separators `,` and `:`. The controller verifies acknowledgement version, job, batch, manifest hash and exact destination/hash list before recording publication.

Publication is atomic **per file**, not for a whole disc/season. A crash may leave a subset of verified final files and no acknowledgement. Retrying verifies existing identical content and finishes the remaining files. It never overwrites a conflict and never deletes the source. Optional source deletion is not implemented in v1; do it separately only after publication has been checked. Handover directories are trusted service-to-service storage: restrict write access to the queue and authorised FileFlows worker.

Remaining FileFlows work: record version; inspect actual node list and path variables; create the manifest library/filter and command flow; verify permissions and error routing; run one real job; then export that tested flow and record its version. The helper and contract are ready for that step, but do not substitute for it.
