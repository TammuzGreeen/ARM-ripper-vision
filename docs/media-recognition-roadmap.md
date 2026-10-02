# Recognition beyond TV

`arm-season-queue/app/media_types.py` defines a shared label-evidence record,
review-required identity candidates and a recognition adapter protocol. Movie,
music and audiobook adapters are explicit stubs raising `NotImplementedError`.
They are not wired into the running TV controller, database or masterlist schema.
There is no non-TV processing or automatic media-type selection yet.

Planned fields and separate validation work:

| Type | Printed identity evidence | Must be verified independently |
| --- | --- | --- |
| Movie | Title, release year, edition, barcode, catalogue ID | Film vs extras, cuts, angles, source-title mapping |
| Music | Album, artist, label, catalogue ID, disc number, track list | Track order, durations, audio metadata and output format |
| Audiobook | Title, author, narrator, publisher, edition, disc number | Chapters, disc order, abridged status and audio output format |

Next implementation steps: collect labelled fixtures for each type; implement
type-specific parsers and uncertainty reporting; add versioned user-maintained
masterlist schemas; match evidence to exact releases; add review UI; then add
separately tested ARM/output adapters. External metadata providers, if introduced,
must be optional and must preserve provenance and disagreement.

The camera only supplies evidence. It never creates or supplements the user's
masterlists. Neither OCR nor a barcode proves playable contents or track mappings.
No adapter may enable ripping merely because an identity candidate exists.
