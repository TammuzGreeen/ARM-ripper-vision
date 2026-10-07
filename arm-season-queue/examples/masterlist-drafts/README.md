# Borgia and Star Trek: Voyager masterlist drafts

**Evidence drafts, not import-ready or approved ripping configurations.**

These contributor-supplied examples illustrate edition identification, printed
disc labels, episode assignments, and the distinction between printed and library
numbering. They can be used for manual metadata comparisons and development of
draft-handling workflows. The active queue's strict masterlist importer rejects
them until the missing technical fields are completed.

| Collection | Seasons | Edition |
| --- | --- | --- |
| [Borgia](borgia/) | 1–3 | 2016 Studiocanal Gesamtedition, 8 Blu-rays; EAN 4006680082547 |
| [Star Trek: Voyager](voyager/) | 1–7 | German split-season DVD releases |

There are 10 season files covering 54 episode-bearing discs and 208 episode
entries. Combined episodes mean entry counts are not counts of individual episodes
or verified technical video titles. Voyager season 1's extras-only disc 5 is
documented but not represented as an episode-bearing disc.

## What is and is not verified

The contributor's drafts distinguish photographic evidence from online
enrichment in `provenance` and `numbering_note`. Their source references are
preserved; publication does not independently certify every episode assignment.
Original photographs are private and are not distributed. Personal photo
filenames and capture dates were removed from the public copies.

All files retain `approved: false`. Validation against the active application
model at commit `4fe79add8f99c2fc0730fdd8afd0449b6a55a234` reports the expected
missing `expected_title_count` and `order` fields for all 54 discs. No technical
title mappings, fingerprints or inventory have been fabricated.

## Using these examples

1. Confirm that your physical release matches the documented edition. A series
   title alone is insufficient.
2. Copy the relevant season file to your private working directory.
3. Check the episode assignments and all unresolved notes against your release.
4. Inspect the actual disc titles with your supported disc-scan workflow. Establish
   eligible-title count, ordering and any required explicit title mapping. Do not
   assume that printed episode count equals technical title count.
5. Adjust combined/split episode entries as supported by the scan evidence.
6. Validate against the current [schema](../../docs/masterlist.schema.json) and
   application validator; resolve blockers and explicitly review before approval.
7. Only then import for supervised setup testing. Do not bulk-import this draft
   directory or merely toggle `approved` to bypass review.

Voyager season 1 uses different printed/library numbering after its combined
pilot. Season 7 also has ordering differences and a combined finale. Preserve
these distinctions when mapping real disc titles.

See each collection's README.txt and [completeness summary](completeness_summary.json).
The older root `examples/masterlists` files use a legacy format; these drafts
target the active `arm-season-queue` data model.
