"""Opt-in, count-gated ordinal mapping for one local-beta job only."""

import time

MIN_RELEVANT_TITLE_SECONDS = 30


def infer_ordinal_mapping(result, tracks, *, enabled, min_length=MIN_RELEVANT_TITLE_SECONDS):
    """Return a traceable inferred map, or a reason it was not safe to infer.

    The candidate set is the ARM-prescanned tracks at least ``min_length``
    seconds long. Shorter titles remain selectable provisional outputs; they
    are not shifted into the episode sequence. Count equality is only a gate,
    never evidence that the inferred mapping is accurate.
    """
    if not enabled:
        return None, 'Automatic ordinal mapping is disabled'
    if not isinstance(result, dict):
        return None, 'Recognition result is unavailable'
    agreement = result.get('agreement') or {}
    if (result.get('accepted') is not True or agreement.get('accepted') is not True
            or agreement.get('status') != 'PASS'):
        return None, 'Recognition did not pass its field-evidence gate'
    if agreement.get('field_disagreements') or agreement.get('operational_failures'):
        return None, 'Recognition has field disagreements or operational failures'
    identity = agreement.get('working_identification') or {}
    formats = identity.get('disc_format') or []
    if isinstance(formats, str):
        formats = [formats]
    formats = {str(value).casefold().replace('-', '').replace(' ', '') for value in formats}
    if 'dvd' not in formats or any('bluray' in value or 'playlist' in value for value in formats):
        return None, 'Ordinal mapping is scoped to positively identified DVDs, not Blu-ray/playlist sources'
    series = identity.get('series_display') or identity.get('series')
    season = identity.get('season')
    episodes = identity.get('episodes')
    if not isinstance(series, str) or not series.strip():
        return None, 'Recognized series is incomplete'
    if isinstance(season, bool) or not isinstance(season, int) or season < 1:
        return None, 'Recognized season is incomplete'
    if (not isinstance(episodes, list) or not episodes
            or any(isinstance(ep, bool) or not isinstance(ep, int) or ep < 1 for ep in episodes)):
        return None, 'Printed episode range is incomplete'
    if episodes != sorted(set(episodes)) or episodes != list(range(episodes[0], episodes[-1] + 1)):
        return None, 'Printed episode range is not a complete ascending contiguous range'
    fields = agreement.get('field_combinations') or {}
    if (not fields.get('episodes', {}).get('classification')
            or fields.get('episodes', {}).get('classification') in ('unknown', 'conflicting')
            or not fields.get('season', {}).get('classification')
            or fields.get('season', {}).get('classification') in ('unknown', 'conflicting')):
        return None, 'Episode range or season lacks usable attributed field evidence'

    relevant = []
    short = []
    minimum = MIN_RELEVANT_TITLE_SECONDS if min_length is None else min_length
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        return None, 'Minimum relevant-title duration is invalid'
    seen = set()
    for track in tracks or []:
        try:
            title_id = int(track['track_number'])
            length = int(track.get('length') or 0)
        except (KeyError, TypeError, ValueError):
            return None, 'ARM scan contains a title without a valid ID or duration'
        if title_id < 0 or title_id in seen:
            return None, 'ARM scan contains invalid or duplicate title IDs'
        if isinstance(track.get('angles'), (list, tuple)) and len(track['angles']) > 1:
            return None, f'MakeMKV source {title_id} has multiple angles; leaving all sources provisional'
        angle_count=track.get('angle_count')
        if angle_count is not None:
            try:
                if int(angle_count) > 1:
                    return None, f'MakeMKV source {title_id} has multiple angles; leaving all sources provisional'
            except (TypeError,ValueError):
                return None, f'MakeMKV source {title_id} has unresolved angle metadata'
        if track.get('playlist') or track.get('playlist_id') or track.get('is_playlist'):
            return None, 'Playlist/duplicate-version source metadata is present; leaving sources provisional'
        seen.add(title_id)
        (relevant if length >= minimum else short).append(track)
    relevant.sort(key=lambda track: int(track['track_number']))
    short.sort(key=lambda track: int(track['track_number']))
    if len(relevant) != len(episodes):
        return None, (f'Count mismatch: {len(relevant)} relevant ARM titles for '
                      f'{len(episodes)} printed episodes; no ordinal mapping made')
    signatures=[]
    for track in relevant:
        signature=(int(track.get('length') or 0),str(track.get('aspect_ratio') or ''),
                   str(track.get('fps') or ''),int(track.get('chapters') or 0))
        if signature in signatures:
            return None, 'Duplicate-looking source titles make ordinal assignment ambiguous; leaving titles provisional'
        signatures.append(signature)
    warning=('Ordinal assignments are inferred, not verified. Matching counts do not prove '
             'episode identity; inspect filenames against the disc images and correct as needed.')
    return {
        'method': 'owner-confirmed-dvd-eligible-title-order-v1',
        'status': 'inferred_unverified',
        'verified': False,
        'used_at': time.time(),
        'disc_format': 'dvd',
        'series': series.strip(),
        'season': season,
        'episodes': list(episodes),
        'expected_episode_count': len(episodes),
        'relevant_title_count': len(relevant),
        'min_relevant_title_seconds': minimum,
        'eligible_makemkv_ids': [int(track['track_number']) for track in relevant],
        'short_title_ids_left_provisional': [int(track['track_number']) for track in short],
        'assignments': [
            {'makemkv_id': int(track['track_number']), 'dvd_title_number': None,
             'ordinal_position': position, 'episode_number': episode}
            for position,(track, episode) in enumerate(zip(relevant, episodes), start=1)
        ],
        'user_confirmed_scope': {
            'confirmed_by': 'project owner',
            'source_ids_restart_each_disc': True,
            'rule': 'first eligible unique DVD episode source follows the first episode in the recognized ordered disc list',
            'ordering_key': 'actual MakeMKV track_number IDs in ascending title order; IDs are preserved, not renumbered',
            'eligible_source_ids': [int(track['track_number']) for track in relevant],
            'episode_list': list(episodes),
            'excluded_short_source_ids': [int(track['track_number']) for track in short],
            'exclusions': ['extras', 'duplicate versions', 'Blu-ray playlists', 'unresolved angles'],
            'dvd_title_numbers': 'not exposed by ARM; never inferred from MakeMKV IDs',
        },
        'warning': warning,
    }, None
