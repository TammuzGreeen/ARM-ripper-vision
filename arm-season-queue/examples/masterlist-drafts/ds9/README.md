# DS9 German DVD masterlists — corrected schema-v2 references

These files contain user-supplied photographic transcriptions and user-confirmed per-disc ordinal mapping rules. They remain `approved: false`: publication and software validation do not establish technical rip readiness or approval of all descriptive details.

Import the individual season JSON files through ARM Vision's masterlist importer. The README and validation.json are supporting documents, not masterlists. Use compatible references after unrestricted disc recognition; do not treat selecting a reference as evidence that an inserted disc has that identity.

| Season | Disc coverage | Episode coverage | Entries |
|---|---|---|---|
| 2 | 4–7 | 13–26 | 14 |
| 5 | 1–7 | 1–26 | 26 |
| 6 | 1–3 | 1–12 | 12 |
| 7 | 1–7 | 1–26 | 26 |

Total: 21 disc records and 78 episode entries. Missing discs have not been invented.

## Correction and validation, 2026-10-21

The supplied files passed JSON Schema validation but failed the current application's Masterlist validator because every disc included both mapping_rules and title_map. Removed the unscanned expanded title_map entries and preserved mapping_rules. Episode titles, numbering, disc groupings and extras were not changed.

All four corrected files pass application validation against repository revision ad8d1ae826850a2b307036d32024d116f77cd63a. Synthetic rule resolution and output-name uniqueness checks pass for all discs; extra scan IDs are rejected. JSON Schema validation is also performed on the corrected files before publication. These checks do not establish successful import into the live service or physical source ordering.

The mapping convention is source 0 → first listed episode on that disc, source 1 → second, and so on, restarting at 0 on every disc. For example, Season 2 Disc 4 sources 0–3 correspond to episodes 13–16. The application must resolve the rule against actual scan results before treating it as an explicit technical mapping. Do not store the unresolved rule alongside its expanded title_map.

## Evidence boundaries

- Original photographs were not supplied for this review. Packaging transcription and exact disc grouping remain attributable to the user's input package, not independent photographic re-verification.
- Season 7 Disc 7 lists finale parts 1 and 2. A scan must establish whether this edition exposes two separate sources or a combined title.
- Extras have no verified source IDs. Do not silently assign bonus sources to episode positions.
- German Paramount DVD is a broad edition descriptor; no unique pressing fingerprint or scanned filesystem label is claimed.
- Technical inventories, DVD title numbers and physical content identity remain unverified. Do not fill them with guesses to bypass readiness checks.
- Preserve every audio and subtitle track through ARM/MakeMKV settings and output checks. The masterlist's provenance text does not itself enforce stream selection.

The original evidence report's claim that both mapping representations are encoded intentionally is superseded by this correction. Original input files remain unchanged outside the repository.
