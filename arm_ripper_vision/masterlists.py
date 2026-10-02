from dataclasses import dataclass
from pathlib import Path
import yaml
from pydantic import BaseModel, Field, field_validator

class SeriesInfo(BaseModel):
    name: str
    year: int | None = None
    imdb_id: str | None = None
class EpisodeEntry(BaseModel):
    episode: int = Field(ge=1)
    title: str
    source_title: int | None = Field(default=None, ge=0)
    edition: str | None = None
class ExtraEntry(BaseModel):
    title: str | None = None
    category: str | None = None
    source_title: int | None = Field(default=None, ge=0)
class DiscEntry(BaseModel):
    disc: int = Field(ge=1)
    label: str | None = None
    episodes: list[EpisodeEntry] = Field(default_factory=list)
    extras: list[ExtraEntry] = Field(default_factory=list)
class SeasonEntry(BaseModel):
    season: int = Field(ge=0)
    discs: list[DiscEntry] = Field(min_length=1)
class NamingConfig(BaseModel):
    episode_template: str = "{series} - S{season:02d}E{episode:02d} - {title}{edition_suffix}"
    extra_template: str = "{series} - {title}"
class Masterlist(BaseModel):
    schema_version: int = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    series: SeriesInfo
    naming: NamingConfig = Field(default_factory=NamingConfig)
    seasons: list[SeasonEntry] = Field(min_length=1)
    @field_validator("schema_version")
    @classmethod
    def schema_v1(cls,v):
        if v != 1: raise ValueError("unsupported schema_version")
        return v
    def flatten_discs(self):
        return [DiscPosition(s.season,d.disc,d) for s in self.seasons for d in s.discs]
    def position_index(self,season,disc):
        for i,p in enumerate(self.flatten_discs()):
            if (p.season,p.disc)==(season,disc): return i
        raise KeyError(f"Season {season} disc {disc} does not exist")
@dataclass(frozen=True)
class DiscPosition:
    season:int
    disc:int
    entry:DiscEntry
    @property
    def key(self): return f"S{self.season:02d}D{self.disc:02d}"
class MasterlistStore:
    def __init__(self,directory:Path): self.directory=directory
    def load_all(self):
        out={}
        if not self.directory.exists(): return out
        for path in sorted([*self.directory.glob("*.yaml"),*self.directory.glob("*.yml")]):
            ml=Masterlist.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            if ml.id in out: raise ValueError(f"Duplicate masterlist id {ml.id}")
            out[ml.id]=ml
        return out
    def get(self,masterlist_id):
        return self.load_all()[masterlist_id]
