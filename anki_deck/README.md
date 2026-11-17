# anki_deck — example sentences for my own KIC Anki deck

Adds a `Sentences` field (rotating example sentences with furigana and English)
to my existing KIC Anki deck **without touching review history or card order**.

The deck is the source of truth: card order, GUIDs, note type, templates and
lesson tags are all read from my own `.apkg` export. Nothing is rebuilt from
the published deck or from `../kic_lessons/`.

## Layout

| Path | What | In git? |
|---|---|---|
| `input/KicAnki.apkg` | My Anki export, with scheduling | no (personal history) |
| `deck/cards.json` | Extracted cards in deck order (includes review state) | no (rebuilt by extract_deck.py) |
| `sentences/authored/L###.jsonl` | Claude-written sentences for cards Tatoeba can't cover (cleaned copies of model output) | yes |
| `prompts/` | Generation prompts and the raw model output | yes |
| `sentences/excluded_tatoeba.txt` | Tatoeba ids rejected on review, with reasons | yes |
| `sentences/L###.json` | Final sentences per card (keyed by guid) | no (rebuilt by select_sentences.py) |
| `reports/L###.md`, `reports/L###_rejected.jsonl` | Validation results, spot-check sample, skipped sentences | no (rebuilt by validate.py / select_sentences.py) |
| `anki/current_*.{html,css}` | Note type templates as exported (reference) | yes |
| `anki/sentence_block.{html,css}` | The sentence block (source) | yes |
| `anki/back_template.html`, `anki/style.css` | Generated: paste into Anki | yes |
| `output/import.txt` | Generated update file for Anki | no |
| `output/preview.html` | Generated browser preview of the back | no |

Shared resources from the parent repo: `../data/jpn_*.tsv` (Tatoeba) and the
bracket-furigana parsing in `../scripts/build_sentences.py`.

## Rules the pipeline enforces

- **Deck position rule, by word.** Sentences are never rewritten. Each kanji
  word is *learned* if it is on a card at or before this card's deck position
  (conjugations included; also numbers + a counter already seen, and a seen word
  + a seen ～suffix card). Two seen words don't combine: 山水 is not learned just
  because 山 and 水 are. Learned words are stored as `[漢字|かんじ]` and their
  furigana follows the toggle; everything else is stored as `{漢字|かんじ}` and
  shown dimmed with furigana always on.
- **Common vocabulary.** A Tatoeba sentence may have at most 2 unlearned words,
  each appearing at least 10 times in the trusted Tatoeba transcriptions
  (filters 世知辛い, 深酒, 木立 …). The validator lists unlearned rare words in
  Claude-written sentences as warnings.
- **Variety.** Picks are made one at a time, penalising words already used in
  that card's sentences; at most 2 Tom sentences per card. Claude-written
  sentences don't reuse sentence frames across cards.
- **Right word, right sense.** The target must match the card's surface *and*
  reading (人[ひと] ≠ 人[にん], 十分[じゅうぶん] ≠ 十分[じゅっぷん]), on word
  boundaries: single-kanji words can't be part of a compound (しょうがく金), counters
  can't be part of a bigger number (十五分), verb stems can't be part of a
  compound noun (分け前).
- **Furigana agrees with the sentence.** Every kanji is bracketed with a kana
  reading; the validator also checks each reading against the readings that
  kanji has anywhere in the deck.
- Sentences are ~22 characters max. Tatoeba sentences come only from trusted
  transcription authors; picks are reviewed, and rejects go in `excluded_tatoeba.txt`.

## Workflow for a lesson

```bash
cd anki_deck
python3 scripts/extract_deck.py                 # after a new export only
python3 scripts/select_sentences.py --lesson 1  # authored + Tatoeba → sentences/L001.json
python3 scripts/validate.py --lesson 1          # errors fail; writes reports/L001.md
python3 scripts/build_import.py --lessons 1     # → output/import.txt + anki/back_template.html
python3 scripts/preview.py --lesson 1           # optional: output/preview.html
```

"Lesson" means the Stage 1 block a card sits in, by deck position
(`block_lesson` in `cards.json`, set by `extract_deck.py`). A card tagged with
two lessons (支持: L006 + L020) belongs to the lesson whose cards surround it,
not its highest tag. Reviewed cards are ordered by note id, which matches the
deck order. Stage 2/3 cards are a later step.

`build_import.py` is cumulative: always pass the full range done so far
(`--lessons 1-12`), and re-importing earlier lessons is harmless.

## Status

- **Done and imported range:** L001–L008 (446 cards, every card has 8–10 sentences).
- **Next:** L009 onwards. `prompts/L009-L020_generate.md` is ready (built by
  `build_prompt.py` after re-running `select_sentences.py` for those lessons).

## Generating sentences for new lessons

Tatoeba covers only part of each lesson; the rest is written by Claude.

1. **Refresh Tatoeba picks** so the counts are current:
   `for L in 9 10 11; do python3 scripts/select_sentences.py --lesson $L; done`
2. **Build the prompt:** `python3 scripts/build_prompt.py --lessons 9-15`
   → `prompts/L009-L015_generate.md`. It lists, per card, how many sentences
   are needed (10 minus what it has) and the existing ones, plus the word list
   in practice order. The rules are in `scripts/prompt_template.md`.
3. **Generate**, one lesson at a time, following that prompt (a chat with
   Sonnet, or a Claude Code agent). Save each lesson's JSON Lines as
   `prompts/L0XX.jsonl` (raw output, kept as-is) and copy it to
   `sentences/authored/L0XX.jsonl` (the copy the pipeline uses and that may be
   cleaned). The `card` value must equal the card's Expression exactly.
4. **Merge and validate:** `select_sentences.py` then `validate.py` per lesson.
   - Sentences whose target can't be found are skipped and listed in
     `reports/L0XX_rejected.jsonl`. Check them: so far most were matcher bugs
     (fix the matcher), a few were real (語り合う ≠ 語る).
   - Errors (E1–E4) must be zero. Warnings: W4 (3 unlearned words) and W5
     (rare words) are reviewed by eye; everyday words are fine.
5. **Read every generated sentence** (naturalness, sense, English). Things
   seen so far: wrong collocations (右に回る, 月が上る → 昇る), odd or
   factually strange sentences, rare/literary words, and a comma habit after
   は/を/に/が. Drop bad sentences from `sentences/authored/` rather than
   replacing them; a card with 8–9 sentences is fine.
6. **Check coverage:** every card up to the last position in the range must
   have sentences (cards with none don't show the block at all).
7. Rebuild the import with the full range and import as below.

## Safety rules

- Never create notes, change the sort field, or change the note type in any way
  other than adding the `Sentences` field.
- The import updates notes matched by GUID. It writes every existing field back
  unchanged (copied byte-for-byte from the export) plus `Sentences`.
  Updating note contents does not touch scheduling, review history or new-card
  positions.
- Never import `input/KicAnki.apkg` back into Anki.
- Adding a field is a schema change and forces a one-way full sync.

## Exporting the deck

1. On the iPad, sync. Then on the Mac, open Anki and sync, so the Mac has the latest history.
2. **File → Export…**
3. Export format: **Anki Deck Package (.apkg)**
4. Include: the KIC deck
5. Tick **Include scheduling information**
6. Untick **Include media** (not needed)
7. Tick **Support older Anki versions** (this lets the scripts read the file with plain Python)
8. Save as `anki_deck/input/KicAnki.apkg`

## Importing into Anki (Mac)

Do not review on the Mac or iPad between the export and these steps. If you do,
the field values are still correct, but export again to be safe.

1. **Sync the iPad, then sync the Mac.**
2. **Back up:** on the Mac, **File → Create Backup**.
3. **Add the field (one time only):** **Tools → Manage Note Types → Japanese KIC →
   Fields → Add**, name it `Sentences`. It must end up 5th, after `Notes`.
   Anki warns that this needs a full sync. Accept.
4. **Templates:** **Cards…** for Japanese KIC.
   - Front: leave unchanged.
   - Back: replace everything with `anki/back_template.html`.
   - Styling: replace everything with `anki/style.css`.
   - Save.
5. **Import:** **File → Import**, choose `output/import.txt`, then check:
   - Note type: **Japanese KIC**
   - Existing notes: **Update**
   - Match scope: **Note type**
   - Field mapping: columns 1–5 → Expression, Reading, Meaning, Notes,
     Sentences; Tags → column 6; GUID → column 7
   - Then Import. Expect *N updated*, **0 new**. If it says it would add new
     notes, cancel.
6. **Check:** open an L001 card in the browser and preview the back. Review one
   card to see the sentence block.
7. **Sync the Mac.** When asked, choose **Upload to AnkiWeb**.
8. **Sync the iPad.** When asked, choose **Download from AnkiWeb**.

Later lessons only need steps 1, 5, 6 and a normal sync. The field and the
template are already in place.
