# TNG German DVD references: approved metadata and user-confirmed mapping rules

These six schema-v2 files are corrected reference masterlists for seasons 2–7. `approved: true` records the user's authorization to publish the corrected references and their explicit confirmation of the mapping convention on 2026-10-21. It does **not** claim that the discs, source IDs, episode content, or stream inventories have been physically verified. Validation results are in `validation.json`.

## Intended use

Import the individual `tng_season_XX.json` files through the application's masterlist importer. Keep unrestricted recognition enabled, without a selected season or expected-disc hint. Recognize the inserted disc first, then look up a compatible series, season, disc and edition. Use a unique compatible reference's episode titles and mapping instructions. A conflicting edition or ambiguous match must not silently select a list. Unknown discs remain eligible for the existing provisional beta workflow.

The user confirmed this rule separately for every disc: MakeMKV source 0 maps to the first **listed entry** on that disc, source 1 to the second, etc. Thus Disc 1 sources 0–3 map to episodes 1–4, while Disc 2 sources 0–3 map to episodes 5–8. Rules use zero-based episode indexes; episode numbers do not restart. Resolve against the actual scan, not filenames or ARM database track IDs. Extra sources must not be silently assigned episode numbers. A combined episode entry must not be split without evidence.

## Current application limitation

At repository commit `8110913d0105b13d3f768a531eb9e95ff2c6fba2`, `formats.resolve_mapping_rule` supports these rules, but the live `arm.plan` requires explicit `title_map`, selected source IDs and complete stream inventories. It does not resolve these rules itself. Consequently these files are importable references, **not a completed deployment of automatic rule-based naming**. The application's local-beta fallback may produce provisional names when the strict planner rejects an incomplete reference.

Integration must resolve the user rule against the current scan, retain its user-confirmed provenance, capture real source inventories, and pass the resolved job mapping to naming. It must distinguish technical failures from missing metadata and must not fabricate stream data or remove checks merely to label these files rip-ready. No live service configuration was changed by this publication.

## Coverage and corrections

| Season | Included discs | Remaining gaps |
|---|---|---|
| 2 | 1–3 | Episodes 13–22 lack a supported physical-disc assignment. E3 preserves supplied packaging transcription `Sherlock Holmes`; research calls it `Sherlock Data Holmes`. |
| 3 | 1–7 | Technical scan verification outstanding. |
| 4 | 1–7 | Technical scan verification outstanding. |
| 5 | 1–3 | E9–12 filled from the cited German episode guide; episodes 13–26 lack a supported physical-disc assignment. |
| 6 | 1, 2, 4–7 | Disc 3 excluded because its four titles belong to DS9. No automatic reassignment to a physical DS9 disc. |
| 7 | 1, 2, 4–7 | Disc 3 absent. Finale E25–26 is one metadata entry; its source structure requires verification. |

Original package files remain untouched. Original photographs were not available for independent re-examination during this correction. Retained packaging spellings and disc layouts come from the supplied package, not a new photographic verification. Research episode lists for missing discs are not converted into invented disc layouts.

Packaging strings such as `Season 6 Part 1` were incorrectly placed in `labels`, which the planner compares to the disc's actual filesystem volume label. They remain in `printed_identifiers`; `labels` is now empty until real volume labels are scanned.

S5E9–12 German title source, checked 2026-10-21: https://www.fernsehserien.de/raumschiff-enterprise-das-naechste-jahrhundert/episodenguide/staffel-5/3803

The contaminated S6D3 input titles were `Statistische Wahrscheinlichkeiten`, `Der glorreiche Ferengi`, `Das Gute und das Böse`, and `Wer trauert um Morn?`. These are not TNG episodes. Preserve the input package as the audit source; do not use that disc entry for matching.

## Media preservation

Retain all available audio and subtitle tracks for every ripped output. That requires ARM/MakeMKV configuration and output validation; these masterlists do not enforce stream selection. German episode titles describe filenames, not an audio-language filter. No angles, playlists, source counts or inventories have been invented.

## Publication scope

This directory contains approved descriptive references with confirmed user mapping rules and explicitly pending technical verification. It is deliberately named `verified-metadata`, not `verified-rips`. Schema and synthetic rule tests do not demonstrate live import, physical ripping or content identity.
