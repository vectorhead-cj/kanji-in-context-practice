# KiC Practice App — Handoff Notes

## Context

This app is a Japanese sentence practice tool built around Kanji in Context
(Revised Edition). It's a GitHub Pages app (vanilla HTML) that:
- Fetches enriched plain sentence JSON from the same GitHub repo
- Calls the Claude API for contextual furigana generation, target validation,
  and answer scoring
- Stores the user's API key in localStorage (never in the repo)
- Stores local pending curation in localStorage for export/import

The sentence data pipeline now emits stable entry IDs and kanji ID metadata.
The app uses plain sentence files, not KiC furigana files, because both
sentence matching and vocabulary pairing are heuristic and should be validated
in sentence context.

---

## 1. Furigana rendering

### How it works
Use standard HTML `<ruby><rt>` tags. The key insight is that `line-height`
on the parent element is what makes or breaks furigana rendering — it needs
to be large enough to accommodate the `rt` text above each character.

### Claude furigana format
Ask Claude to return furigana in **bracket format**:
```
今日[きょう]は学校[がっこう]に行[い]く。
```
Only kanji get annotated, not kana. Then convert to ruby HTML in the app.
Render through DOM nodes or escaped text; do not inject raw model output.

### Conversion function sketch
```javascript
function buildRubyHTML(annotated, curWords, prevWords) {
  if (!annotated) return "";
  let html = annotated;

  // Highlight current lesson words (blue + bold)
  for (const w of (curWords || [])) {
    const pat = w.word
      .split("")
      .map(c => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?:\\[[^\\]]*\\])?")
      .join("");
    html = html.replace(new RegExp(pat, "g"), m => `<mark class="kic-cur">${m}</mark>`);
  }

  // Highlight previous lesson words (bold only)
  for (const w of (prevWords || [])) {
    const pat = w.word
      .split("")
      .map(c => c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?:\\[[^\\]]*\\])?")
      .join("");
    html = html.replace(
      new RegExp(pat, "g"),
      m => m.includes("kic-cur") ? m : `<mark class="kic-prev">${m}</mark>`
    );
  }

  // Convert bracket furigana → ruby HTML
  html = html.replace(
    /([^\[<\s]+?)\[([^\]]+)\]/g,
    (_, k, r) => `<ruby>${k}<rt>${r}</rt></ruby>`
  );

  return html;
}
```

### CSS for furigana display
```css
.jp-sentence {
  font-family: 'Noto Serif JP', 'Hiragino Mincho ProN', serif;
  font-size: 26px;
  line-height: 2.8;        /* Critical — must be large enough for rt above kanji */
}

.jp-sentence ruby { display: inline ruby; }

.jp-sentence rt {
  font-size: 0.45em;
  color: #666;
  text-align: center;
  transition: opacity 0.2s;
}

/* Toggle furigana visibility */
.furi-off rt { opacity: 0; }
.furi-on rt  { opacity: 1; }

/* KiC word highlighting */
mark { background: none; }
mark.kic-cur  { color: #2a7fd4; font-weight: 700; }  /* Current lesson — blue */
mark.kic-prev { font-weight: 700; }                   /* Previous lessons — bold only */
```

### Furigana toggle
Hide/show by toggling a class on the parent — do NOT use `display:none` on
`rt` as that collapses line height and makes the text jump.

```javascript
// React
const [showFuri, setShowFuri] = useState(false);
<div className={showFuri ? "jp-sentence furi-on" : "jp-sentence furi-off"}
     dangerouslySetInnerHTML={{ __html: buildRubyHTML(annotated, curWords, prevWords) }} />
```

### Claude API prompt for furigana and target analysis
```javascript
const FURIGANA_SYSTEM = `Given a Japanese sentence and candidate KiC target
words, return only valid JSON. Include bracket-format furigana
kanji[reading], target readings, and whether each target is a reliable quiz
item in this sentence context. Flag bad pairings, substring matches, and
contextual reading conflicts.`;
```

---

## 2. Claude API integration

### Model
Always use: `claude-sonnet-4-20250514`

### API key
- User supplies their own Anthropic API key in the settings UI
- Store in `localStorage` only — never in the repo
- Add an API key field to the settings panel
- Validate on first use and show a clear error if invalid

### Basic call wrapper
```javascript
async function callClaude(systemPrompt, userContent, apiKey) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-20250514",
      max_tokens: 1000,
      system: systemPrompt,
      messages: [{ role: "user", content: userContent }],
    }),
  });
  const data = await r.json();
  return data.content?.find(b => b.type === "text")?.text || "";
}
```

**Important:** The `anthropic-dangerous-direct-browser-access: true` header
is required for direct browser API calls. Without it the API will reject
the request. This was likely the reason the API key didn't work previously.

### Scoring prompt
```javascript
const SCORING_SYSTEM = `You are a Japanese teacher. Return only valid JSON with no markdown fences.`;

const scoringPrompt = `Sentence: ${sentence}
Vocabulary readings submitted (forfeited=${forfeited}):
${allWords.map(w => `  ${w.word}: "${userReadings[w.word] || ""}"`).join("\n")}
Student translation: "${englishAnswer}"

Return this exact JSON shape:
{
  "vocab": { "WORD": { "correct": true, "correctReading": "hiragana" } },
  "translation": { "score": 0, "note": "feedback" }
}
Score vocab by exact hiragana match (づ ≠ ず). Score translation 0-5.`;
```

---

## 3. Sentence JSON structure

Files are at:
```
sentences/{grade}/{bucket}/plain/{lesson}.json
```

Where:
- `grade`: `grade1` or `grade2`  
- `bucket`: `short`, `medium`, `long`
- `lesson`: `L001` – `L156`

Each entry:
```json
{
  "id": "grade2:medium:L020:e967fd624e",
  "ja": "地下鉄は、市街電車よりはやい。",
  "en": ["The subway is faster than the streetcar."],
  "lesson": "L020",
  "required_words": 1,
  "char_length": 15,
  "length_bucket": "medium",
  "kic_words_current_lesson": [
    { "word": "街", "lesson": "L020", "kanji_ids": ["0371"] }
  ],
  "kic_words_previous_lessons": [
    { "word": "地下鉄", "lesson": "L002", "kanji_ids": ["0032", "0146", "0147"] },
    { "word": "電車", "lesson": "L003", "kanji_ids": ["0061", "0062"] }
  ],
  "target_kanji_ids": ["0371"],
  "sentence_kanji_ids": ["0032", "0061", "0062", "0146", "0147", "0371"]
}
```

`id` is the stable join key for app caches and `sentences/augmentation.json`.
It includes grade, bucket, lesson, and a content hash based on the sentence and
candidate vocabulary metadata.

---

## 3.5 Augmentation and bad-pairing curation

Bundled curation lives at:
```
sentences/augmentation.json
```

The app lookup order is:
1. Bundled augmentation entry by sentence `id`
2. Local pending curation by sentence `id`
3. Local AI analysis cache by sentence `id`
4. Claude fallback

Every round has a "Mark Bad Pairing" button regardless of Claude's opinion.
Claude may warn that a pairing is suspicious, but the user decision is what
creates pending curation data. Settings exposes export/import controls so
pending local curation can be periodically reviewed and promoted into the repo.

---

## 4. Scoring rules

- Vocab: **2 points per word**, exact hiragana match required
- Translation: **0–5 points**, Claude grades on accuracy, grammar, nuance
- If user clicks "show furigana" before submitting: vocab scores are
  **forfeited** (all zero), translation scoring unaffected
- Max points per round: `(number of kic words × 2) + 5`

---

## 5. Practice mode: JP → EN only (current implementation)

The app currently implements JP→EN mode only. Each round shows a Japanese
sentence and has two independent input areas:

### Input area 1 — Vocabulary reading (kana)
- One input box per KiC word appearing in the sentence
- User types the **hiragana reading** of each word
- Graded **independently** from the translation
- **2 points per word**, exact hiragana match required (づ ≠ ず)
- Current lesson words and previous lesson words shown in separate labeled groups
- Claude may suppress bad targets from the quiz by returning `quiz: false`
- If user clicks "show furigana": all vocab input boxes are **disabled**
  and vocab scores are **forfeited** (all zero). Translation scoring is unaffected.

### Input area 2 — English translation
- One textarea for the full English translation of the sentence
- Graded **independently** from vocab readings
- **0–5 points**, Claude grades on accuracy, grammar, nuance
- Never forfeited — user can always attempt the translation regardless of
  whether they showed furigana

### Submission
- Both areas submitted together with a single Submit button
- Claude scores both in one API call and returns results for each
- Per-word ✓/✗ shown with correct reading for wrong answers
- Translation score shown as 5 dots with brief feedback note
- Reference English translation(s) revealed after submission
- The full sentence furigana is revealed after submission

### Max points per round
`(number of KiC words in sentence × 2) + 5`

Example: sentence has 2 KiC words → max 9 points per round.
