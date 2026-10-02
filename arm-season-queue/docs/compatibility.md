# ARM Neu compatibility and evidence

Inspected reference: [ARM Neu commit f6ec2e3](https://github.com/uprightbass360/automatic-ripping-machine-neu/tree/f6ec2e3fd47cf951e89094e0a9d6999ab94d781f), `VERSION=19.1.0`. This is **source verification**, not evidence that any user's deployment runs that code. No deployed version or API was accessible in this work session. Deployment-specific validation must be performed by the operator.

## Check the actual deployment first

Record the ARM container image tag/digest and effective version from `GET <ARM_URL>/api/v1/system/version`. Read `/openapi.json` if available and inspect the actual container's source. Use a read-only copy of that source or run the included checker where it is visible:

```sh
python scripts/check_source.py /path/to/actual/arm/install
```

`scripts/reference-source.json` contains hashes of the exact inspected API, ripping, model, naming and version files. The checker normalises CRLF only. Matching these files verifies this adapter's critical source paths; it is not an attestation of every dependency or a hardware test. Also check the deployed `arm-contracts` dependency and the live API response fields; this reference serialises through shared contracts.

Only after matching the critical source and checking settings, set:

```dotenv
ARM_EXPECTED_VERSION=19.1.0
ARM_VERIFIED_SOURCE_SHA=f6ec2e3fd47cf951e89094e0a9d6999ab94d781f
```

These variables record an operator-performed compatibility check; the application cannot read container source through the API. Do not fill them merely to bypass a check. A differing source requires inspecting that version and updating the adapter/tests, not substituting a new string. The queue checks live version/settings at activation, resumption and restart recovery. It verifies global pause and drive mode before new assignments, and per-job settings/readbacks before starting.

## Capability findings

| Requirement | Inspected evidence | Implementation implication |
|---|---|---|
| Prescan before waiting | `arm/ripper/main.py`: video prescan loop before `utils.check_for_wait` | Wait for `manual_paused` and a valid title inventory |
| Persistent hold | `utils.py`: `check_for_wait` plus `_poll_manual_wait`; `system.py`: persisted AppState flag | Global pause must already be on; additionally pause each reserved job |
| Manual-wait timeout | `_poll_manual_wait` proceeds when timeout expires and global pause is off | A long timeout alone is insufficient |
| Explicit start | `POST /api/v1/jobs/{id}/start` sets `manual_start` | Start only after durable reservation, metadata readback and preview |
| Drive mode | `GET /api/v1/drives` exposes mount/mode/current job | Restrict assignment to one configured drive and current insertion |
| Job details | `GET /api/v1/jobs/{id}/detail` returns job/config/tracks | Bind identity and title-structure digest; never write ARM DB |
| Per-track selection/episodes | `PATCH /jobs/{id}/tracks/{track_id}` accepts enabled, custom_filename, episode_number/name | Use database track ID for update, MakeMKV ID for mapping |
| Naming | `PUT /jobs/{id}/title`, `POST /jobs/{id}/multi-title`, `PATCH /jobs/{id}/naming`, `GET /jobs/{id}/naming-preview` | Unique per-job staging folder; preview must match desired names |
| Title-selection path | `makemkv.py`: auto + MAXLENGTH>99998 uses `all`; otherwise `process_single_tracks` respects enabled | Require auto mode and MAXLENGTH=99998 |
| Ripper-only finalisation | `arm_ripper.py::_post_rip_handoff` calls `finalize_output`, then commits `success` | Wait for final success, not a generic rip-complete event |
| Completion limitations | `naming.py::_move_track` skips missing files and leaves source filename fields unchanged | Independently check each required output using saved naming preview + final `job.path` |
| Source DVD titles | `TrackInfoProcessor` retains MakeMKV title ID, duration, aspect, FPS, chapters/size; it does not persist original DVD title/angle mapping | Never infer DVD-title number as MakeMKV ID + 1; use evidenced explicit maps |
| Source stream inventory | `SINFO` handler retains limited video data; track detail does not expose complete audio/subtitle inventory or chapters | Supply independently evidenced source inventory in the masterlist |

The job config PATCH handler also changes process-global configuration. This companion deliberately does **not** call it. Configure the tested settings once in ARM, while idle, rather than letting one job change unrelated jobs' settings.

## Known hold limitation

`utils.is_ripping_paused()` catches a database error and returns `False`. Thus even the persisted global-pause approach is not a strict fail-closed guarantee during an ARM database failure before the companion can apply a per-job pause. Controller outage alone does not release normal persisted global pause; a pause-database read failure is a separate edge case. ARM restarts can also leave a waiting job with no surviving ripper process; the `/start` API signals a job flag, not a durable task launcher.

No ARM patch is supplied because the deployed version is unknown and no live limitation has been reproduced there. If the deployed source shares this behavior and strict hold under database failure is required, the minimal targeted change to evaluate is: fail closed on pause-state read errors and provide a durable per-job external-controller hold checked before any ripping path. A separate source inventory/API enhancement could expose original DVD title, angles and full stream inventories. Neither should be applied speculatively.

For a restart-stranded physical job, keep it held, inspect ARM, cancel that queue-owned waiting job if necessary, then eject/re-present/reinsert to create a fresh job. Never claim that flipping `manual_start` necessarily restarts a dead ripper thread. The live test report leaves this scenario outstanding.

ARM's `ARM_API_KEY` in this source is for an external disc metadata service, not authentication of its local REST API. Use a private service network or an authenticating reverse proxy; `ARM_BEARER_TOKEN` supports the latter without embedding credentials in code.
