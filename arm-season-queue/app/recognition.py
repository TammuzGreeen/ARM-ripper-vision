"""No masterlist is passed into OCR or extraction. Local images never leave this service."""
import re
import unicodedata
from collections import Counter

from .formats import digest
from .text_normalization import normalize_structural_labels


def normalize(text):
    text = unicodedata.normalize('NFKD', text.casefold())
    return ' '.join(re.sub(r'[^\w]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).split())


def printed_range(value):
    found = re.fullmatch(r'(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?',value)
    return set(range(int(found[1]),int(found[2] or found[1])+1)) if found else set()


def extract(text, confidence=0.0, barcodes=None):
    label_text = normalize_structural_labels(text)
    seasons = {int(v) for v in re.findall(r'\bseason\s*[:.#-]?\s*(\d{1,2})\b', label_text, re.I)}
    discs = {int(v) for v in re.findall(r'\bdisc\s*[:.#-]?\s*(\d{1,2})\b', label_text, re.I)}
    episodes = []
    for match in re.finditer(r'\bS(\d{1,2})E(\d{1,3})(?:\s*[-–]\s*(?:S\d{1,2})?E?(\d{1,3}))?\s*[-–:]?\s*([^\n]*)', text, re.I):
        seasons.add(int(match[1]))
        episodes.append({'season': int(match[1]), 'first': int(match[2]), 'last': int(match[3] or match[2]), 'title': match[4].strip()})
    for match in re.finditer(r'\bepisode\s*(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*[-–:]?\s*([^\n]*)', label_text, re.I):
        episodes.append({'season': None, 'first': int(match[1]), 'last': int(match[2] or match[1]), 'title': match[3].strip()})
    return {'raw_text': text, 'confidence': round(confidence, 3),
            'season': next(iter(seasons)) if len(seasons) == 1 else None,
            'disc': next(iter(discs)) if len(discs) == 1 else None,
            'conflicts': {'seasons': sorted(seasons), 'discs': sorted(discs)} if len(seasons)>1 or len(discs)>1 else {},
            'title_candidates': [x.strip() for x in text.splitlines() if len(x.strip())>5],
            'edition_candidates': [x.strip() for x in text.splitlines() if re.search(r'edition|effects|effekte|remaster|original|part|teil', x, re.I)],
            'episodes': episodes, 'barcodes': barcodes or [],
            'catalogue_candidates': re.findall(r'\b[A-Z]{1,5}[_-]\d{4,}\b', text),
            'languages': re.findall(r'\b(?:deutsch|german|english|englisch|french|français|spanish|español|italiano)\b', text, re.I)}


def read_words(data):
    """Keep uncertain words for display only; matching still uses >=80 scores."""
    lines, observed, scores = {}, {}, []
    for i, word in enumerate(data['text']):
        score = float(data['conf'][i])
        if not word.strip() or score < 0:
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        observed.setdefault(key, []).append(word)
        if score >= 80:
            lines.setdefault(key, []).append(word)
            scores.append(score)
    join = lambda groups: '\n'.join(' '.join(words) for words in groups.values())
    return join(lines), join(observed), scores


def ocr(frame, language):
    import cv2
    import numpy as np
    import pytesseract
    try:
        from pyzbar.pyzbar import decode
        barcodes = [x.data.decode('utf-8', errors='replace') for x in decode(frame)]
    except (ImportError, OSError):
        barcodes = []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # CLAHE improves local contrast without assuming text/label colours.
    gray = cv2.createCLAHE(clipLimit=2, tileGridSize=(8,8)).apply(gray)
    if gray.shape[1] < 1800:
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    best = None
    for rotation in range(4):
        candidate = np.rot90(gray, rotation).copy()
        data = pytesseract.image_to_data(candidate, lang=language, config='--psm 11', output_type=pytesseract.Output.DICT, timeout=30)
        text, observed, scores = read_words(data)
        quality = (sum(scores), len(observed))
        result = extract(text, sum(scores)/max(1,len(scores))/100, barcodes)
        result['observed_text'] = observed  # Never passed into identity matching.
        result['rotation'] = rotation*90
        if best is None or quality > best[0]:
            best = (quality, result)
    return best[1]


def consensus(frames):
    result = {'frames': frames, 'accepted': False, 'reason': 'Present a readable series, season, disc and edition label'}
    if len(frames) < 2 or any(f['conflicts'] for f in frames):
        result['reason'] = 'Insufficient independent frames or conflicting printed numbers'
        return result
    pairs = Counter((f['season'], f['disc']) for f in frames if f['confidence'] >= .80)
    if not pairs:
        return result
    (season, disc), count = pairs.most_common(1)[0]
    if season is None or disc is None or count < 2 or len(pairs)>1:
        result['reason'] = 'Season/disc readings are incomplete or disagree; recapture'
        return result
    result.update(accepted=True, season=season, disc=disc, reason='Consistent printed numbers in multiple frames')
    return result


def match(result, masters):
    if not result.get('accepted'):
        return []
    matches = []
    for master in masters:
        if not master.approved or master.season != result['season']:
            continue
        for disc in master.discs:
            if disc.number != result['disc']:
                continue
            good = []
            for f in result['frames']:
                if f['confidence'] < .80 or (f['season'], f['disc']) != (master.season, disc.number):
                    continue
                text = ' ' + normalize(f['raw_text']) + ' '
                visible = lambda x: (' '+normalize(x)+' ') in text
                if not any(visible(x) for x in [master.series]+master.title_aliases):
                    continue
                if not all(visible(x) or x in f['barcodes'] for x in master.edition_tokens):
                    continue
                if disc.printed_identifiers and not any(visible(x) or x in f['barcodes'] for x in disc.printed_identifiers):
                    continue
                printed_numbers = {n for e in f['episodes'] for n in range(e['first'],e['last']+1)}
                expected = set().union(*(printed_range(e.printed) for e in disc.episodes))
                if printed_numbers and not printed_numbers.issubset(expected):
                    continue
                printed_titles = {int(e.printed):normalize(e.title) for e in disc.episodes if e.printed.isdigit()}
                if any(e['first']==e['last'] and e['title'] and printed_titles.get(e['first'])!=normalize(e['title']) for e in f['episodes']):
                    continue
                good.append(f)
            if len(good) >= 2:
                matches.append({'master': master.id, 'disc': disc.id, 'series': master.series,
                                'season':master.season,'disc_number':disc.number,'edition':master.edition_name,
                                'masterlist_sha256':digest(master),
                                'confidence': min(f['confidence'] for f in good)})
    return matches

