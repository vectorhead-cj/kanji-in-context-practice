#!/usr/bin/env python3
"""
validate.py — independent checks on sentences/L###.json, plus a review report.

Sentences mark learned words [base|reading] (furigana follows the toggle)
and unlearned words {base|reading} (furigana always shown).

Errors (exit 1):
  E1 learned       a word marked learned contains a kanji not seen at or
                   before the card's deck position (target kanji excepted) —
                   it would be shown without furigana while unknown
  E2 furigana      a kanji outside a bracket, or a bracket reading that is not kana
  E3 target        target word (surface + reading) not found, or highlight
                   span does not cover it
  E4 format        empty English, duplicate sentence, malformed record, card
                   guid not in the deck
Warnings (listed in the report for human review):
  W1 reading       a bracket reading that cannot be built from readings of that
                   kanji found anywhere in the deck (allowing sound changes)
  W2 count         fewer than --per-card sentences
  W3 length        sentence surface longer than 30 characters
  W4 unlearned     more than 2 unlearned words in one sentence
  W5 rare word     an unlearned word seen fewer than 10 times in trusted Tatoeba
  W6 digits        Arabic digits instead of kanji numerals

Writes reports/L###.md with the results and a random spot-check sample.

Usage (from anki_deck/):
  python3 scripts/validate.py --lesson 1
"""

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from functools import lru_cache

from select_sentences import load_pool, MAX_UNLEARNED, MIN_FREQ
from common import (
    HERE, ANKI_UNIT_RE, Form, find_target, is_kanji, kanji_in,
    known_kanji_by_position, load_cards, parse_forms, strip_html, target_kanji, tokenize,
)

BRACKET_RE = re.compile(r"[\[{]([^\[\]{}|]+)\|([^\[\]{}]+)[\]}]")
LEARNED_RE = re.compile(r"\[([^\[\]|]+)\|([^\[\]]+)\]")
UNLEARNED_RE = re.compile(r"\{([^{}|]+)\|([^{}]+)\}")
KANA_RE = re.compile(r"^[぀-ゟ゠-ヿー]+$")
DAKU = str.maketrans("かきくけこさしすせそたちつてとはひふへほ",
                     "がぎぐげござじずぜぞだぢづでどばびぶべぼ")
HANDAKU = str.maketrans("はひふへほ", "ぱぴぷぺぽ")


def surface(b: str) -> str:
    return BRACKET_RE.sub(lambda m: m.group(1), b)


def reading_inventory(cards) -> dict[str, set[str]]:
    """kanji (or jukujikun group) -> readings seen in the deck's Reading fields."""
    inv = defaultdict(set)
    for c in cards:
        for m in ANKI_UNIT_RE.finditer(strip_html(c["Reading"])):
            base, r = m.group(1), m.group(2)
            base = "".join(ch for ch in base if is_kanji(ch)) if len(kanji_in(base)) == 1 else base
            inv[base].add(r)
    if "く" in inv.get("来", ()):
        inv["来"] |= {"き", "こ"}          # irregular 来る
    return inv


def variants(r: str) -> set[str]:
    out = {r}
    if r:
        out |= {r[0].translate(DAKU) + r[1:], r[0].translate(HANDAKU) + r[1:]}
    if len(r) >= 2 and r[-1] in "つちくき":
        out |= {v[:-1] + "っ" for v in list(out)}
    return out


def reading_ok(base: str, reading: str, inv) -> bool:
    if base in inv and reading in inv[base]:
        return True

    @lru_cache(maxsize=None)
    def seg(i: int, j: int) -> bool:
        if i == len(base):
            return j == len(reading)
        for k in range(len(base), i, -1):
            for r in inv.get(base[i:k], ()):
                for v in variants(r):
                    if reading.startswith(v, j) and seg(k, j + len(v)):
                        return True
        return False

    return seg(0, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", type=int, required=True)
    ap.add_argument("--per-card", type=int, default=10)
    ap.add_argument("--sample", type=int, default=12, help="cards in the spot-check sample")
    args = ap.parse_args()
    tag = f"L{args.lesson:03d}"

    cards = load_cards()
    by_guid = {c["guid"]: c for c in cards}
    known = known_kanji_by_position(cards)
    inv = reading_inventory(cards)
    data = json.loads((HERE / "sentences" / f"{tag}.json").read_text())
    _, freq = load_pool()

    errors, warnings = [], []
    n_sent = 0
    src_count = defaultdict(int)
    for rec in data:
        card = by_guid.get(rec.get("guid"))
        label = rec.get("expression", "?")
        if card is None:
            errors.append(f"E4 {label}: guid {rec.get('guid')} not in deck")
            continue
        allowed = set(known[card["deck_position"]]) | target_kanji(card)
        forms = parse_forms(card)
        seen = set()
        sents = rec.get("sentences", [])
        if len(sents) < args.per_card:
            warnings.append(f"W2 {label}: only {len(sents)} sentences")
        for s in sents:
            n_sent += 1
            src_count[s.get("src", "?").split(":")[0]] += 1
            ja, en, hl = s.get("ja", ""), s.get("en", ""), s.get("hl")
            surf = surface(ja)
            where = f"{label}: {surf}"
            if not ja or not isinstance(hl, list) or len(hl) != 2:
                errors.append(f"E4 {where}: malformed record")
                continue
            if not en.strip():
                errors.append(f"E4 {where}: empty English")
            if surf in seen:
                errors.append(f"E4 {where}: duplicate")
            seen.add(surf)
            learned_kanji = kanji_in("".join(m.group(1) for m in LEARNED_RE.finditer(ja)))
            bad = sorted(learned_kanji - allowed)
            if bad:
                errors.append(f"E1 {where}: marked learned but kanji not yet seen: {''.join(bad)}")
            unl = [(m.group(1), m.group(2).replace("|", "")) for m in UNLEARNED_RE.finditer(ja)]
            if len(unl) > MAX_UNLEARNED:
                warnings.append(f"W4 {where}: {len(unl)} unlearned words: "
                                f"{'、'.join(b for b, _ in unl)}")
            for b, r in unl:
                if freq[(b, r)] < MIN_FREQ:
                    warnings.append(f"W5 {where}: rare word {b}（{r}） seen {freq[(b, r)]}×")
            if kanji_in(BRACKET_RE.sub("", ja)):
                errors.append(f"E2 {where}: kanji without furigana")
            for m in BRACKET_RE.finditer(ja):
                base, reading = m.group(1), m.group(2).replace("|", "")
                if not KANA_RE.match(reading):
                    errors.append(f"E2 {where}: non-kana reading [{base}|{reading}]")
                elif len(reading) < len(kanji_in(base)):
                    errors.append(f"E2 {where}: reading too short for [{base}|{reading}]")
                elif m.group(0)[0] == "[" and not reading_ok(base, reading, inv):
                    # Learned words only: unlearned ones may not be in the deck at all.
                    warnings.append(f"W1 {where}: unusual reading [{base}|{reading}]")
            toks = tokenize(UNLEARNED_RE.sub(r"[\1|\2]", ja))
            hit = find_target(toks, forms) if toks else None
            if hit is None:
                errors.append(f"E3 {where}: target {card['Expression']} not found")
            else:
                start, end, _ = hit
                if not (hl[0] <= start and end <= hl[1]):
                    errors.append(f"E3 {where}: highlight {hl} does not cover target {start}-{end}")
            if re.search(r"[0-9０-９]", surf):
                warnings.append(f"W6 {where}: digits instead of kanji numerals")
            if len(surf) > 30:
                warnings.append(f"W3 {where}: {len(surf)} characters")

    # ── report ──
    rnd = random.Random(args.lesson)
    sample = rnd.sample(data, min(args.sample, len(data)))
    sample.sort(key=lambda r: r["deck_position"])
    lines = [
        f"# {tag} validation report", "",
        f"- Cards: {len(data)}",
        f"- Sentences: {n_sent} ({', '.join(f'{k}: {v}' for k, v in sorted(src_count.items()))})",
        f"- Errors: {len(errors)}",
        f"- Warnings: {len(warnings)}", "",
    ]
    if errors:
        lines += ["## Errors", ""] + [f"- {e}" for e in errors] + [""]
    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]
    lines += ["## Spot-check sample", "",
              "**Bold** is the target. Words not learned yet at that card are "
              "shown with their reading: 漢字（かんじ）.", ""]
    for r in sample:
        lines.append(f"### #{r['deck_position']} {r['expression']} — {r['meaning'][:60]}")
        lines.append("")
        for s in rnd.sample(r["sentences"], min(3, len(r["sentences"]))):
            surf = surface(s["ja"])
            a, b = s["hl"]
            text = surf[:a] + "**" + surf[a:b] + "**" + surf[b:]
            for m in UNLEARNED_RE.finditer(s["ja"]):
                text = text.replace(m.group(1), f"{m.group(1)}（{m.group(2).replace('|', '')}）", 1)
            lines.append(f"- {text} — {s['en']} `{s['src']}`")
        lines.append("")
    out = HERE / "reports" / f"{tag}.md"
    out.write_text("\n".join(lines))

    print(f"{tag}: {len(data)} cards, {n_sent} sentences, "
          f"{len(errors)} errors, {len(warnings)} warnings -> {out.relative_to(HERE)}")
    for e in errors[:30]:
        print("  ", e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
