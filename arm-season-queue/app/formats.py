"""Versioned masterlists; printed numbers remain separate from library numbers."""
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Episode(Model):
    printed: str
    number: int = Field(ge=1, le=999)
    title: str = Field(min_length=1)
    end: int | None = Field(default=None, ge=1, le=999)
    part: int | None = Field(default=None, ge=1)
    numbering_note: str = ''

    @model_validator(mode='after')
    def numbering(self):
        if self.end is not None and self.end < self.number:
            raise ValueError('Combined episode end precedes start')
        if self.printed != str(self.number) and not self.numbering_note:
            raise ValueError('Printed/library numbering difference needs numbering_note')
        return self


class TitleMap(Model):
    makemkv_id: int = Field(ge=0)
    dvd_title: int | None = Field(default=None, ge=1)
    angle: int | None = Field(default=None, ge=1)
    episode_index: int = Field(ge=0)
    evidence: str = Field(min_length=1)


class Inventory(Model):
    video_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    chapters: int | None = None
    audio: list[str] = Field(default_factory=list)
    subtitles: list[str] = Field(default_factory=list)
    audio_channels: list[int] = Field(default_factory=list)
    complete: bool = False
    evidence: str = ''


class Disc(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    number: int = Field(ge=1)
    printed_identifiers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    episodes: list[Episode] = Field(min_length=1)
    expected_title_count: int = Field(ge=1)
    order: Literal['ascending_dvd_title', 'explicit']
    selection_ids: list[int] = Field(default_factory=list)
    title_map: list[TitleMap] = Field(default_factory=list)
    structural_signature: str | None = None
    inventory: Inventory = Field(default_factory=Inventory)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent(self):
        if self.expected_title_count != len(self.episodes):
            raise ValueError('One episode entry (including combined/part entries) per eligible title required')
        if self.title_map:
            if len({t.makemkv_id for t in self.title_map}) != len(self.title_map):
                raise ValueError('Duplicate MakeMKV ID')
            if sorted(t.episode_index for t in self.title_map) != list(range(len(self.episodes))):
                raise ValueError('Title mapping must cover every episode entry exactly once')
            if self.selection_ids and set(self.selection_ids) != {t.makemkv_id for t in self.title_map}:
                raise ValueError('selection_ids and title_map disagree')
            if self.order == 'ascending_dvd_title':
                if any(t.dvd_title is None for t in self.title_map):
                    raise ValueError('DVD-title order requires evidenced DVD title numbers')
                ordered = sorted(self.title_map, key=lambda t: (t.dvd_title, t.angle or 1))
                if [t.episode_index for t in ordered] != list(range(len(self.episodes))):
                    raise ValueError('Mapping disagrees with ascending DVD-title policy')
        return self


class Masterlist(Model):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    series: str = Field(min_length=1)
    season: int = Field(ge=0, le=99)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    edition: str = Field(min_length=1)
    edition_name: str = Field(min_length=1)
    title_aliases: list[str] = Field(default_factory=list)
    edition_tokens: list[str] = Field(min_length=1)
    approved: bool = False
    provenance: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    discs: list[Disc] = Field(min_length=1)

    @model_validator(mode='after')
    def distinct(self):
        if len({d.id for d in self.discs}) != len(self.discs) or len({d.number for d in self.discs}) != len(self.discs):
            raise ValueError('Disc IDs and numbers must be unique')
        names = [destination(self, e) for d in self.discs for e in d.episodes]
        if len(set(names)) != len(names):
            raise ValueError('Episodes produce colliding destination filenames')
        if self.approved and self.unresolved:
            raise ValueError('Resolve masterlist-level questions before approving')
        return self


def safe_name(text):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text).strip(' .')
    if not name or name in ('.', '..') or len(name.encode('utf-8')) > 180:
        raise ValueError('Empty or overlong filename component')
    return name


def destination(master, ep):
    code = f'S{master.season:02d}E{ep.number:02d}'
    if ep.end is not None:
        code += f'-E{ep.end:02d}'
    part = f' - part{ep.part}' if ep.part else ''
    title = safe_name(master.series)
    filename = safe_name(f'{title} {code} - {ep.title} - {master.edition_name}{part}') + '.mkv'
    return str(PurePosixPath('tv', title, f'Season {master.season:02d}', filename))


def digest(value):
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
