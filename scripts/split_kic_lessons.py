#!/usr/bin/env python3
"""
split_kic_lessons.py

Split an Anki KiC notes export into one pipe-separated file per lesson.

Lesson tags L### and L###A both map to the same lesson number. Cards with
multiple lesson tags are written to every matching lesson file.

Usage:
  python3 split_kic_lessons.py \
    --input ../data/kic_cards_augmented.txt \
    --output-dir ../kic_lessons
"""

import argparse
import re
from pathlib import Path

LESSON_RE = re.compile(r'\bL\d{3}\b')
AUG_LESSON_RE = re.compile(r'\bL\d{3}A\b')

ANKI_HEADER = (
    "#separator:|\n"
    "#tags column:5\n"
    "#tags column separator:space\n"
)


def tab_row_to_pipe(line: str) -> str:
    return "|".join(line.rstrip("\n").split("\t")) + "\n"


def lesson_numbers_from_tags(tags: str) -> list[int]:
    numbers: set[int] = set()
    for token in tags.split():
        if LESSON_RE.fullmatch(token):
            numbers.add(int(token[1:]))
        elif AUG_LESSON_RE.fullmatch(token):
            numbers.add(int(token[1:4]))
    return sorted(numbers)


def lesson_filename(lesson_num: int) -> str:
    return f"KIC_L{lesson_num:03d}.csv"


def split_lessons(input_path: Path, output_dir: Path) -> None:
    rows: list[str] = []

    with input_path.open(encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#") and line.strip():
                rows.append(line)

    by_lesson: dict[int, list[str]] = {}
    untagged: list[str] = []
    duplicate_entries = 0

    for line in rows:
        parts = line.rstrip("\n").split("\t")
        tags = parts[4] if len(parts) > 4 else ""
        lesson_nums = lesson_numbers_from_tags(tags)
        if not lesson_nums:
            untagged.append(line)
            continue
        if len(lesson_nums) > 1:
            duplicate_entries += len(lesson_nums) - 1
        for lesson_num in lesson_nums:
            by_lesson.setdefault(lesson_num, []).append(line)

    output_dir.mkdir(parents=True, exist_ok=True)

    def write_rows(path: Path, lesson_rows: list[str]) -> None:
        path.write_text(
            ANKI_HEADER + "".join(tab_row_to_pipe(line) for line in lesson_rows),
            encoding="utf-8",
        )

    written_counts: dict[str, int] = {}
    for lesson_num in sorted(by_lesson):
        filename = lesson_filename(lesson_num)
        path = output_dir / filename
        write_rows(path, by_lesson[lesson_num])
        written_counts[filename] = len(by_lesson[lesson_num])

    untagged_path = output_dir / "KIC_UNTAGGED.csv"
    if untagged:
        write_rows(untagged_path, untagged)
        written_counts["KIC_UNTAGGED.csv"] = len(untagged)

    source_count = len(rows)
    lesson_total = sum(written_counts.values())

    print(f"Source:        {source_count} cards from {input_path}")
    print(f"Lesson files:  {lesson_total} rows across {len(written_counts)} files in {output_dir}")
    print(f"Double entries: {duplicate_entries} (extra copies from multi-lesson cards)")
    for filename in sorted(written_counts):
        print(f"  {filename}: {written_counts[filename]}")
    if lesson_total == source_count + duplicate_entries:
        print("OK: lesson file total = source count + double entries.")
    else:
        print(
            f"ERROR: expected {source_count + duplicate_entries} rows, "
            f"wrote {lesson_total}."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Split KiC Anki export by lesson tag.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "kic_cards_augmented.txt",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "kic_lessons",
    )
    args = parser.parse_args()
    split_lessons(args.input, args.output_dir)


if __name__ == "__main__":
    main()
