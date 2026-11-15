#!/usr/bin/env python3
"""
build_prompt.py — write a sentence-generation prompt for a range of lessons.

For each card in the lessons, the prompt asks for (10 − sentences it already
has) new sentences and lists the existing ones so they aren't repeated. Run
select_sentences.py for the lessons first, so the counts reflect the current
Tatoeba picks and authored sentences.

The rules live in scripts/prompt_template.md; this script adds the card tables
and the word list (every card up to the last one in the range, in practice
order).

Usage (from anki_deck/):
  python3 scripts/build_prompt.py --lessons 9-20
  -> prompts/L009-L020_generate.md
"""

import argparse
import json
import re

from build_import import parse_lessons
from common import HERE, load_cards, strip_html

WORD_RE = re.compile(r"[\[{]([^|\]}]+)\|[^\]}]+[\]}]")


def plain_reading(c: dict) -> str:
    return "、".join(re.sub(r"([^\s\[\]]+?)\[([^\]]+)\]", r"\2", a.strip()).replace(" ", "")
                    for a in strip_html(c["Reading"]).split("、"))


def meaning(c: dict) -> str:
    m = re.sub(r"<br\s*/?>", "; ", c["Meaning"])
    m = strip_html(m)
    m = re.sub(r"\s*([^\s\[\]；;：]+?)\[([^\]]+)\]", r"\1(\2)", m)   # 一日[ついたち] → 一日(ついたち)
    return m.replace("|", "/").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lessons", required=True, help="e.g. 9-20")
    args = ap.parse_args()
    lessons = parse_lessons(args.lessons)

    cards = sorted(load_cards(), key=lambda c: c["deck_position"])
    by_guid = {c["guid"]: c for c in cards}
    sections, maxpos, total, ncards = [], 0, 0, 0
    for L in lessons:
        rows = []
        for o in json.loads((HERE / "sentences" / f"L{L:03d}.json").read_text()):
            n = 10 - len(o["sentences"])
            if n <= 0:
                continue
            c = by_guid[o["guid"]]
            maxpos = max(maxpos, c["deck_position"])
            total += n
            ncards += 1
            existing = " / ".join(WORD_RE.sub(r"\1", s["ja"]) for s in o["sentences"]) or "—"
            rows.append(f"| {c['deck_position']} | {strip_html(c['Expression'])} | {plain_reading(c)} "
                        f"| {meaning(c)} | **{n}** | {existing} |")
        if rows:
            need = sum(int(r.split("**")[1]) for r in rows)
            sections.append(
                f"### L{L:03d} — {len(rows)} cards, {need} sentences\n\n"
                "| Pos | Word (copy exactly) | Reading | Meaning | Write | Existing sentences (don't repeat these) |\n"
                "|---|---|---|---|---|---|\n" + "\n".join(rows))

    first, last = f"L{lessons[0]:03d}", f"L{lessons[-1]:03d}"
    rng = first if first == last else f"{first}–{last}"
    example = f"L{lessons[0]:03d}–L{min(lessons[0] + 3, lessons[-1]):03d}"
    template = (HERE / "scripts" / "prompt_template.md").read_text()
    md = (template.replace("{RANGE}", rng).replace("{FIRST}", first).replace("{EXAMPLE}", example)
          + "\n## Cards to write\n\n" + "\n\n".join(sections)
          + "\n\n## Words I know, in practice order (number, word, reading)\n\n"
          + "\n".join(f"{c['deck_position']} {strip_html(c['Expression'])}（{plain_reading(c)}）"
                      for c in cards if c["deck_position"] <= maxpos) + "\n")
    out = HERE / "prompts" / f"{first}-{last}_generate.md"
    out.write_text(md)
    print(f"Wrote {out.relative_to(HERE)}: {ncards} cards, {total} sentences, "
          f"word list 1–{maxpos}, {len(md)} characters")


if __name__ == "__main__":
    main()
