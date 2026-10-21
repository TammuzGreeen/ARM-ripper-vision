# TOS Blu-ray masterlist draft

`arm_vision_masterlist_v2.json` is a schema-v2 metadata-only draft for Season 1 Disc 6. It is not a complete 20-disc masterlist or an executable extraction plan. It passed the application Masterlist model at repository revision ad8d1ae826850a2b307036d32024d116f77cd63a. Live import and ripping were not tested.

`source_pairs.csv` preserves all eight reported source IDs, four playlists, perspectives and version assignments. Blank episode numbers mean unknown, not episode zero. This CSV is supporting evidence, not an ARM masterlist import file.

## Why title_map is empty

The format requires each title_map entry to reference a real episode or extra. The supplied text gives no episode identities for the four playlist pairs. Inventing episodes 1–4, or disguising unknown episodes as extras, would produce misleading filenames. Once the four episode numbers in playlist order are supplied, the paired title_map can reference the same episode_index twice, with separate source IDs and version values. Do not use the one-source-per-episode mapping rule.

## Companion rules to implement

1. Use explicit MakeMKV disc metadata and edition evidence to identify the disc. Keep reported descriptions distinct from actual filesystem labels.
2. Group real scan sources by playlist and reported perspective; verify paired structure and classify extras separately. Duration alone does not establish episode identity.
3. Apply the supplied confirmed convention: perspective 1 Remastered, perspective 2 Original. Do not ask to reconfirm it on every disc; flag actual contradictions.
4. Bind playlist groups to supplied episode identities. Do not assume playlist numbers or title IDs repeat across discs.
5. Prefer separately enumerated MakeMKV source IDs for the two versions. The current adapter rejects explicit angle selectors; preserve perspective evidence without pretending that unsupported angle commands work.
6. Retain every available audio and subtitle stream in each output and verify against real scan inventories. No stream data has been fabricated here.
7. Requested filenames: `Star Trek TOS SxxExx Remastered.mkv` and `Star Trek TOS SxxExx Original.mkv`, without episode titles. The inspected formatter normally adds an episode title or an Episode N fallback. This exact policy therefore needs a naming implementation change; it cannot be enforced by adding unsupported JSON properties.

## Needed to finish

- Season 1 Disc 6: episode number for each of 00000.mpls, 00001.mpls, 00002.mpls, 00003.mpls, and the numbering convention if ambiguous.
- Raw MakeMKV diagnostic output for source/stream verification.
- For additional discs: their ordered episode lists and corresponding scan metadata.

No hardware was operated and no API calls to recognition models were made. Published to GitHub at the user's request as an incomplete metadata draft; publication does not establish rip readiness. Original source text remains unchanged.
