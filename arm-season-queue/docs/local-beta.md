# Local operational beta

`LOCAL_BETA_ENABLED=true` is an opt-in local execution mode for a single ARM
Neu drive. It requires `DRY_RUN_ONLY=false` and `FILEFLOWS_ENABLED=false`.
ARM remains the only optical scanner/ripper: its automatic prescan result is
reused; the companion never launches a second MakeMKV scan. The controller
requires the configured drive's current job, automatic drive mode, persistent
ARM global pause, exact job identity, matching insertion capture, compatible
per-title lossless configuration, sufficient free space, and no duplicate
reservation before it configures or starts a job.

## Selection and naming policy

- A fresh unrestricted session has no required series, season, disc, starting
  cursor, or pinned masterlist. End the old batch with **Cancel batch**, then use
  **Start fresh unrestricted beta session**. Activation requires a connected
  Automatic-mode camera, a reliably closed tray, the verified ARM profile and
  global pause, and no current/active ARM job. It preserves old batches, events,
  reservations, files and assessments. Masterlists are searched only after
  image-only model extraction and may optionally enrich a compatible insertion;
  no-match and different-series results are normal, not “wrong batch” failures.
  Each fresh insertion keeps its own event, identity observations, model inputs
  and optional lookup result.

- A unique compatible **approved, scan-backed** masterlist match supplies its
  explicit title selection and names. Its unselected source IDs are recorded
  with a reason.
- Otherwise, every ARM-prescanned source title with a valid nonzero duration
  in the supported per-title range is selected. No title is silently dropped;
  invalid technical source data fails the job visibly.
- By default, unmapped titles remain provisional. The explicit opt-in
  `ARM_AUTO_ORDINAL_MAPPING=true` permits sequential assignments only when
  recognition passes its field-evidence gate, the season and contiguous printed
  episode range are complete, and the count of ARM titles at least 30 seconds
  long equals the episode count. Titles shorter than 30 seconds remain
  provisional and do not shift the sequence. This duration threshold is a
  heuristic, not MakeMKV proof that a title is an episode.
- Ordinal assignments are always labeled `inferred_unverified`; the source
  MakeMKV IDs, title scan metadata, observed identity/range, algorithm, counts,
  and warning are retained. Equal counts do not prove episode identity or order.
  Inspect inferred filenames against the retained disc images and use review
  corrections to fix them. A compatible approved mapping takes precedence; any
  existing match prevents ordinal inference. The feature never writes to a
  masterlist.
- The owner-confirmed ordinal convention is scoped to one unique DVD episode
  source per printed episode. ARM MakeMKV IDs are sorted as actual source IDs
  (which restart per disc); an independent one-based ordinal position records
  their order in the disc's ordered episode list. DVD title numbers remain
  unknown unless ARM explicitly supplies them and are never computed as
  MakeMKV ID + 1. A Blu-ray/playlist, explicit angle ambiguity, duplicate-looking
  titles, long extras/count mismatch or incomplete evidence stays provisional.
- Unmapped titles receive a unique `Source Title NNN - PROVISIONAL` name in a
  job-specific output directory. The output remains local and awaits review.
- Review corrections are append-only in the per-job feedback history. Filename
  updates are restricted to the job directory, reject collisions, use a
  same-directory two-phase rename, and preserve the prior name and mapping.
- Checking “Episode mapping” during review records the operator's verification;
  without that review, inferred assignments remain unverified.
- Local beta validation checks that the output exists, has a video stream,
  duration is consistent with ARM's scan, and decodes. Where no approved source
  inventory exists, the report explicitly says inventory is unknown rather than
  claiming it matched.

## Automatic tray preparation

`TRAY_MONITOR_ENABLED=true` enables a read-only `CDROM_DRIVE_STATUS` ioctl on
only `TRAY_MONITOR_DEVICE`, which must be the configured ARM drive. The queue
container receives that block device with read-only device permission and the
host optical group. It does not read sectors, close/eject the drive, or launch
a scan to learn state. `CDS_NO_DISC` is a **closed** tray with no medium, not an
open tray. Unavailable, moving and unrecognized states are reported as unknown;
three consistent samples (0.5 seconds apart by default) debounce transitions.

In Automatic camera mode, a newly observed closed-to-open transition starts a
preparation cycle. After a one-second settling interval, 12 stable frames are
collected and frozen as the private empty reference. Molded tray rings are not
treated as a disc; the helper compares the live scene with the opening frame or
previously saved empty reference. A scene change before calibration completes
blocks capture and is reported as early placement. The GUI says **Calibrating**
then **Ready—place disc**; media presentation is captured automatically. The
baseline is immutable while waiting for media. A saved baseline is validated
before it is refreshed, so a disc-containing view cannot replace it as empty.
An already-open tray at startup is not treated as a new opening. If the first
calibration is interrupted by a restart, close and reopen the tray empty rather
than resuming an unanchored scene.

ARM AUTO_EJECT owns the opening after a completed rip. The helper waits for the
finished disc to be removed, then asks the operator to close the tray manually;
it does **not** calibrate on that automatic post-rip opening. The next manual
empty-tray opening starts preparation. Read-only tray state is obtained from
the helper's Linux tray ioctl on the configured drive; ARM's source-verified
API is used only for the supported close operation after evidence is saved.

For a fresh automatic capture, the original and cropped frames are persisted
and flushed before the companion invokes ARM's explicit `close` API. That ARM
operation owns closure and starts ARM's prescan; recognition proceeds from the
retained frames while ARM scans. Only after recognition and the matching ARM
prescan are ready does the queue configure and explicitly start the reserved
per-job rip. Global ARM ripping pause stays enabled; the per-job start is the
only start path. After success, ARM AUTO_EJECT remains the sole automatic eject
owner. The GUI shows tray state and the current preparation/rip/review stage.

Do not place a hand in the mechanism when automatic close is enabled. Verify
camera framing and the disc-removal detector on the first physical cycle; these
are software safeguards, not proof of physical correctness. `LOCAL_BETA_TRAY_CYCLE_ENABLED`
does not issue tray-open commands; manual opening is detected by the read-only
monitor.

## Follow-up (not implemented)

Evaluate an optional GPT vision fallback through the API in a separate task.
Verify the exact available model ID and configuration at that time. This
follow-up adds no API dependency or external image processing to the current
deployment; current images remain local and FileFlows stays disabled.

## Review and feedback

The authenticated GUI has a local review list. Operators can mark each completed
result **correct** or **incorrect**, save corrections, or leave it unassessed.
Unassessed outputs are never described as correct. These actions do not start
another rip, delete media, or dispatch downstream work. Original captures, model inputs/responses,
model tags/digests, normalized recognition, optional masterlist provenance,
ARM source list, actual outputs, checksums, and successive review revisions
remain in private state. Review is not model training.

Generate a private evaluation snapshot without invoking a model:

```sh
python -m tools.evaluate_local_beta_feedback --state /path/to/private/state \
  --output /path/to/private/reports/local-beta-feedback.json
```

The job-ID hash split holds out approximately 20% of reviewed cases as an
evaluation set; do not use those cases while tuning a parser, prompt, or matcher.
The report includes raw model transcriptions, combined fields, human-corrected
values, model versions, and correction counts. To compare a proposed parser,
prompt, or matcher, run it against the development examples and save candidate
results in this schema (keyed by job and source-title ID):

```json
{"jobs":{"123":{"identity":{"series":"Example","season":2},
  "titles":{"0":{"episode_number":3,"episode_name":"Example episode"}}}}}
```

Then pass `--predictions candidate-results.json`. The command reports exact
matches only for fields a human actually corrected, separately for development
and held-out evaluation jobs. It does not call Qwen or train/change any model.
Keep reports and candidate files outside Git.

## Recovery

Before an application/schema migration, stop the queue and take a SQLite online
backup (`sqlite3.Connection.backup`) plus a copy of the private evidence and
media directories. To roll back after a schema change, stop the new application
and restore the matching pre-migration database and its evidence tree together;
do not point an older image at a database that has already been migrated by a
newer image. Restore the matching Compose/environment and image digest, then
restart ARM with no disc in the drive and verify the global pause before
enabling the queue. Do not remove existing media as part of rollback.

Software tests do not qualify a physical camera insertion, optical read, or
ARM rip. The first beta batch must verify those on hardware; all files remain
local pending review.
