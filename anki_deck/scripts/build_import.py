#!/usr/bin/env python3
"""
build_import.py — build the Anki update file and the new back template.

Outputs:
  output/import.txt         tab-separated "Notes in Plain Text" for
                            File → Import with "Existing notes: Update".
                            One row per note that has sentences, matched by GUID.
                            Every existing field is written back byte-for-byte
                            from the export; only the new Sentences field is new.
  anki/back_template.html   current back template + sentence block
  anki/style.css            current styling + sentence block styling

Never creates notes: every row's GUID must exist in deck/cards.json.

Usage (from anki_deck/):
  python3 scripts/build_import.py --lessons 1
  python3 scripts/build_import.py --lessons 1-6
"""

import argparse
import json
import sys

from common import HERE, load_cards

FIELD = "Sentences"
TAGS_ANCHOR = '  <div class="card-tags-container">'


def parse_lessons(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def field_value(sentences: list[dict]) -> str:
    # Each item: [bracketed ja, highlight start, highlight end, English, source]
    # source is "tatoeba:<id>" or "claude" (shown as "generated").
    payload = {"v": 1, "s": [[s["ja"], s["hl"][0], s["hl"][1], s["en"], s["src"]]
                             for s in sentences]}
    return html_escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def check_plain(value: str, what: str) -> str:
    if any(ch in value for ch in "\t\n\r") or value.startswith('"'):
        sys.exit(f"{what} contains a tab/newline/leading quote; refusing to write an ambiguous file")
    return value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lessons", required=True, help="e.g. 1 or 1-6")
    args = ap.parse_args()

    deck = json.loads((HERE / "deck" / "cards.json").read_text())
    fields = deck["fields"]
    if FIELD in fields:
        sys.exit(f"{FIELD} is already in the exported note type; re-check field order before importing")
    by_guid = {c["guid"]: c for c in load_cards()}

    rows = []
    for lesson in parse_lessons(args.lessons):
        path = HERE / "sentences" / f"L{lesson:03d}.json"
        for rec in json.loads(path.read_text()):
            card = by_guid.get(rec["guid"])
            if card is None:
                sys.exit(f"{path.name}: guid {rec['guid']} is not in the deck — would create a note")
            if not rec["sentences"]:
                continue
            # GUID goes last: ~90 GUIDs start with "#", which would make a
            # first-column row look like a comment line to the importer.
            cols = [card[f] for f in fields] + [field_value(rec["sentences"]),
                                                " ".join(card["tags"]), card["guid"]]
            if cols[0].startswith("#"):
                sys.exit(f"{card['Expression']}: first column starts with '#'")
            rows.append("\t".join(check_plain(v, f"{card['Expression']}") for v in cols))

    header = [
        "#separator:tab",
        "#html:true",
        f"#notetype:{deck['note_type']}",
        "#deck:Kanji in Context: Revised Edition",
        f"#tags column:{len(fields) + 2}",
        f"#guid column:{len(fields) + 3}",
        "#columns:" + "\t".join(fields + [FIELD, "tags", "guid"]),
    ]
    out = HERE / "output" / "import.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(header + rows) + "\n")

    back = (HERE / "anki" / "current_back.html").read_text()
    block = (HERE / "anki" / "sentence_block.html").read_text()
    if back.count(TAGS_ANCHOR) != 1:
        sys.exit("Could not find the tags block in the current back template")
    (HERE / "anki" / "back_template.html").write_text(back.replace(TAGS_ANCHOR, block + TAGS_ANCHOR))
    css = (HERE / "anki" / "current_style.css").read_text().rstrip("\n") + "\n"
    (HERE / "anki" / "style.css").write_text(css + (HERE / "anki" / "sentence_block.css").read_text())

    print(f"Wrote {out.relative_to(HERE)}: {len(rows)} notes (update only, matched by GUID)")
    print("Wrote anki/back_template.html and anki/style.css")


if __name__ == "__main__":
    main()
