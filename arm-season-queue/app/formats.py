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
    title: str | None = Field(default=None, min_length=1)
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


class TitleMap(Model):
    makemkv_id: int = Field(ge=0)
    dvd_title: int | None = Field(default=None, ge=1)
    angle: int | None = Field(default=None, ge=1)
    episode_index: int | None = Field(default=None, ge=0)
    extra_id: str | None = None
    version: str | None = Field(default=None, min_length=1)
    output_name: str | None = Field(default=None, min_length=1)
    evidence: str = ''
    inventory: Inventory | None = None


class Extra(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    title: str | None = Field(default=None, min_length=1)
    description: str = ''
    provenance: list[str] = Field(default_factory=list)


class MappingRule(Model):
    kind: Literal['ascending_makemkv_id_to_episode_index']
    edition: str = Field(min_length=1)
    first_makemkv_id: int = Field(default=0, ge=0)
    first_episode_index: int = Field(default=0, ge=0)
    evidence: str = Field(min_length=1)


class Disc(Model):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    number: int = Field(ge=1)
    printed_identifiers: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    episodes: list[Episode] = Field(default_factory=list)
    extras: list[Extra] = Field(default_factory=list)
    extras_only: bool = False
    expected_title_count: int | None = Field(default=None, ge=0)
    order: Literal['ascending_dvd_title', 'explicit'] | None = None
    selection_ids: list[int] = Field(default_factory=list)
    title_map: list[TitleMap] = Field(default_factory=list)
    mapping_rules: list[MappingRule] = Field(default_factory=list)
    structural_signature: str | None = None
    inventory: Inventory = Field(default_factory=Inventory)
    unresolved: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def coherent(self):
        if self.extras_only and self.episodes:
            raise ValueError('An extras-only disc cannot contain episode entries')
        if len({extra.id for extra in self.extras}) != len(self.extras):
            raise ValueError('Extra IDs must be unique within a disc')
        if self.expected_title_count is not None and self.selection_ids and self.expected_title_count != len(set(self.selection_ids)):
            raise ValueError('expected_title_count describes distinct source titles and must match selection_ids')
        if self.title_map:
            output_keys = [(t.makemkv_id, t.angle or 1, t.version or '') for t in self.title_map]
            if len(set(output_keys)) != len(output_keys):
                raise ValueError('Duplicate source/angle/version output mapping')
            for item in self.title_map:
                if (item.episode_index is None) == (item.extra_id is None):
                    raise ValueError('Each output maps to exactly one episode or extra')
                if item.episode_index is not None and item.episode_index >= len(self.episodes):
                    raise ValueError('Episode mapping index is outside this disc episode list')
                if item.extra_id is not None and item.extra_id not in {extra.id for extra in self.extras}:
                    raise ValueError('Extra mapping refers to an unknown extra ID')
            if self.selection_ids and set(self.selection_ids) != {t.makemkv_id for t in self.title_map}:
                raise ValueError('selection_ids and output mappings disagree')
            if self.order == 'ascending_dvd_title':
                if any(t.dvd_title is None for t in self.title_map):
                    raise ValueError('DVD-title order requires evidenced DVD title numbers')
                if len({t.makemkv_id for t in self.title_map}) != len(self.title_map):
                    raise ValueError('Ascending DVD-title order cannot represent multiple outputs per source title')
                ordered = sorted(self.title_map, key=lambda t: (t.dvd_title, t.angle or 1))
                if [t.episode_index for t in ordered] != list(range(len(self.episodes))):
                    raise ValueError('Mapping disagrees with ascending DVD-title policy')
        if self.mapping_rules and (self.title_map or self.selection_ids):
            raise ValueError('Resolve mapping rules into explicit scan-backed outputs before adding title_map/selection_ids')
        return self

    def inventory_for(self, title_map: TitleMap) -> Inventory:
        """A title override is authoritative; incomplete overrides never fall back."""
        return title_map.inventory if title_map.inventory is not None else self.inventory


class Masterlist(Model):
    schema_version: Literal[1, 2] = 1
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]+$')
    series: str = Field(min_length=1)
    season: int = Field(ge=0, le=99)
    provider_ids: dict[str, str] = Field(default_factory=dict)
    edition: str | None = Field(default=None, min_length=1)
    edition_name: str | None = Field(default=None, min_length=1)
    title_aliases: list[str] = Field(default_factory=list)
    edition_tokens: list[str] = Field(default_factory=list)
    approved: bool = False
    provenance: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    discs: list[Disc] = Field(min_length=1)

    @model_validator(mode='after')
    def distinct(self):
        if self.schema_version == 1:
            if not self.edition or not self.edition_name or not self.edition_tokens:
                raise ValueError('Masterlist v1 requires edition identity and edition_tokens; migrate drafts explicitly to v2')
            for disc in self.discs:
                if not disc.episodes or disc.expected_title_count is None or disc.order is None:
                    raise ValueError('Masterlist v1 technical fields are unchanged; migrate metadata-only drafts explicitly to v2')
                if disc.expected_title_count != len(disc.episodes):
                    raise ValueError('Masterlist v1 requires one episode entry per eligible source title')
                if disc.title_map and (any(item.episode_index is None or item.extra_id is not None
                                            for item in disc.title_map)
                                       or sorted(item.episode_index for item in disc.title_map) != list(range(len(disc.episodes)))):
                    raise ValueError('Masterlist v1 mappings must remain one-source-title-to-one-episode')
                if any(not episode.title for episode in disc.episodes):
                    raise ValueError('Masterlist v1 episode titles cannot be unknown')
        elif self.schema_version == 2:
            if self.approved and not self.edition:
                raise ValueError('Approved masterlist v2 requires an identified edition')
        if len({d.id for d in self.discs}) != len(self.discs) or len({d.number for d in self.discs}) != len(self.discs):
            raise ValueError('Disc IDs and numbers must be unique')
        if self.schema_version == 2:
            for disc in self.discs:
                if disc.expected_title_count is not None and disc.selection_ids and disc.expected_title_count != len(set(disc.selection_ids)):
                    raise ValueError(f'Disc {disc.number}: expected_title_count describes distinct source titles and must match selection_ids')
                if disc.title_map and disc.selection_ids and set(disc.selection_ids) != {item.makemkv_id for item in disc.title_map}:
                    raise ValueError(f'Disc {disc.number}: selection_ids and output mappings disagree')
        names = []
        for disc in self.discs:
            if disc.title_map:
                for mapped in disc.title_map:
                    names.append(output_destination(self, disc, mapped))
            else:
                names.extend(destination(self, e) for e in disc.episodes if e.title)
        if len(set(names)) != len(names):
            raise ValueError('Selected episode/extra/version outputs produce colliding destination filenames')
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
    label = safe_name(ep.title or f'Episode {ep.number}')
    edition = safe_name(master.edition_name or master.edition or 'Unspecified edition')
    filename = safe_name(f'{title} {code} - {label} - {edition}{part}') + '.mkv'
    return str(PurePosixPath('tv', title, f'Season {master.season:02d}', filename))


def output_destination(master, disc, mapped):
    title = safe_name(master.series)
    edition = safe_name(master.edition_name or master.edition or 'Unspecified edition')
    version = f' - {safe_name(mapped.version)}' if mapped.version else ''
    if mapped.episode_index is not None:
        episode = disc.episodes[mapped.episode_index]
        if episode.title is None:
            label = f'Episode {episode.number}'
        else:
            label = safe_name(episode.title)
        code = f'S{master.season:02d}E{episode.number:02d}'
        if episode.end is not None:
            code += f'-E{episode.end:02d}'
        if episode.part:
            code += f' - part{episode.part}'
        filename = safe_name(f'{title} {code} - {label}{version} - {edition}') + '.mkv'
        return str(PurePosixPath('tv', title, f'Season {master.season:02d}', filename))
    extra = next(value for value in disc.extras if value.id == mapped.extra_id)
    label = safe_name(mapped.output_name or extra.title or extra.id)
    filename = safe_name(f'{title} S{master.season:02d}D{disc.number:02d} EXTRA - {label}{version} - {edition}') + '.mkv'
    return str(PurePosixPath('tv', title, f'Season {master.season:02d}', 'Extras', filename))


def rip_readiness(master, disc):
    """Return actionable execution blockers, keeping useful metadata importable."""
    issues = []
    if master.schema_version == 1 and (not disc.title_map or disc.expected_title_count is None):
        issues.append('Legacy v1 mapping/count fields are incomplete; migrate deliberately to v2')
    if disc.unresolved:
        issues.extend(disc.unresolved)
    if disc.expected_title_count is None:
        issues.append('Source title count has not been established by a technical scan')
    if disc.order is None:
        issues.append('Source selection order has not been established by a technical scan or explicit mapping')
    if not disc.selection_ids and (master.schema_version != 1 or not disc.title_map):
        issues.append('Selected source-title IDs are missing')
    if not disc.title_map:
        issues.append('Explicit source-title-to-output mappings are missing')
    if disc.expected_title_count is not None and disc.selection_ids and disc.expected_title_count != len(set(disc.selection_ids)):
        issues.append('Expected source-title count does not equal the distinct selected source-title count')
    if disc.selection_ids and disc.title_map and set(disc.selection_ids) != {item.makemkv_id for item in disc.title_map}:
        issues.append('Selection IDs and output mappings do not agree')
    if disc.mapping_rules:
        issues.append('User-supplied mapping rule must be resolved against the actual scan')
    for item in disc.title_map:
        if item.angle is not None:
            issues.append(f'Optical angle selection for MakeMKV ID {item.makemkv_id} is not supported by this ARM API adapter')
        inventory = disc.inventory_for(item)
        if not inventory.complete or not inventory.evidence:
            issues.append(f'Complete source stream inventory is missing for MakeMKV ID {item.makemkv_id}')
        if item.episode_index is not None and not disc.episodes[item.episode_index].title:
            issues.append(f'Episode title is unknown for output mapped from MakeMKV ID {item.makemkv_id}')
        if item.extra_id is not None:
            extra = next((extra for extra in disc.extras if extra.id == item.extra_id), None)
            if not extra or not (item.output_name or extra.title):
                issues.append(f'Extra output name is missing for MakeMKV ID {item.makemkv_id}')
    if master.approved and not master.edition:
        issues.append('Edition identity is missing')
    return list(dict.fromkeys(issues))


def resolve_mapping_rule(master, disc, scan_ids, dvd_titles=None):
    """Resolve a scoped user ID-order rule only when the scan exactly supports it."""
    if len(disc.mapping_rules) != 1:
        raise ValueError('Exactly one explicit mapping rule is required')
    rule = disc.mapping_rules[0]
    if rule.edition != master.edition:
        raise ValueError('Mapping rule edition scope does not match this masterlist')
    ids = sorted(int(value) for value in scan_ids)
    expected_ids = list(range(rule.first_makemkv_id, rule.first_makemkv_id + len(disc.episodes)))
    if ids != expected_ids:
        raise ValueError('Technical scan IDs contradict the user-supplied mapping rule')
    if rule.first_episode_index + len(ids) > len(disc.episodes):
        raise ValueError('Mapping rule episode range exceeds disc metadata')
    dvd_titles = dvd_titles or {}
    return [TitleMap(makemkv_id=makemkv_id, dvd_title=dvd_titles.get(makemkv_id),
                     episode_index=rule.first_episode_index + offset,
                     evidence=rule.evidence)
            for offset, makemkv_id in enumerate(ids)]


def digest(value):
    if isinstance(value, BaseModel):
        value = value.model_dump()
    # A null title-level inventory means "use the disc default". Before that
    # field existed, older masterlist snapshots naturally omitted it. Normalize
    # the two equivalent representations so existing batch/master hashes survive
    # the additive schema extension; non-null overrides remain hash-significant.
    if isinstance(value, dict) and 'schema_version' in value and 'discs' in value:
        value = dict(value)
        discs = []
        for disc in value.get('discs', []):
            if not isinstance(disc, dict):
                discs.append(disc)
                continue
            disc = dict(disc)
            if isinstance(disc.get('title_map'), list):
                disc['title_map'] = [
                    ({k: v for k, v in item.items() if not (
                        (k == 'inventory' and v is None)
                        or (value.get('schema_version') == 1 and k in ('extra_id', 'version', 'output_name') and v is None)
                    )}
                     if isinstance(item, dict) else item)
                    for item in disc['title_map']
                ]
            if value.get('schema_version') == 1:
                disc.pop('extras', None)
                disc.pop('extras_only', None)
                disc.pop('mapping_rules', None)
            discs.append(disc)
        value['discs'] = discs
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
