# Claude Code Instructions — KiC Practice App

## Context

This is a Japanese sentence practice app built around the textbook
*Kanji in Context (Revised Edition)*. The repo structure, data pipeline,
and design decisions are documented in README.md. Read it fully before
starting.

This briefing covers what needs to be **built or updated** by Claude Code.

---

## What already exists

The following scripts exist and are **functionally correct** but need
updates as noted:

### `scripts/augment_anki.py`
Adds missing lesson tags to the raw Anki notes export. No changes needed.
Run it as documented in the README to produce `data/kic_augmented.txt`.

### `scripts/build_sentences.py`
Filters Tatoeba sentences by allowed kanji set and builds JSON files.
**Needs one update:**

Currently outputs a single `sentences.json`. It needs to be updated to:
- Accept `--output-dir` instead of `--output`
- Write one file per lesson: `{output-dir}/L001.json`, `L002.json` etc.
- Accept `--max-per-lesson` (default 50) — keep at most this many
  sentences per lesson, balanced across `length_bucket` values
  (short/medium/long). If a bucket has fewer than `max/3` sentences,
  fill remaining slots from other buckets.

The JSON structure per sentence is unchanged:
```json
{
  "ja": "今日は学校に行く。",
  "en": ["I'm going to school today."],
  "lesson": "L003",
  "required_words": 2,
  "char_length": 10,
  "length_bucket": "short",
  "furigana": {
    "今日": "今[こん]日[にち]今日[きょう]",
    "学校": "学[がっ]校[こう]"
  }
}
```

Each `L###.json` file is just an array of these sentence objects.

---

## What needs to be built

### `app/index.html`

A single self-contained HTML file. No external frameworks except:
- Vanilla JS (no React, no bundler)
- Tailwind CSS via CDN for styling
- Fetch API for JSON loading and Anthropic API calls

#### Layout

Two views:

**1. Settings view** (shown on first load if no API key saved)
- API key input field (saved to `localStorage` as `anthropic_api_key`)
- Base grade selector: Grade 1 / Grade 1+2 (saved to `localStorage`)
- Current lesson range: two selectors, "From" and "To", L001–L156
  (saved to `localStorage`)
- Max required words: number input 1–5 (default 2, saved to `localStorage`)
- Furigana display: "All kanji" / "Current lesson only" (saved to `localStorage`)
- Practice direction: "JP → EN" / "EN → JP" / "Mixed" (saved to `localStorage`)
- Save button → switches to practice view

**2. Practice view**
- Top bar: current lesson range, settings gear icon
- Sentence display area (large, centered)
- Answer input (textarea)
- Submit button
- Score display area (shows after submission)
- Next button (appears after submission)

#### Sentence loading

Sentences are stored in `sentences/grade1/` and `sentences/grade2/`
relative to `index.html`. When the user sets their config, load all
lesson files within the selected range:

```javascript
// Example: grade2, L001 to L010
const files = ['L001', 'L002', ..., 'L010'];
const sentences = await Promise.all(
  files.map(l =>
    fetch(`sentences/grade2/${l}.json`).then(r => r.json())
  )
).then(arrays => arrays.flat());
```

Cache loaded sentences in memory. Reload only when config changes.

#### Sentence selection

Pick a random sentence from the loaded pool, weighted to prefer variety:
- Track which sentences have been shown this session
- Prefer unseen sentences
- Maintain rough balance across `length_bucket` values (short/medium/long)
- For EN→JP direction, show `en[0]` and expect Japanese input
- For JP→EN direction, show `ja` with furigana and expect English input
- For Mixed, alternate randomly

#### Furigana rendering

The `furigana` field in each sentence is a map of `{ word: reading }`.
Reading format is Anki native: `一[いっ]分[ぷん]`.

To render:
1. Start with the plain `ja` string
2. For each entry in `furigana` (longest word first to avoid substring conflicts):
   - Parse the reading string: split on `]` boundaries to get
     `[{kanji, reading}, ...]` pairs
   - Replace the word in the sentence with HTML ruby:
     `<ruby>一<rt>いっ</rt></ruby><ruby>分<rt>ぷん</rt></ruby>`
3. Render the resulting HTML in the sentence display area

When furigana mode is "Current lesson only", only apply furigana for
words whose reading was matched from the current lesson range (you can
track this by noting which lesson each furigana entry came from —
alternatively, simplify by always showing all furigana for now and
noting this as a future improvement).

#### Scoring via Claude API

When the user submits an answer, call the Anthropic API:

```javascript
const response = await fetch('https://api.anthropic.com/v1/messages', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'x-api-key': apiKey,
    'anthropic-version': '2023-06-01',
    'anthropic-dangerous-direct-browser-access': 'true'
  },
  body: JSON.stringify({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 300,
    messages: [{
      role: 'user',
      content: buildScoringPrompt(sentence, userAnswer, direction)
    }]
  })
});
```

The scoring prompt should be:

```
You are scoring a Japanese language practice answer.

Japanese sentence: {ja}
Reference English translation(s): {en.join(' / ')}
Student's answer: {userAnswer}
Direction: {JP→EN or EN→JP}

Score the answer on:
1. Meaning accuracy (did they capture the core meaning?)
2. Completeness (did they miss anything significant?)

Respond with ONLY valid JSON in this exact format:
{
  "score": <1-5>,
  "verdict": "<one of: Excellent / Good / Partial / Incorrect>",
  "note": "<one sentence of specific feedback>"
}

Be generous with paraphrases — a correct meaning expressed differently
should score 4-5. Only score 1-2 if the meaning is wrong or missing.
For EN→JP, accept any grammatically valid Japanese conveying the correct
meaning, not just the reference sentence.
```

Display the score as:
- A coloured badge (5=green, 4=light green, 3=yellow, 2=orange, 1=red)
- The verdict in bold
- The note in smaller text below
- The reference translation(s) revealed below the score

#### Error handling

- If API key is missing or invalid: show a clear message with a link to settings
- If a lesson JSON file fails to load: skip it silently and continue
- If the API call fails: show "Scoring unavailable — check your API key" and
  still reveal the reference translation so the user can self-assess

#### Persistence

Save to `localStorage`:
- `anthropic_api_key`
- `kic_base_grade` (1 or 2)
- `kic_lesson_from` (e.g. "L001")
- `kic_lesson_to` (e.g. "L010")
- `kic_max_required_words`
- `kic_furigana_mode` ("all" or "current")
- `kic_direction` ("jp_en", "en_jp", "mixed")

---

## Repo structure to create

```
kic-practice/
├── README.md                     (already written)
├── .gitignore
├── data/
│   └── .gitkeep                  (jpn_eng_sentences.tsv goes here, not committed)
├── scripts/
│   ├── augment_anki.py           (already written, copy as-is)
│   └── build_sentences.py        (update as described above)
├── sentences/
│   ├── grade1/                   (populated by build_sentences.py)
│   └── grade2/                   (populated by build_sentences.py)
└── index.html                    (build this)
```

### `.gitignore`
```
data/jpn_eng_sentences.tsv
data/kic_original.txt
data/kic_augmented.txt
__pycache__/
*.pyc
.DS_Store
```

Note: `kic_original.txt` and `kic_augmented.txt` are excluded because
they are derived from a third-party Anki deck and should not be
redistributed. The `sentences/` JSON files ARE committed since they are
derived data needed to serve the app.

---

## Design notes

- Keep the UI clean and mobile-friendly — this will primarily be used
  on iPhone/iPad
- Large readable Japanese text (minimum 28px for sentences)
- The practice loop should feel fast — no unnecessary clicks between
  sentences
- Dark mode support is a nice-to-have but not required
- The settings view should be accessible via a gear icon at all times,
  not just on first load

---

## Testing

After building, verify:
1. Settings save and persist across page reload
2. Sentences load correctly for a given lesson range
3. Furigana renders correctly (ruby annotations visible)
4. API scoring returns and displays correctly
5. Next button loads a new sentence without reloading the page
6. Works on mobile viewport (375px wide)
