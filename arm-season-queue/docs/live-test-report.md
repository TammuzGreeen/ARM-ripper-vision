# Verification and qualification

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
