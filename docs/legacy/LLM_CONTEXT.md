# LLM Project Context

This document is the canonical context handoff for an LLM or developer entering this repository. It records the project's purpose, the decisions made during initial discovery, the intended behavior by version, the known ARM Neu integration facts, and the boundaries that should not be silently changed.

## 1. Purpose

ARM Ripper Vision is a self-hosted controller/helper around **Automatic Ripping Machine Neu (ARM Neu)** for deterministic ripping of TV-series box sets and similarly complex releases.

ARM remains the ripping engine. It owns the optical drive, MakeMKV invocation, ripping configuration, stream selection/preservation, automatic extras behavior, and output placement.

ARM Ripper Vision adds a controlled batch workflow driven by human-maintained YAML masterlists. Its main job is to make sure known titles receive the correct identity and filename: series name/ID and, for episodes, season number, episode number, and episode title.

A typical use case is:

1. The operator wants to rip *Star Trek: Deep Space Nine*.
2. They start the helper and select the DS9 masterlist.
3. They optionally choose the season/disc at which to begin.
4. They start the batch and insert that disc.
5. ARM prescans it.
6. The helper associates the newly created ARM job with the expected masterlist disc.
7. Known episode tracks receive the correct metadata and filenames.
8. ARM performs the actual non-destructive rip using its own configuration.
9. After success the helper advances to the next masterlist disc and waits for it.
10. The process continues across discs and seasons until the masterlist ends or the operator stops it.

The masterlist is authoritative for known identity/naming information. It is **not** intended to become a replacement ARM configuration file.

## 2. Core principles

- Use ARM's public HTTP API wherever possible.
- Do not access or modify ARM's database directly.
- Do not patch ARM as part of normal operation.
- Do not mutate the user's NAS/media library during development.
- Keep destructive/live operations behind explicit, testable state transitions.
- ARM/MakeMKV remains responsible for the rip itself.
- The helper does not transcode.
- The helper does not prune audio or subtitle streams.
- Ripping is intended to remain non-destructive and preserve streams according to ARM/MakeMKV configuration.
- Unmapped ARM tracks must be left untouched.
- ARM may therefore continue to rip extras according to its own configured extras rules.
- Masterlists are plain YAML files in a mounted directory/dataset and are the authoritative source for their content.
- SQLite stores runtime/controller state, not authoritative masterlist data.
- v1 is deliberately simple and trusts operator-selected insertion order. Stronger evidence belongs to later versions.

## 3. Product/version plan

### v1 — deterministic insertion-order controller

v1 is the basic useful product.

It supports exactly one optical drive and one controlled batch. The operator selects a masterlist and starting position. The controller uses the next new ARM job as the next expected masterlist disc.

v1 does **not** try to prove that the physical disc is correct using CRC, duration fingerprints, OCR, or a webcam. The operator-selected masterlist position and insertion order are trusted.

### v1.1 — safer evidence-based matching

v1.1 adds additional evidence before trusting a disc:

- CRC/fingerprint where available;
- disc label;
- title count;
- title durations;
- structural/title-layout fingerprint;
- reinsertion detection;
- mismatch/ambiguity detection.

When available evidence conflicts with the expected disc, v1.1 should fail closed rather than silently applying the next masterlist entry.

### v2 — Vision and multi-drive

v2 adds webcam/vision as a stronger independent physical-disc identification signal. The architecture already contains a `DiscVisionProvider` abstraction so v1 can mock/disable vision without coupling the controller to camera code.

Vision may use OCR/visible text to help identify series, season, disc, release, or catalog information. It should strengthen deterministic masterlist matching, not replace the masterlist with opaque guesses.

v2 should also evolve the architecture to support multiple optical drives. The v1 ARM-wide global hold is too coarse for independent multi-drive control and must be redesigned/reworked at that point.

### v3 — TrueNAS App

v3 packages the mature service as a proper TrueNAS App. Until then, generic Docker Compose is the primary deployment target.

## 4. Masterlist decisions

There were no pre-existing masterlists. The project defines a new, versioned YAML schema.

A masterlist is **series/release-family centric**, not one file per individual season. Examples:

- `star-trek-tos.yaml`
- `star-trek-ds9.yaml`
- `star-trek-tng.yaml`
- `borgia.yaml`

One file may contain multiple seasons and multiple discs per season.

The current use case does not need optimization for owning several physical editions of the same series. The schema can evolve for release distinctions without making them central to v1.

### What belongs in the masterlist

The masterlist should contain only information the helper needs to identify/name known content, such as:

- stable masterlist ID;
- series name;
- optional series-level IMDb ID;
- optional year;
- season number;
- disc number;
- known episode number;
- known episode title;
- explicit MakeMKV/source title number when known;
- optional edition label where needed;
- optionally named/known extras.

Episode titles should be treated as user-authored masterlist data; the helper does not need an external metadata provider to supply them.

Provider IDs are series-level in v1. IMDb is the main desired optional identifier. Episode-level provider IDs are not required.

### What does NOT belong in the masterlist

The masterlist should not control general ripping settings such as:

- audio language/track selection;
- subtitle selection;
- transcoding;
- codec settings;
- ARM extras policy;
- general MakeMKV behavior.

Those settings belong in ARM/MakeMKV.

### Main-title mapping

Whenever known, use explicit MakeMKV source-title numbers to map disc titles to episodes.

For simple discs, this can be as direct as:

`source title 0 -> S02E01`

`source title 1 -> S02E02`

and so on.

If exact title IDs are unavailable or cannot be matched in **v1**, fall back to eligible titles in ascending ARM `track_number` order.

For the v1 fallback, an eligible title means that ARM exposes it as processable and it has no non-empty `skip_reason`.

v1.1 should become stricter and stop on structural mismatch instead of casually falling back.

### Extras

ARM already has its own configuration for ripping extras.

Therefore:

- explicitly known extras may be represented in the masterlist and named by the helper;
- extras not specified in the masterlist are left entirely to ARM;
- unmapped tracks are not disabled merely because they are absent from the masterlist;
- unspecified extras may keep ARM's own generated name and extras-folder behavior;
- extras should not be assigned fake episode numbers.

This distinction is important: the masterlist identifies known content; it is not an exhaustive list of every track ARM is allowed to rip.

## 5. Metadata and filename behavior

For mapped episode tracks, v1 should primarily set:

- `episode_number`;
- `episode_name`;
- `custom_filename`.

It should leave `enabled` unchanged unless an exact source-title mapping requires selecting a specific track that ARM has disabled.

The default filename stem is:

`Series Name - S02E05 - Episode Title`

An optional edition suffix is supported:

`Series Name - S01E01 - Episode Title [Remastered]`

The custom filename supplied to ARM is a **stem** and should not append `.mkv`.

The helper should also correct the ARM job's series-level identity/season/disc metadata as needed so ARM does not retain a bad automatic movie/series identification.

The helper itself does not query OMDb, TMDB, TVDB, IMDb, or another provider in v1. ARM may continue using its own configured metadata provider independently.

## 6. Non-destructive ripping requirement

The operator explicitly prefers a non-destructive rip including all subtitles and other source streams.

ARM Ripper Vision therefore must not introduce stream-pruning behavior. Audio, subtitle, MakeMKV arguments, transcode behavior, and similar rip configuration remain ARM/MakeMKV concerns.

The controller's responsibility is to identify/name known outputs and coordinate safe batch progression, not to alter the content of the rip.

## 7. ARM control model

v1 communicates with ARM **only through the HTTP API**.

No ARM completed-media mount is required for controller logic. No direct ARM database inspection is permitted.

The intended safe controlled-batch sequence is:

1. Operator starts a batch.
2. Helper confirms ARM API connectivity.
3. Helper records ARM's current global `ripping-enabled` / Auto-Start value.
4. Helper disables global Auto-Start.
5. Helper snapshots existing ARM jobs.
6. Operator inserts the expected disc.
7. ARM creates/prescans a new job.
8. Helper sees the first new job after the batch started and assigns it to the next expected masterlist position.
9. The job must be paused/manual-waiting before the helper edits it.
10. Helper writes known job/track metadata.
11. Helper should read the job back and validate that ARM accepted the intended changes before live start.
12. Helper explicitly starts the approved job.
13. ARM performs the rip.
14. On success, helper advances to the next masterlist position and waits for the next new job.
15. At end of the masterlist, helper restores the previous global Auto-Start state.
16. On an explicit normal stop, helper restores the previous global Auto-Start state.

The global hold is deliberately ARM-wide in v1. This is a conservative fail-safe but means v1 should be treated as a one-drive/one-controlled-batch system.

## 8. ARM configuration policy

The helper should **validate**, not silently rewrite, important ARM settings.

If ARM configuration is incompatible with deterministic operation, the helper should refuse/stop the batch and tell the operator what must be changed.

Important known examples:

- `ALLOW_DUPLICATES=true` is needed for TV box sets because ARM's duplicate check happens before the prescan/manual-wait stage.
- `MANUAL_WAIT=true` is expected for controlled jobs.
- `MAXLENGTH` must remain at or below `99998` to avoid ARM's MakeMKV "all titles" fast path.

The fast path matters because it can collapse multi-angle Blu-ray titles and bypass deterministic per-title selection. With a finite `MAXLENGTH <= 99998`, ARM uses the per-title path where `track.enabled` and explicit MakeMKV title numbers matter.

Do not auto-edit the user's ARM configuration to fix these settings.

There is intentionally no ARM version compatibility gate in v1. Isolate ARM-specific behavior in the adapter and make API paths configurable.

## 9. Known ARM API facts

The environment investigation established these ARM Neu API behaviors:

- `POST /api/v1/jobs/{id}/start` starts a manually paused job and sets manual start.
- `POST /api/v1/jobs/{id}/pause` pauses a job.
- `GET /api/v1/system/ripping-enabled` reads global Auto-Start/ripping state.
- `POST /api/v1/system/ripping-enabled` changes that state.
- `PUT /api/v1/jobs/{job_id}/title` edits job title/identity metadata.
- `GET /api/v1/jobs/{id}/detail` returns job, config, and tracks.
- Track editing supports fields including `enabled`, `filename`, `ripped`, `custom_filename`, `episode_number`, and `episode_name`.

ARM `track_number` corresponds to the MakeMKV source title ID.

ARM `track_id` is an ARM database/API record identifier and is **not** the MakeMKV title number.

The exact per-track update route was not conclusively established during discovery. It must remain configurable and be verified before first live use rather than guessed.

ARM API authentication is optional/configurable by environment variables. Default behavior is no authentication. The adapter may support common configurable auth forms without making any one of them mandatory.

Do not block v1 based on a specific ARM version number.

## 10. Polling and job association

v1 polls ARM every **3 seconds** by default. It does not depend on ARM webhooks.

At batch start, existing ARM jobs should be snapshotted/ignored. The first **new** ARM job detected after batch start is treated as the expected selected disc.

After a successful disc, the same rule applies: the next new ARM job is associated with the next expected masterlist disc.

This is intentionally simple v1 behavior. v1.1 and v2 add evidence that can reject a wrong disc.

## 11. Batch progression

A batch can start anywhere in the selected masterlist, for example DS9 Season 2 Disc 1, and then continue automatically through later discs and seasons.

After a successful rip:

- advance automatically;
- clear the current job association;
- wait indefinitely for the next disc;
- do not time out merely because no disc is inserted.

At the end of the masterlist, complete the batch and restore the pre-batch ARM global ripping state.

The operator can stop a batch and later start another masterlist or another position if the next physical disc is unavailable.

There is no per-disc approval during normal v1 operation after the operator has deliberately started the batch.

## 12. Failure behavior

A failed ARM job must **not** automatically advance the masterlist.

The web UI should require explicit operator input and provide these choices:

- Retry the same masterlist disc.
- Skip this disc and advance.
- Stop the batch.

The controller should keep the system in a safe held state while waiting for this input.

## 13. Restart/crash behavior

Runtime state is persisted in SQLite.

After a helper/container restart, an unfinished batch must **not** silently resume.

Instead:

- restore the persisted batch context;
- keep the system safe;
- mark the batch as requiring restart review/operator input;
- let the operator choose whether to resume, restart, or stop.

On a normal batch end/explicit stop, restore the ARM `ripping-enabled` state that existed before the helper acquired control.

After an abnormal helper restart/crash, do not silently restore or resume until the operator decides what to do. This avoids unexpectedly releasing ARM jobs after a controller failure.

## 14. Web UI

v1 includes a simple web UI.

Technology direction: FastAPI with a small server-rendered UI (Jinja/HTMX or equivalent simple server rendering). A separate React/Vue SPA is unnecessary.

The UI is intended for a **trusted LAN** and has **no login** in v1.

The UI should support the workflow/status/control functions, including:

- list/select masterlists;
- choose starting season/disc;
- start a controlled batch;
- show current batch and expected disc;
- show ARM connectivity/hold status;
- stop a batch;
- handle failed-job Retry / Skip / Stop;
- handle restart/recovery decisions.

Masterlists are **read-only in the web UI**. YAML editing happens externally in the mounted masterlist directory, for example with a text editor.

Application configuration also remains environment-variable based rather than becoming a full web settings editor.

## 15. API

FastAPI/OpenAPI should expose controller functionality as an API as well as the web UI.

Useful API areas include:

- health;
- ARM connectivity/status;
- masterlists;
- current batch/queue;
- start/stop batch;
- failure/recovery actions;
- future recognition/evidence information.

Future-version endpoints can evolve without pretending that v1 has working vision.

## 16. Persistence

Use SQLite with migrations in a configurable persistent data directory.

Persist enough information to safely understand controller history/state, including:

- batches;
- current masterlist position;
- current ARM job association;
- jobs already present/seen;
- previous ARM global hold state;
- failure/recovery state;
- timestamps;
- audit events.

Masterlist content itself remains authoritative YAML rather than being copied into SQLite as the primary source.

## 17. Vision abstraction

Vision is **architected now but implemented later**.

v1 should include a small `DiscVisionProvider` abstraction and a disabled/mock provider. Do not add live camera dependencies just to satisfy the interface.

v2 can add webcam capture, OCR, and physical-disc recognition behind that interface.

The future recognition system should expose evidence/confidence rather than silently deciding from a weak guess.

## 18. Deployment

v1 targets generic Docker Compose.

Requirements:

- Dockerfile;
- Compose file;
- environment-variable configuration;
- persistent data volume;
- read-only masterlist volume;
- health check;
- non-root application process;
- no Docker socket mount;
- no ARM media-library mount needed by the helper.

TrueNAS-specific packaging belongs to v3 rather than being mixed into v1.

## 19. FileFlows and media post-processing

FileFlows is explicitly outside v1 project scope.

ARM Ripper Vision's job is to help rip the correct files and name them correctly. Moving, transcoding, organizing, or publishing those files later can be handled independently by vanilla FileFlows or other tooling.

Do not add a FileFlows integration unless project scope is explicitly changed.

## 20. Real-world test/reference cases

These observations informed the architecture. They are useful as fixtures/examples but must **not** be hard-coded as universal behavior.

### Star Trek: Deep Space Nine Season 2

Disc labels alone are not reliable identity: tested discs shared a label while having different CRCs.

Known mappings:

**Disc 1**

- S02E01 — Die Heimkehr
- S02E02 — Der Kreis
- S02E03 — Die Belagerung
- S02E04 — Der Symbiont

**Disc 2**

- S02E05 — Die Konspiration
- S02E06 — Das Melora-Problem
- S02E07 — Profit oder Partner
- S02E08 — Die Ermittlung

**Disc 3**

- S02E09 — Rätselhafte Fenna
- S02E10 — Auge des Universums
- S02E11 — Rivalen
- S02E12 — Metamorphosen

The episode order follows ascending MakeMKV source-title order for these tested discs.

### Borgia

A Borgia Season 2 disc was automatically misidentified by ARM as a *Lucrezia Borgia* movie. Correcting the ARM job through its API worked. This demonstrates why the helper must be able to impose the known series/season/disc identity from the selected masterlist.

### Star Trek: The Original Series Blu-ray

A tested Season 1 Disc 1 had 67 MakeMKV titles and multiple angles for episode playlists. Example structure included pairs such as title 0/1 for two angles of one playlist, 2/3 for another, and so on.

ARM's fast "all titles" path preserved only part of this structure, demonstrating that multi-angle releases require explicit source-title/edition mapping and the per-title MakeMKV path. Duration alone is not a safe identity mechanism.

## 21. Example environment observed during discovery

The original test installation used ARM Neu 19.1.0 and MakeMKV 1.18.3, with ARM UI and API/ripper containers separated inside one ARM application.

Those values are **reference data only**. The project must work with other installations through configuration and must not hard-code the original hostnames, IP addresses, ports, device names, or filesystem layout.

The intended generic Docker default for communication with an ARM Compose service may be something like `http://arm-rippers:8080`, but `ARV_ARM_BASE_URL` must remain configurable.

## 22. Testing expectations

Prefer mocked ARM APIs and fixtures before live hardware testing.

Important test areas include:

- masterlist schema validation;
- simple DVD episode mapping;
- explicit source-title mapping;
- v1 ordered fallback;
- unmapped tracks remaining untouched;
- known and unknown extras behavior;
- custom filename generation;
- multi-angle/edition mappings;
- ARM job identity correction;
- global hold acquisition/restoration;
- queue advancement;
- end-of-masterlist completion;
- failed job requiring input;
- helper restart requiring review;
- mocked vision provider;
- API error handling;
- persistence/migrations.

Later versions should add structural mismatch, ambiguous-disc, reinsertion, OCR/vision, and multi-drive tests.

No live NAS mutation should be necessary for unit/integration tests.

## 23. Security/privacy assumptions

v1's web UI has no authentication and is designed for a trusted private LAN. Do not present it as suitable for direct Internet exposure.

ARM credentials/tokens, if configured, belong in environment/secrets and must not be committed.

Camera imagery becomes a privacy consideration in v2. v1 should not collect images.

## 24. Repository/documentation expectations

The repository should remain understandable without knowledge of the original discovery conversation.

Maintain:

- `README.md` for onboarding and normal use;
- `ROADMAP.md` for staged development;
- this file for full LLM/developer context;
- architecture/configuration/masterlist/ARM compatibility notes;
- Docker deployment instructions;
- troubleshooting;
- security/privacy notes;
- tests and CI;
- changelog;
- semantic versioning;
- issue templates;
- license.

## 25. Current implementation caveats

The repository is still an early implementation, not a declaration that live ARM ripping is production-ready.

Before calling v1 complete:

- verify the exact ARM per-track update endpoint and payload on a live/representative ARM Neu API;
- implement read-back validation before starting a configured job;
- complete known-extra naming;
- complete Retry / Skip / Stop failure controls;
- complete restart/recovery controls;
- broaden mocked ARM API/state-machine tests;
- test hold ownership/restoration thoroughly;
- run staged live validation without risking existing media.

Do not remove safety checks merely to make a live test proceed.

## 26. Guidance for an LLM modifying this repository

When implementing a change, preserve these distinctions:

1. **ARM owns ripping configuration; the helper owns deterministic identity/workflow.**
2. **Known masterlist tracks may be edited; unmapped tracks are not disabled.**
3. **v1 trusts insertion order; do not quietly claim v1 has v1.1 evidence safety.**
4. **v1.1 adds structural evidence; v2 adds vision and multi-drive.**
5. **A global ARM hold is a v1 fail-safe, not the final multi-drive design.**
6. **A crash/restart requires operator input; do not silently resume.**
7. **Do not query external metadata providers in v1 just because an ID is present.**
8. **Do not add transcoding, stream pruning, FileFlows, or media-library management without an explicit scope change.**
9. **Do not hard-code details from the original test installation.**
10. **Do not guess an unverified ARM endpoint. Keep integration details configurable until verified.**

If a requested change conflicts with these decisions, make the conflict explicit before changing the architecture.

