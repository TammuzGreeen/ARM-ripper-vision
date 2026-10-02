"""Future recognition contracts; not connected to the TV queue or masterlist editor.

Observations are evidence, never masterlist mutations or permission to rip.
Non-TV adapters deliberately fail until release-aware matching is implemented.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class MediaType(str, Enum):
    TV = 'tv'
    MOVIE = 'movie'
    MUSIC = 'music'
    AUDIOBOOK = 'audiobook'


@dataclass(frozen=True)
class LabelEvidence:
    raw_text: str
    image_refs: tuple[str, ...] = ()
    barcodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityCandidate:
    media_type: MediaType
    title: str
    # Examples: release year, artist, author, narrator, edition, catalogue ID.
    printed_fields: tuple[tuple[str, str], ...] = ()
    evidence_refs: tuple[str, ...] = ()
    requires_review: bool = True


class RecognitionAdapter(Protocol):
    media_type: MediaType

    def identify(self, evidence: LabelEvidence) -> tuple[IdentityCandidate, ...]: ...


class PlannedRecognitionAdapter:
    """Explicit unsupported result, never a confident empty or fabricated match."""

    def __init__(self, media_type: MediaType):
        if media_type == MediaType.TV:
            raise ValueError('TV recognition remains in app.recognition')
        self.media_type = media_type

    def identify(self, evidence: LabelEvidence) -> tuple[IdentityCandidate, ...]:
        raise NotImplementedError(f'{self.media_type.value} recognition is planned, not implemented')


PLANNED_ADAPTERS = {
    kind: PlannedRecognitionAdapter(kind)
    for kind in (MediaType.MOVIE, MediaType.MUSIC, MediaType.AUDIOBOOK)
}
