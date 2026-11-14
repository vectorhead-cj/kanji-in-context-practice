#!/usr/bin/env python3
"""
extract_deck.py — read-only extraction of my KIC Anki deck from an .apkg export.

Writes:
  deck/cards.json          one record per note, in deck order
  anki/current_front.html  the note type's current front template
  anki/current_back.html   the note type's current back template
  anki/current_style.css   the note type's current styling

Deck order: new cards are ordered by their new-queue position (cards.due).
Already-reviewed cards come first; their original queue position is no longer
stored by Anki, so they are ordered by (stage, lesson, note id).

No scheduling data is written out beyond a coarse state ("new"/"review"/...).

Usage (from anki_deck/):
  python3 scripts/extract_deck.py --apkg input/KicAnki.apkg
"""

import argparse
import json
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
LESSON_RE = re.compile(r"^L(\d{3})$")
STAGE_RE = re.compile(r"^Stage(\d)$")
KANJI_ID_RE = re.compile(r"^(\d{4})$")
STATE = {0: "new", 1: "learning", 2: "review", 3: "relearning"}
EXPECTED_FIELDS = ["Expression", "Reading", "Meaning", "Notes"]


def open_collection(apkg: Path, tmp: Path) -> sqlite3.Connection:
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
        if "collection.anki21" in names:
            name = "collection.anki21"
        elif "collection.anki21b" in names:
            raise SystemExit(
                "Export uses the new compressed format. Re-export with "
                "'Support older Anki versions' ticked."
            )
        else:
            name = "collection.anki2"
        z.extract(name, tmp)
    return sqlite3.connect(f"file:{tmp / name}?mode=ro", uri=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apkg", type=Path, default=HERE / "input" / "KicAnki.apkg")
    ap.add_argument("--out", type=Path, default=HERE / "deck" / "cards.json")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmpdir:
        con = open_collection(args.apkg, Path(tmpdir))
        models = json.loads(con.execute("select models from col").fetchone()[0])
        if len(models) != 1:
            raise SystemExit(f"Expected one note type, found {len(models)}")
        model = next(iter(models.values()))
        fields = [f["name"] for f in model["flds"]]
        if fields[: len(EXPECTED_FIELDS)] != EXPECTED_FIELDS:
            raise SystemExit(f"Unexpected fields: {fields}")

        rows = con.execute(
            "select n.id, n.guid, n.flds, n.tags, c.ord, c.type, c.due "
            "from cards c join notes n on n.id = c.nid where n.mid = ?",
            (model["id"],),
        ).fetchall()
        con.close()

    cards = []
    for nid, guid, flds, tags, ord_, ctype, due in rows:
        values = dict(zip(fields, flds.split("\x1f")))
        tag_list = tags.split()
        lessons = sorted({int(m.group(1)) for t in tag_list if (m := LESSON_RE.match(t))})
        stages = [int(m.group(1)) for t in tag_list if (m := STAGE_RE.match(t))]
        cards.append({
            "guid": guid,
            "note_id": nid,
            "card_ord": ord_,
            "state": STATE.get(ctype, str(ctype)),
            "new_position": due if ctype == 0 else None,
            "stage": stages[0] if stages else None,
            "lesson_tags": lessons,
            "kanji_ids": [t for t in tag_list if KANJI_ID_RE.match(t)],
            "tags": tag_list,
            **{k: values.get(k, "") for k in fields},
        })

    def order_key(c):
        if c["new_position"] is None:
            return (0, c["stage"] or 0, max(c["lesson_tags"] or [0]), c["note_id"])
        return (1, c["new_position"], 0, 0)

    cards.sort(key=order_key)
    for i, c in enumerate(cards, 1):
        c["deck_position"] = i

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(
        {"note_type": model["name"], "fields": fields, "sort_field": model["sortf"], "cards": cards},
        ensure_ascii=False, indent=1,
    ) + "\n")

    tmpl = model["tmpls"][0]
    (HERE / "anki" / "current_front.html").write_text(tmpl["qfmt"] + "\n")
    (HERE / "anki" / "current_back.html").write_text(tmpl["afmt"] + "\n")
    (HERE / "anki" / "current_style.css").write_text(model["css"] + "\n")

    print(f"Note type: {model['name']}  fields: {fields}  sort field: {fields[model['sortf']]}")
    print(f"Wrote {len(cards)} cards to {args.out.relative_to(HERE)}")


if __name__ == "__main__":
    main()
