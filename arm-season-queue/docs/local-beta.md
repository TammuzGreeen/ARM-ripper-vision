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

- A unique compatible **approved, scan-backed** masterlist match supplies its
  explicit title selection and names. Its unselected source IDs are recorded
  with a reason.
- Otherwise, every ARM-prescanned source title with a valid nonzero duration
  in the supported per-title range is selected. No title is silently dropped;
  invalid technical source data fails the job visibly. No episode identity or
  ordering is inferred from title count, duration, or position.
- Unmapped titles receive a unique `Source Title NNN - PROVISIONAL` name in a
  job-specific output directory. The output remains local and awaits review.
- Review corrections are append-only in the per-job feedback history. Filename
  updates are restricted to the job directory, reject collisions, use a
  same-directory two-phase rename, and preserve the prior name and mapping.
- Local beta validation checks that the output exists, has a video stream,
  duration is consistent with ARM's scan, and decodes. Where no approved source
  inventory exists, the report explicitly says inventory is unknown rather than
  claiming it matched.

## Review and feedback

The authenticated GUI has a local review list. **Accept**, **Save corrections
and mark reviewed**, and **Needs attention** do not start another rip, delete
media, or dispatch downstream work. Original captures, model inputs/responses,
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
