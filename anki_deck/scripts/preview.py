#!/usr/bin/env python3
"""
preview.py — render the new back template for a few cards, the way Anki fills
it in (field substitution, {{#Field}} sections, {{furigana:}} filter), into
output/preview.html for checking in a browser. Uses the exact field value
build_import.py writes, so HTML escaping is exercised too.

Usage (from anki_deck/):
  python3 scripts/preview.py --lesson 1 --cards 一人 分かる 十、十
"""

import argparse
import html
import json
import re

from build_import import field_value
from common import HERE, load_cards


def furigana(value: str) -> str:
    return re.sub(r" ?([^ >\[\]]+?)\[([^\]]+)\]", r"<ruby>\1<rt>\2</rt></ruby>", value)


def fill(template: str, fields: dict) -> str:
    def section(m):
        return m.group(2) if fields.get(m.group(1), "").strip() else ""
    out = re.sub(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", section, template, flags=re.S)
    out = re.sub(r"\{\{furigana:(\w+)\}\}", lambda m: furigana(fields.get(m.group(1), "")), out)
    return re.sub(r"\{\{(\w+)\}\}", lambda m: fields.get(m.group(1), ""), out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lesson", type=int, default=1)
    ap.add_argument("--cards", nargs="*", default=["一人", "分かる", "十、十"])
    args = ap.parse_args()

    by_guid = {c["guid"]: c for c in load_cards()}
    recs = {r["expression"]: r for r in
            json.loads((HERE / "sentences" / f"L{args.lesson:03d}.json").read_text())}
    back = (HERE / "anki" / "back_template.html").read_text()
    css = (HERE / "anki" / "style.css").read_text()

    frames = []
    for name in args.cards:
        rec = recs[name]
        card = by_guid[rec["guid"]]
        fields = {k: card[k] for k in ("Expression", "Reading", "Meaning", "Notes")}
        fields["Sentences"] = field_value(rec["sentences"])
        fields["Tags"] = " ".join(card["tags"])
        doc = (f"<!doctype html><html><head><meta charset='utf-8'>"
               f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
               f"<style>body{{margin:0}} .card{{padding:16px;background:#fff;color:#000;}}"
               f".card.nightMode{{background:#2f2f31;color:#eee}}{css}</style></head>"
               f"<body class='card'>{fill(back, fields)}</body></html>")
        frames.append(f"<h3>{html.escape(name)}</h3>"
                      f"<iframe srcdoc=\"{html.escape(doc, quote=True)}\"></iframe>")

    page = ("<!doctype html><html><head><meta charset='utf-8'><title>Back preview</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<style>body{font-family:sans-serif;margin:16px;background:#ddd}"
            "iframe{width:100%;max-width:700px;height:620px;border:1px solid #999;background:#fff}"
            "</style></head><body>" + "".join(frames) + "</body></html>")
    out = HERE / "output" / "preview.html"
    out.write_text(page)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
