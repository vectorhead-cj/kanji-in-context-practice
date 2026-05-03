#!/usr/bin/env python3
"""
build_sentences.py

Builds per-lesson JSON files from:
  - Tatoeba Japanese-English sentence pairs (TSV)
  - KiC notes TSV exported from Anki (File → Export Notes, plain text with tags)

Usage:
  python3 build_sentences.py \
    --tatoeba data/jpn_eng_sentences.tsv \
    --kic data/kic_augmented.txt \
    --output-dir sentences/grade2 \
    --base-grade 2 \
    --max-required-words 2 \
    --max-per-lesson 50

Writes, for each length bucket and variant:

  {output-dir}/short/plain/L001.json
  {output-dir}/short/furigana/L001.json
  … same for medium/ and long/

Plain files omit KiC readings; furigana/ files include them.

Output JSON structure per sentence (plain/):
  {
    "id": "grade2:short:L003:abc123def0",
    "ja": "今日は学校に行く。",
    "en": ["I'm going to school today.", "Today I go to school."],
    "lesson": "L003",
    "required_words": 2,
    "char_length": 10,
    "length_bucket": "short",
    "kic_words_current_lesson": [
      {"word": "学校", "lesson": "L003", "kanji_ids": ["0049", "0051"]}
    ],
    "kic_words_previous_lessons": [
      {"word": "今日", "lesson": "L001", "kanji_ids": ["0016", "0027"]}
    ],
    "target_kanji_ids": ["0049", "0051"],
    "sentence_kanji_ids": ["0016", "0027", "0049", "0051"]
  }

furigana/ files add readings:
    "kic_words_current_lesson": [
      {"word": "学校", "lesson": "L003", "reading": "学[がっ]校[こう]"}
    ]

KiC word metadata is sourced only from KiC cards (no external tokenizer). Words
are matched via substring search against the Japanese sentence. All KiC words
from allowed lessons (L001..current) are checked, not just the current lesson,
so earlier lesson words also get metadata. Longer word matches take priority
over single-kanji substrings.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict

# ── Grade 1 kanji (80) ────────────────────────────────────────────────────────
GRADE_1 = set(
    "一右雨円王音下火花貝学気九休玉金空月犬見五口校左三山子四糸字耳"
    "七車手十出女小上森人水正生青夕石赤千川先早草足村大男竹中虫町天"
    "田土二日入年白八百文木本名目立力林六"
)

# ── Grade 2 kanji (160) ───────────────────────────────────────────────────────
GRADE_2 = set(
    "引羽雲園遠何科夏家歌画回会海絵外角活間丸岩顔汽記帰弓牛魚京強教"
    "近兄形計元言原戸古午後語工公広交光考行高黄合国黒今才細作算止市"
    "矢姉思紙寺自時室社弱首秋週春書少場色食心新親図数西声星晴切雪船"
    "線前組走多太体台地池知茶昼長鳥朝直通弟店点電刀冬当東答頭同道読"
    "内南肉馬売買麦半番父風分聞米歩母方北毎妹万明鳴毛門夜野友用曜来"
    "里理話"
)

def get_base_kanji(grade: int) -> set:
    if grade == 1:
        return set(GRADE_1)
    elif grade == 2:
        return GRADE_1 | GRADE_2
    else:
        raise ValueError(f"Unsupported grade: {grade}")

def extract_kanji(text: str) -> set:
    """Extract all CJK kanji characters from a string."""
    return set(ch for ch in text if '\u4e00' <= ch <= '\u9fff')

def length_bucket(n: int) -> str:
    if n < 15:
        return "short"
    elif n <= 30:
        return "medium"
    else:
        return "long"

def get_all_word_forms(raw_word: str) -> list:
    """
    Return all word forms from a raw KiC word string.
    Handles alternates separated by 、or comma, strips ～/〜 placeholders,
    and expands （X） optional-kana notation into real forms:
      - 買（い）物  → ['買い物', '買物']   (embedded: both with and without)
      - （お）金    → ['お金']             (leading prefix: with-prefix only,
                                           to avoid conflicting with the bare
                                           standalone 金[きん] entry)
    Only returns forms that contain at least one kanji.
    """
    forms = [f.strip() for f in re.split(r'[、,]', raw_word) if f.strip()]
    seen = set()
    cleaned = []

    def _add(f):
        if f and f not in seen and extract_kanji(f):
            seen.add(f)
            cleaned.append(f)

    for f in forms:
        f = re.sub(r'^[～〜]', '', f)
        f = f.split('　')[0].split(' ')[0]

        if '（' in f:
            # expanded form: （X） → X  (e.g. 買（い）物 → 買い物, （お）金 → お金)
            expanded = re.sub(r'（([^）]*)）', r'\1', f)
            _add(expanded)
            # bare form: （X） → ''  (e.g. 買（い）物 → 買物)
            # Skip for leading-prefix patterns to avoid reading conflicts
            if not f.startswith('（'):
                bare = re.sub(r'（[^）]*）', '', f)
                _add(bare)
        else:
            _add(f)

    return cleaned

def parse_anki_reading(raw: str) -> str:
    """
    Normalise Anki native furigana format: strip whitespace and strip
    （X） optional-kana markers (which are expanded in get_all_word_forms).
    Input:  '（お） 金[かね]'  or  '買[か]（い） 物[もの]'
    Output: '金[かね]'        or  '買[か]物[もの]'
    """
    result = re.sub(r'\s+', '', raw.strip())
    result = re.sub(r'（[^）]*）', '', result)
    return result

TAGLESS_LESSON = 'L000'   # sentinel for words with no L### lesson tag
KANJI_ID_RE = re.compile(r'\b\d{4}\b')

def load_kic(filepath: str):
    """
    Reads Anki 'Export Notes' TSV format:
      col 0: word
      col 1: reading  (Anki furigana: 一[いっ] 分[ぷん])
      col 2: meaning
      col 3: (empty)
      col 4: tags     (e.g. '0001 KC1 L001 Stage1')

    Returns:
      lesson_words:  dict { lesson -> list of (word_form, reading, kanji_ids) }
                     Words with no L### tag are stored under TAGLESS_LESSON ('L000')
                     so they remain available for furigana lookup.
      lesson_kanji:  dict { lesson -> set of kanji chars }
      all_lessons:   sorted list of real lesson strings ['L001'..'L156']
                     (TAGLESS_LESSON is excluded — not a real lesson)
    """
    lesson_words = defaultdict(list)
    lesson_kanji = defaultdict(set)
    lesson_re    = re.compile(r'\bL\d{3}\b')

    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) < 5:
                continue
            word_raw = parts[0].strip()
            reading  = parse_anki_reading(parts[1])
            tags     = parts[4].strip()

            m = lesson_re.search(tags)
            lesson = m.group(0) if m else TAGLESS_LESSON
            kanji_ids = tuple(KANJI_ID_RE.findall(tags))

            forms = get_all_word_forms(word_raw)
            for form in forms:
                lesson_words[lesson].append((form, reading, kanji_ids))
                if lesson != TAGLESS_LESSON:
                    lesson_kanji[lesson].update(extract_kanji(form))

    all_lessons = sorted(k for k in lesson_words if k != TAGLESS_LESSON)
    return lesson_words, lesson_kanji, all_lessons

def load_tatoeba(filepath: str):
    """
    Returns dict: { ja_sentence -> [en_translation, ...] }
    One Japanese sentence may have multiple English translations.
    """
    sentences = defaultdict(list)
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            parts = line.split('\t')
            if len(parts) < 4:
                continue
            ja = parts[1].strip()
            en = parts[3].strip()
            if ja and en and en not in sentences[ja]:
                sentences[ja].append(en)
    return sentences

def get_matched_kic_word_spans(
    sentence: str,
    word_records: list,
    current_lesson: str = None,
    covered_positions: set = None,
) -> list:
    """
    Return distinct KiC word matches with internal span positions.
    Sorted longest-first to avoid double-counting substrings.
    If current_lesson is supplied, same-length matches from that lesson win.
    """
    def sort_key(record):
        form, _, lesson, _ = record
        current_rank = 0 if lesson == current_lesson else 1
        return (-len(form), current_rank)

    sorted_records = sorted(word_records, key=sort_key)
    covered = set(covered_positions or set())
    matches = []
    for form, reading, lesson, kanji_ids in sorted_records:
        if not form:
            continue
        idx = sentence.find(form)
        if idx == -1:
            continue
        positions = set(range(idx, idx + len(form)))
        if positions & covered:
            continue
        covered |= positions
        matches.append({
            "word": form,
            "lesson": lesson,
            "reading": reading,
            "kanji_ids": list(kanji_ids),
            "_start": idx,
            "_end": idx + len(form),
        })
    return matches


def get_covered_positions(matches: list) -> set:
    return {
        pos
        for match in matches
        for pos in range(match["_start"], match["_end"])
    }


def format_kic_words(words: list, include_reading: bool) -> list:
    if include_reading:
        return [
            {
                "word": w["word"],
                "lesson": w["lesson"],
                "kanji_ids": w["kanji_ids"],
                "reading": w["reading"],
            }
            for w in words
        ]
    return strip_kic_readings(words)


def strip_kic_readings(words: list) -> list:
    """Return KiC word metadata for plain JSON output."""
    return [
        {"word": w["word"], "lesson": w["lesson"], "kanji_ids": w.get("kanji_ids", [])}
        for w in words
    ]


def unique_sorted_kanji_ids(words: list) -> list:
    return sorted({
        kanji_id
        for word in words
        for kanji_id in word.get("kanji_ids", [])
    })


def sentence_id(base_grade: int, bucket: str, lesson: str, sentence: dict) -> str:
    """
    Stable content ID for app caches and augmentation overrides.
    Candidate words are part of the hash because the same sentence can be a
    good pairing for one target and a bad pairing for another.
    """
    canonical = {
        "grade": base_grade,
        "bucket": bucket,
        "lesson": lesson,
        "ja": sentence["ja"],
        "current": strip_kic_readings(sentence["kic_words_current_lesson"]),
        "previous": strip_kic_readings(sentence["kic_words_previous_lessons"]),
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"grade{base_grade}:{bucket}:{lesson}:{digest}"

def sample_balanced(results: list, max_count: int) -> list:
    """
    Select up to max_count sentences from results, balanced across
    length_bucket values (short/medium/long). Each bucket gets ~max/3.
    If a bucket has fewer than its quota, surplus is redistributed to
    the remaining buckets.
    """
    BUCKETS = ['short', 'medium', 'long']
    by_bucket = {b: [] for b in BUCKETS}
    for s in results:
        by_bucket[s['length_bucket']].append(s)

    # Sort each bucket by char_length so we pick shorter, simpler sentences first
    for b in BUCKETS:
        by_bucket[b].sort(key=lambda x: x['char_length'])

    # Distribute quota evenly; remainder goes to first buckets
    quota = {b: max_count // 3 for b in BUCKETS}
    for i in range(max_count % 3):
        quota[BUCKETS[i]] += 1

    # Iteratively clamp buckets that can't fill their quota and
    # redistribute surplus to buckets that can absorb more
    changed = True
    while changed:
        changed = False
        surplus = 0
        have_capacity = []
        for b in BUCKETS:
            if len(by_bucket[b]) < quota[b]:
                surplus += quota[b] - len(by_bucket[b])
                quota[b] = len(by_bucket[b])
                changed = True
            elif len(by_bucket[b]) > quota[b]:
                have_capacity.append(b)
        if surplus and have_capacity:
            per_extra = surplus // len(have_capacity)
            leftover  = surplus % len(have_capacity)
            for i, b in enumerate(have_capacity):
                quota[b] += per_extra + (1 if i < leftover else 0)

    selected = []
    for b in BUCKETS:
        selected.extend(by_bucket[b][:quota[b]])
    return selected


def clean_grade_output_dir(output_dir: str) -> None:
    """Remove legacy flat L###.json / L###_furigana.json and old bucket trees."""
    if not os.path.isdir(output_dir):
        return
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if name in ('short', 'medium', 'long') and os.path.isdir(path):
            shutil.rmtree(path)
        elif name.endswith('.json'):
            os.remove(path)


def build_sentences(
    tatoeba_path: str,
    kic_path: str,
    output_dir: str,
    base_grade: int,
    max_required_words: int,
    max_per_lesson: int,
):
    print("Loading KiC cards...")
    lesson_words, lesson_kanji, all_lessons = load_kic(kic_path)
    total_forms = sum(len(v) for v in lesson_words.values())
    print(f"  {len(all_lessons)} lessons, {total_forms} word forms")

    print("Loading Tatoeba sentences...")
    tatoeba = load_tatoeba(tatoeba_path)
    print(f"  {len(tatoeba)} unique Japanese sentences")

    base_kanji = get_base_kanji(base_grade)
    print(f"  Base kanji set: Grade 1–{base_grade} ({len(base_kanji)} kanji)")

    os.makedirs(output_dir, exist_ok=True)
    clean_grade_output_dir(output_dir)

    # Build cumulative allowed sets per lesson.
    cumulative_kanji              = set(base_kanji)
    allowed_kanji_per_lesson      = {}
    cumulative_word_records       = []
    allowed_word_records_per_lesson = {}

    for lesson in all_lessons:
        cumulative_kanji = cumulative_kanji | lesson_kanji[lesson]
        cumulative_word_records = cumulative_word_records + [
            (form, reading, lesson, kanji_ids)
            for form, reading, kanji_ids in lesson_words[lesson]
        ]
        allowed_kanji_per_lesson[lesson] = set(cumulative_kanji)
        allowed_word_records_per_lesson[lesson] = list(cumulative_word_records)

    print("Filtering sentences per lesson...")
    total_kept = 0

    for lesson in all_lessons:
        allowed_kanji      = allowed_kanji_per_lesson[lesson]
        allowed_word_records = allowed_word_records_per_lesson[lesson]
        current_word_records = [
            (form, reading, lesson, kanji_ids)
            for form, reading, kanji_ids in lesson_words[lesson]
        ]
        current_kanji      = lesson_kanji[lesson]
        lesson_results     = []

        for ja, en_list in tatoeba.items():
            ja_kanji = extract_kanji(ja)

            if not ja_kanji & current_kanji:
                continue
            if ja_kanji - allowed_kanji:
                continue

            current_lesson_words = get_matched_kic_word_spans(ja, current_word_records, lesson)
            required_word_count = len(current_lesson_words)
            if required_word_count == 0 or required_word_count > max_required_words:
                continue

            char_len = len(ja)
            lesson_results.append({
                "ja":             ja,
                "en":             en_list,
                "lesson":         lesson,
                "required_words": required_word_count,
                "char_length":    char_len,
                "length_bucket":  length_bucket(char_len),
                "_current_kic_word_matches": current_lesson_words,
            })

        selected = sample_balanced(lesson_results, max_per_lesson)
        total_kept += len(selected)

        bucket_rows = defaultdict(list)
        previous_word_records = [
            record for record in allowed_word_records_per_lesson[lesson]
            if record[2] != lesson
        ]
        for s in selected:
            s2 = dict(s)
            current_lesson_words = s2.pop("_current_kic_word_matches")
            previous_lesson_words = get_matched_kic_word_spans(
                s2["ja"],
                previous_word_records,
                covered_positions=get_covered_positions(current_lesson_words),
            )
            s2["kic_words_current_lesson"] = format_kic_words(current_lesson_words, include_reading=True)
            s2["kic_words_previous_lessons"] = format_kic_words(previous_lesson_words, include_reading=True)
            s2["target_kanji_ids"] = unique_sorted_kanji_ids(s2["kic_words_current_lesson"])
            s2["sentence_kanji_ids"] = unique_sorted_kanji_ids(
                s2["kic_words_current_lesson"] + s2["kic_words_previous_lessons"]
            )
            s2["id"] = sentence_id(base_grade, s2["length_bucket"], lesson, s2)
            bucket_rows[s2["length_bucket"]].append(s2)

        for bucket in ("short", "medium", "long"):
            rows = bucket_rows[bucket]
            plain_dir = os.path.join(output_dir, bucket, "plain")
            furi_dir  = os.path.join(output_dir, bucket, "furigana")
            os.makedirs(plain_dir, exist_ok=True)
            os.makedirs(furi_dir, exist_ok=True)
            plain_payload = []
            for row in rows:
                plain_row = dict(row)
                plain_row["kic_words_current_lesson"] = strip_kic_readings(row["kic_words_current_lesson"])
                plain_row["kic_words_previous_lessons"] = strip_kic_readings(row["kic_words_previous_lessons"])
                plain_payload.append(plain_row)
            with open(os.path.join(plain_dir, f"{lesson}.json"), "w", encoding="utf-8") as f:
                json.dump(plain_payload, f, ensure_ascii=False, indent=2)
            with open(os.path.join(furi_dir, f"{lesson}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)

        if lesson_results:
            buckets = {"short": 0, "medium": 0, "long": 0}
            for s in selected:
                buckets[s["length_bucket"]] += 1
            print(f"  {lesson}: {len(selected):3d}/{len(lesson_results):4d} kept  "
                  f"(short={buckets['short']} medium={buckets['medium']} long={buckets['long']})")
        else:
            print(f"  {lesson}: (none found)")

    print(f"\nTotal sentences written: {total_kept}")
    print(f"Output directory: {output_dir}/{{short,medium,long}}/{{plain,furigana}}/")
    print("Done!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build per-lesson sentence JSON files for KiC practice app')
    parser.add_argument('--tatoeba',            required=True, help='Path to Tatoeba TSV file')
    parser.add_argument('--kic',                required=True, help='Path to Anki notes TSV export')
    parser.add_argument('--output-dir',         default='sentences', help='Grade root, e.g. sentences/grade2')
    parser.add_argument('--base-grade',         type=int, default=2, choices=[1, 2],
                        help='Base kanji grade level (1 or 2, default: 2)')
    parser.add_argument('--max-required-words', type=int, default=2,
                        help='Max KiC words from current lesson per sentence (default: 2)')
    parser.add_argument('--max-per-lesson',     type=int, default=50,
                        help='Max sentences to keep per lesson, balanced across length buckets (default: 50)')
    args = parser.parse_args()

    build_sentences(
        tatoeba_path=args.tatoeba,
        kic_path=args.kic,
        output_dir=args.output_dir,
        base_grade=args.base_grade,
        max_required_words=args.max_required_words,
        max_per_lesson=args.max_per_lesson,
    )
