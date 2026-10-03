"""Planned fingerprint lookup contract; no hashing, network lookup or cache yet.

Provider-specific algorithm IDs must stay distinct. A database match is a
candidate until its source-title mapping and edition are checked locally.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscFingerprint:
    algorithm: str
    value: str


def lookup_disc(fingerprint: DiscFingerprint) -> dict:
    raise NotImplementedError('Local/TheDiscDB/OVID lookup is planned, not connected')
