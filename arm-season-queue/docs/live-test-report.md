# Verification and qualification

GitHub Actions has built the Linux amd64 image, passed 32 companion tests inside
that image, checked non-root HTTP startup, and published the tested image.
Legacy package CI also passes. Tests include actual FFmpeg-generated media,
stream inspection, validation/copy/acknowledgement, duplicate handling, path
containment, authentication, recognition conflicts and explicit unsupported
non-TV adapters. No ARM simulator is used.

The inspected ARM reference is version 19.1.0 at commit
f6ec2e3fd47cf951e89094e0a9d6999ab94d781f. This does not attest any deployed ARM
instance. Physical webcam quality, real-disc ripping, FileFlows operation,
controller/ARM recovery during a real rip, and Jellyfin grouping still require
operator qualification. Follow the compatibility guide and start with a reviewed
one-disc masterlist. Example source mappings must be checked against the physical
release before use.

Known limits: quarter-turn OCR and conservative consensus do not resolve severe
glare or unreadable print; photos cannot establish source-title/angle mappings.
The inspected ARM pause-state database-error handling is not strictly fail-closed.
Record live hardware details, job IDs and deployment results privately, outside
this repository.
