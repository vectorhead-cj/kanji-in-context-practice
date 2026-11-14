#!/usr/bin/env python3
"""
select_sentences.py — choose ~10 example sentences per card for one lesson block.

Sources, in order of preference:
  1. sentences/authored/L###.jsonl   Claude-written sentences (always used)
  2. Tatoeba (trusted transcriptions), ranked, to fill up to --per-card

Every sentence goes through common.prepare(): the target must match by
surface + reading, and every other kanji word is labelled learned (on a card
at or before this one) or unlearned (furigana always shown). Sentences are
never rewritten. Authored sentences that fail prepare(), or duplicate another
sentence for the card, are skipped and listed in reports/L###_rejected.jsonl.

Tatoeba sentences are only used if
  * they have at most MAX_UNLEARNED unlearned words, and
  * every unlearned word is common: it appears at least MIN_FREQ times in the
    trusted Tatoeba transcriptions (filters 世知辛い, 深酒, 木立, …).
Picks are made one at a time, penalising words already used in this card's
sentences (variety), with at most MAX_TOM sentences mentioning トム.

Writes sentences/L###.json.

Usage (from anki_deck/):
  python3 scripts/select_sentences.py --lesson 1
"""

import argparse
import json
import re
from collections import Counter, defaultdict

from common import (
    HERE, REPO, Vocab, block_lesson, kanji_in, load_authors, load_cards,
    load_tatoeba, load_transcriptions, prepare, strip_html,
)

MAX_LEN = 22           # surface characters
MAX_UNLEARNED = 2      # unlearned kanji words per Tatoeba sentence
MIN_FREQ = 10          # occurrences in trusted Tatoeba for an unlearned word
MAX_TOM = 2            # Tatoeba sentences with トム per card
REPEAT_PENALTY = 4.0   # per word shared with an already chosen sentence
LATIN_RE = re.compile(r"[A-Za-z0-9０-９Ａ-Ｚａ-ｚ₀-₉]")
WORD_RE = re.compile(r"[\[{]([^|\]}]+)\|[^\]}]+[\]}]")
KATAKANA_RE = re.compile(r"[ァ-ヶー]{2,}")


def surface(bracketed: str) -> str:
    return WORD_RE.sub(r"\1", bracketed)


def lesson_cards(cards, lesson):
    return [c for c in cards if c["stage"] == 1 and block_lesson(c) == lesson]


def load_authored(lesson: int) -> dict[str, list[dict]]:
    path = HERE / "sentences" / "authored" / f"L{lesson:03d}.jsonl"
    out = defaultdict(list)
    if path.exists():
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip() and not line.startswith("#"):
                rec = json.loads(line)
                rec["_line"] = n
                out[rec["card"]].append(rec)
    return out


def load_excluded() -> set[str]:
    """Tatoeba ids rejected on review (one per line, '# reason' allowed)."""
    path = HERE / "sentences" / "excluded_tatoeba.txt"
    if not path.exists():
        return set()
    return {ln.split("#")[0].strip() for ln in path.read_text().splitlines()
            if ln.split("#")[0].strip()}


def word_frequencies(transcriptions) -> Counter:
    """(base, reading) -> how many trusted transcriptions contain that bracket."""
    freq = Counter()
    for _, t in transcriptions:
        for m in re.finditer(r"\[([^\[\]|]+)\|([^\[\]]+)\]", t):
            freq[(m.group(1), m.group(2).replace("|", ""))] += 1
    return freq


class WordFreq:
    """
    How common a word is in trusted Tatoeba. Tatoeba often brackets compounds
    by morpheme ([誕生|たんじょう][日|び]), so a word missing as a whole is
    scored by its best split into bracketed parts: the rarest part counts.
    """

    def __init__(self, counts: Counter):
        self.counts = counts
        self.by_surface = defaultdict(dict)
        for (b, r), n in counts.items():
            self.by_surface[b][r] = n

    def __getitem__(self, word: tuple[str, str]) -> int:
        surface, reading = word
        from functools import lru_cache

        @lru_cache(maxsize=None)
        def best(i: int, j: int) -> int:
            if i == len(surface):
                return 10 ** 9 if j == len(reading) else 0
            top = 0
            for k in range(len(surface), i, -1):
                for r, n in self.by_surface.get(surface[i:k], {}).items():
                    if n > top and reading.startswith(r, j):
                        top = max(top, min(n, best(k, j + len(r))))
            return top

        whole = self.counts.get(word, 0)
        return max(whole, best(0, 0) if whole < MIN_FREQ else 0)


def load_pool():
    d = REPO / "data"
    authors = load_authors(d / "jpn_authors.tsv")
    sents, _ = load_tatoeba(d / "jpn_eng_sentences.tsv")
    trans = load_transcriptions(d / "jpn_transcriptions.tsv", authors)
    en_by_id = {}
    for ja, v in sents.items():
        for i in v["ids"]:
            en_by_id[i] = (ja, v["en"])
    by_kanji = defaultdict(list)
    for i, (author, t) in trans.items():
        if i not in en_by_id:
            continue
        rec = (i, author, t, en_by_id[i][1][0])
        for k in kanji_in(t):
            by_kanji[k].append(rec)
    counts = word_frequencies(trans.values())
    import common
    if not common.COMPOUNDS:
        common.COMPOUNDS.update(b for (b, _), n in counts.items() if n >= 3 and len(b) >= 2)
        for c in load_cards():
            for f in re.split(r"[、,]", strip_html(c["Expression"])):
                f = re.sub(r"[（）()～〜]", "", f).strip()
                k = "".join(ch for ch in f if kanji_in(ch))
                if len(k) >= 2:
                    common.COMPOUNDS.add(k)
    return by_kanji, WordFreq(counts)


def content_words(ja: str, target_span: tuple[int, int]) -> set[str]:
    """Words used for the variety penalty: kanji words (not the target) + katakana."""
    words, pos = set(), 0
    s = surface(ja)
    for m in WORD_RE.finditer(ja):
        start = s.find(m.group(1), pos)
        if not (target_span[0] <= start < target_span[1]):
            words.add(m.group(1))
        pos = max(pos, start + len(m.group(1)))
    words |= set(KATAKANA_RE.findall(s))
    return words


def base_score(p: dict) -> float:
    s = surface(p["ja"])
    return abs(len(s) - 11) + 2.5 * len(p["unlearned"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", type=int, required=True)
    ap.add_argument("--per-card", type=int, default=10)
    args = ap.parse_args()

    all_cards = load_cards()
    vocab = Vocab(all_cards)
    cards = lesson_cards(all_cards, args.lesson)
    pool, freq = load_pool()           # also fills common.COMPOUNDS, needed by prepare()
    authored = load_authored(args.lesson)
    unknown_keys = set(authored) - {c["Expression"] for c in cards}
    if unknown_keys:
        raise SystemExit(f"Authored sentences for cards not in L{args.lesson:03d}: {unknown_keys}")

    excluded = load_excluded()
    out, rejected = [], []
    for c in cards:
        chosen, seen, used_words = [], set(), Counter()

        for rec in authored.get(c["Expression"], []):
            p = prepare(rec["ja"], c, vocab)
            reason = None
            if p is None:
                reason = "target not found with this reading/word boundary, or bad brackets"
            elif surface(p["ja"]) in seen:
                reason = "duplicate of another sentence for this card"
            if reason:
                rejected.append({"line": rec["_line"], "card": c["Expression"],
                                 "ja": rec["ja"], "en": rec["en"], "reason": reason})
                continue
            seen.add(surface(p["ja"]))
            used_words.update(content_words(p["ja"], p["hl"]))
            chosen.append({"ja": p["ja"], "hl": p["hl"], "en": rec["en"], "src": "claude"})

        if len(chosen) < args.per_card:
            first = sorted(kanji_in(strip_html(c["Expression"])))
            cands = []
            for tid, author, t, en in pool.get(first[0], []) if first else []:
                if tid in excluded:
                    continue
                p = prepare(t, c, vocab)
                if not p:
                    continue
                s = surface(p["ja"])
                if len(s) > MAX_LEN or LATIN_RE.search(s) or s in seen:
                    continue
                if len(p["unlearned"]) > MAX_UNLEARNED:
                    continue
                if any(freq[w] < MIN_FREQ for w in p["unlearned"]):
                    continue
                cands.append((base_score(p), tid, p, en, s, content_words(p["ja"], p["hl"])))
            cands.sort(key=lambda x: (x[0], x[1]))

            en_seen, toms = set(), 0
            while len(chosen) < args.per_card and cands:
                best_i, best_v = None, None
                for i, (sc, tid, p, en, s, words) in enumerate(cands):
                    if en.lower() in en_seen or s[:5] in {x[:5] for x in seen}:
                        continue
                    if "トム" in s and toms >= MAX_TOM:
                        continue
                    v = sc + REPEAT_PENALTY * sum(1 for w in words if used_words[w])
                    if best_v is None or v < best_v:
                        best_i, best_v = i, v
                if best_i is None:
                    break
                sc, tid, p, en, s, words = cands.pop(best_i)
                en_seen.add(en.lower())
                seen.add(s)
                used_words.update(words)
                toms += "トム" in s
                chosen.append({"ja": p["ja"], "hl": p["hl"], "en": en,
                               "src": f"tatoeba:{tid}"})

        out.append({
            "guid": c["guid"],
            "deck_position": c["deck_position"],
            "expression": strip_html(c["Expression"]),
            "reading": strip_html(c["Reading"]),
            "meaning": strip_html(c["Meaning"]),
            "sentences": chosen,
        })

    path = HERE / "sentences" / f"L{args.lesson:03d}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    short = [(o["expression"], len(o["sentences"])) for o in out if len(o["sentences"]) < args.per_card]
    print(f"Wrote {path.relative_to(HERE)}: {len(out)} cards, "
          f"{sum(len(o['sentences']) for o in out)} sentences")
    print(f"Cards below {args.per_card}: {len(short)}")
    for e, n in short:
        print(f"  {e}: {n}")
    rej_path = HERE / "reports" / f"L{args.lesson:03d}_rejected.jsonl"
    if rejected:
        rej_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejected))
        print(f"Rejected {len(rejected)} authored sentences -> {rej_path.relative_to(HERE)}")
    elif rej_path.exists():
        rej_path.unlink()


if __name__ == "__main__":
    main()
