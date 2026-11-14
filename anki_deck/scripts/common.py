"""
Shared helpers for the anki_deck pipeline.

Core ideas
----------
* A sentence is stored in Tatoeba bracket form: `[一人|ひとり]で[行|い]く。`
  Per-character readings are allowed: `[学校|がっ|こう]`.
* A card's target word is parsed from its Anki `Reading` field
  (`一[いっ] 分[ぷん]`, `分[わ]かる`, `（お） 金[かね]`) into one or more
  *forms*, each a list of units (surface, reading) plus trailing okurigana.
* A target matches a sentence only if surface AND reading agree, on unit
  boundaries that do not cut a whole-reading bracket in half. This is what
  keeps 人[ひと] from matching 人[にん], 十分[じゅうぶん] from 十分[じゅっぷん].
* Words are never rewritten. Each kanji word in a sentence is classified:
  - learned: the target, or a word that is on a card at or before this
    card's deck position (incl. conjugations, numbers + seen counters, seen
    words + seen ～suffix cards). Stored as [base|reading]; its furigana
    follows the card's furigana toggle.
  - unlearned: anything else. Stored as {base|reading}; its furigana is
    always shown.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # anki_deck/
REPO = HERE.parent                                      # repo root
sys.path.insert(0, str(REPO / "scripts"))

from build_sentences import (  # noqa: E402  (reuse parent-repo parsing)
    load_authors, load_tatoeba, load_transcriptions, parse_transcription,
)

KANJI_RE = re.compile(r"[㐀-鿿豈-﫿々]")
BRACKET_RE = re.compile(r"\[([^\[\]|]+)\|([^\[\]]+)\]")
ANKI_UNIT_RE = re.compile(r"([^\s\[\]]+?)\[([^\]]+)\]")
NUMERALS = set("一二三四五六七八九十百千万")
# Kana that may follow the stem of a verb whose okurigana is a single kana,
# so 足す doesn't match 足りない and 回る doesn't match 回した.
# る also covers ichidan verbs (見ます, 見て, 見ない).
GODAN_NEXT = {
    "う": "わいうえおっ", "く": "かきくけこい", "ぐ": "がぎぐげごい",
    "す": "さしすせそ", "つ": "たちつてとっ", "ぬ": "なにぬねのん",
    "ぶ": "ばびぶべぼん", "む": "まみむめもん",
    "る": "らりるれろっ" + "まてたなよさずぬ",   # ichidan: ます, て, た, ない, よう, させる, ず, ぬ
}
BIG_NUMERALS = set("百千万")
# Real multi-kanji words (deck expressions + Tatoeba bracket words). Filled by
# select_sentences.load_pool(); used to decide whether a single-kanji target
# next to another kanji is part of a compound (十分, 金曜日) or not (倍食べる).
COMPOUNDS: set[str] = set()
NUMERAL_READINGS = {
    "一": "いち いっ ひと", "二": "に ふた", "三": "さん みっ み", "四": "よん よ し よっ",
    "五": "ご いつ", "六": "ろく ろっ むっ む", "七": "なな しち なの", "八": "はち はっ やっ よう",
    "九": "きゅう く ここの", "十": "じゅう じゅっ じっ とお", "百": "ひゃく びゃく ぴゃく",
    "千": "せん ぜん", "万": "まん", "何": "なん",
}


def is_kanji(ch: str) -> bool:
    return bool(KANJI_RE.fullmatch(ch))


def kanji_in(text: str) -> set[str]:
    return {ch for ch in text if is_kanji(ch)}


def strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ")


# ── Deck ─────────────────────────────────────────────────────────────────────

def load_cards() -> list[dict]:
    return json.loads((HERE / "deck" / "cards.json").read_text())["cards"]


def block_lesson(card: dict) -> int | None:
    """Lesson block a card sits in (Stage 1 cards sit at their latest lesson tag)."""
    return max(card["lesson_tags"]) if card["lesson_tags"] else None


def known_kanji_by_position(cards: list[dict]) -> dict[int, frozenset]:
    """deck_position -> kanji seen on any card at or before that position."""
    seen: set[str] = set()
    out = {}
    for c in sorted(cards, key=lambda c: c["deck_position"]):
        seen |= kanji_in(strip_html(c["Expression"]))
        out[c["deck_position"]] = frozenset(seen)
    return out


# ── Target forms ─────────────────────────────────────────────────────────────

@dataclass
class Form:
    units: list[tuple[str, str]]     # [(surface, reading)], kanji part incl. kana prefix
    okurigana: str                   # trailing kana required after the units
    display: str                     # dictionary surface, for reports
    verb: bool = False               # conjugating verb: reject noun compounds (分け前)
    okuri_full: str = ""             # dictionary okurigana (す in 足す)
    suffix: bool = False             # ～回, ～足: a counter that follows a number

    @property
    def surface(self) -> str:
        return "".join(s for s, _ in self.units)

    @property
    def reading(self) -> str:
        return "".join(r for _, r in self.units)


def _is_verb(okuri: str, meaning: str) -> bool:
    """
    A conjugating verb: needs okurigana, and either an English "to …" meaning
    or a multi-kana verb ending (明ける, 分かる). Meaning alone is not enough:
    多少 "to some extent" has no okurigana and is not a verb.
    """
    if not okuri:
        return False
    if meaning.lower().lstrip().startswith("to "):
        return True
    return len(okuri) >= 2 and okuri[-1] in "うくぐすつぬぶむる"


def _conjugable_tail(okuri: str, meaning: str) -> str:
    """How much of the okurigana must literally follow the stem."""
    if not okuri:
        return ""
    if okuri in ("な", "の", "に"):
        return ""                                  # 十分な → 十分に / 十分だ
    if _is_verb(okuri, meaning):
        return okuri[:-1]                          # 分かる → 分か(って), 明ける → 明け(たら)
    if okuri[-1] in "なの":
        return okuri[:-1]                          # 明らかな → 明らか(に/だ)
    if okuri.endswith("い"):
        return okuri[:-1]                          # 円い → 円(く)
    return okuri


def parse_forms(card: dict) -> list[Form]:
    """All readings/forms of a card's target word (alternates split on 、)."""
    meaning = strip_html(card["Meaning"])
    forms = []
    is_suffix = strip_html(card["Expression"]).lstrip().startswith(("～", "〜"))
    for alt in re.split(r"[、,]", strip_html(card["Reading"])):
        alt = alt.strip().lstrip("～〜")
        if not alt:
            continue
        alt = re.sub(r"（([^）]*)）", r"\1", alt)            # （お） 金 → お 金
        units: list[tuple[str, str]] = []
        pos = 0
        compact = re.sub(r"\s+", " ", alt)
        okuri = ""
        for m in ANKI_UNIT_RE.finditer(compact):
            prefix = compact[pos:m.start()].replace(" ", "")
            for ch in prefix:                              # kana before / between kanji units
                units.append((ch, ch))
            units.append((m.group(1), m.group(2)))
            pos = m.end()
        okuri = compact[pos:].replace(" ", "")
        if not any(kanji_in(s) for s, _ in units):
            continue
        # Drop leading pure-kana units that are katakana words (アメリカ 人): keep as literal.
        tail = _conjugable_tail(okuri, meaning)
        forms.append(Form(units=units, okurigana=tail,
                          display="".join(s for s, _ in units) + okuri,
                          verb=_is_verb(okuri, meaning),
                          okuri_full=okuri, suffix=is_suffix))
    return forms


def target_kanji(card: dict) -> set[str]:
    return kanji_in(strip_html(card["Expression"]))


# ── Sentence tokens ──────────────────────────────────────────────────────────

@dataclass
class Tok:
    surface: str
    reading: str
    group: int          # bracket index (-1 for plain text)
    whole: bool         # True if this token is a multi-char whole-reading bracket
    start: int = 0      # surface offset
    end: int = 0


def normalise_brackets(bracketed: str, card: dict | None = None) -> str:
    """
    Fix bracket layouts that generated sentences use but the matcher doesn't:
      [お金|おかね]  -> お[金|かね]        kana prefix/suffix moved out of the bracket
      [二足|にそく]  -> [二|に][足|そく]    number split from a ～counter target
    """
    counter = None
    if card is not None and strip_html(card["Expression"]).lstrip().startswith(("～", "〜")):
        counter = {f.surface for f in parse_forms(card)}

    def fix(m):
        base, reading = m.group(1), m.group(2)
        if "|" in reading:
            return m.group(0)
        pre = post = ""
        while base and not is_kanji(base[0]) and reading.startswith(base[0]):
            pre += base[0]; base = base[1:]; reading = reading[1:]
        while base and not is_kanji(base[-1]) and reading.endswith(base[-1]):
            post = base[-1] + post; base = base[:-1]; reading = reading[:-1]
        if not base or not reading:
            return m.group(0)
        if counter:
            for c in counter:
                num = base[:-len(c)]
                if base.endswith(c) and num and all(ch in NUMERAL_READINGS for ch in num):
                    def splits(i, j):
                        if i == len(num):
                            yield j
                            return
                        for r in NUMERAL_READINGS[num[i]].split():
                            if reading.startswith(r, j):
                                yield from splits(i + 1, j + len(r))
                    for j in splits(0, 0):
                        if j < len(reading):
                            return f"{pre}[{num}|{reading[:j]}][{c}|{reading[j:]}]{post}"
        return f"{pre}[{base}|{reading}]{post}"

    return BRACKET_RE.sub(fix, bracketed)


def tokenize(bracketed: str) -> list[Tok] | None:
    """Split a bracket sentence into tokens; None if a kanji lacks a reading."""
    toks: list[Tok] = []
    g = 0
    for seg in parse_transcription(bracketed):
        if seg["kind"] == "plain":
            if kanji_in(seg["text"]):
                return None
            toks.extend(Tok(ch, ch, -1, False) for ch in seg["text"])
        else:
            base, krs = seg["base"], seg["kanji_readings"]
            if len(krs) == len(base):
                toks.extend(Tok(ch, r, g, False) for ch, r in zip(base, krs))
            else:
                toks.append(Tok(base, seg["whole_reading"], g, True))
            g += 1
    pos = 0
    for t in toks:
        t.start, t.end = pos, pos + len(t.surface)
        pos = t.end
    return toks


def surface_of(toks: list[Tok]) -> str:
    return "".join(t.surface for t in toks)


def find_target(toks: list[Tok], forms: list[Form]) -> tuple[int, int, Form] | None:
    """Return (start, end, form) surface span of the first target match."""
    for form in forms:
        if any(u == "～" for u, _ in form.units):
            hit = _find_gap_target(toks, form)
            if hit:
                return hit
            continue
        tsurf, tread = form.surface, form.reading
        for i in range(len(toks)):
            surf = read = ""
            j = i
            while j < len(toks) and len(surf) < len(tsurf):
                surf += toks[j].surface
                read += toks[j].reading
                j += 1
            if surf != tsurf or read != tread:
                continue
            # A per-char bracket may continue past the match; that is fine only
            # if the remaining chars are kana-free continuation of the same word
            # (e.g. 一日中). Require that we did not cut a whole-reading token.
            end = toks[j - 1].end
            rest = surface_of(toks[j:])
            if not rest.startswith(form.okurigana):
                continue
            # Reject if the next token is a kanji inside the same per-char
            # bracket — the target would be part of a longer word (三日月 ≠ 三日).
            if j < len(toks) and toks[j].group != -1 and toks[j].group == toks[j - 1].group:
                continue
            if i > 0 and toks[i - 1].group != -1 and toks[i - 1].group == toks[i].group:
                continue
            if form.verb:
                if not form.okurigana and form.okuri_full in GODAN_NEXT:
                    nxt = toks[j].surface if j < len(toks) else ""
                    if nxt not in GODAN_NEXT[form.okuri_full]:
                        continue
                k = j + len(form.okurigana)
                if k < len(toks) and kanji_in(toks[k].surface):
                    continue                   # stem + kanji (分け前): compound noun, not the verb
                if (k + 1 < len(toks) and toks[k].surface in "いきぎしじちにびみり"
                        and kanji_in(toks[k + 1].surface)):
                    continue                   # masu-stem + kanji (足し算, 回り道); not 明ける前
            if form.suffix:
                # ～回 / ～足 / ～家 / ～の下で: attached to the word before it — a
                # number (三回, 何回), a name (鈴木家, ブラウン家) or a noun (先生の下で).
                prev = toks[i - 1].surface if i > 0 else ""
                if not (prev and (kanji_in(prev[-1]) or re.match(r"[ァ-ヶー]", prev[-1]))):
                    continue
                return toks[i].start, end + len(form.okurigana), form
            prev_k = i > 0 and kanji_in(toks[i - 1].surface)
            next_k = (not form.okurigana and j < len(toks) and kanji_in(toks[j].surface))
            kanji_units = [s for s, _ in form.units if kanji_in(s)]
            # Standalone single-kanji word (人, 金, 十): no kanji neighbours at all,
            # otherwise it is part of a compound (しょうがく金, 十分, 人々).
            if len(tsurf) == 1 and (prev_k or next_k):
                big_ok = (tsurf in BIG_NUMERALS and prev_k and toks[i - 1].surface in NUMERALS
                          and not next_k)       # 二千, 三百 are fine
                if not big_ok:
                    if not COMPOUNDS:
                        continue               # no word list loaded: be strict
                    a = i
                    while a > 0 and kanji_in(toks[a - 1].surface):
                        a -= 1
                    b = j
                    while not form.okurigana and b < len(toks) and kanji_in(toks[b].surface):
                        b += 1
                    run = surface_of(toks[a:b])
                    t0 = toks[i].start - toks[a].start
                    if any(run[x:y] in COMPOUNDS
                           for x in range(0, t0 + 1) for y in range(t0 + 1, len(run) + 1)
                           if y - x >= 2):
                        continue               # part of a real word (十分, 金曜日, 奨学金)
            # Counter words starting with a numeral (五分, 一時): not inside a
            # longer number (十五分, 十一時), except before 百/千/万.
            if kanji_units and kanji_units[0][0] in NUMERALS and prev_k \
                    and toks[i - 1].surface in NUMERALS and tsurf[0] not in BIG_NUMERALS:
                continue
            return toks[i].start, end + len(form.okurigana), form
    return None


def _match_units(toks: list[Tok], i: int, units: list[tuple[str, str]]) -> int | None:
    """Match units starting at token i on token boundaries; return the end index."""
    surf = "".join(u for u, _ in units)
    read = "".join(r for _, r in units)
    s_ = r_ = ""
    j = i
    while j < len(toks) and len(s_) < len(surf):
        s_ += toks[j].surface
        r_ += toks[j].reading
        j += 1
    return j if (s_ == surf and r_ == read) else None


def _find_gap_target(toks: list[Tok], form: Form):
    """Cards with a number gap inside (来る～日): left part, numerals, right part."""
    g = [u for u, _ in form.units].index("～")
    left, right = form.units[:g], form.units[g + 1:]
    for i in range(len(toks)):
        j = _match_units(toks, i, left)
        if j is None:
            continue
        k = j
        while k < len(toks) and toks[k].surface and all(
                ch in NUMERALS or ch == "何" for ch in toks[k].surface):
            k += 1
        if k == j:
            continue
        end = _match_units(toks, k, right)
        if end is None:
            continue
        return toks[i].start, toks[end - 1].end + len(form.okurigana), form
    return None


# ── Learned vocabulary ───────────────────────────────────────────────────────

DAKU = str.maketrans("かきくけこさしすせそたちつてとはひふへほ",
                     "がぎぐげござじずぜぞだぢづでどばびぶべぼ")
HANDAKU = str.maketrans("はひふへほ", "ぱぴぷぺぽ")


def reading_variants(r: str) -> set[str]:
    """Sound changes a word can take inside a longer word (年→どし, 一→いっ)."""
    out = {r}
    if r:
        out |= {r[0].translate(DAKU) + r[1:], r[0].translate(HANDAKU) + r[1:]}
    if len(r) >= 2 and r[-1] in "つちくき":
        out |= {v[:-1] + "っ" for v in list(out)}
    return out


class Vocab:
    """What the learner has seen, by deck position."""

    def __init__(self, cards: list[dict]):
        self.piece: dict[tuple[str, str], int] = {}    # whole word, kanji only
        self.suffix: dict[tuple[str, str], int] = {}   # ～部, ～回 cards
        self.counter: dict[tuple[str, str], int] = {}  # unit right after a numeral (三人 → 人/にん)
        self.spans: dict[str, list] = {}               # words with kana inside (世の中)

        def put(d, k, pos):
            if pos < d.get(k, 10 ** 9):
                d[k] = pos

        for c in sorted(cards, key=lambda c: c["deck_position"]):
            pos = c["deck_position"]
            is_suffix = strip_html(c["Expression"]).lstrip().startswith(("～", "〜"))
            for f in parse_forms(c):
                kunits = [(s_, r) for s_, r in f.units if kanji_in(s_)]
                for a, b in zip(kunits, kunits[1:]):
                    if all(ch in NUMERALS for ch in a[0]) and not all(ch in NUMERALS for ch in b[0]):
                        put(self.counter, b, pos)
                for u in kunits:
                    if all(ch in NUMERALS for ch in u[0]):
                        put(self.piece, u, pos)            # numerals on their own
                inner = f.units[[i for i, (s_, _) in enumerate(f.units) if kanji_in(s_)][0]:]
                if all(kanji_in(s_) for s_, _ in inner):
                    key = ("".join(s_ for s_, _ in inner), "".join(r for _, r in inner))
                    put(self.suffix if is_suffix else self.piece, key, pos)
                    if key == ("来", "く"):                # irregular 来る: 来ます, 来ない
                        put(self.piece, ("来", "き"), pos)
                        put(self.piece, ("来", "こ"), pos)
                else:
                    self.spans.setdefault(inner[0][0][0], []).append(
                        ("".join(s_ for s_, _ in inner), "".join(r for _, r in inner), pos))

    def word_known(self, surface: str, reading: str, p: int) -> bool:
        """
        Is surface/reading a word seen at or before position p? Allowed shapes:
          seen word                          勉強, 来(き)ます, 今年
          numerals + seen counter            十月, 三人 (counter seen after a numeral)
          either of the above + ～suffix     中心部 (if ～部 seen)
        Two seen words do NOT combine (山水, 深酒 stay unlearned).
        Pieces may take a sound change (年→どし, 一→いっ).
        """
        from functools import lru_cache

        def seen(d, k):
            return d.get(k, 10 ** 9) <= p

        def readings(d, sub):
            return {r for (s_, r), pos in d.items() if s_ == sub and pos <= p}

        @lru_cache(maxsize=None)
        def go(i: int, j: int, state: str) -> bool:
            # state: start → (word | num); num → (num | counter→word); word → suffix
            if i == len(surface):
                return j == len(reading) and state != "start"
            for k in range(len(surface), i, -1):
                sub = surface[i:k]
                is_num = all(ch in NUMERALS for ch in sub)
                steps = []
                if state == "start":
                    steps += [(r, "num" if is_num else "word") for r in readings(self.piece, sub)]
                if state == "num":
                    if is_num:
                        steps += [(r, "num") for r in readings(self.piece, sub)]
                    steps += [(r, "word") for r in readings(self.counter, sub)]
                    steps += [(r, "word") for r in readings(self.suffix, sub)]
                if state == "word":
                    steps += [(r, "word") for r in readings(self.suffix, sub)]
                for r, nxt in steps:
                    for v in reading_variants(r):
                        if reading.startswith(v, j) and go(k, j + len(v), nxt):
                            return True
            return False

        return go(0, 0, "start")


@dataclass
class Run:
    toks: list[Tok]
    learned: bool

    @property
    def surface(self) -> str:
        return "".join(t.surface for t in self.toks)

    @property
    def reading(self) -> str:
        return "".join(t.reading for t in self.toks)


def classify(toks: list[Tok], vocab: Vocab, p: int, target: tuple[int, int],
             merge_target: bool = True) -> list:
    """
    Split a sentence into plain-kana tokens and kanji word runs, each run
    labelled learned/unlearned for the card at deck position p.
    """
    ts, te = target
    label: dict[int, object] = {}            # token index -> run key
    for i, t in enumerate(toks):
        if kanji_in(t.surface) and ts <= t.start and t.end <= te:
            # Gap words (来る十五日) keep their original brackets so the number
            # stays separate from 日; otherwise the target is one ruby span.
            label[i] = ("target",) if merge_target else ("target", t.group)
    # words with kana inside (世の中) seen so far
    for i, t in enumerate(toks):
        for surf, read, pos in vocab.spans.get(t.surface[:1], ()):
            if pos > p or i in label:
                continue
            s_ = r_ = ""
            j = i
            while j < len(toks) and len(s_) < len(surf):
                s_ += toks[j].surface
                r_ += toks[j].reading
                j += 1
            if s_ == surf and r_ == read and not any(k in label for k in range(i, j)):
                for k in range(i, j):
                    if kanji_in(toks[k].surface):
                        label[k] = ("span", i)
    out, run, run_key = [], [], None

    def flush():
        nonlocal run
        if run:
            if run_key[0] in ("target", "span"):
                learned = True
            else:
                r = Run(run, False)
                learned = vocab.word_known(r.surface, r.reading, p)
            out.append(Run(run, learned))
            run = []

    for i, t in enumerate(toks):
        if not kanji_in(t.surface):
            flush()
            run_key = None
            out.append(t)
            continue
        key = label.get(i, ("group", t.group if t.group != -1 else ("solo", i)))
        if run and key != run_key:
            flush()
        run_key = key
        run.append(t)
    flush()
    return out


def serialise(parts: list) -> str:
    """Learned runs → [base|reading], unlearned → {base|reading}, kana as is."""
    out = []
    for x in parts:
        if isinstance(x, Run):
            o, c = ("[", "]") if x.learned else ("{", "}")
            out.append(f"{o}{x.surface}|{x.reading}{c}")
        else:
            out.append(x.surface)
    return "".join(out)


def unlearned_words(parts: list) -> list[tuple[str, str]]:
    return [(x.surface, x.reading) for x in parts if isinstance(x, Run) and not x.learned]


def prepare(bracketed: str, card: dict, vocab: Vocab) -> dict | None:
    """Match the target, classify every word. None if the target isn't there."""
    toks = tokenize(normalise_brackets(bracketed, card))
    if toks is None:
        return None
    hit = find_target(toks, parse_forms(card))
    if hit is None:
        return None
    start, end, form = hit
    kanji_end = end if "～" in form.surface else start + len(form.surface)
    parts = classify(toks, vocab, card["deck_position"], (start, kanji_end),
                     merge_target="～" not in form.surface)
    return {"ja": serialise(parts), "hl": [start, end], "form": form.display,
            "unlearned": unlearned_words(parts)}
