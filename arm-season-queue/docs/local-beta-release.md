# Local-beta release and qualification

The local-beta queue is the single naming owner: it creates the final basename
from the recognized identity and (when explicitly matched) the approved
masterlist mapping, then sends that basename to ARM as `custom_filename`. ARM
does the lossless rip and writes that exact basename to its normal completed
series/season directory. Unknown or incomplete identities use a provisional
`Source Title NNN` name; source ID, recognition attribution, and later human
assessment remain in local state. Episode identity is never inferred from
runtime or source ordering.

Use `compose.local-beta.yaml` with the existing private Compose deployment and
private env file. Pin `QUEUE_RELEASE_IMAGE`, `ARM_RELEASE_IMAGE`, and
`QUEUE_RELEASE_COMMIT` to the commit being deployed. The ARM overlay is based
on the inspected ARM Neu 19.1.0 image and applies a checked, idempotent patch to
the supported finalizer: a rendered track folder already represented by
`job.path` is not appended again. It also grants group write/execute on the
final output directory so the queue can perform reviewed, non-overwriting
renames. The queue joins ARM's media group. Review keeps the actual planned and
completed paths separate.

Set `AUTO_EJECT: true` in the effective ARM config mounted at
`/etc/arm/config/arm.yaml`. Verify the setting using ARM's settings API after
startup. The queue overlay disables queue-open and post-rip eject; the separate
close-after-capture option defaults off. Do not infer physical ejection from a
configuration readback; qualify it during the supervised physical test.

Before deploying, snapshot the active queue database consistently, state files,
effective ARM config, and Compose rendering. Keep FileFlows disabled and retain
the existing media mount; do not enable NFS delivery or deletion. Do not restart
ARM during a rip or with a disc that must not be reprocessed. Verify both image
digests, ARM source profile, batch pause, tray setting, and healthy queue before
asking the operator to exchange the disc.
