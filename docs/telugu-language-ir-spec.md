# Telugu / Tenglish language handling and the Language IR

Specification for the language subsystem: what exists, what the ASR discards,
how text is represented, and the invariants that keep a speaker's actual words
from being silently rewritten.

Status of this document: **specification, not implementation.** No code in
`engine/` was changed to produce it and none of the dataclasses below exist in
the tree. Sections 1-3 are an audit of what is on disk and are cited to
file:line; section 5 is a design. Where a number is quoted it says where it
came from, and every number in sections 1-3 was either read from a committed
record or recomputed in this pass from committed records - none is estimated.

---

## 1. Current state: what the tree has

### 1.1 The language-fitness gate (`engine/language_fitness.py`)

Added in `aa63985`. A pre-selection gate that consults the offline WER table
and refuses when the model that just ran cannot transcribe the language.

- Cut points: `WER_BAR = 0.25` (`engine/language_fitness.py:78`),
  `WER_REFUSE = 1.0` (`:84`), `MIN_CLIP_COUNT = 10` (`:88`).
- Verdicts `usable | poor | unusable | not_measured`, the report shape at
  `FitnessReport` (`engine/language_fitness.py:93-117`), which also carries
  `alternatives` (other models measured on the language, best first),
  `excluded` (records too small or off the production beam to be measurements)
  and `superseded` (duplicates that lost the clip-count race).
- `fitness(language, model, runs_dir)` (`:274-326`) scores the model that
  **ran**, not the one requested.
- `is_refusable` (`:329-352`) refuses only on `unusable`. The `poor` band's
  single measured member is Telugu-on-medium at 91.54%, whose only measured
  alternative (`small`, 151.08%) is worse, so a refusal would name a fix the
  corpus does not have.
- Wired in at `selekyt/pipeline.py:758-766`, which raises on `unusable` unless
  `--skip-language-check` is passed, and writes the verdict into the report at
  `selekyt/pipeline.py:1182` so a bypass leaves evidence rather than deleting
  it.

The module's docstring states its own limit plainly
(`engine/language_fitness.py:64-69`): WER is a property of the language-model
pair, not of the file, so the verdict is a prior. It does not listen to the
audio; the coverage gate does that.

### 1.2 Language fields on the corpus (`engine/corpus.py`)

`EditorialCorpus` carries two deliberately separate fields
(`engine/corpus.py:171-172`):

```python
language: str               # as reported by the caller; metadata
detected_languages: list[str]  # script-range evidence over a bounded sample
```

The distinction and its reason are documented at `engine/corpus.py:484-488`.
Detection runs over a bounded sample only - the first 64 units, truncated to
4000 characters (`engine/corpus.py:511-513`), never the whole transcript.

### 1.3 Script-range detection (`engine/context_guard.py`)

`detect_languages` (`engine/context_guard.py:55-73`) counts characters per
script block (`_SCRIPT_RANGES`, `:7-14`, Telugu `[ఀ-౿]`), counts
`[A-Za-z]` as "english" (`:62-64`), and admits a language at a **count >= 2**
(`:66-68`). Hindi output additionally reports Marathi (`:69-70`) - the two
share a script and the function cannot separate them.

This is the pipeline's only per-text language evidence, and it is a script
classifier being used as a language classifier. Section 6 lists what that
costs.

### 1.4 Where Telugu script structure IS handled: caption layout

`engine/caption_director.py` is the one place the pipeline already reasons
about Telugu as an abugida rather than as a bag of code points, and it does it
correctly:

- The Brahmic ranges including Telugu (`:38-49`).
- Virama detected by Unicode combining class 9, script-independent (`:57-60`),
  so "ప్ర" (ప + ് + ర) is one cluster.
- Marks keyed on general category, not combining class, because the Telugu
  anusvara "ం" is `Mc` with combining class 0 and treating it as a new
  syllable "doubled the measured width of ordinary Telugu prose" (`:63-68`).
- `caption_width` (`:83-143`) counts syllable clusters, measured against real
  Noto Sans Telugu type; the code-point rule it replaced was +145%
  conservative and broke a 34-column Telugu line at 14 (`:98-106`).

Any new code that measures or segments Telugu text must reuse this logic
rather than reinventing it.

### 1.5 Sentence termination across scripts

`engine/story_units.py:108`:

```python
_TERMINAL = re.compile(r"[.!?।॥？！]+")
```

The Devanagari danda `।` (U+0964) / double danda `॥` (U+0965) are the
conventional Telugu sentence terminators - the Telugu block itself (section 4)
contains no dedicated sentence punctuation. This is load-bearing: the corpus's
Hindi source carried **no terminal mark of any kind**, the candidate scan
therefore closed zero spans, and the run reported "no story passed the gate"
for a structural reason rather than an editorial one
(`docs/product-gap-map.md:182-195`).

### 1.6 The ASCII boundary

`engine/nle_export.py:230-234` `_ascii_name` produces an ASCII-safe value for
the EDL `M2` annotation and records the substitution in
`non_ascii_name` (`:198`, `:339`) rather than silently. This is the only
existing place non-ASCII text is replaced for a downstream format, and it
*substitutes a fallback* - it does not romanize.

### 1.7 What does NOT exist

Grepping for `normaliz|transliter|romaniz|ISO 15919|Hunterian|phone` over the
tree returns only:

- `tools/wer_bench.py:82-91` `_normalize` - LibriSpeech-convention WER
  flattening (lowercase, strip punctuation, collapse whitespace). It is
  **script-blind**: it applies `str.isalnum()` per character, which is a no-op
  on Telugu, and it does no script or language work at all.
- `tools/caption_ocr.py:115` mirrors that same flattening so the OCR reference
  matches the WER harness.

There is **no** language normalisation, no transliteration, no romanization,
no phonetic interpretation, no code-switch detection, and no language IR
anywhere in the tree. `models/core.py:5-18` defines `Word(text, start, end,
confidence)` and `Segment(start, end, text, words, coherence, completeness)`
- neither carries a language, a script, a span label, or any provenance beyond
the word-level ASR confidence.

---

## 2. The measured quality problem and what it implies

### 2.1 The numbers, and where each one lives

All figures are from committed records in `corpus/wer/runs/`, produced by
`tools/wer_bench.py` against OpenSLR SLR66 Telugu (`te_in`, female speakers,
50 utterances) and Multilingual LibriSpeech. Telugu references are in
`corpus/wer/telugu-slr66-female/manifest.jsonl`.

| Pair | WER | CER | Clips <= 25% bar | Record |
| --- | --- | --- | --- | --- |
| `te` / `small` | 151.08% | 127.40% | **0 / 50** | `telugu-small.json` |
| `te` / `medium` | 91.54% | 69.24% | **1 / 50** | `telugu-medium.json` |
| `pl` / `small` | 11.01% | 2.45% | 34 / 35 | `polish-small.json` |
| `it` / `small` | 20.11% | 4.53% | 26 / 40 | `italian-small.json` |

The clip-level distribution (recomputed in this pass from the `per_clip`
arrays) is worse than the aggregate suggests: `telugu-small` per-clip WER
ranges 0.71-4.40 with **none** inside the bar; `telugu-medium` ranges
0.25-2.27 with exactly one. Polish on the same model has 34 of 35 inside.
Polish is not a large-language privilege - it is a same-model, same-pipeline
comparison, which is what makes the Telugu figure a language-handling finding
rather than a model-size one.

### 2.2 The failure is not only lexical - it is script instability

This pass recomputed the Unicode script distribution of every non-space
character in the 50 ASR hypotheses of each Telugu record (counting by the
first word of the `unicodedata.name` of each character):

| Script share of ASR output | `small` (WER 151%) | `medium` (WER 91.5%) |
| --- | --- | --- |
| Telugu | **56.4%** | **68.7%** |
| Devanagari | 23.9% | 13.0% |
| Latin | 8.8% | 12.4% |
| Malayalam | 4.9% | - |
| Kannada | - | 2.2% |
| + Armenian, Hangul, CJK, Tibetan, Arabic, Ethiopic, Gurmukhi, Bengali, Sinhala, U+FFFD | ~4.8% | ~3.4% |

The references, over the same 50 utterances, are **98.83% Telugu** with the
remaining 1.17% being 8 backslashes, 8 `n` characters and one ampersand - all
of them a double-escaping artifact in the committed manifest (the
`reference` fields end with a literal `\` + `n`, code points U+005C U+006E,
verified by hex inspection). **The reference corpus contains zero genuine
Latin-script spans.** It is a monolingual Telugu corpus.

The single most illustrative clip is `tef_01033_00007107192`
(`corpus/wer/runs/telugu-small.json`, `per_clip[0]`):

- reference: `అనగా మన దేశానికి హిందీ అధికార భాష`
- hypothesis: `अनागा मनदेसान की फिंदी अदिकार बाशा`
- WER 1.00, CER 0.645

The model heard the phonetics almost correctly and wrote them in the **wrong
script** - Devanagari instead of Telugu, word for word. CER 0.645 on a clip
whose words are 100% wrong is the sound of a script substitution being charged
as character substitutions.

### 2.3 What this implies for the subsystem

1. **A transcript at 91% WER is not a transcript an editor can trust**, and no
   amount of downstream correction fixes it - every downstream artifact
   (captions, cut points, topic clusters, redundancy groups) is derived from
   it. The fitness gate refuses the worst case; the `poor` case still ships,
   which is why the IR in section 5 carries the fitness verdict *into the
   record itself*.
2. **The script of the ASR output is not evidence of the language spoken.**
   A script switch inside the transcript is, on the measured evidence, more
   likely a model artifact than a speaker behaviour. Any code-switch detector
   built on script alone will have a precision bounded by Telugu-script purity
   of 56-69%. Section 3 and section 6 develop this.
3. **Because the ASR is the lossy step, the IR's job is to stop the *next*
   step from losing more.** The verbatim ASR text is the best available record
   of what was said; it is already wrong, and every transform applied to it
   must be non-destructive, invertible where possible, and provenance-tagged
   where not.
4. **The standing rule against manufactured metrics binds here.** SLR66 has no
   code-switching ground truth (measured: zero Latin spans in the reference),
   no Telugu speaker was available (`docs/TRANSCRIPTION_EVALUATION.md:29-36`),
   and no new measurement may be invented to justify a design choice.

---

## 3. What faster-whisper exposes and what the pipeline discards

Installed version: `faster-whisper` 1.2.1, `.venv/lib/python3.12/site-packages/
faster_whisper/transcribe.py`. The library exposes four objects; the
production path keeps five scalars from them.

### 3.1 The exposed surface (library dataclasses)

`Word` (`transcribe.py:32`): `start`, `end`, `word`, `probability`.

`Segment` (`transcribe.py:47`): `id`, `seek`, `start`, `end`, `text`,
`tokens`, **`avg_logprob`**, **`compression_ratio`**, **`no_speech_prob`**,
`words`, **`temperature`**.

`TranscriptionInfo` (`transcribe.py:101`): `language`,
**`language_probability`**, `duration`, **`duration_after_vad`**,
**`all_language_probs`** (a full per-language ranking), plus
`transcription_options` and `vad_options`.

`TranscriptionOptions` (`transcribe.py:71`) carries the decode's own decision
thresholds - `log_prob_threshold`, `no_speech_threshold`,
`compression_ratio_threshold` and the temperature ladder. `detect_language`
returns `(language, probability, all_language_probs)` and consumes a slice
**from the start of the audio** (`tools/wer_bench.py:1054-1069` implements the
recording of it).

### 3.2 What the production path keeps

`providers/whisper.py:221-250` `_transcribe_chunk`:

```python
segments, info = model.transcribe(
    str(path), language=language, beam_size=beam_size,
    word_timestamps=True, vad_filter=vad_filter,
    condition_on_previous_text=False,
)
...
return out, info.language          # line 250
```

Retained: the segment text and bounds, per-word `word`/`start`/`end`, per-word
`probability` (`providers/whisper.py:233-241`), and **one** scalar from the
info object: `info.language`. Across chunks these are aggregated by majority
vote (`providers/whisper.py:508`) into a single `detected` language for the
whole source.

### 3.3 What is discarded, and what it would have answered

`tools/decode_probe.py` exists *precisely* to read what production throws
away. Its docstring (`tools/decode_probe.py:4-16`) states the design intent:
"The production `_transcribe_chunk` calls `model.transcribe(...)` and keeps
only `info.language` from the rest of that object carries the answer to the
one question the root-cause analysis could not settle from transcript shape
alone."

| Discarded field | Would answer |
| --- | --- |
| `info.language_probability` | Whether the language claim is a guess. **Measured to be useless in the forced path:** every clip in `telugu-small.json`, `telugu-medium.json`, `polish-small.json` and `italian-small.json` records `language_probability: 1.000`, because `--language` was forced. In the auto-detect path it is the informative number: `detect-small-telugu-tutorial.json` records `te 0.8808, ta 0.0862, en 0.0084, ml 0.0082, hi 0.004` over a 60 s window - the Tamil confusion at 8.6% is itself a signal. |
| `info.all_language_probs` | A per-language ranking - the *second* choice is what a code-switch hypothesis needs, and it is never recorded by production. |
| `info.duration_after_vad` | How much audio the VAD gate removed, i.e. whether a coverage collapse is a gate artifact. |
| Per-segment `avg_logprob` | Per-span decode confidence, the one signal that could corroborate a script switch as real speech rather than decoder noise. |
| Per-segment `no_speech_prob` | Whether the decoder gated this span as non-speech. |
| Per-segment `compression_ratio` | Repetition/hallucination detection (a classic whisper failure signature). |
| Per-segment `temperature` | Whether this span failed the confidence thresholds and was re-decoded further up the ladder `[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]`. `tools/decode_probe.py:8-16` identifies this as the explanation for the >4x decode time and the run-to-run instability on Telugu. |
| `Segment.tokens`, `id`, `seek` | Exact position of the decode within its window, for reproducible re-decode. |

**Critically, none of this is per-segment language.** faster-whisper reports
`language` per *transcribe call* (in production, per 5-minute chunk), not per
segment. There is no library field that says "this span is English" - a
code-switch detector cannot call an API for it; it must infer from script,
timing and the confidence signals above. And because `detect_language` reads
only a slice from the **start** of the audio, even the chunk-level language
claim is blind to anything said later.

### 3.4 The cost of the discard, already measured

`providers/whisper.py:59-63` documents the consequence in production terms:
on `telugu-launch` the forced-Telugu decode returned 0 segments with the VAD
on and 5 segments / 24.88 s of nonsense with it off (54.7% Telugu letters,
the rest romanized noise plus one Chinese character), **and 23 segments on a
repeat run of the same file**. The run-to-run instability is exactly what the
discarded `temperature` field would have explained, and the script-purity
figure matches the distribution in section 2.2. A script-consistency guard was
tried and passed the garbage (`providers/whisper.py:405-413`) - which is the
direct empirical argument for why script evidence alone cannot license a
decision in this subsystem.

---

## 4. Romanization: ISO 15919, with a lossy search alias

### 4.1 The choice

**Canonical retrieval form: ISO 15919 (2001), romanization of Indic scripts.**
For ASCII-only consumers, its own 7-bit fallback (`ṭ` = `.t`, `ś` = `sh`,
`ē` = `ee`).

**Query-side convenience: a lossy ASCII-folded alias** (NFD, strip combining
marks, lowercase), explicitly non-canonical, never stored as the canonical
form, never written back.

**Hunterian is deliberately rejected as the canonical form** and used, if at
all, only as an additional optional alias.

### 4.2 Justification

The requirement is not "can an English reader pronounce it" but "can a
retrieval key be derived without losing information the verbatim carries".
That is the difference between a *transliteration* and a *transcription*.

**ISO 15919 is lossless with respect to the script.** It is a one-to-one
grapheme mapping, so script -> Latin -> script is deterministic. It
distinguishes exactly the contrasts Telugu script encodes: ట ṭ vs త t (and
the whole retroflex vs dental series ఠ ṭh / డ ḍ / ఢ ḍh against థ th / ద d /
ధ dh), శ ś vs ష ṣ vs స s, the retroflex lateral ళ ḷ, ణ ṇ, ఱ ṟ, vowel length
ఎ e vs ఏ ē and ఒ o vs ఓ ō, anusvara ం ṁ and visarga ః ḥ. A lossless map is
what makes provenance honest: the canonical form is recomputable from the
verbatim, so it can never silently disagree with the record it describes, and
no information is destroyed in producing it.

**Hunterian is lossy by design.** It writes dental త and retroflex ట both as
`t`, ద/డ both as `d`, శ and ష both as `sh`, and - most destructively for a
retrieval index - it applies **schwa deletion** for modern Indo-Aryan
languages (Hindi कानपुर -> `kānpur`, not `kānapura`). Two distinct source
strings can map to one Hunterian string, so a Hunterian key collides, and the
round trip is not deterministic. Hunterian is the Government of India's
official romanization and is what an editor will actually type at a search
box, which is exactly why it belongs on the *query* side as an alias and not
on the *storage* side as the canonical key.

**The 7-bit fallback is required by an existing constraint, not chosen for
taste:** EDL is an ASCII format (`engine/nle_export.py:230-234`), and the
ASCII fallback keeps one canonical derivation rather than a second,
divergent romanization for ASCII paths.

### 4.3 What romanization does NOT do - the dialect constraint

Romanization operates on the **script**, not on the language variety. ISO
15919 maps క -> `ka` regardless of whether the speaker realized it as [k],
[g] or [x], because Telugu script writes the letter, not the phone. So:

- Romanization cannot falsify a dialect, because it does not touch the
  phonetic level. The danger to the dialect is in **phonetic interpretation**
  and in **contextual correction**, which is why section 5 makes both
  optional, non-destructive, annotation-only fields.
- Conversely, romanization cannot *record* the dialect either. The IR's
  dialect guarantee rests entirely on the verbatim field and on the absence of
  any destructive transform, never on the romanization.

### 4.4 Script facts the implementation must respect

From the Unicode Telugu block, U+0C00-U+0C7F, script property `Telugu`:

- 128 code points, 101 assigned and 27 reserved as of Unicode 17.0.
- Signs: U+0C00 combining candrabindu, U+0C01 candrabindu, **U+0C02 anusvara**
  (the bindu), U+0C03 visarga, U+0C04 combining anusvara above, U+0C3C nukta,
  U+0C3D avagraha, **U+0C4D sign virama** (halant), U+0C77 siddham sign.
- Independent vowels U+0C05-U+0C14 (with U+0C0D, U+0C11 reserved); consonants
  U+0C15-U+0C39 (ka through ha; U+0C29 reserved), including U+0C31 ఱ RRA and
  U+0C34 ఴ LLLA; archaic additions U+0C58-U+0C5A, U+0C5C-U+0C5D.
- Dependent vowel signs U+0C3E-U+0C44, U+0C46-U+0C48, U+0C4A-U+0C4C, with
  U+0C45 and U+0C49 reserved; vocalic signs U+0C62-U+0C63.
- **U+0C55 / U+0C56 are the length marks; U+0C60 / U+0C61 are *letters*
  (vocalic RR / LL), not signs** - a common mix-up.
- Numerals U+0C66-U+0C6F; traditional fraction signs U+0C78-U+0C7F.

Three consequences already encoded or required:

1. **Virama has combining class 9** (`engine/caption_director.py:57-60`), so
   conjunct detection needs no per-script table. Any new segmentation must
   reuse this.
2. **Anusvara U+0C02 is category `Mc` with combining class 0** - a *spacing*
   mark that does not start a new syllable
   (`engine/caption_director.py:63-68`). Romanization must map it (-> ṁ)
   without treating it as a consonant.
3. **The Kannada block begins at U+0C80, immediately after Telugu.** A
   block-range check with an off-by-one or an inclusive-when-exclusive end
   silently classifies Kannada as Telugu; `engine/context_guard.py:8` uses
   `[ఀ-౿]`, which is correct, and new code must not widen it. This
   is an invariant because the failure is silent.

### 4.5 Script switching: what it gives and what it does not

Telugu script (U+0C00-U+0C7F) and Latin are disjoint at the character level,
so **a script switch is trivially and exactly detectable**: the boundary is
the code point, no model required, and the script of every span is certain.
What that certainty buys and does not buy:

**It gives:**
- The exact character and word positions of every script change.
- The script of each span, with zero error and zero compute.
- A lower bound on where a code-switch could be, since a spoken switch into
  English cannot surface as Telugu script.

**It does not give:**
- **The language of a span.** A Latin span inside Telugu speech may be
  English, may be romanized Telugu (the everyday Tenglish writing practice),
  may be a loanword, or may be ASR noise. Latin -> "english" as
  `engine/context_guard.py:62-64` does is wrong for this domain: on
  Tenglish input the majority case is romanized *Telugu*, not
  English.
- **That a switch was spoken at all.** Measured: on a monolingual Telugu
  corpus, `detect_languages` reports **five** languages on the `small`
  hypothesis (`['english','hindi','malayalam','marathi','telugu']`) and **six**
  on the `medium` hypothesis (`['bengali','english','hindi','kannada',
  'marathi','telugu']`), while the reference is pure Telugu
  (`['english','telugu']` - and its `english` is itself a false positive from
  the `\n` escaping artifact, since the Latin count of 8 clears the >= 2
  threshold at `engine/context_guard.py:66-68`). Script switches in this ASR
  output are predominantly decoder artifacts.
- **Same-script language pairs.** Hindi and Marathi are inseparable this way
  (`engine/context_guard.py:69-70`), and Telugu/Kannada/Tamil are close
  enough that the detector confuses them.
- **Intra-word switching.** Script changes inside a word are visible but the
  word is one ASR timing unit and must not be re-tokenized (see invariants).
- **Anything about dialect or register.** Script carries no information about
  Telangana spoken form versus literary Telugu - which is precisely why the
  dialect constraint cannot be enforced at this layer and must be enforced as
  a no-destructive-transform invariant instead.

The consequence for the design: script spans are recorded as **facts**;
language labels on those spans are recorded as **hypotheses** with a `basis`
field, and no hypothesis ever licenses a transform on the verbatim.

---

## 5. The Language IR: field set

Design rule, stated once: **the verbatim field is authoritative and
immutable. Every other field is a derived view that names the transform that
produced it. Nothing is applied in place; no transform may overwrite the
verbatim.** If a derived field cannot be computed without inventing
information, it is `None` and a warning explains why.

None of these types exist in the tree. This is a specification.

### 5.1 `WordSpan` - one ASR word and its classification

```python
@dataclass(frozen=True)
class WordSpan:
    text: str                 # verbatim, from ASR; never re-encoded
    start: float              # seconds, source-absolute
    end: float                # seconds, source-absolute
    asr_probability: float | None   # Word.probability, or None if absent
    script: str               # Unicode script property value of the first
                              # non-mark character: "Telugu", "Latin",
                              # "Devanagari", "Common", "Mixed", ...
    span_id: int              # index into ScriptSpan list
    language_hypothesis: str | None  # ISO 639-3, or None
    hypothesis_basis: str     # "" | "script_range" | "script_range+asr_lang"
                              # | "acoustic_lid" | "manual"
```

Invariants:

- `text` is non-empty. A word with no text is not a `WordSpan`; the existing
  filter at `engine/corpus.py:241-245` already drops those.
- `0.0 <= start <= end`. Matches the clamping contract of
  `engine/corpus.py:232-239`.
- `script` is computed from the **Unicode script property**, not from the
  block tables, except that the fast path in `engine/context_guard.py` may
  keep its block table for backwards parity. Block tables misclassify
  unassigned code points and PUA; the script property does not.
- `script == "Mixed"` when the word spans more than one script. **The word is
  never split**, because ASR word timing is atomic and splitting it destroys
  the timing invariants the cut algebra depends on (see the word-boundary
  work described in `docs/product-gap-map.md:65`).
- `language_hypothesis` defaults to `None`. It is only set when corroborated,
  and `hypothesis_basis` must say what corroborated it. **A Latin span is
  never auto-labelled `eng`** (section 4.5).

### 5.2 `ScriptSpan` - a maximal run of one script

```python
@dataclass(frozen=True)
class ScriptSpan:
    index: int
    char_start: int           # offset into the verbatim string
    char_end: int             # half-open
    word_start: int           # index into the WordSpan list
    word_end: int             # half-open
    start: float              # from word bounds - never synthesized
    end: float
    script: str               # as for WordSpan
    is_switch: bool           # True iff index > 0 and script != spans[-1].script
```

Invariants:

- Bounds come from the words, never interpolated: `start ==
  words[word_start].start` and `end == words[word_end - 1].end`. A span with
  no words is dropped rather than given invented times.
- Spans are contiguous and non-overlapping; concatenating their text yields
  the verbatim exactly.
- A span whose `script` is not the source language's script is *recorded*, not
  repaired and not dropped.

### 5.3 `CanonicalForm` - the derived retrieval view

```python
@dataclass(frozen=True)
class CanonicalForm:
    iso15919: str | None        # lossless transliteration of the non-Latin
                                # spans; Latin spans pass through unchanged
    iso15919_ascii: str | None  # the standard's own 7-bit fallback
    search_alias: str | None    # NFD + strip marks + lowercase; LOSSY,
                                # non-canonical, query-side only
    wer_normalised: str | None  # tools/wer_bench._normalize-compatible
                                # flattening, script-preserving
```

Invariants:

- **Invertibility gate.** For every Telugu-script span, applying the reverse
  ISO 15919 map to `iso15919` must reproduce the span exactly. **If any code
  point in the span cannot be mapped** - unassigned (the 27 reserved code
  points), U+FFFD replacement character, PUA, or an unmapped archaic letter -
  **the whole `iso15919` field is `None`** and a warning names the code point
  and its position. A best-effort romanization is how a falsified record
  enters an index. `iso15919_ascii` is `None` whenever `iso15919` is.
- **Latin spans pass through untouched.** Applying a transliterator to an
  already-Latin span double-romanizes it and produces garbage. The
  transform is applied per span on script evidence, never to the whole
  string.
- **`search_alias` is derived from `iso15919`, not from the verbatim**, so
  there is exactly one lossy step and it is downstream of the canonical one.
- **`wer_normalised` must be byte-identical to
  `tools/wer_bench._normalize(text)`** so any IR-derived comparison remains
  comparable to the committed WER records. It preserves script; it does not
  romanize.
- No canonical field is ever written back to the verbatim.

### 5.4 `TransformRecord` - provenance, one entry per derived field

```python
@dataclass(frozen=True)
class TransformRecord:
    field: str            # which derived field this produced
    transform: str        # "script_segment" | "iso15919" | "iso15919_ascii"
                          # | "ascii_fold" | "wer_normalise" | "label_spans"
    invertible: bool      # can the verbatim be recovered from `field`?
    lossy: bool           # does information get destroyed?
    version: str          # transform implementation version
    params: dict          # e.g. {"reverse_map": "iso15919-r1"}
```

Invariants:

- One record per non-`None` derived field. A derived field with no
  `TransformRecord` is a schema violation.
- `iso15919` records `invertible=True, lossy=False`. `search_alias` records
  `invertible=False, lossy=True`. `wer_normalised` records `invertible=False,
  lossy=True` - punctuation and case are destroyed, which is why it is a
  comparison aid and not a display form.
- `version` exists because a transform change can make a stored canonical
  disagree with a re-derived one; with the version, a reader detects it
  instead of trusting it.

### 5.5 `AsrSignals` - the fields production currently discards

```python
@dataclass(frozen=True)
class AsrSignals:
    engine: str                 # "faster_whisper" | "sherpa_onnx" | ...
    model: str                  # the model that RAN
    requested_model: str | None
    language_forced: str | None # the --language code, or None if auto
    detected_language: str | None
    language_probability: float | None
    language_ranking: list[tuple[str, float]] | None   # all_language_probs
    duration_after_vad: float | None
    decode: list[SegmentDecode]  # one per ASR segment
```

```python
@dataclass(frozen=True)
class SegmentDecode:
    index: int
    avg_logprob: float | None
    compression_ratio: float | None
    no_speech_prob: float | None
    temperature: float | None
    seek: int | None
```

Invariants:

- `language_probability` is recorded **but carries no weight when
  `language_forced` is not None.** Measured: it is 1.000 on all 175 clips
  across the four committed per-clip records precisely because language was
  forced. Any consumer that treats 1.000 as confidence is reading an artifact
  of the flag, not a measurement.
- `language_ranking` is the second choice made visible; a `te 0.88 / ta 0.09`
  ranking (as in `detect-small-telugu-tutorial.json`) is a different claim
  from `te 0.99 / ta 0.01` and the IR must keep the difference.
- When `engine != "faster_whisper"` the `decode` list is empty and the fields
  are `None`, never invented. `providers/transcribe.py` already records which
  engine ran in provenance; the IR inherits that.

### 5.6 `LanguageIR` - the record

```python
@dataclass(frozen=True)
class LanguageIR:
    # the source of truth
    verbatim: str                       # ASR text, whitespace-trimmed only
    # structure
    words: tuple[WordSpan, ...]
    spans: tuple[ScriptSpan, ...]
    # derived views
    canonical: CanonicalForm
    # what produced them
    provenance: tuple[TransformRecord, ...]
    asr: AsrSignals
    # the verdict that was in force when this record was built
    fitness_verdict: str               # usable | poor | unusable | not_measured
    fitness_detail: str                # FitnessReport.detail, verbatim
    fitness_record: str | None         # the .json the number came from
    # human-readable caveats; never empty when a derived field is None
    warnings: tuple[str, ...]
```

Invariants (these are the ones that carry the dialect constraint):

1. **`verbatim` is never mutated by anything in this subsystem.** There is no
   API that takes a `LanguageIR` and returns one with a different `verbatim`.
   Correction is additive (a `warnings` entry, a hypothesis), never
   destructive.
2. **`verbatim` is the only field a caption or display path may read for
   text.** Canonical forms are retrieval and comparison aids; the tofu
   incident (`docs/product-gap-map.md:94`, a Telugu render that shipped as 13
   hollow rectangles) is the standing proof that an ASCII-friendly path must
   not decide what a viewer sees.
3. **No field encodes a language variety.** The IR has no `register`, no
   `is_literary`, no `dialect` field. Its absence is the constraint: a
   consumer cannot select a canonical word for a dialect word because the IR
   offers no place to put one.
4. **A `poor` or `not_measured` verdict is carried into the record, not
   promoted to an absent one.** An index built on `canonical.iso15919` must
   not launder a 91.54%-WER transcript into an authoritative-looking key; the
   verdict travels with it.
5. **Every `None` derived field has a warning naming what blocked it.** A
   silent `None` is the same shape as a silent fabrication.
6. **Deterministic.** Given the same `(verbatim, words, asr)`, the IR is
   byte-identical, matching the determinism contract of `build_corpus`
   (`engine/corpus.py:497`).
7. **Warnings surface to `report.json`**, following the existing pattern where
   a skipped gate records its evidence rather than deleting it
   (`selekyt/pipeline.py:1182`).

---

## 6. Failure modes: every way this subsystem could falsify what a speaker said

Each is stated with the evidence that makes it real, or marked as a hazard
with no measured instance.

1. **Script substitution by the ASR destroys the apparent language, and a
   "repair" would destroy the evidence.** Measured: 23.9% of the `small`
   Telugu hypotheses are Devanagari, including a clip whose phonetics were
   nearly right and whose script was entirely wrong (section 2.2). If a later
   stage rewrites Devanagari to Telugu to make the transcript "consistent",
   the record no longer shows what the model did, and the auditor cannot
   distinguish a repaired transcript from a correct one. **Mitigation: the
   verbatim stands; the substitution is recorded as a warning and as a span
   `script` value.**
2. **Script switches treated as spoken code-switches.** Measured: five
   spurious languages reported on a monolingual source (section 4.5). A
   code-switch detector that treats a script change as a language change
   produces false switches at a rate bounded by the 56-69% script purity of
   the ASR output. **Mitigation: `language_hypothesis` defaults to `None`;
   `is_switch` is a fact, the language label is a hypothesis with a basis.**
3. **Latin auto-labelled English.** The existing `detect_languages` does this
   (`engine/context_guard.py:62-64`). In Tenglish the Latin spans are more
   often romanized Telugu, and in the measured ASR they are frequently noise.
   **Mitigation: invariant in 5.1.**
4. **A low Latin-count threshold producing phantom languages.** Measured in
   this pass: a pure-Telugu reference reports `english` because 8 `n`
   characters from a `\n` escaping artifact clear the `>= 2` threshold. Eight
   characters of noise added a language to a monolingual text. **Mitigation:
   span-level labelling with corroboration instead of a global count
   threshold; the artifact itself is in the manifest, not the pipeline.**
5. **Lossy romanization stored as the canonical form.** Hunterian's schwa
   deletion and dental/retroflex collapse make two distinct utterances share
   one key, and the round trip is not deterministic. A retrieval index built
   on it silently equates different speech. **Mitigation: ISO 15919 as
   canonical; Hunterian only as an optional query alias.**
6. **Double romanization of Latin spans.** Running an Indic transliterator
   over an entire mixed string converts an English or romanized word into
   nonsense. **Mitigation: per-span application on script evidence, invariant
   in 5.3.**
7. **Best-effort romanization through unmappable code points.** The Telugu
   block has 27 reserved code points; the measured ASR also emits U+FFFD,
   Armenian, Hangul and CJK. A romanizer that maps the unknown to something
   plausible writes fabricated text into a retrieval key. **Mitigation: the
   invertibility gate - one unmappable code point nulls the whole field and
   raises a warning naming it.**
8. **Phonetic interpretation or contextual correction overwriting the
   verbatim.** This is the exact failure the mission constraint forbids: a
   Telangana spoken form replaced by a literary Telugu form because the
   literary form is what a corrector's prior expects. **Mitigation: the IR
   offers no field that can hold a "corrected" verbatim; corrections are
   hypotheses and warnings only. No measured instance, and the constraint is
   stated as an invariant precisely because it is not measurable here.**
9. **Punctuation or danda removal destroying sentence structure.** Measured
   consequence elsewhere in the corpus: a source with no terminal mark
   produced zero candidate spans and the run reported an editorial failure for
   a structural reason (`docs/product-gap-map.md:182-195`). Any normalization
   that strips `।`/`॥` to satisfy a Latin-centric cleaner repeats it.
   **Mitigation: `wer_normalised` is a comparison aid, never a display or
   segmentation input; `split_sentences` keeps reading the verbatim.**
10. **Code-point counting instead of syllable-cluster counting.** Measured and
    already fixed once for captions: the code-point rule was +145%
    conservative and broke a 34-column Telugu line at 14
    (`engine/caption_director.py:98-106`). Any new width or length logic over
    Telugu repeats the defect unless it reuses `caption_width`.
11. **Block-range off-by-one against Kannada.** Kannada begins at U+0C80, one
    past the Telugu block end. An inclusive-end range silently classifies
    Kannada as Telugu with no error. No measured instance; stated as an
    invariant because the failure mode is silent.
12. **`language_probability == 1.000` read as confidence.** Measured: it is an
    artifact of `--language` being forced on all 175 committed per-clip
    records. A consumer that routes or gates on it is routing on a flag.
    **Mitigation: invariant in 5.5.**
13. **A stored canonical disagreeing with the verbatim after a transform
    version change.** **Mitigation: `TransformRecord.version`; a mismatch is
    detectable rather than trusted.**
14. **The `poor` band shipping an authoritative-looking index entry.** A
    91.54%-WER transcript whose canonical form is clean ASCII looks every bit
    as trustworthy as Polish's. **Mitigation: invariant 4 in 5.6 - the
    verdict travels inside the record.**
15. **Splitting a mixed-script word to "fix" it.** This breaks ASR word
    timing, which the cut algebra and the clip text/words agreement
    guarantees depend on (`docs/product-gap-map.md:65`). **Mitigation:
    `script == "Mixed"` and the word stays atomic, invariant in 5.1.**
16. **A display path choosing the ASCII form.** The tofu render
    (`docs/product-gap-map.md:94`) shipped because an ASCII-friendly path
    produced ink that was not readable. **Mitigation: invariant 2 in 5.6.**

---

## 7. Deliberately out of scope

1. **No ASR retraining, fine-tuning or new model download.** Disk is at ~2.1
   GB (`docs/product-gap-map.md:18-24`) and no download is authorized. The
   measured quality gap is a property of the language-model pair
   (`engine/language_fitness.py:64-69`); this subsystem routes around it, it
   does not try to close it.
2. **No acoustic language identification.** No LID model is installed and no
   code-switching dataset exists in the corpus (measured: zero Latin spans in
   the SLR66 reference). The `basis` value `"acoustic_lid"` is reserved in
   the spec so the field's meaning is fixed, but nothing implements it.
3. **No measurement of code-switch detection quality, and no invented one.**
   With a monolingual reference corpus and no Telugu speaker available
   (`docs/TRANSCRIPTION_EVALUATION.md:29-36`), precision and recall for a
   switch detector cannot be computed here. The spec fails closed
   (`language_hypothesis` is `None` without corroboration) rather than
   presenting an unmeasured number as a result.
4. **No translation, no literary-Telugu conversion, no grammatical
   normalization, no speaker identification, no diarization.** None of these
   preserve what was said; several of them are the thing the constraint
   forbids.
5. **No destructive code-switch correction.** Detection and hypothesis
   labelling only; no transform on the verbatim is ever licensed by a switch.
6. **No replacement of the existing WER harness.** `tools/wer_bench.py`'s
   `_normalize` stays the comparator of record; `wer_normalised` is defined
   as byte-identical to it so the IR never creates a second, divergent
   definition of error.
7. **No typed-transcript / Latin-script Tenglish input handling.** The IR
   specifies the representation of ASR output. Normalizing human-typed
   Tenglish (which has no fixed spelling, no standard, and is written
   inconsistently by every author) is a different problem with different
   failure modes, and conflating the two is how a romanization spec turns
   into a spelling corrector.
8. **No changes to `engine/`, `tests/`, or `engine/memory/`.** This document
   is the deliverable; nothing was implemented.

---

## Sources

Repo records and code (cited by file:line throughout). Standards and
background:

- ISO 15919 (romanization of Indic scripts, 2001) - scope, the 7-bit ASCII
  fallback, and the Telugu mappings (ట ṭ / త t, శ ś / ష ṣ / స s, ళ ḷ, ఎ e /
  ఏ ē, ం ṁ, ః ḥ): https://en.wikipedia.org/wiki/ISO_15919
- Hunterian transliteration - official Government of India romanization,
  dental/retroflex collapse, schwa deletion:
  https://en.wikipedia.org/wiki/Hunterian_transliteration
- Unicode Telugu block U+0C00-U+0C7F - script property, assigned and reserved
  code points, virama and anusvara categories, length marks vs vocalic
  letters: https://en.wikipedia.org/wiki/Telugu_(Unicode_block)
- Code-switching typology - intersentential, intra-sentential, tag and
  intra-word switching, insertional vs alternational:
  https://en.wikipedia.org/wiki/Code-switching

The official Unicode code chart PDF for U+0C00 was fetched but its text layer
is FlateDecode-compressed and not extractable; the block's code point
inventory above is from the secondary source, and the two properties that
matter for implementation (virama combining class 9, anusvara category `Mc`
with combining class 0) are independently confirmed by working code in
`engine/caption_director.py:57-68`.

Web search was unavailable for much of this pass; where the literature on
code-switching detection could not be retrieved, no source is cited and the
claims in section 4.5 rest on the repo's own measurements plus the general
typology above, not on uncited literature.
