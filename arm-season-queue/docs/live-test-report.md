# Verification and qualification

## Fresh unrestricted beta preparation (2026-10-21)

The stale Voyager-season batch was canceled through the queue's supported
workflow; its batch record, job 5, evidence, files, and assessments were retained.
ARM reported job 5 terminal (`success`), with no current drive job. A new,
independent unrestricted session is active with a null masterlist snapshot and
no starting-disc/entry cursor. The previous reservation remains in history and
is not reused by the new session.

The correction was deployed as
`arm-season-queue:local-beta-unrestricted-7cacbd4`. Runtime preflight passed:
ARM Neu 19.1.0 source/API profile, both configured Ollama models, storage,
read-only `/dev/sr0` tray monitor and `/dev/video0` camera were ready; the
physical tray was observed closed, the camera was Automatic, and no ARM job was
current. `DRY_RUN_ONLY=false`, local beta enabled, FileFlows disabled, and ARM's
global ripping pause remained enabled (`ripping_enabled=false`). MakeMKV's
selection setting is `+sel:all`. The GUI exposes explicit per-job start and
correct/incorrect/unassessed review controls; no new per-job start was issued
during preparation.

The active Docker suite passed **218 tests**, including generated-media
integration and new regressions for different consecutive series, no reference
match, fresh-session isolation, empty-tray calibration, and post-eject tray
handling. The push was integrated with the concurrent `origin/main` update and
completed at commit `7cacbd4`. These are software/deployment checks only. No new
disc was presented and no new scan, rip, or ejection occurred in this
preparation phase; physical camera opening, automatic baseline capture, disc
recognition, ARM prescan, per-job start, ripped files, and ARM-owned ejection
remain unqualified until the supervised unrestricted test.

## Read-only tray monitoring and automatic empty-view preparation (2026-10-21)

The implementation now polls Linux `CDROM_DRIVE_STATUS` on only the configured
optical device opened read-only. Closed/no-disc is distinct from open; unstable
open/closed readings are debounced and unknown readings fail closed immediately.
The camera workflow persists its tray-cycle and empty-view baseline, detects
early placement, waits for removal after ARM AUTO_EJECT, freezes capture
evidence before asking ARM to close, and records cycle provenance. ARM remains
the sole scanner, close, rip and automatic eject owner. The durable overlay
opts in to ordinal mapping, read-only monitoring and ARM-mediated close; global
ARM pause remains the gate and the queue's explicit per-job start path remains
the only start mechanism. FileFlows and external image processing remain off.

The Python 3.12 Docker test image passed the active suite: **210 tests passed**.
This includes state/debounce/calibration/restart/repeated-callback regressions
and the generated-media integration test. This is software validation only;
it does not qualify the camera's disc detector, physical tray transitions,
ARM's scan/close/start/eject behavior, or the resulting files. The next optional
follow-up is to evaluate an API-based GPT vision fallback; the exact served
model ID/configuration must be verified then. It is not implemented here and no
images were sent externally for this task.

### Local beta deployment status

The source-identifiable queue release `arm-season-queue:local-beta-tray-a2bff3d`
was deployed using the durable private beta overlay. The running container
reported ordinal mapping and read-only tray monitoring enabled, `ARM_DRIVE` and
`TRAY_MONITOR_DEVICE` both `/dev/sr0`, ARM-mediated close enabled, FileFlows off,
and `DRY_RUN_ONLY=false`. Docker granted the helper read-only device access; a
read-only ioctl returned the closed/no-disc state. Queue preflight and ARM
source/API checks passed against ARM Neu 19.1.0 with the verified source
reference SHA. ARM global ripping remains paused (`ripping_enabled=false`), the
existing beta batch is running, and the queue's explicit per-job start control
is available. The tray was closed and empty at deployment; no setup disc was
processed. This establishes software deployment only. **Physical manual-open
detection, empty-view calibration, disc capture, ARM close/prescan, actual rip,
and ARM ejection remain pending the operator's supervised live test.**

## Opt-in ordinal mapping implementation (2026-10-20)

The current local-beta implementation adds `ARM_AUTO_ORDINAL_MAPPING`, default
off. If enabled, it requires accepted field-evidence recognition, a complete
contiguous printed episode range, and a matching count of ARM titles at least
30 seconds long. Shorter titles remain provisional. Each assignment retains its
MakeMKV source ID and scan metadata and is marked inferred/unverified; matching
counts do not prove episode identity or order. This describes implementation
policy only; it is not physical-disc validation. See `docs/local-beta.md`.

## Scanner handoff race guard and historical dry-run assessment (2026-10-11)

The host-side info scanner now treats ARM's tray-close-triggered prescan as a
handoff phase: it waits for the current inserted-disc job to become
`manual_paused` with `manual_start=false`, verifies that no other active job or
MakeMKV info process exists, and requires that state to remain stable for five
seconds before launching its isolated info-only scan. When tray closure is
needed, an already-existing job ID cannot satisfy the new-insertion handoff.
The wait is bounded at 1200 seconds by default. Regression tests exercise the
prescan transition, scanner activity, stable hold, and stale-job rejection.

An already-retained, fully scanned dry run can now receive its operator
assessment after it has moved into dry-run history; the active run is not
replaced or changed. An unscanned run may be superseded without assessment only
after the bounded camera capture has explicitly timed out. Other incomplete
reviews remain blocked from starting another run.

These are source-level and local-suite checks only. The updated helper's
race-free behavior has not yet been qualified against a physical ARM prescan;
the next supervised run must verify that ARM reaches its held state before the
host scanner starts. A software `Test successful` assessment does not imply a
physical scan, rip, or FileFlows qualification.

## Field-level model evidence and retained DS9 dry run (2026-10-11)

The old blanket agreement rule is replaced by per-field evidence combination.
The retained transcriptions were re-evaluated without another model request:

| Field | Qwen3-VL | Qwen2.5-VL | Combination |
|---|---|---|---|
| Series | Star Trek: Deep Space Nine | Star Trek | Compatible partial: use the more specific Qwen3 reading; the franchise reading does not independently confirm DS9 |
| Season | 2 | No usable numeric season (`DEEP SPACE NINE` was under `Season:`) | Single-source: 2, attributed to Qwen3 |
| Disc | 1 | 1 | Corroborated: 1 |
| Printed episode range | 1–4 | 1–4 | Corroborated: episodes 1–4 |
| Packaging/descriptive text | `VFB 25451` | `CBS DVD`, `DOLBY`, `CBS STUDIOS INC.`, `R1`, `EU 113126` | Retained as attributed descriptive/identifier evidence, not series candidates |

The working identification is **Star Trek: Deep Space Nine — Season 2 —
Disc 1 — Episodes 1–4**. `field-evidence-combination-v1` reports PASS for
identification, but the active private masterlist has no compatible DS9 entry;
matching is `none`. This does not authorize a rip.

The queue was deployed as `arm-season-queue:field-evidence-20261011-r3` after
backing up its SQLite state and private evidence. A fresh host-side, networkless
MakeMKV `info`-only scan then completed while ARM's global rip pause remained on
(`ripping_enabled=false`),
its `/dev/sr0` current-job field was empty, and no active ARM jobs were listed.
The helper also serializes helper invocations, rejects a host-visible competing
MakeMKV scanner, and rechecks ARM during its bounded scan. The private report is
under `/srv/arm-vision/local-test/voyager-s2-d1/state/dry-run-scans/`.

The scan found DVD label `EU_103539`, four titles (MakeMKV IDs 0–3, DVD titles
1–4), each with MPEG-2 720x576 video, five audio streams (German, English,
Spanish, French, Italian), and ten subtitle streams. This validates a four-title
physical inventory, not episode identity or title-to-episode order. With no
compatible approved mapping or human assignments, the plan proposes:

- `.../Season 02/Source Title 000 - PROVISIONAL.mkv`
- `.../Season 02/Source Title 001 - PROVISIONAL.mkv`
- `.../Season 02/Source Title 002 - PROVISIONAL.mkv`
- `.../Season 02/Source Title 003 - PROVISIONAL.mkv`

All paths are proposals under
`/srv/arm-vision/local-test/voyager-s2-d1/completed/tv/Star Trek_ Deep Space
Nine/`; no file was created. The plan remains blocked on absent technical
mapping and four unassigned episode identities, with `ready_for_ripping: false`.
No ARM scan/start/cancel/eject endpoint, rip, transcode, FileFlows dispatch, or
agent assessment was performed. The queue UI's final review and operator
assessment remain theirs to complete.

ARM's prescan owner was its job-2 worker in `arm/ripper/main.py`, invoking
`makemkvcon --robot --messages=-stdout info --cache=1 dev:/dev/sr0
--minlength=0`. The live ARM configuration has `PRESCAN_TIMEOUT=300` and
`PRESCAN_RETRIES=3`; the drive-specific overrides are null, so those global
values apply. Failed attempts sleep 30 then 60 seconds, and after the
last failure ARM logs that it will retry during the rip phase. The retained log
shows attempt 1 timing out after 300 seconds and attempt 2 starting. At the later
safe handoff there was no current drive job, no active ARM job, and no visible
ARM/MakeMKV worker. Thus the observed retry was ARM's bounded retry loop, not the
host helper; no worker cancellation was needed or issued. The scanner timeout
root cause (disc/device/MakeMKV hang) is not established by those logs alone.

The active application suite ran in the newly built image with **166 tests
passing**, including field omissions, franchise/subseries compatibility,
equivalent ranges, genuine conflicts, packaging text, absent references, dry-run
planning and scanner safeguards. This is software test evidence, not a claim of
ARM ripping or FileFlows qualification.

## Optional-masterlist workflow refactor (local source validation, 2026-10-09)

Recognition agreement is now independent of masterlist matching. The dry-run UI
starts without a selected list, reports no-match/ambiguous references distinctly,
and continues through information-only scan to provisional source-title-ID
proposals. Confirmed compatible technical mappings can enrich a plan; descriptive
drafts cannot provide instructions. Current-job human assignments remain separate
from reusable masterlists. Prior disc dry runs remain available in review history.

The active suite passes **162 tests** in a network-isolated container using the
previous application image as its Python environment with current source mounted
read-only. This covers optional and ambiguous matches, unmatched-source proposals,
job-specific mappings, draft trust separation, dry-run execution isolation and
scanner job-state checks. This is source/test validation only: the updated image
has not been deployed, and no camera capture, tray closure, disc scan, ARM rip,
FileFlows dispatch, or operator assessment was performed for this refactor.

## Local dry-run-only preparation (2026-10-08)

The companion now has a separate dry-run review record, MakeMKV info-output
parser, per-title inventory reconciliation, and a proposal screen using the
same `app.arm.plan` title-selection/readiness/filename logic as a normal queue
plan. It has an execution-boundary `DRY_RUN_ONLY` mode: batch activation,
actions, human dispatch authorization, and reservation polling are blocked.
Camera evidence is attached to the separate dry-run record and is not released
for production insertion pairing. The final plan is always `ready_for_ripping:
false`; assessments persist on the dry-run record only.

The host-side scanner helper is deliberately separate from the queue and has no
media-output mount or network. It verifies the local ARM global pause and empty
job state, closes the drive by kernel ioctl, waits a bounded time for a ready
data medium, and invokes only MakeMKV `info`. ARM's actual critical sources
match the inspected reference. `POST /drives/{id}/scan` starts the processing
wrapper, and the tray-close API also triggers that wrapper, so neither route is
used. The host drive watcher is inactive and no matching host udev rule was
found at preparation time. ARM remains globally paused; MakeMKV's persistent
`app_DefaultSelectionString` is `+sel:all`; the configured local staging and
completed paths are on ext4. FileFlows is disabled.

The live camera preview shows the physical open optical tray in the camera's
field of view and a label-up DVD can be read in that position. No separate
automatic transfer mechanism is present or assumed. The preview frame was a
temporary diagnostic, not the dry-run's fresh retained capture. At the end of
preparation no dry-run had started; camera calibration, capture, scan, GUI
proposal and assessment were then outstanding.

The active application suite passes **159 tests** in a network-isolated Linux
container, including dry-run parsing/planning, scan inventory and mapping
conflict blockers, excluded titles, database privacy, same-capture association,
non-pairing, no-reservation behavior, and a dry-run tick that does not query ARM
or mutate production state. This test result does not qualify the physical
camera, optical drive, MakeMKV scanner helper on the present medium, or ARM's
pause-database failure behavior.

## Completed supervised dry-run; final review pending (2026-10-08)

The supervised local workflow then ran against one fresh camera presentation.
Three frames were retained; the two configured recognition models completed but
disagreed on the printed episode range, so the result remained unmatched. The
bounded host helper closed the tray and completed a network-isolated MakeMKV
`info` scan only after ARM's global pause was verified and ARM's automatically
created job reached `manual_paused`. No ARM scan/processing route was called.
The shared planner built four local-SSD proposed paths, but the plan remains
blocked on camera identity, always `ready_for_ripping: false`; the final GUI
review and operator assessment are pending. No rip, transcode, FileFlows
request, production reservation/completion, or media output file was created.
The scan report, capture and private local deployment configuration remain
outside Git.

## Recognition normalization correction

The retained raw model stream completions contain the original transcriptions
`Printed episodes/range: 1-4` and `Printed episodes/range: EPISODES 1-4`.
Initially, structural-label normalization changed `episodes` to `episode`,
leaving the `/range` suffix unhandled; the episode parser therefore omitted one
model's range. The other model's redundant `EPISODES` token was also not
accepted after the field label. Separately, `Disc: 1` was omitted by the disc
number parser, and absent episode titles were incorrectly mandatory in the
candidate proof. These caused the recorded missing-fields and model
disagreement reasons; the original response evidence was intact.

The first retained-only reevaluation then passed semantic agreement but exposed
an integration issue: the reevaluation result held the unique match inside the
agreement object, while the controller's fail-closed handoff consumes the
validated top-level `matches` field. The reevaluation path now carries the
validated match through that same controller gate; it does not bypass it.

The corrected parser accepts explicitly labeled English/German/French/Spanish
episode range fields, whitespace and dash variants, and the actual two response
forms. It does not infer episodes from unlabeled numbers. Agreement compares
conflicting semantic observations while treating a missing value as missing;
both models must still independently supply required series, season and episode
range evidence. Episode titles are optional. Regression coverage also verifies
that a genuinely missing required episode observation stays missing rather than
being copied from the masterlist.

The retained run was re-evaluated using only the existing response streams,
images and attached scan. Both models independently normalize to series
`star trek voyager`, season 2, disc 1, episodes `[1, 2, 3, 4]`. The unique
compatible approved entry is `star_trek_voyager_s2_de_split_dvd` / Disc 1.
The existing info scan corroborates the approved mapping and per-title
inventories for MakeMKV IDs 0–3 / DVD titles 1–4; the episode association still
comes from the user-approved mapping, not title count alone. The plan now has no
blockers, proposes four filenames, and remains `ready_for_ripping: false`.
Deployment image `arm-season-queue:dry-run-only-20261009-r3` is active. No new
inference, physical drive action, assessment, rip, FileFlows dispatch, or media
output was performed; the operator review remains pending.
At the final read-only status check, ARM's global ripping pause remained on and
the detected `/dev/sr0` job was `fail` with `Received signal 15`, no manual-start
request, and no output; the companion has zero reservations/active batches and
the local completed directory contains zero media files. This ARM job state is
reported separately and was not modified by the recognition reevaluation.

## GUI/masterlist update (2026-10-08)

The active application suite passes **134 tests** in the rebuilt Linux image,
including generated-media validation, four-language structural-label parsing,
human-correction revision binding, disabled-local-handoff behavior, and import
checks for all 10 Borgia/Voyager metadata drafts. The drafts now import as v2
metadata (`approved: false`); 55 discs and 208 episode entries remain blocked
from ripping because no technical scan-backed mappings/inventories are claimed.
Voyager S1 Disc 5 is represented as extras-only metadata without invented title
IDs.

The queue was deployed locally as
`arm-season-queue:gui-masterlist-20261008`, image
`sha256:e8ded17f567fc93dc557d6089aef0f316d1362c560263e368bb603343c3ba187`.
Health and authenticated state/UI asset requests returned HTTP 200. The queue
has no optical-drive mapping or Docker socket; its camera is the only device,
ARM media is mounted read-only, and local-test FileFlows handoff is disabled.
ARM global status was read back as `ripping_enabled=false` and
there were zero ARM jobs, loaded masters, queue reservations, or rips. Persistent
state and private deployment configuration were backed up before deployment;
no database schema migration was performed. No disc was presented or inserted,
no capture was taken, and no FileFlows processing was invoked. A real browser
screenshot/viewport inspection was unavailable on this host; responsive CSS and
served GUI assets were checked, but visual browser rendering is not claimed.

This local deployment check does not qualify camera recognition, physical disc
association, ARM ripping/recovery, DVD angle selection, NAS/TrueNAS integration,
FileFlows, or media-library playback. Continue to use manual supervision.

## Integrated local deployment check (2026-10-07)

The current working branch includes GitHub `main` through draft-publication
commit `7b2de50`. The deployed queue image is `arm-season-queue:ffe735e`, built
from `ffe735eb76df8a8af20f7bd2141a873cd06f31f4` (image
`sha256:f0a47f1d3a32c93a8e2f1d4bb4c7a0fada37691cd906cf9a956713d0607976df`).
The active application suite passed **114 tests** in a network-isolated
transient container. The deployment serves HTTP health and the authenticated UI.

The local ARM Neu image's critical source profile matches reference commit
`f6ec2e3fd47cf951e89094e0a9d6999ab94d781f`. Its live API reports version
19.1.0; the queue's compatibility preflight is ready. ARM global pause was set
through its API and read back as `ripping_enabled=false`. The effective ripping
settings match the inspected requirements. The active batch count was zero.
Although Docker reports no configured device mapping and the optical overlay is
disabled, `/dev/sr0` unexpectedly existed as a block-device node inside the ARM
container. Effective device access was not tested. ARM was stopped immediately
after this discrepancy was found; it remains stopped while the disc is present.
Do not treat the earlier no-passthrough assumption as proof of isolation. These
are setup checks, not qualification of physical ripping, cancellation, ejection,
or ARM's known pause-database failure behavior.

A private `info`-only MakeMKV scan runner is prepared outside the repository.
It rechecks ARM pause/no-active-job state and uses a separate read-only
container with no network or ARM connection. The first attempt stopped because
the MakeMKV registration key was expired/missing; ARM's supported private API
preflight refreshed it and verified the key. The next attempt enumerated an
ASUS BW-16D1H-U but could not open the disc with read-only device permissions
(`TCOUNT:0`). MakeMKV requires full drive access or `CAP_SYS_RAWIO` for its info
scan. After the operator approved temporary raw access, a scan-only MakeMKV
2.0.0 helper ran with network disabled and temporary read/write access to the
two optical nodes. It still reported `Failed to open disc` and `TCOUNT:0`; no
disc titles or episode mapping were established. A bounded low-level TEST UNIT
READY query returned SCSI sense key `0x02` (Not Ready), ASC/ASCQ `0x30/0x00`
(Incompatible medium installed). The helper's shared-library dependencies all
resolved; this does not prove the disc is readable or isolate a media/drive
fault. After an operator-authorized tray reseat, a bounded 60-second readiness
wait reported “becoming ready” initially and then “Incompatible medium
installed” for the remaining polls; it never reached ready, so no further info
scan ran. No title data or mapping was established. Both scan reports remain
private.

The Logitech MX Brio is detected at 3840×2160. Queue camera status is connected
in manual-capture mode, and the authenticated preview returned JPEG frames.
Camera calibration is still pending; no disc was presented, no capture was
retained, and no recognition inference was run. Exact local Qwen model preflight
passed for `qwen3-vl:30b-a3b-instruct-q4_K_M` and
`qwen2.5vl:7b-q8_0` with their expected digests.

The NFS preflight successfully verified raw/completed directories and a
create/read/rename/remove probe. The queue's NFS media mount is read-only; queue
state/evidence, model storage, Docker and temporary work remain on local storage.
No direct NAS administration or FileFlows validation was performed.

The retained-output benchmark is offline and uses the production agreement
evaluator plus independent retained ground truth. Revision v8 verified all 16
retained model/image requests, images, transcriptions and completion markers, and
performed no inference. The Borgia draft is still private, incomplete and not
loaded or approved. The active state contained zero approved masterlists, so its
policy simulation rejected all captures; false-accept and false-reject rates
were not measurable. Four physical discs/eight images do not qualify this system
for production. Reports remain in private durable storage outside Git.

The previous published baseline's GitHub Actions built the Linux amd64 image, passed 32 companion tests inside
that image, checked non-root HTTP startup, and published the tested image.
Legacy package CI also passes. Tests include actual FFmpeg-generated media,
stream inspection, validation/copy/acknowledgement, duplicate handling, path
containment, authentication, recognition conflicts and explicit unsupported
non-TV adapters. No ARM simulator is used.

The source/API match and local service checks do not establish production
qualification. Physical disc identity, read-only scan/title mapping, ripping,
controller/ARM recovery during a real rip, cancellation/ejection, FileFlows
operation and Jellyfin grouping remain untested. Example source mappings must
be established from the actual physical release and technical scan before use.

Known limits: quarter-turn OCR and conservative consensus do not resolve severe
glare or unreadable print; photos cannot establish source-title/angle mappings.
The inspected ARM pause-state database-error handling is not strictly fail-closed.
Record live hardware details, job IDs and deployment results privately, outside
this repository.
