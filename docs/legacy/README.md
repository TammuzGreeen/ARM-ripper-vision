# ARM Ripper Vision

A deterministic, masterlist-driven helper for [Automatic Ripping Machine Neu (ARM Neu)](https://github.com/automatic-ripping-machine/automatic-ripping-machine).

ARM Ripper Vision does **not** replace ARM. ARM continues to handle the optical drive, MakeMKV, ripping, stream preservation, extras policy, and output. This helper controls the batch workflow and applies known TV-series metadata and filenames from human-maintained YAML masterlists.

## Status

Early development — **v0.1.x / v1 foundation**.

The initial controller, ARM API adapter, masterlist model, persistent state, Docker deployment, web UI, and test scaffolding are in place. Live ARM integration still needs validation against a running ARM Neu installation, particularly the configurable per-track update route.

See [ROADMAP.md](ROADMAP.md) for the planned progression through safer disc identification, vision, multi-drive support, and TrueNAS packaging.

## Intended workflow

1. Start ARM Ripper Vision.
2. Open the web UI.
3. Select a series masterlist, for example *Star Trek: Deep Space Nine*.
4. Select the season/disc to start from.
5. Start the controlled batch.
6. Insert that disc.
7. ARM prescans the disc while global Auto-Start is held.
8. The helper associates the new ARM job with the expected masterlist position.
9. Known episode tracks receive season, episode, title, and custom filename metadata.
10. ARM performs the rip using its existing ripping configuration.
11. After success, the helper advances to the next expected disc and waits for insertion.
12. The batch ends at the end of the masterlist or when the operator stops it.

## Design principles

- ARM remains the ripping engine and authority for ripping settings.
- The helper uses the ARM HTTP API only; it does not access ARM's database.
- Masterlists describe what is known about a release; they do not configure audio, subtitles, transcoding, or general MakeMKV behavior.
- Ripping should remain non-destructive. Audio/subtitle handling remains configured in ARM/MakeMKV.
- Unmapped tracks are left untouched so ARM can process configured extras.
- Known extras may optionally be named in the masterlist.
- A helper restart never silently resumes an interrupted batch.
- v1 intentionally trusts the selected masterlist position and insertion order. Stronger disc identification belongs to v1.1 and v2.

## v1 scope

- One optical drive.
- One active controlled batch.
- Trusted-LAN web UI with no authentication.
- Read-only YAML masterlists mounted into the container.
- SQLite runtime state with Alembic migrations.
- ARM API polling every 3 seconds by default.
- Global ARM Auto-Start hold while a controlled batch is active.
- Explicit MakeMKV source-title mappings where known.
- Ordered eligible-title fallback when exact source-title IDs are unavailable.
- Episode metadata and custom filename assignment.
- Optional known-extra naming.
- ARM handles unspecified extras.
- Configurable ARM API authentication; default is none.
- Docker Compose deployment.

Not in v1: transcoding, stream pruning, media moving, FileFlows integration, webcam/OCR, vision-based recognition, multi-drive control, or TrueNAS App packaging.

## Masterlists

Masterlists are one YAML file per series/release family and can contain multiple seasons and discs.

Example:

```yaml
schema_version: 1
id: star-trek-ds9

series:
  name: "Star Trek Deep Space Nine"
  year: 1993
  imdb_id: tt0106145

seasons:
  - season: 2
    discs:
      - disc: 1
        episodes:
          - episode: 1
            title: "Die Heimkehr"
            source_title: 0
          - episode: 2
            title: "Der Kreis"
            source_title: 1
```

For mapped episodes the helper sets episode number, episode title, and a custom filename. It leaves unrelated ARM tracks alone. Exact source-title mappings are preferred; v1 can fall back to eligible tracks in ascending ARM/MakeMKV title order.

Masterlists are intentionally edited outside the web UI. See `examples/masterlists/`.

## Filename convention

Default episode filename stem:

```text
Series Name - S02E05 - Episode Title
```

An optional edition suffix is supported:

```text
Series Name - S01E01 - Episode Title [Remastered]
```

The helper supplies a filename stem; ARM remains responsible for the resulting media file.

## Quick start

```bash
cp .env.example .env
mkdir -p data masterlists
cp examples/masterlists/star-trek-ds9.example.yaml masterlists/star-trek-ds9.yaml

# Edit .env and the YAML for your installation/release.
docker compose up --build -d
```

Open:

```text
http://<docker-host>:8099
```

## ARM configuration assumptions

The controlled workflow expects ARM jobs to reach a paused/manual-wait state before metadata is changed and the approved job is explicitly started.

For the deterministic per-title MakeMKV path:

- keep `MANUAL_WAIT=true`;
- keep `ALLOW_DUPLICATES=true` for TV box sets;
- use a finite `MAXLENGTH <= 99998` so ARM does not switch to its MakeMKV "all titles" fast path.

The helper does not automatically rewrite ARM configuration. Unsafe/incompatible values should stop the controlled workflow and require operator action.

The ARM endpoints are configurable through environment variables. The exact per-track update route must be verified against the target ARM Neu API before live use.

## Configuration

Copy `.env.example` to `.env`. Important settings include:

```text
ARV_ARM_BASE_URL=http://arm-rippers:8080
ARV_ARM_POLL_INTERVAL_SECONDS=3
ARV_ARM_AUTH_TYPE=none
ARV_DATA_DIR=/data
ARV_MASTERLIST_DIR=/config/masterlists
```

Persistent runtime data belongs in `/data`; masterlists are mounted read-only at `/config/masterlists`.

## Development

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

CI runs the test suite on pushes and pull requests.

## Safety

The global ARM ripping hold is intentionally conservative in v1, but it is ARM-wide and can affect unrelated jobs. v1 therefore targets one controlled drive/batch.

On normal batch completion or an explicit stop, the helper restores the Auto-Start state it observed before taking control. After a helper/container restart, it does not automatically resume or silently restore state; operator input is required.

Use mocked ARM endpoints for development before connecting the controller to a live optical drive.

## Documentation

Architecture and ARM API notes are in `docs/`. The development roadmap is in [ROADMAP.md](ROADMAP.md).

## License

MIT. See [LICENSE](LICENSE).

