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

Writes one file per lesson: {output-dir}/L001.json, L002.json, etc.
Each file is a JSON array of sentence objects.

Output JSON structure per sentence:
  {
    "ja": "今日は学校に行く。",
    "en": ["I'm going to school today.", "Today I go to school."],
    "lesson": "L003",
    "required_words": 2,
    "char_length": 10,
    "length_bucket": "short",
    "furigana": {
      "今日": "今[こん]日[にち]今日[きょう]",
      "学校": "学[がっ]校[こう]",
      "行く": "行[い]行[ゆ]"
    }
  }

Furigana is sourced only from KiC cards (no external tokenizer).
Words matched via substring search against the Japanese sentence.
All KiC words from allowed lessons (base + L001..current) are checked,
not just the current lesson, so earlier lesson words also get furigana.
Longer word matches take priority over single-kanji substrings.
"""

import json
import os
import re
import argparse
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
    Handles alternates separated by 、or comma, and strips
    prefix placeholders like ～ or 〜.
    Only returns forms that contain at least one kanji.
    """
    forms = [f.strip() for f in re.split(r'[、,]', raw_word) if f.strip()]
    cleaned = []
    for f in forms:
        f = re.sub(r'^[～〜]', '', f)   # strip leading placeholder
        f = f.split('　')[0].split(' ')[0]  # strip trailing annotations
        if f and extract_kanji(f):
            cleaned.append(f)
    return cleaned

def parse_anki_reading(raw: str) -> str:
    """
    Normalise Anki native furigana format by stripping whitespace.
    Input:  '一[いっ] 分[ぷん]'  or  '一人[ひとり]'
    Output: '一[いっ]分[ぷん]'  or  '一人[ひとり]'
    Keeps the kanji[reading] format intact for ruby rendering in the app.
    """
    return re.sub(r'\s+', '', raw.strip())

def load_kic(filepath: str):
    """
    Reads Anki 'Export Notes' TSV format:
      col 0: word
      col 1: reading  (Anki furigana: 一[いっ] 分[ぷん])
      col 2: meaning
      col 3: (empty)
      col 4: tags     (e.g. '0001 KC1 L001 Stage1')

    Returns:
      lesson_words:  dict { lesson -> list of (word_form, reading) }
      lesson_kanji:  dict { lesson -> set of kanji chars }
      all_lessons:   sorted list of lesson strings ['L001'..'L156']
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
            if not m:
                continue
            lesson = m.group(0)

            forms = get_all_word_forms(word_raw)
            for form in forms:
                lesson_words[lesson].append((form, reading))
                lesson_kanji[lesson].update(extract_kanji(form))

    all_lessons = sorted(lesson_words.keys())
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

def get_matched_current_words(sentence: str, current_word_pairs: list) -> int:
    """
    Count distinct KiC words from the current lesson that appear in sentence.
    Sorted longest-first to avoid double-counting substrings.
    """
    sorted_pairs = sorted(current_word_pairs, key=lambda x: len(x[0]), reverse=True)
    covered = set()
    count = 0
    for form, _ in sorted_pairs:
        if not form:
            continue
        idx = sentence.find(form)
        if idx == -1:
            continue
        positions = set(range(idx, idx + len(form)))
        if positions & covered:
            continue
        covered |= positions
        count += 1
    return count

def build_furigana_map(sentence: str, allowed_word_pairs: list) -> dict:
    """
    Build { word_form -> reading } for all KiC words found in the sentence,
    across all allowed lessons. Longer words take priority over substrings.
    """
    furigana = {}
    sorted_pairs = sorted(allowed_word_pairs, key=lambda x: len(x[0]), reverse=True)
    covered_positions = set()

    for form, reading in sorted_pairs:
        if not form or not reading:
            continue
        idx = sentence.find(form)
        if idx == -1:
            continue
        positions = set(range(idx, idx + len(form)))
        if positions & covered_positions:
            continue
        furigana[form] = reading
        covered_positions |= positions

    return furigana

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

    # Build cumulative allowed sets per lesson
    cumulative_kanji      = set(base_kanji)
    cumulative_word_pairs = []
    allowed_kanji_per_lesson      = {}
    allowed_word_pairs_per_lesson = {}

    for lesson in all_lessons:
        cumulative_kanji      = cumulative_kanji | lesson_kanji[lesson]
        cumulative_word_pairs = cumulative_word_pairs + lesson_words[lesson]
        allowed_kanji_per_lesson[lesson]      = set(cumulative_kanji)
        allowed_word_pairs_per_lesson[lesson] = list(cumulative_word_pairs)

    print("Filtering sentences per lesson...")
    total_kept = 0

    for lesson in all_lessons:
        allowed_kanji      = allowed_kanji_per_lesson[lesson]
        allowed_word_pairs = allowed_word_pairs_per_lesson[lesson]
        current_word_pairs = lesson_words[lesson]
        current_kanji      = lesson_kanji[lesson]
        lesson_results     = []

        for ja, en_list in tatoeba.items():
            ja_kanji = extract_kanji(ja)

            if not ja_kanji & current_kanji:
                continue
            if ja_kanji - allowed_kanji:
                continue

            required_word_count = get_matched_current_words(ja, current_word_pairs)
            if required_word_count == 0 or required_word_count > max_required_words:
                continue

            char_len = len(ja)
            furigana = build_furigana_map(ja, allowed_word_pairs)

            lesson_results.append({
                "ja":             ja,
                "en":             en_list,
                "lesson":         lesson,
                "required_words": required_word_count,
                "char_length":    char_len,
                "length_bucket":  length_bucket(char_len),
                "furigana":       furigana,
            })

        selected = sample_balanced(lesson_results, max_per_lesson)
        total_kept += len(selected)

        out_path = os.path.join(output_dir, f"{lesson}.json")
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(selected, f, ensure_ascii=False, indent=2)

        if lesson_results:
            buckets = {"short": 0, "medium": 0, "long": 0}
            for s in selected:
                buckets[s["length_bucket"]] += 1
            print(f"  {lesson}: {len(selected):3d}/{len(lesson_results):4d} kept  "
                  f"(short={buckets['short']} medium={buckets['medium']} long={buckets['long']})")
        else:
            print(f"  {lesson}: (none found)")

    print(f"\nTotal sentences written: {total_kept}")
    print(f"Output directory: {output_dir}/")
    print("Done!")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build per-lesson sentence JSON files for KiC practice app')
    parser.add_argument('--tatoeba',            required=True, help='Path to Tatoeba TSV file')
    parser.add_argument('--kic',                required=True, help='Path to Anki notes TSV export')
    parser.add_argument('--output-dir',         default='sentences', help='Output directory for L###.json files')
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
