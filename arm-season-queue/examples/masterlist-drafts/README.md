# Borgia and Star Trek: Voyager masterlist drafts

**Importable v2 metadata drafts, not approved or rip-ready configurations.**

These contributor-supplied examples illustrate edition identification, printed
disc labels, episode assignments, and the distinction between printed and library
numbering. They can be imported for recognition and human review. Missing scan data
is shown as incomplete execution readiness; it cannot authorize a rip.

| Collection | Seasons | Edition |
| --- | --- | --- |
| [Borgia](borgia/) | 1–3 | 2016 Studiocanal Gesamtedition, 8 Blu-rays; EAN 4006680082547 |
| [Star Trek: Voyager](voyager/) | 1–7 | German split-season DVD releases |

There are 10 season files covering 54 episode-bearing discs and 208 episode
entries. Combined episodes mean entry counts are not counts of individual episodes
or verified technical video titles. Voyager season 1's extras-only disc 5 is
represented separately from episode-bearing discs.

## What is and is not verified

The contributor's drafts distinguish photographic evidence from online
enrichment in `provenance` and `numbering_note`. Their source references are
preserved; publication does not independently certify every episode assignment.
Original photographs are private and are not distributed. Personal photo
filenames and capture dates were removed from the public copies.

All files use `schema_version: 2` and retain `approved: false`. Descriptive
metadata imports without technical fields. No technical title mappings,
fingerprints or inventory have been fabricated. `completeness_summary.json`
distinguishes metadata importability from rip readiness.

## Using these examples

1. Confirm that your physical release matches the documented edition. A series
   title alone is insufficient.
2. Copy the relevant season file to your private working directory.
3. Check the episode assignments and all unresolved notes against your release.
4. Inspect the actual disc titles with your supported disc-scan workflow. Establish
   eligible source-title count, ordering and explicit output mappings. Do not
   assume that printed episode count equals technical title count.
5. Adjust combined/split episode entries as supported by the scan evidence.
6. Import the draft for metadata/review if useful. Validate against the current
   [schema](../../docs/masterlist.schema.json); resolve identity questions before
   approving and technical blockers before execution.
7. Never merely toggle `approved` or treat metadata importability as rip readiness.

Voyager season 1 uses different printed/library numbering after its combined
pilot. Season 7 also has ordering differences and a combined finale. Preserve
these distinctions when mapping real disc titles.

See each collection's README.txt and [completeness summary](completeness_summary.json).
The older root `examples/masterlists` files use a legacy format; these drafts
target the active `arm-season-queue` data model.
