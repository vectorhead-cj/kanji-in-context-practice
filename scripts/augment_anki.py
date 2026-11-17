#!/usr/bin/env python3
"""
augment_anki.py

Augments Anki KiC notes export (tab-separated, tags in column 5):

1. Kanji-number inference — adds missing KiC kanji ids inferred from the
   word's kanji characters. Inferred ids are written as NNNNA (e.g. 0307 → 0307A).
2. Lesson inference — adds missing L### tags inferred as the latest lesson among
   the card's kanji ids. Inferred lessons are written as LNNNA (e.g. L039 → L039A).

Usage:
  python3 augment_anki.py \
    --input ../data/kic_cards_orig.txt \
    --output ../data/kic_cards_augmented.txt
"""

import re
import argparse
from collections import defaultdict

# Kanji numbers whose lesson could not be inferred from deck data alone
# (verified against the physical KiC Revised Edition book)
MANUAL_KANJI_LESSON = {
    '0147': 'L006',
    '0164': 'L007',
    '0287': 'L014',
    '0330': 'L017',
    '0378': 'L021',
    '0476': 'L029',
    '0519': 'L033',
    '0692': 'L048',
    '0926': 'L069',
    '1177': 'L093',
    '1369': 'L111',
    '1468': 'L119',
    '1594': 'L125',
    '1660': 'L128',
    '1708': 'L131',
    '1738': 'L132',
    '1820': 'L136',
    '1872': 'L140',
    '1981': 'L146',
    '2036': 'L150',
}

KANJI_ID_RE       = re.compile(r'\b\d{4}\b')
AUG_KANJI_ID_RE   = re.compile(r'\b\d{4}A\b')
LESSON_RE         = re.compile(r'\bL\d{3}\b')
AUG_LESSON_RE     = re.compile(r'\bL\d{3}A\b')


def extract_kanji(text: str) -> set:
    return {ch for ch in text if '一' <= ch <= '鿿'}


def get_all_word_forms(raw_word: str) -> list:
    forms = [f.strip() for f in re.split(r'[、,]', raw_word) if f.strip()]
    seen, cleaned = set(), []

    def _add(form: str):
        if form and form not in seen and extract_kanji(form):
            seen.add(form)
            cleaned.append(form)

    for form in forms:
        form = re.sub(r'^[～〜]', '', form)
        form = form.split('　')[0].split(' ')[0]
        if '（' in form:
            expanded = re.sub(r'（([^）]*)）', r'\1', form)
            _add(expanded)
            if not form.startswith('（'):
                _add(re.sub(r'（[^）]*）', '', form))
        else:
            _add(form)
    return cleaned


def lesson_to_int(lesson: str) -> int:
    if LESSON_RE.fullmatch(lesson):
        return int(lesson[1:])
    if AUG_LESSON_RE.fullmatch(lesson):
        return int(lesson[1:4])
    return -1


def to_augmented_knum(kid: str) -> str:
    return kid + 'A'


def to_augmented_lesson(lesson: str) -> str:
    return lesson + 'A'


def tag_sort_key(token: str):
    if re.fullmatch(r'\d{4}', token):
        return (0, token)
    if re.fullmatch(r'\d{4}A', token):
        return (0, token)
    if token.startswith('KC'):
        return (1, token)
    if LESSON_RE.fullmatch(token) or AUG_LESSON_RE.fullmatch(token):
        return (2, token)
    if token.startswith('Stage'):
        return (3, token)
    return (4, token)


def parse_tag_tokens(tags: str) -> list:
    return tags.split()


def canonical_kanji_ids(word: str, tags: str) -> set:
    return set(KANJI_ID_RE.findall(tags))


def build_kanji_to_kic_id(records) -> dict:
    kic_id_card_kanji = defaultdict(list)
    for word, tags in records:
        ids = canonical_kanji_ids(word, tags)
        card_kanji = set()
        for form in get_all_word_forms(word):
            card_kanji.update(extract_kanji(form))
        for kid in ids:
            kic_id_card_kanji[kid].append(card_kanji)

    kic_id_to_kanji = {}
    for kid, sets in kic_id_card_kanji.items():
        common = set.intersection(*sets) if sets else set()
        if len(common) == 1:
            kic_id_to_kanji[kid] = next(iter(common))

    return {ch: kid for kid, ch in kic_id_to_kanji.items()}


def infer_kanji_ids(word: str, tags: str, kanji_to_kic_id: dict) -> tuple[set, set]:
    """Return (canonical_ids, newly_inferred_canonical_ids)."""
    have = canonical_kanji_ids(word, tags)
    inferred = set()
    for form in get_all_word_forms(word):
        for ch in extract_kanji(form):
            kid = kanji_to_kic_id.get(ch)
            if kid:
                inferred.add(kid)
    new = inferred - have
    return have | inferred, new


def build_kanji_lesson_map(records, kanji_to_kic_id: dict) -> dict:
    kanji_lesson = {}
    for word, tags in records:
        if not LESSON_RE.search(tags):
            continue
        lesson = LESSON_RE.search(tags).group(0)
        expanded, _ = infer_kanji_ids(word, tags, kanji_to_kic_id)
        for knum in expanded:
            cur = kanji_lesson.get(knum)
            if cur is None or lesson_to_int(lesson) < lesson_to_int(cur):
                kanji_lesson[knum] = lesson
    kanji_lesson.update(MANUAL_KANJI_LESSON)
    return kanji_lesson


def infer_lesson(canonical_ids: set, kanji_lesson: dict) -> str:
    lessons = [kanji_lesson[k] for k in canonical_ids if k in kanji_lesson]
    if not lessons:
        return ''
    return max(lessons, key=lesson_to_int)


def augment_tags(word: str, tags: str, kanji_to_kic_id: dict, kanji_lesson: dict) -> tuple[str, int, int]:
    tokens = parse_tag_tokens(tags)
    meta_tokens = [
        t for t in tokens
        if not KANJI_ID_RE.fullmatch(t) and not AUG_KANJI_ID_RE.fullmatch(t)
    ]

    expanded_ids, new_ids = infer_kanji_ids(word, tags, kanji_to_kic_id)
    original_ids = canonical_kanji_ids(word, tags)

    id_tokens = sorted(original_ids)
    id_tokens.extend(sorted(to_augmented_knum(k) for k in new_ids))

    kanji_augmented = len(new_ids)

    has_lesson = bool(LESSON_RE.search(tags) or AUG_LESSON_RE.search(tags))
    lesson_augmented = 0
    if not has_lesson:
        inferred = infer_lesson(expanded_ids, kanji_lesson)
        if inferred:
            meta_tokens.append(to_augmented_lesson(inferred))
            lesson_augmented = 1

    new_tags = ' '.join(sorted(id_tokens + meta_tokens, key=tag_sort_key))
    return new_tags, kanji_augmented, lesson_augmented


def augment(input_path: str, output_path: str):
    with open(input_path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    header_lines = [l for l in lines if l.startswith('#')]
    data_lines = [l for l in lines if not l.startswith('#') and l.strip()]

    records = []
    rows = []
    for line in data_lines:
        parts = line.split('\t')
        if len(parts) < 5:
            rows.append((line, None))
            continue
        word = parts[0]
        tags = parts[4].strip()
        records.append((word, tags))
        rows.append((line, len(records) - 1))

    print(f'Records found: {len(records)}')

    kanji_to_kic_id = build_kanji_to_kic_id(records)
    print(f'Kanji->id map: {len(kanji_to_kic_id)} entries')

    kanji_lesson = build_kanji_lesson_map(records, kanji_to_kic_id)
    print(f'Kanji->lesson map: {len(kanji_lesson)} entries')

    kanji_aug_total = 0
    lesson_aug_total = 0
    lesson_unresolved = 0
    out_lines = list(header_lines)

    for line, rec_idx in rows:
        if rec_idx is None:
            out_lines.append(line)
            continue
        parts = line.split('\t')
        word, tags = records[rec_idx]
        new_tags, k_aug, l_aug = augment_tags(word, tags, kanji_to_kic_id, kanji_lesson)
        kanji_aug_total += k_aug
        lesson_aug_total += l_aug
        if not LESSON_RE.search(tags) and not AUG_LESSON_RE.search(tags) and l_aug == 0:
            lesson_unresolved += 1
        parts[4] = new_tags
        out_lines.append('\t'.join(parts))

    print(f'Kanji ids augmented (count): {kanji_aug_total}')
    print(f'Cards with lesson augmented: {lesson_aug_total}')
    print(f'Cards still without lesson:    {lesson_unresolved}')

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_lines) + '\n')

    print(f'Written to: {output_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Augment KiC Anki export with inferred kanji ids and lesson tags'
    )
    parser.add_argument('--input', required=True, help='Original Anki notes export (.txt)')
    parser.add_argument('--output', required=True, help='Augmented output file')
    args = parser.parse_args()
    augment(args.input, args.output)
