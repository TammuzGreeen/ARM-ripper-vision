# Architecture

The current Docker runtime is `arm-season-queue/`: local V4L2 capture → independent OCR extraction → comparison with a user-maintained masterlist → persistent recognition/insertion reservation → verified ARM API metadata/start → output validation → atomic manifest → separate FileFlows publisher/acknowledgement.

Camera evidence never creates or updates masterlists. Users maintain the authoritative entries; conflicts are shown for review. Read the [current formats/state model](../arm-season-queue/docs/formats.md) and [migration notes](migration-camera-companion.md).

The original `arm_ripper_vision/` controller is preserved as legacy code, not launched by current Docker deployment.
