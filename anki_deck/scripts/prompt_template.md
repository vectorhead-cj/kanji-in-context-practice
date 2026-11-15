# Task: example sentences for my Japanese flashcards (Kanji in Context, {RANGE})

You're writing example sentences for my Anki deck. Each card is one vocabulary word. On the back of the card I'll see the sentence with furigana and can reveal the English. I study the deck in a fixed order: the **practice order number** ("Pos") of each card.

This file covers {RANGE}, listed per lesson under "Cards to write". The column **Write** says how many sentences each card needs; some cards already have sentences (shown so you don't repeat them).

## How we'll work

- **This chat covers only some of the lessons.** I work through them in several chats, a few lessons each. My message with this file says which lessons to do in this chat (for example "Do {EXAMPLE}"). Do only those, and ignore the other lessons' tables. If I didn't say which lessons, ask before writing anything.
- **One lesson per reply**, in table order. After each lesson, stop and wait until I say "next". When the lessons for this chat are done, say so and stop.
- If a lesson is too long for one reply, stop at the end of a card and say so; I'll say "continue".
- Before writing a card's sentences, check the word list at the end for what I know at that card. The list runs to the last card in this file; for a card at number N, only entries 1 to N count.

## Rules

**The target word**

1. Use the target word in the sense given in Meaning, with **exactly the reading given** (人 as ひと is a different word from 人 as にん; 十分 as じゅうぶん "enough" is a different word from 十分 as じゅっぷん "ten minutes"). If Reading lists several readings (separated by 、), spread the sentences over them.
2. Verbs and adjectives may be conjugated (分かる → 分かりません, 円い → 円く). But the target must be the word itself, not part of a different word: 足す must not appear as 足し算 or 足りない; 分ける not as 分け前 or 引き分け.
3. A **single-kanji word** must stand as its own word, not be part of a compound: 金 (gold) not in 金曜日 or 奨学金; 十 not in 十分 or 七十; 人 not in 人々. (倍 in 私の倍食べる is fine: 倍 is its own word there.)
4. **Number + counter words** (五分, 三日, 一時, 四月…) must not sit inside a bigger number: 五分 not in 十五分, 一時 not in 十一時.
5. **Counter cards written with ～** (～回, ～足, ～度…): the counter must follow a number written in kanji or 何: 三回, 何回, 一足.

**Vocabulary**

6. At a card with practice order number N, I know **the words on cards 1 to N** (the list at the end), including their conjugated forms, and numbers combined with counters I've seen (十月, 三人).
7. Two known words do **not** combine into a new known word: 山 and 水 are known, 山水 is not.
8. Each sentence may contain **at most 2 kanji words that I don't know yet**, and those must be **common, everyday words** (the kind in any beginner/intermediate textbook). No idioms, proverbs, set phrases, literary, rare, slangy or crude words. Words normally written in kana (これ, ある, とても, ください…) and katakana loanwords are always fine.
9. Write **normal Japanese**: use kanji where Japanese normally uses kanji, even for words I don't know yet. Never rewrite a word in kana to avoid an unknown kanji. Write numbers in **kanji numerals** (三つ, 二十日, 五千), never 0–9 digits.

**Quality**

10. **Short and natural**: at most about 22 characters, intermediate grammar, something a native speaker would actually say or write. Use natural collocations: turning right is 右に曲がる, not 右に回る.
11. **Variety**: within a card, every sentence uses a different situation and different vocabulary. Across cards, don't reuse a sentence pattern (no 駅まで歩いてN分 on every minute card, no 今日はN日です on every date card). Don't repeat or closely paraphrase the existing sentences listed for a card.
12. The English is a natural translation of the whole sentence, in the same sense as the card.
13. Punctuation as in normal written Japanese: no comma right after は, を, に or が in a short sentence (石けんを店で買った, not 石けんを、店で買った). A comma between clauses is fine (雨が降ったので、…).

**Furigana format**

14. Every word containing kanji goes in a bracket `[kanji|reading]`, reading in hiragana, **one bracket per word**: `[今日|きょう]は[雨|あめ]が[降|ふ]っている。` Every kanji must be inside a bracket.
15. Okurigana and prefixes stay **outside** the bracket: `[食|た]べる`, `お[金|かね]`, `ご[飯|はん]`.
16. A number and its counter are **separate brackets** when the card is the counter: `[三|さん][足|ぞく]`, `[何|なん][回|かい]`. When the card is the whole number word, keep it in one bracket: `[五分|ごふん]`, `[二十日|はつか]`.
17. Words with kana inside are bracketed around the kanji only: `[世|よ]の[中|なか]`.

## Output format

For each lesson, one code block of JSON Lines, one sentence per line, cards in table order. Put the lesson as a comment on the first line:

```jsonl
# {FIRST}
{"card": "<Word exactly as in the table>", "ja": "<bracketed sentence>", "en": "<English>", "new_words": ["<kanji words used that I don't know yet at this card>"]}
```

After the block, write one short line for each sentence you're unsure about (reading, naturalness, vocabulary), or "No doubts." Then stop and wait.
