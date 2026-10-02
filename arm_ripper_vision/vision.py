from dataclasses import dataclass, field
from typing import Protocol
@dataclass(frozen=True)
class DiscVisionCandidate:
    series: str | None = None
    season: int | None = None
    disc: int | None = None
    release: str | None = None
    visible_text: tuple[str,...] = field(default_factory=tuple)
    catalog_number: str | None = None
    confidence: float | None = None
class DiscVisionProvider(Protocol):
    async def status(self) -> dict[str,object]: ...
    async def observe(self) -> list[DiscVisionCandidate]: ...
class DisabledVisionProvider:
    async def status(self): return {"enabled":False,"reason":"Vision is planned for v2"}
    async def observe(self): return []
