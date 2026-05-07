#!/usr/bin/env python3
"""
build_sentences.py

Builds per-lesson JSON files from:
  - Tatoeba Japanese-English sentence pairs (TSV)
  - Tatoeba bracket-form transcriptions (TSV) — authoritative furigana source
  - Tatoeba author whitelist (TSV) — restricts to trusted transcribers
  - KiC notes TSV exported from Anki (File → Export Notes, plain text with tags)
  - Optional extra corpora in JSONL format (--corpus name:path, repeatable)

Usage:
  python3 build_sentences.py \
    --tatoeba ../data/jpn_eng_sentences.tsv \
    --transcriptions ../data/jpn_transcriptions.tsv \
    --authors ../data/jpn_authors.tsv \
    --kic ../data/kic_augmented.txt \
    --corpus claude:../data/claude_supplement.jsonl \
    --output-dir ../sentences/grade2 \
    --base-grade 2 \
    --max-required-words 2

Each kept sentence must contain at least one matched KiC vocabulary word
from its catalog lesson (current_lesson) — that's what makes the sentence
relevant practice for that lesson. Sentences whose catalog lesson came
purely from an unmatched non-base-grade kanji (with no KiC vocab match
at that level) are discarded. This requirement also acts as a natural
per-lesson cap, so there is no artificial sentence limit.

Length buckets (by character count):
  very_short: ≤5    short: 6–14    medium: 15–30    long: ≥31

Writes one file per (length bucket, lesson):

  {output-dir}/very_short/L001.json
  {output-dir}/short/L001.json
  {output-dir}/medium/L001.json
  {output-dir}/long/L001.json

Output JSON structure per sentence:
  {
    "id": "grade2:short:L003:abc123def0",
    "ja_transcribed": "[今日|きょう]は[学校|がっ|こう]に[行|い]く。",
    "en": ["I'm going to school today."],
    "source": "tatoeba",
    "lesson": "L003",
    "transcription_author": "tommy_san",
    "char_length": 10,
    "length_bucket": "short",
    "required_words": 1,
    "kic_words_current_lesson": [
      {"word": "学校", "lesson": "L003", "kanji_ids": ["0049", "0051"], "reading": "がっこう", "quiz": true}
    ],
    "kic_words_previous_lessons": [
      {"word": "今日", "lesson": "L001", "kanji_ids": ["0016", "0027"], "reading": "きょう", "quiz": true}
    ],
    "non_kic_words": [
      {"surface": "行", "reading": "い", "kanji_ids": ["0123"], "max_kic_lesson": "L007", "quiz": true}
    ],
    "target_kanji_ids": ["0049", "0051"],
    "sentence_kanji_ids": ["0016", "0027", "0049", "0051", "0123"]
  }

A sentence's catalog lesson is bumped up whenever an unmatched (non-KiC-word)
kanji belongs to a higher KiC lesson than any of the matched KiC vocabulary,
so a sentence is filed under the latest-introduced kanji it contains.
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
    return set(ch for ch in text if '一' <= ch <= '鿿')

def is_kanji(ch: str) -> bool:
    return len(ch) == 1 and '一' <= ch <= '鿿'

BUCKETS = ('very_short', 'short', 'medium', 'long')

def length_bucket(n: int) -> str:
    if n <= 5:
        return "very_short"
    elif n <= 14:
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
            expanded = re.sub(r'（([^）]*)）', r'\1', f)
            _add(expanded)
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
    """
    result = re.sub(r'\s+', '', raw.strip())
    result = re.sub(r'（[^）]*）', '', result)
    return result

TAGLESS_LESSON = 'L000'
KANJI_ID_RE = re.compile(r'\b\d{4}\b')
LESSON_RE = re.compile(r'\bL\d{3}\b')

def lesson_to_int(lesson: str) -> int:
    return int(lesson[1:])

def int_to_lesson(n: int) -> str:
    return f"L{n:03d}"

# ── Loaders ──────────────────────────────────────────────────────────────────

def load_authors(filepath: str) -> set:
    """
    Load trusted transcription authors. The file's third tab-separated column
    is the username; we skip blank rows and the leading magic '_ok_' marker.
    Other heuristic: usernames that don't contain whitespace are accepted as-is.
    """
    authors = set()
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3:
                continue
            username = parts[2].strip()
            if not username or username == '_ok_':
                continue
            authors.add(username)
    return authors


def load_tatoeba(filepath: str):
    """
    Returns:
      sentences: dict[ja -> {"ids": [first_id], "en": [translations]}]
      ja_by_id:  dict[id_str -> ja]
    """
    sentences = {}
    ja_by_id = {}
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 4:
                continue
            ja_id = parts[0].lstrip('﻿').strip()
            ja = parts[1].strip()
            en = parts[3].strip()
            if not ja or not en:
                continue
            entry = sentences.setdefault(ja, {"ids": [], "en": []})
            if ja_id and ja_id not in entry["ids"]:
                entry["ids"].append(ja_id)
            if en not in entry["en"]:
                entry["en"].append(en)
            if ja_id and ja_id not in ja_by_id:
                ja_by_id[ja_id] = ja
    return sentences, ja_by_id


def load_transcriptions(filepath: str, allowed_authors: set):
    """
    Returns dict[ja_id -> (author, transcription)] filtered by allowed authors.
    """
    out = {}
    with open(filepath, encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 5:
                continue
            ja_id = parts[0].strip()
            author = parts[3].strip()
            transcription = parts[4].strip()
            if not ja_id or not transcription or author not in allowed_authors:
                continue
            if ja_id in out:
                continue  # keep first
            out[ja_id] = (author, transcription)
    return out


def load_corpus_jsonl(filepath: str) -> list:
    """
    Load a supplemental sentence corpus from a JSONL file.
    Each line must be: {"ja_transcribed": "...", "en": ["..."]}
    Returns list of dicts with those two keys.
    """
    records = []
    with open(filepath, encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            rec = json.loads(line)
            if 'ja_transcribed' not in rec or 'en' not in rec:
                raise ValueError(f"{filepath}:{lineno}: missing 'ja_transcribed' or 'en'")
            if isinstance(rec['en'], str):
                rec['en'] = [rec['en']]
            records.append(rec)
    return records


def load_kic(filepath: str):
    """
    Reads Anki 'Export Notes' TSV format and returns:
      lesson_words:  dict { lesson -> list of (word_form, reading, kanji_ids) }
                     Words with no L### tag stored under TAGLESS_LESSON.
      lesson_kanji:  dict { lesson -> set of kanji chars in any word taught that lesson }
      all_lessons:   sorted list of real lesson strings
      kanji_index:   dict { kanji_char -> {"kic_id": "0049", "lesson": "L007"} }
                     Built primarily from single-kanji cards (word == single CJK char);
                     compound-only kanji fall back to earliest appearing lesson and
                     a kic_id of None.
      lesson_cards:  dict { lesson -> list of {"word", "reading_raw", "tags", "forms"} }
                     One entry per Anki card, preserving file order. The raw bracket
                     reading and full tags string are kept verbatim for reporting.
    """
    lesson_words = defaultdict(list)
    lesson_kanji = defaultdict(set)
    lesson_cards = defaultdict(list)
    kanji_first_lesson = {}         # kanji_char -> earliest lesson seen
    kic_id_card_kanji = defaultdict(list)  # kic_id -> list of card-kanji-sets

    with open(filepath, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) < 5:
                continue
            word_raw     = parts[0].strip()
            reading_raw  = parts[1].strip()
            reading      = parse_anki_reading(parts[1])
            meaning      = parts[2].strip() if len(parts) > 2 else ""
            tags         = parts[4].strip()

            m = LESSON_RE.search(tags)
            lesson = m.group(0) if m else TAGLESS_LESSON
            kanji_ids = tuple(KANJI_ID_RE.findall(tags))

            forms = get_all_word_forms(word_raw)
            card_kanji = set()
            for form in forms:
                lesson_words[lesson].append((form, reading, kanji_ids))
                if lesson != TAGLESS_LESSON:
                    lesson_kanji[lesson].update(extract_kanji(form))
                    for ch in extract_kanji(form):
                        card_kanji.add(ch)
                        cur = kanji_first_lesson.get(ch)
                        if cur is None or lesson_to_int(lesson) < lesson_to_int(cur):
                            kanji_first_lesson[ch] = lesson

            if lesson != TAGLESS_LESSON and card_kanji:
                for kid in kanji_ids:
                    kic_id_card_kanji[kid].append(card_kanji)

            if lesson != TAGLESS_LESSON and forms:
                lesson_cards[lesson].append({
                    "word": word_raw,
                    "reading_raw": reading_raw,
                    "meaning": meaning,
                    "tags": tags,
                    "forms": forms,
                })

    # Resolve kic_id → kanji char by intersecting kanji sets across all cards
    # that carry the same kic_id in their tags. The kanji that is common to
    # every such card is the one that kic_id refers to.
    kic_id_to_kanji = {}
    for kid, sets in kic_id_card_kanji.items():
        common = set.intersection(*sets) if sets else set()
        if len(common) == 1:
            kic_id_to_kanji[kid] = next(iter(common))

    kanji_to_kic_id = {ch: kid for kid, ch in kic_id_to_kanji.items()}

    kanji_index = {}
    for ch, lesson in kanji_first_lesson.items():
        kanji_index[ch] = {
            "kic_id": kanji_to_kic_id.get(ch),
            "lesson": lesson,
        }

    all_lessons = sorted(k for k in lesson_words if k != TAGLESS_LESSON)
    return lesson_words, lesson_kanji, all_lessons, kanji_index, lesson_cards


# ── Transcription parser ─────────────────────────────────────────────────────

BRACKET_RE = re.compile(r'\[([^\[\]|]+)\|([^\[\]]+)\]')

def parse_transcription(text: str) -> list:
    """
    Parse a Tatoeba bracket transcription into ordered segments.

    Each segment is one of:
      {"kind": "plain",   "text": "..."}                         literal kana/punct
      {"kind": "bracket", "base": "学校",
                          "kanji_readings": ["がっ", "こう"],     per-char readings
                          "whole_reading": "がっこう"}            convenience
    """
    segments = []
    last = 0
    for match in BRACKET_RE.finditer(text):
        start, end = match.span()
        if start > last:
            segments.append({"kind": "plain", "text": text[last:start]})
        base = match.group(1)
        readings = match.group(2).split('|')
        whole = ''.join(readings)
        # Detect per-char mode: equal counts AND no empty readings.
        if len(readings) == len(base) and all(r for r in readings):
            kanji_readings = readings
        else:
            kanji_readings = [whole]
        segments.append({
            "kind": "bracket",
            "base": base,
            "kanji_readings": kanji_readings,
            "whole_reading": whole,
            "_start": start,
            "_end": end,
        })
        last = end
    if last < len(text):
        segments.append({"kind": "plain", "text": text[last:]})
    return segments


def transcription_surface(segments: list) -> str:
    """Reconstruct the surface sentence from parsed segments."""
    out = []
    for seg in segments:
        if seg["kind"] == "plain":
            out.append(seg["text"])
        else:
            out.append(seg["base"])
    return ''.join(out)


# ── Reading attribution to KiC matches ───────────────────────────────────────

def _index_segments_by_surface(segments):
    """Annotate each segment with its [start, end) surface position."""
    pos = 0
    for seg in segments:
        if seg["kind"] == "plain":
            length = len(seg["text"])
        else:
            length = len(seg["base"])
        seg["surface_start"] = pos
        seg["surface_end"] = pos + length
        pos += length


def _slice_bracket_reading(seg, base_start: int, base_end: int):
    """
    Extract reading for [base_start, base_end) chars within a bracket's base.
    Returns (reading_str, ok) where ok=False indicates the slice can't be
    cleanly extracted (e.g. partial slice into a single-reading bracket).
    """
    base = seg["base"]
    readings = seg["kanji_readings"]
    if base_start == 0 and base_end == len(base):
        return seg["whole_reading"], True
    if len(readings) == len(base):
        return ''.join(readings[base_start:base_end]), True
    return None, False


def attribute_reading(segments, span_start: int, span_end: int, fallback: str):
    """
    Compute the contextual reading for a surface span [span_start, span_end).
    Walks segments and concatenates per-char bracket readings + plain text.
    Falls back to `fallback` if a partial bracket overlap can't be sliced.
    Returns (reading_str, quiz_ok). quiz_ok is False when neither path
    produced a clean reading.
    """
    parts = []
    cleanly_sliced = True
    for seg in segments:
        if seg["surface_end"] <= span_start or seg["surface_start"] >= span_end:
            continue
        local_start = max(span_start, seg["surface_start"]) - seg["surface_start"]
        local_end = min(span_end, seg["surface_end"]) - seg["surface_start"]
        if seg["kind"] == "plain":
            parts.append(seg["text"][local_start:local_end])
        else:
            r, ok = _slice_bracket_reading(seg, local_start, local_end)
            if ok:
                parts.append(r)
            else:
                cleanly_sliced = False
                break
    if cleanly_sliced:
        return ''.join(parts), True
    if fallback:
        return fallback, False
    return '', False


# ── KiC vocabulary matching ──────────────────────────────────────────────────

def get_matched_kic_word_spans(sentence: str, word_records, current_lesson=None,
                                covered_positions=None):
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


# ── Unmatched (non-KiC) vocabulary grouping ──────────────────────────────────

def find_non_kic_units(segments, matched_spans, kanji_index, base_kanji):
    """
    Walk segments, ignoring positions inside matched_spans, and group remaining
    brackets (with short hiragana glue) into non-KiC vocabulary units.

    Returns list of dicts:
      {
        "surface": "受け皿",
        "reading": "うけざら",
        "kanji_ids": ["0123"],
        "max_kic_lesson": "L020" or None,
        "quiz": True,
        "_start": int, "_end": int,
      }

    `max_kic_lesson` reflects only non-base-grade kanji, so a sentence whose
    only "extra" kanji are already free under the chosen base grade does not
    get its catalog lesson bumped.

    Hiragana stretches between brackets up to length 2 are treated as okurigana
    glue and absorbed into the preceding bracket's word; longer stretches end
    the current unit. Punctuation always ends a unit.
    """
    GLUE_LIMIT = 2
    PUNCT = set("、。「」『』！？!?・…‥　 \"'(){}[]<>")

    def in_matched(start, end):
        for ms, me in matched_spans:
            if start < me and ms < end:
                return True
        return False

    units = []
    i = 0
    while i < len(segments):
        seg = segments[i]
        if seg["kind"] != "bracket" or in_matched(seg["surface_start"], seg["surface_end"]):
            i += 1
            continue

        # Start a new unit at this bracket; greedily extend through glue + adjacent brackets.
        surface_parts = [seg["base"]]
        reading_parts = [seg["whole_reading"]]
        kanji_chars = list(extract_kanji(seg["base"]))
        unit_start = seg["surface_start"]
        unit_end = seg["surface_end"]
        j = i + 1
        while j < len(segments):
            nxt = segments[j]
            if nxt["kind"] == "plain":
                # Determine if this plain run is short kana glue between two brackets.
                txt = nxt["text"]
                if (len(txt) <= GLUE_LIMIT
                        and all('぀' <= ch <= 'ゟ' for ch in txt)
                        and j + 1 < len(segments)
                        and segments[j + 1]["kind"] == "bracket"
                        and not in_matched(segments[j + 1]["surface_start"],
                                            segments[j + 1]["surface_end"])):
                    surface_parts.append(txt)
                    reading_parts.append(txt)
                    unit_end = nxt["surface_end"]
                    j += 1
                    continue
                break
            else:  # bracket
                if in_matched(nxt["surface_start"], nxt["surface_end"]):
                    break
                # Consecutive brackets without glue: only merge if they are truly
                # adjacent (no surface gap), which in this format means they sit
                # back-to-back. Check via surface positions.
                if nxt["surface_start"] != unit_end:
                    break
                surface_parts.append(nxt["base"])
                reading_parts.append(nxt["whole_reading"])
                kanji_chars.extend(extract_kanji(nxt["base"]))
                unit_end = nxt["surface_end"]
                j += 1
                continue

        # Build kanji_ids and max_kic_lesson from kanji chars. Base-grade
        # kanji contribute their kic_id (so they're displayable / quizzable)
        # but do not bump max_kic_lesson — they're considered already known.
        kanji_ids = []
        max_lesson_int = -1
        max_lesson = None
        for ch in kanji_chars:
            entry = kanji_index.get(ch)
            if not entry:
                continue
            if entry.get("kic_id"):
                kanji_ids.append(entry["kic_id"])
            if ch not in base_kanji and entry.get("lesson"):
                lint = lesson_to_int(entry["lesson"])
                if lint > max_lesson_int:
                    max_lesson_int = lint
                    max_lesson = entry["lesson"]

        units.append({
            "surface": ''.join(surface_parts),
            "reading": ''.join(reading_parts),
            "kanji_ids": sorted(set(kanji_ids)),
            "max_kic_lesson": max_lesson,
            "quiz": True,
            "_start": unit_start,
            "_end": unit_end,
        })
        i = j

    return units


# ── Output formatting ────────────────────────────────────────────────────────

def format_kic_word(word):
    return {
        "word": word["word"],
        "lesson": word["lesson"],
        "kanji_ids": word["kanji_ids"],
        "reading": word.get("reading", ""),
        "quiz": word.get("quiz", True),
    }


def format_non_kic_word(unit):
    return {
        "surface": unit["surface"],
        "reading": unit["reading"],
        "kanji_ids": unit["kanji_ids"],
        "max_kic_lesson": unit["max_kic_lesson"],
        "quiz": unit["quiz"],
    }


def unique_sorted_kanji_ids_from_words(words: list) -> list:
    return sorted({
        kanji_id
        for word in words
        for kanji_id in word.get("kanji_ids", [])
    })


def sentence_id(base_grade: int, bucket: str, lesson: str, ja: str, sentence: dict) -> str:
    """Stable content ID for app caches and downstream reference."""
    canonical = {
        "grade": base_grade,
        "bucket": bucket,
        "lesson": lesson,
        "ja": ja,
        "current": [{"word": w["word"], "lesson": w["lesson"]} for w in sentence["kic_words_current_lesson"]],
        "previous": [{"word": w["word"], "lesson": w["lesson"]} for w in sentence["kic_words_previous_lessons"]],
        "non_kic": [w["surface"] for w in sentence["non_kic_words"]],
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
    return f"grade{base_grade}:{bucket}:{lesson}:{digest}"


# ── Sampling and output dir mgmt ─────────────────────────────────────────────

def split_by_bucket(results: list) -> dict:
    """Group sentences by length bucket, sorted by char_length asc within each."""
    by_bucket = {b: [] for b in BUCKETS}
    for s in results:
        by_bucket[s['length_bucket']].append(s)
    for b in BUCKETS:
        by_bucket[b].sort(key=lambda x: x['char_length'])
    return by_bucket


def clean_grade_output_dir(output_dir: str) -> None:
    """Remove all existing files/subdirs under output_dir."""
    if not os.path.isdir(output_dir):
        return
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


# ── Main pipeline ────────────────────────────────────────────────────────────

def build_sentences(
    tatoeba_path: str,
    transcriptions_path: str,
    authors_path: str,
    kic_path: str,
    output_dir: str,
    base_grade: int,
    max_required_words: int,
    corpora: dict = None,
):
    print("Loading authors...")
    authors = load_authors(authors_path)
    print(f"  {len(authors)} trusted transcription authors")

    print("Loading KiC cards...")
    lesson_words, lesson_kanji, all_lessons, kanji_index, lesson_cards = load_kic(kic_path)
    total_forms = sum(len(v) for v in lesson_words.values())
    total_cards = sum(len(v) for v in lesson_cards.values())
    print(f"  {len(all_lessons)} lessons, {total_cards} cards, {total_forms} word forms, "
          f"{len(kanji_index)} kanji indexed")

    print("Loading Tatoeba sentences...")
    tatoeba, ja_by_id = load_tatoeba(tatoeba_path)
    print(f"  {len(tatoeba)} unique Japanese sentences")

    print("Loading transcriptions...")
    transcriptions = load_transcriptions(transcriptions_path, authors)
    print(f"  {len(transcriptions)} transcriptions from trusted authors")

    base_kanji = get_base_kanji(base_grade)
    print(f"  Base kanji set: Grade 1–{base_grade} ({len(base_kanji)} kanji)")

    # Build unified sentence list: (ja_transcribed, en, source, author, expected_surface)
    # expected_surface is set for Tatoeba (to validate transcription) and None for corpora.
    unified = []
    for ja, meta in tatoeba.items():
        author = transcription = None
        for ja_id in meta["ids"]:
            t = transcriptions.get(ja_id)
            if t:
                author, transcription = t
                break
        if transcription:
            unified.append((transcription, meta["en"], "tatoeba", author, ja))

    for corpus_name, records in (corpora or {}).items():
        print(f"Loading corpus '{corpus_name}' ({len(records)} records)...")
        for rec in records:
            unified.append((rec["ja_transcribed"], rec["en"], corpus_name, None, None))

    os.makedirs(output_dir, exist_ok=True)
    clean_grade_output_dir(output_dir)

    # Cumulative word records per lesson (for the longest-first matching sort).
    cumulative_word_records = []
    for lesson in all_lessons:
        cumulative_word_records.extend(
            (form, reading, lesson, kanji_ids)
            for form, reading, kanji_ids in lesson_words[lesson]
        )

    # Single global pass: each sentence is processed once, then assigned to its
    # tag_lesson based on max-introduced kanji across matched + unmatched vocab.
    print("Processing sentences...")
    by_tag_lesson = defaultdict(list)
    matched_forms = set()
    stats = {
        "no_transcription": 0,
        "surface_mismatch": 0,
        "kanji_outside": 0,
        "no_quiz_target": 0,
        "no_current_lesson_word": 0,
        "too_many_required": 0,
        "kept": 0,
    }

    for ja_transcribed, en, source, author, expected_surface in unified:
        if not ja_transcribed:
            stats["no_transcription"] += 1
            continue

        segments = parse_transcription(ja_transcribed)
        ja = transcription_surface(segments)
        if expected_surface is not None and ja != expected_surface:
            stats["surface_mismatch"] += 1
            continue
        _index_segments_by_surface(segments)

        ja_kanji = extract_kanji(ja)
        # Hard filter: every kanji must be base-grade or in KiC.
        if not all(ch in base_kanji or ch in kanji_index for ch in ja_kanji):
            stats["kanji_outside"] += 1
            continue

        # KiC vocab matches across ALL lessons (we'll classify current/previous later).
        all_matches = get_matched_kic_word_spans(ja, cumulative_word_records)

        matched_spans = [(m["_start"], m["_end"]) for m in all_matches]
        non_kic_units = find_non_kic_units(segments, matched_spans, kanji_index, base_kanji)

        # Drop units with zero kanji_ids AND no max_kic_lesson — those are
        # all-katakana names or punctuation-bracket cases, not useful as quiz targets.
        non_kic_units = [u for u in non_kic_units if u["kanji_ids"] or u["max_kic_lesson"]]

        # Need at least one quiz target.
        if not all_matches and not non_kic_units:
            stats["no_quiz_target"] += 1
            continue

        # Compute tag_lesson.
        matched_max_int = -1
        for m in all_matches:
            mi = lesson_to_int(m["lesson"])
            if mi > matched_max_int:
                matched_max_int = mi
        unmatched_max_int = -1
        for u in non_kic_units:
            if u["max_kic_lesson"]:
                ui = lesson_to_int(u["max_kic_lesson"])
                if ui > unmatched_max_int:
                    unmatched_max_int = ui
        tag_int = max(matched_max_int, unmatched_max_int, 1)
        tag_lesson = int_to_lesson(tag_int)

        # Attribute readings to each KiC match using the transcription.
        kic_words = []
        for m in all_matches:
            reading, quiz_ok = attribute_reading(
                segments, m["_start"], m["_end"], fallback=m.get("reading", "")
            )
            kic_words.append({
                "word": m["word"],
                "lesson": m["lesson"],
                "kanji_ids": m["kanji_ids"],
                "reading": reading,
                "quiz": quiz_ok and bool(reading),
            })

        current_words = [w for w in kic_words if w["lesson"] == tag_lesson]
        previous_words = [w for w in kic_words if w["lesson"] != tag_lesson]

        # Constraint: at least one matched KiC vocab word at the catalog lesson.
        # If tag_lesson came purely from an unmatched non-base-grade kanji,
        # current_words will be empty and the sentence is not useful for
        # practising that lesson.
        if not current_words:
            stats["no_current_lesson_word"] += 1
            continue

        # Constraint: cap current-lesson KiC vocab count.
        if len(current_words) > max_required_words:
            stats["too_many_required"] += 1
            continue

        char_len = len(ja)
        bucket = length_bucket(char_len)

        sentence = {
            "ja_transcribed": ja_transcribed,
            "en": en,
            "source": source,
            "lesson": tag_lesson,
            "transcription_author": author,
            "char_length": char_len,
            "length_bucket": bucket,
            "required_words": len(current_words),
            "kic_words_current_lesson": [format_kic_word(w) for w in current_words],
            "kic_words_previous_lessons": [format_kic_word(w) for w in previous_words],
            "non_kic_words": [format_non_kic_word(u) for u in non_kic_units],
        }
        sentence["target_kanji_ids"] = unique_sorted_kanji_ids_from_words(
            sentence["kic_words_current_lesson"]
        )
        sentence["sentence_kanji_ids"] = sorted(set(
            unique_sorted_kanji_ids_from_words(
                sentence["kic_words_current_lesson"]
                + sentence["kic_words_previous_lessons"]
            )
            + [kid for u in sentence["non_kic_words"] for kid in u["kanji_ids"]]
        ))
        sentence["id"] = sentence_id(base_grade, bucket, tag_lesson, ja, sentence)

        by_tag_lesson[tag_lesson].append(sentence)
        for w in kic_words:
            matched_forms.add(w["word"])
        stats["kept"] += 1

    print(f"  kept={stats['kept']} "
          f"no_transcription={stats['no_transcription']} "
          f"surface_mismatch={stats['surface_mismatch']} "
          f"kanji_outside={stats['kanji_outside']} "
          f"no_quiz_target={stats['no_quiz_target']} "
          f"no_current_lesson_word={stats['no_current_lesson_word']} "
          f"too_many_required={stats['too_many_required']}")

    print("Writing per-lesson files...")
    total_written = 0
    counts_by_lesson = {}
    for lesson in all_lessons:
        sentences_for_lesson = by_tag_lesson.get(lesson, [])
        bucket_rows = split_by_bucket(sentences_for_lesson)
        for bucket in BUCKETS:
            bucket_dir = os.path.join(output_dir, bucket)
            os.makedirs(bucket_dir, exist_ok=True)
            with open(os.path.join(bucket_dir, f"{lesson}.json"), "w", encoding="utf-8") as f:
                json.dump(bucket_rows[bucket], f, ensure_ascii=False, indent=2)
            total_written += len(bucket_rows[bucket])
        counts_by_lesson[lesson] = {b: len(bucket_rows[b]) for b in BUCKETS}
        if sentences_for_lesson:
            counts_str = " ".join(f"{b}={len(bucket_rows[b])}" for b in BUCKETS)
            print(f"  {lesson}: {len(sentences_for_lesson):4d} kept  ({counts_str})")

    summary_path = os.path.join(output_dir, "summary.md")
    write_summary_file(
        summary_path, all_lessons, counts_by_lesson, lesson_cards, matched_forms,
    )
    print(f"\nTotal sentences written: {total_written}")
    print(f"Output directory: {output_dir}/{{{','.join(BUCKETS)}}}/")
    print(f"Summary file: {summary_path}")
    print("Done!")


def write_summary_file(
    path: str,
    lessons: list,
    counts_by_lesson: dict,
    lesson_cards: dict,
    matched_forms: set,
) -> None:
    """
    Write a markdown summary with two sections:
      1. A table of per-lesson, per-bucket sentence counts (rows = lesson,
         columns = length bucket, plus a totals row and column).
      2. A "Words without practice sentences" section listing every Anki
         card whose forms never appeared in any kept sentence, sub-headed
         by lesson.
    """
    headers = ["Lesson", *BUCKETS, "total"]
    aligns = ["---", *(["---:"] * (len(headers) - 1))]
    column_totals = {b: 0 for b in BUCKETS}
    grand_total = 0

    rows = []
    for lesson in lessons:
        counts = counts_by_lesson.get(lesson, {b: 0 for b in BUCKETS})
        row_total = sum(counts[b] for b in BUCKETS)
        grand_total += row_total
        for b in BUCKETS:
            column_totals[b] += counts[b]
        rows.append([lesson, *(str(counts[b]) for b in BUCKETS), str(row_total)])
    rows.append([
        "**total**",
        *(f"**{column_totals[b]}**" for b in BUCKETS),
        f"**{grand_total}**",
    ])

    missing_by_lesson = {}
    total_cards = 0
    total_missing = 0
    for lesson in lessons:
        cards = lesson_cards.get(lesson, [])
        total_cards += len(cards)
        missing = [c for c in cards if not any(f in matched_forms for f in c["forms"])]
        if missing:
            missing_by_lesson[lesson] = missing
            total_missing += len(missing)

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Sentence build summary\n\n")
        f.write("## Per-lesson sentence counts\n\n")
        f.write("| " + " | ".join(headers) + " |\n")
        f.write("| " + " | ".join(aligns) + " |\n")
        for row in rows:
            f.write("| " + " | ".join(row) + " |\n")
        f.write("\n")
        f.write(
            f"## Words without practice sentences "
            f"({total_missing} of {total_cards} cards)\n\n"
        )
        if not missing_by_lesson:
            f.write("_All KiC cards have at least one practice sentence._\n")
            return
        for lesson in lessons:
            cards = missing_by_lesson.get(lesson)
            if not cards:
                continue
            f.write(f"### {lesson} ({len(cards)})\n\n")
            for c in cards:
                f.write(f"- {c['word']} — {c['reading_raw']} — {c['meaning']} — `{c['tags']}`\n")
            f.write("\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build per-lesson sentence JSON files for KiC practice app')
    parser.add_argument('--tatoeba',         required=True, help='Path to Tatoeba JP-EN sentences TSV')
    parser.add_argument('--transcriptions',  required=True, help='Path to Tatoeba transcriptions TSV')
    parser.add_argument('--authors',         required=True, help='Path to Tatoeba author whitelist TSV')
    parser.add_argument('--kic',             required=True, help='Path to augmented KiC notes TSV')
    parser.add_argument('--corpus',          action='append', default=[], metavar='NAME:PATH',
                        help='Extra corpus JSONL file, e.g. --corpus claude:../data/claude_supplement.jsonl (repeatable)')
    parser.add_argument('--output-dir',      default='sentences', help='Grade output root, e.g. sentences/grade2')
    parser.add_argument('--base-grade',      type=int, default=2, choices=[1, 2],
                        help='Base kanji grade level (1 or 2, default: 2)')
    parser.add_argument('--max-required-words', type=int, default=2,
                        help='Max KiC words from the tagged lesson per sentence (default: 2)')
    args = parser.parse_args()

    corpora = {}
    for entry in args.corpus:
        if ':' not in entry:
            parser.error(f"--corpus must be NAME:PATH, got: {entry!r}")
        name, path = entry.split(':', 1)
        corpora[name] = load_corpus_jsonl(path)

    build_sentences(
        tatoeba_path=args.tatoeba,
        transcriptions_path=args.transcriptions,
        authors_path=args.authors,
        kic_path=args.kic,
        output_dir=args.output_dir,
        base_grade=args.base_grade,
        max_required_words=args.max_required_words,
        corpora=corpora,
    )
