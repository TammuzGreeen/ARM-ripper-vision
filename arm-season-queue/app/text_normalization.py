"""Language-neutral parsing helpers for printed disc-label field names.

Only structural labels (season, disc, episode/range) are canonicalized. Series,
edition names, identifiers, episode titles and the retained source transcription
are never translated or rewritten.
"""
import re


_STRUCTURAL_LABELS = (
    (re.compile(r"\b(?:episodes?|episoden?|épisodes?|episodios?|folgen?)\b", re.IGNORECASE), "episode"),
    (re.compile(r"\b(?:seasons?|staffeln?|saisons?|temporadas?)\b", re.IGNORECASE), "season"),
    (re.compile(r"\b(?:discs?|disks?|dvds?|disques?|discos?)\b", re.IGNORECASE), "disc"),
)


def normalize_structural_labels(text: str) -> str:
    """Canonicalize supported EN/DE/FR/ES printed field labels for parsing."""
    result = text or ""
    for pattern, canonical in _STRUCTURAL_LABELS:
        result = pattern.sub(canonical, result)
    return result
