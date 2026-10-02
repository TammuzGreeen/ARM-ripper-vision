# Versioned formats

## Masterlist v1

You supply and supplement the masterlist: one UTF-8 YAML/JSON file per series/season/edition. Camera and photo recognition only provide evidence for identification and consistency checks. They cannot add, replace or edit entries; conflicts are held for your review. The canonical validation schema is `masterlist.schema.json` (also served at `/api/schema/masterlist`). Unknown keys are rejected rather than silently ignored. The DS9 example is complete for the supplied Disc 1 evidence and deliberately unresolved for unobserved discs.

Important fields:

| Field | Meaning |
|---|---|
| `schema_version` | Must be `1` |
| `id` | Stable portable identifier; letters, digits, underscore, hyphen |
| `series`, `season` | Authoritative series and library season |
| `provider_ids` | Optional string mapping for provenance; never used to overwrite printed evidence |
| `edition`, `edition_name` | Stable release ID and human-readable filename suffix |
| `title_aliases` | Exact normalised title phrases actually visible on media |
| `edition_tokens` | Every token/phrase must appear in at least two good frames, or as a decoded barcode |
| `approved`, `unresolved` | Initial approval and remaining questions; approved lists cannot have master-level unresolved fields |
| `provenance` | Source descriptions, including `recognition:<event-uuid>` references that protect images from retention cleanup |
| `discs[].number` | The printed disc number, independent of insertion order |
| `printed_identifiers` | Optional exact catalogue/text/barcode identifiers; at least one required when supplied |
| `labels` | Optional known scan volume labels, used as an insertion consistency check, never as primary identity |
| `episodes` | Printed sequence, with separate printed number, library number/title, optional combined `end`, optional `part`, and explicit `numbering_note` |
| `expected_title_count` | Eligible source-title count, equal to episode-entry count (a combined/part entry represents one output) |
| `selection_ids` | Optional explicit MakeMKV IDs. Without these, every scanned title is eligible; unexpected extras block |
| `order` | `ascending_dvd_title` or `explicit`; runtime never changes this order |
| `title_map` | Evidenced MakeMKV ID → original DVD title/angle → zero-based episode entry index |
| `structural_signature` | Optional SHA-256 of normalised source-title inventory from the actual API; a mismatch blocks |
| `inventory` | Independently evidenced expected video codec, size/FPS/chapters, full audio/subtitle language lists with duplicates, optional audio channels |
| `discs[].unresolved` | Initial mapping/inventory/release questions that block only that disc |

`track_id` from ARM is a database row ID used only for API updates. `track_number` from this inspected ARM source is the MakeMKV ID. `dvd_title` is a different identifier and cannot be inferred arithmetically. The source/angle fields are provenance for an approved MakeMKV mapping, not instructions to invent an unexposed angle. If different angles are not independently selectable in ARM/MakeMKV's scan, this adapter does not invent a selection mechanism.

`ascending_dvd_title` requires a supplied mapping with original DVD-title numbers in the same order as the episode entries. The sample's Disc 1 has IDs 0–3 and DVD titles 1–4 because the owner explicitly accepted those mappings. Disc 2/3 do not inherit that mapping.

The known DS9 durations are approximately 43:34, 43:31, 43:33 and 43:35. They are validation context, not episode matching evidence. Output duration tolerance is max(15 seconds, 1% of scanned duration). Container timestamps can differ by seconds without indicating an incorrect episode.

Audio/subtitle language lists are multisets: two English subtitle streams must remain two. `ger/deu`, `fre/fra`, and `dut/nld` normalise to the same ISO language. The validator requires the complete inventory and its provenance. Empty lists mean the source has no streams of that type, not “unknown.” A missing/incomplete inventory blocks initial ripping and also fails output validation. It does not become a claim that all source tracks were preserved.

Combined episodes use e.g. `number: 1, end: 2`, with `printed: '1-2'` and a numbering note explaining that representation. Multipart files use `part: 1`, `part: 2` on otherwise matching episode entries. Explicit mappings can reorder entries when the release evidence requires it; do not use `ascending_dvd_title` for a contrary mapping.

## Library naming

Destinations are relative to `LIBRARY_HOST`:

```text
tv/Star Trek Deep Space Nine/Season 02/
  Star Trek Deep Space Nine S02E01 - Die Heimkehr - German DVD Part 1.mkv
```

Original/remastered effects can use separate masterlists and different edition suffixes under the same series/season directory. Jellyfin's [current TV naming documentation](https://jellyfin.org/docs/general/server/media/shows/#multiple-versions) describes grouping same-episode versions in the same season folder. This scheme follows that documentation; grouping has **not** been tested against a live Jellyfin deployment. Combined episode and part suffixes follow the same page's documented formats. Do not assume version labels establish visual content: the physical edition and approved mapping establish that identity.

## Recognition evidence

An event has a UUID, creation time, input source, released flag, optional unique ARM job ID, status and structured body. `result.frames[]` retains cropped JPEG references, raw OCR text, word-filtered confidence, detected rotation, season/disc, conflicts, episode numbers/ranges/titles, title/edition candidates, barcodes, catalogue candidates and language words. `result.accepted` means extraction consensus, not a guessed metadata provider match. `matches[]` is produced afterwards against all approved lists.

Typical lifecycle:

```text
processing → ready → bound
           ↘ review → ready (explicit evidence review)
unpaired events → expired / invalidated
unprotected old images → evidence_expired
```

Photographs do not create masterlists, episodes or DVD-title mappings. You maintain these fields through manual import/editing. Recognition evidence may support your review, and you can manually add `recognition:<uuid>` provenance references when useful. OCR observations remain separate from the masterlist; discrepancies do not overwrite your entries.

## Persistent association and reservations

SQLite uses WAL, `synchronous=FULL`, immediate transactions, unique job/event IDs and a partial unique index on nonfailed disc reservations. Each controller startup baselines existing ARM jobs. Only a newly observed configured-drive insertion can automatically pair with exactly one released, fresh event. A continuously visible presentation stays latched. A new presentation invalidates older unused evidence even if unreadable. Polling outages discard unused events and rebaseline, preferring a one-time review over stale assignment.

The reservation retains batch/masterlist snapshot and digest, event/evidence, job identity (`job_id`, `start_time`, drive and source type), observed label, source-title structure digest, actual ARM track IDs, approved MakeMKV/DVD-title mapping, episode/destination plan and naming preview. A reused job ID with a changed identity blocks recovery. If moving to another ARM database/server, use a new state directory and preserve old manifests.

Reservation states: `reserved`, `starting`, `ripping`, `validating`, `ripped`, `review`, `failed`, `cancelled`. Publication is separately `pending` or `published`. `starting` is written before the remote start call. An uncertain start is reconciled from ARM state and requires explicit retry if still waiting without a start flag. There is no claim of a distributed transaction across SQLite and ARM; reconciliation plus idempotent metadata updates handle that boundary.

`ripped` is written only after a verified ready manifest exists durably. A crash between manifest publication and SQLite update is recovered through identical content comparison. Failed/cancelled reservations do not consume their disc. Reinsertion needs a fresh event. Progress follows the actual reserved disc entry, not insertion position.
