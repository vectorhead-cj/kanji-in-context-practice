#!/usr/bin/env python3
"""
augment_anki.py

Adds missing lesson tags to Anki KiC export file.

Usage:
  python3 augment_anki.py \
    --input kic_all.txt \
    --output kic_all_augmented.txt

Works directly on the raw Anki export format (doubled quotes) without
unescaping, to avoid round-trip bugs.
"""

import re
import argparse
from collections import defaultdict

# ── Manual mappings: kanji numbers whose lesson was missing from the deck ─────
# Verified against the physical KiC Revised Edition book
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

# Works on raw format where quotes are doubled: class=""bottom""
TAG_RE      = re.compile(r'(class=""bottom"">)(.*?)(</div>)')
LESSON_RE   = re.compile(r'\bL\d{3}\b')
KNUM_RE     = re.compile(r'\b\d{4}\b')
RECORD_RE   = re.compile(r'"(.*?)"\t"(.*?)"(?=\n"|$)', re.DOTALL)

def get_tags_raw(back: str) -> str:
    m = TAG_RE.search(back)
    return m.group(2).strip() if m else ''

def get_lesson(tags: str) -> str:
    m = LESSON_RE.search(tags)
    return m.group(0) if m else ''

def get_knums(tags: str) -> list:
    return KNUM_RE.findall(tags)

def tag_sort_key(t):
    if re.match(r'^\d{4}$', t): return (0, t)
    if re.match(r'^KC',    t):  return (1, t)
    if re.match(r'^L\d',   t):  return (2, t)
    if re.match(r'^Stage', t):  return (3, t)
    return (4, t)

def build_kanji_lesson_map(records):
    kanji_lesson = {}
    for _, back in records:
        tags   = get_tags_raw(back)
        lesson = get_lesson(tags)
        if not lesson:
            continue
        for knum in get_knums(tags):
            if knum not in kanji_lesson or lesson < kanji_lesson[knum]:
                kanji_lesson[knum] = lesson
    # Manual mappings applied last so they always override inferred values
    kanji_lesson.update(MANUAL_KANJI_LESSON)
    return kanji_lesson

def infer_lesson(tags: str, kanji_lesson: dict) -> str:
    lessons = [kanji_lesson[k] for k in get_knums(tags) if k in kanji_lesson]
    return max(lessons) if lessons else ''

def augment(input_path: str, output_path: str):
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Preserve header lines
    lines       = content.split('\n')
    header      = '\n'.join(l for l in lines if l.startswith('#'))
    data        = '\n'.join(l for l in lines if not l.startswith('#'))

    records = [(f, b) for f, b in RECORD_RE.findall(data)]
    print(f"Records found: {len(records)}")

    kanji_lesson = build_kanji_lesson_map(records)
    print(f"Kanji->lesson map: {len(kanji_lesson)} entries")

    augmented    = 0
    uninferrable = 0
    output_parts = [header] if header else []

    for front, back in records:
        tags           = get_tags_raw(back)
        existing_lesson = get_lesson(tags)

        if existing_lesson:
            output_parts.append(f'"{front}"\t"{back}"')
            continue

        inferred = infer_lesson(tags, kanji_lesson)

        if not inferred:
            uninferrable += 1
            output_parts.append(f'"{front}"\t"{back}"')
            continue

        # Build new tag string
        tag_list = tags.split() + [inferred]
        tag_list.sort(key=tag_sort_key)
        new_tags = ' '.join(tag_list)

        # Replace in raw back string (doubled-quote format throughout)
        new_back = TAG_RE.sub(
            lambda m: m.group(1) + new_tags + m.group(3),
            back
        )

        output_parts.append(f'"{front}"\t"{new_back}"')
        augmented += 1

    print(f"Cards augmented:          {augmented}")
    print(f"Cards still unresolved:   {uninferrable}")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_parts))

    print(f"Written to: {output_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Augment Anki KiC export with missing lesson tags')
    parser.add_argument('--input',  required=True, help='Original Anki export .txt file')
    parser.add_argument('--output', required=True, help='Output augmented .txt file')
    args = parser.parse_args()
    augment(args.input, args.output)
