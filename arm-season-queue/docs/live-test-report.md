# Verification and qualification

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
`arm-season-queue:gui-masterlist-20261007`, image
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
