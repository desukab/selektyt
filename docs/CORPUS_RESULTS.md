# Corpus results — first run, 2026-09-19

This is the first time SelekyT has been run on more than one source. It was
run by `tools/corpus_run.py` over `corpus/manifest.json`; the raw record is
`corpus/results.jsonl` (one JSON object per source, including every failure),
and every number below is read out of those artifacts, not asserted.

**Result: 7 of 11 sources produced deliverables, 4 did not.** All eight
required axes were attempted.

| Source | Axis | Status | Segments | Clips | Lang |
|---|---|---|---|---|---|
| nasa360-ep5 | longform-multi-speaker | **OK** | 425 | 6 | en |
| emwcon-panel-2018 | longform-multi-speaker | **OK** | 842 | 5 | en |
| pentagon-esper-briefing | news | **OK** | 278 | 5 | en |
| royal-society-cloke | podcast | **OK** | 156 | 4 | en |
| ooi-vertical | documentary | **OK** | 26 | 3 | en |
| hadhazy-protest | difficult-audio | **OK** | 38 | 3 | hu |
| telugu-tutorial | non-roman-script | **OK** | 4 | 1 | te |
| hindi-baiga-interview | interview | NO_STORIES_PASSED | 3 | 0 | hi |
| telugu-launch | non-roman-script | NO_SPEECH_DETECTED | 0 | — | te |
| alice-1903 | difficult-audio | NO_SPEECH_DETECTED | 0 | — | en |
| roman-portrait-trailer | creator | NO_SPEECH_DETECTED | 0 | — | en |

Every OK source was also audited with `tools/nle_check.py`, with the `pycmx`
independent EDL parse executing rather than skipping. **This section has been
wrong twice, and the second wrong version is the more instructive one.**

The first version of this paragraph said the interchange audit reported relink
failures on all seven *because `corpus_run.py` deletes the source media*, and
called that an audit artifact rather than an export defect. That explanation
does not survive checking the code. `corpus_run.py`'s cleanup is

```python
shutil.rmtree(media_dir / media.name, ignore_errors=True)
```

and `fetch()` writes a regular **file**. `shutil.rmtree` on a file raises
`NotADirectoryError`, which `ignore_errors=True` swallowed, so the media was
never deleted by the harness at all. (Verified directly: the file survives.)
The sources that are gone were removed out of band for disk, not by the tool —
which means `--keep-media` was not doing anything, and "re-run with
`--keep-media` to exercise the relink checks" was advice about a switch that
had no effect. Fixed to `unlink()`; `tests/test_corpus_run.py` now asserts the
media really is gone and that the source probe survives it.

The second, worse problem: the relink checks that *did* run were resolving media
paths from the wrong base. Every corpus EDL carries a **repo-root-relative**
`* FROM CLIP:` — `corpus/media/emwcon-panel-2018.webm` — written verbatim from
the path the CLI was handed, while sitting in `project/timeline/01.edl`. An NLE
resolves a relative FROM CLIP against the **timeline's own directory**, so all
seven projects would have imported with every clip offline and the editor
staring at a relink dialog for a file that never moved. `nle_check.py` checked
`Path(p).exists()` from *its own* working directory, which was the repo root —
the same directory the emitter had written the relative path from. Both sides
resolved the same broken string the same lucky way, so the audit passed an
export that a real NLE rejects. The FCPXML side has absolutised since an earlier
run hit exactly this; the EDL side did not.

Both are fixed, and both fixes are pinned by tests that fail on the pre-fix code:
the EDL now carries an absolute path and refuses to export one it cannot resolve
(mirroring the guarantee `render_fcpxml` already had), and the audit resolves a
relative FROM CLIP against the timeline's directory, as an NLE does. The
`NOT RUN` verdict is now derived from the media the artifacts actually name
rather than from a separate path in `pipeline_state.json`, which carried the
same working-directory assumption.

`tools/editorial_audit.py` **failed on four of the seven** — `emwcon-panel-2018`,
`ooi-vertical`, `pentagon-esper-briefing`, `royal-society-cloke` — every one of
them a selection bound landing inside a word (`end 115.86s splits ' arrays,'
/ ' traveling'`). That is a real defect, and it is fixed in `engine/edit_plan.py`
(both bounds now snap back to the enclosing word boundary).

**That fix has since been confirmed on one of the four**, and the confirmation
exposed a second defect the first audit could not see.

`royal-society-cloke` was re-run with `--keep-media` after the word-alignment
fix. The four word-split failures are gone — clips 02/03/04 and the cross-clip
check all pass. But clip 01 still failed, on something else: its last caption
cue read `Somebody`, a word that appears in the SRT but **not in the
transcript**, and its cue ended at 52.485 s on a 52.421 s render. The selection
ends at 235.237 s; the word `Somebody` spans 235.220–235.380 s, so it
*straddles* the bound. `_word_aligned` correctly snaps that end back to the
word's start for the render — the render is right — but `build_blocks` in
`engine/caption_director.py` still built captions from the un-snapped
`candidate.words`, so the caption track described audio the deliverable does not
contain. Fixed: a word whose span crosses `candidate.end` is now excluded.
Regression test in `tests/test_captions.py`, verified to fail on the pre-fix
code.

After both fixes, `royal-society-cloke` is the first corpus source where both
audits are fully green:

```
clip 01     PASS  silence share 6.8%; rendered 52.42s, 33 cues
clip 02     PASS  silence share 10.7%; rendered 27.50s, 21 cues
clip 03     PASS  silence share 6.8%; rendered 21.26s, 14 cues
clip 04     PASS  silence share 7.8%; rendered 26.26s, 14 cues
cross-clip  PASS  no duplicated sentences
ALL CHECKS PASS
```

and `tools/nle_check.py` on the same retained media reports **62/62 PASS**,
including `edl media exists` and `fcpxml media relinkable`.

**That 62 was itself a false pass, and is the reason the count is now worth
something.** It was measured before the path-resolution fix above, so `edl
media exists` passed only because the audit and the emitter shared a working
directory. Re-audited with a relative FROM CLIP resolved the way an NLE resolves
it, the same project reported `edl media exists` **FAIL on all four clips** —
`project/timeline/corpus/media/royal-society-cloke.webm` does not exist. The
EDLs were then re-emitted with `tools/regenerate_timelines.py` (see below), and
the audit passes 62/62 for real: `01 edl media exists … 3 clip path(s)`, no
`NOT RUN`, no working-directory dependence.

`pycmx` parses every EDL independently of the project venv; timelines agree with
both rendered presets to 0.53 frames, and EDL↔FCPXML to 0.00 frames.

**What is still not established by this.** The other three sources were
re-audited, and they **still fail** — for a reason that is worth being precise
about, because it separates two different fixes:

| Source | After re-emit | Remaining failure |
|---|---|---|
| `emwcon-panel-2018` | clips 01, 05 FAIL | `end 67.09s splits ' was,'/' at'`; `end 225.13s splits ' comes'/' I'` |
| `ooi-vertical` | clip 01 FAIL | `end 115.86s splits ' arrays,'/' traveling'` |
| `pentagon-esper-briefing` | clip 02 FAIL | `end 461.50s splits ' them'/' What'`; `end 472.82s splits ' compound'/' Also,'` |

These are word-split failures, and the word-split fix lives in
`engine/edit_plan.py:: _word_aligned`, which runs **upstream** of where
`selections.json` is written — it moves the selection bounds themselves. The
`selections.json` on disk for these three still holds the pre-fix bounds, so
re-emitting captions from it reproduces the pre-fix captions byte-for-byte
(hence `unchanged` on all of `emwcon-panel-2018` and `ooi-vertical`). The
straddle fix, by contrast, is purely caption-side, which is why it *was*
recoverable this way — `pentagon-esper-briefing` clip 04 was rewritten and now
passes, its last word ` U` spanning 922.120–922.960 s against an end of
922.862 s.

So: the caption-straddle fix is confirmed on two sources. The word-split fix is
confirmed on one (`royal-society-cloke`, fully re-run). **The other three still
need real re-runs**, and no claim in this document says otherwise.

## The finding that matters more than the pass rate

**Three of the seven "OK" sources are not actually usable**, and the
mechanical audits cannot see it.

- `telugu-tutorial` produced exactly **1 clip** whose text is
  `నినాన్త్డందాన ంపెర్లిస్లిని. పాత్న్న్న్త్త్నవై సస్కకన్సి.` — fluent-looking
  Telugu script that is phonetic noise. It passes the caption-transcript
  equality check *because the check compares captions against the transcript,
  not against reality*. The transcript is wrong, so the captions faithfully
  reproduce a wrong transcript. Geometry, timing, EDL/FCPXML agreement: all
  pass. The clip is garbage.
- `hadhazy-protest` transcribes Hungarian into short fragments
  (`Azért akarjunk így, hogy nagyon jó.` — "That's why we want it this way,
  that it's very good") that are individually plausible Hungarian but, read
  in sequence, do not reconstruct what was said. 38 segments from 126 s of
  outdoor interview.
- `hindi-baiga-interview` got far enough to *fail*: 3 segments of
  low-quality Devanagari, and no complete standalone story passed the
  editorial gate.

This is the single most important result of the run, and it is the reason
`docs/BLINDED_PROTOCOL.md` refuses to let mechanical metrics stand in for
human judgment. **A machine audit of a bad transcript passes, because the
audit has no ground truth to compare against.** The WER criteria P8/P9 in
`docs/EVALUATION.md` — never measured, on either source — are the ones that
would have caught this, and this run is the evidence that they are not
academic.

The `small` model is inadequate for non-English at the quality the product
implies. That is a model-choice finding, measurable by re-running these three
sources at `medium` and `large-v3`, and it is the cheapest follow-up in this
document.

## The four failures, each diagnosed

**`telugu-launch` — NO_SPEECH_DETECTED, and the diagnosis is wrong.** The
source has speech. The `.ogv` container is structurally damaged (non-monotonic
DTS, mis-marked keyframes); a clean remux of the same file transcribes to 11
segments. The pipeline's "no audible spoken content" error therefore conflates
two unrelated causes — genuinely speechless media and an unreadable container —
and tells a user their source has no speech when it has plenty. This is the
most misleading failure mode in the product and it is still open.

**`roman-portrait-trailer` — NO_SPEECH_DETECTED, and this one is correct.**
The file is a music-and-motion reel with no spoken content. The pipeline
rejects it cleanly. This is the criterion-P12 case working as designed.

**`alice-1903` — NO_SPEECH_DETECTED, also correct.** A 1903 silent film.
Note the framing: this was filed under *difficult-audio* in the manifest, but
a silent film is not difficult audio, it is absent audio. The axis needs a
source with *degraded* speech, and `hadhazy-protest` is the one that supplies
it — `alice-1903` is a negative test that happens to sit in the wrong row.

**`hindi-baiga-interview` — NO_STORIES_PASSED.** Transcription succeeded (3
segments) but no candidate met the standalone-story gate. Whether that is the
gate correctly rejecting unusable material, or the gate being too strict for
a 54-second source with sparse speech, cannot be determined from this run —
it needs a human who speaks Hindi to read the transcript against the audio.

## Four real defects the corpus found, all fixed and pinned

1. **The rendered deliverable shipped with no audio stream.** On the OOI
   source (AV1/Opus WebM), input seeking past ~15 s dropped the audio stream
   on 7 of 8 sampled positions while the container provably held 7601 audio
   packets across all 152 s. ffmpeg reported no error; `engine/qa.py` caught
   it and failed the run. Fixed in `engine/render.py` by probing each part
   for audio and falling back to output seeking. Pinned by a test that fails
   on the pre-fix code.
2. **The audit's source-interval check was comparing the wrong thing.** It
   compared each EDL's in/out against the *selection span*, but the EDL
   describes the *retained intervals* — dead air is removed before export, so
   the first event legitimately starts after the selection start. On OOI
   clip 01 this reported a 26-frame disagreement about an export that was
   correct. Fixed in `tools/nle_check.py` to compare against the edit plan's
   retained span. Verified still-catches-real-errors by perturbing the plan.
3. **Every corpus EDL would have imported with every clip offline.** All
   seven OK sources shipped a repo-root-relative `* FROM CLIP:` inside a
   timeline that lives two directories below the repo root, so an NLE
   resolving it against the timeline's own directory finds nothing. The
   FCPXML alongside each one was correct — it has absolutised since an
   earlier run hit this exact failure — so the two formats disagreed about
   where the media was, and only the format nobody had opened was wrong.
   Fixed at the emitter (`_from_clip_path`, which refuses to ship a path it
   cannot make absolute) and at the audit, which now resolves a relative FROM
   CLIP against the timeline's directory instead of its own working
   directory. Two tests pin it; both fail on the pre-fix code. The shipped
   files are regenerated without a re-run by
   `tools/regenerate_timelines.py`, and the fact that every FCPXML came back
   byte-identical is the evidence that the re-emit reproduced the same
   timeline rather than a new one.
4. **The corpus harness never deleted the media it claimed to.**
   `shutil.rmtree` on a regular file is a silent no-op under
   `ignore_errors=True`, so `keep_media=False` left every source on disk and
   the `NOT RUN` branch above was guarding against a condition that never
   occurred. Fixed to `unlink()`, and `tests/test_corpus_run.py` asserts the
   deletion actually happens and — the reason this mattered — that the
   recorded frame rate outlives the media. That probe write was itself
   silently dropped on every re-run until the same session, because it was
   guarded on a directory `run_one` had just `rmtree`d.

## Determinism, re-verified on a fresh download

`nasa360-ep5` was run twice from two independent downloads of the same URL,
weeks of intervening commits apart. The selections are identical:

```
prior run : 414.300-439.160, 327.860-346.280, 654.940-671.380
corpus run: 414.300-439.160, 327.860-346.280, 654.940-671.380
```

Criterion P13 holds on a real re-run, not only in the unit test.

## What this run does not establish

- **No human has judged any of this.** Seven sources passing mechanical audits
  is evidence the machinery is sound across containers, frame rates, aspect
  ratios and languages. It is zero evidence that a human editor would ship
  any of these cuts. `docs/BLINDED_PROTOCOL.md` is the design that would
  settle it, and it has not been run.
- **WER is still unmeasured.** The non-English findings above are impressions
  from reading transcripts, not scores. Until P8/P9 are run against a
  ground-truth corpus, "the `small` model is inadequate" is an observation,
  not a measurement.
- **The Telugu container bug is unfixed.** The misdiagnosis described above
  is recorded, not repaired.
- **Frame rate coverage is still narrow.** The corpus added 25 fps
  (`royal-society-cloke`, `hindi-baiga-interview`, `emwcon-panel-2018`), 30 fps
  and 29.97 fps. 23.976, 50 and 59.94 remain untested, and no 4K/H.265 source
  was attempted — that axis is still open and is still the largest
  unquantified risk named in `docs/BUSINESS_MODEL.md` §9.
- **Six of the seven OK sources still ship broken EDLs.** The path fix is in
  the emitter, but the artifacts on disk were written by the old emitter, and
  only `royal-society-cloke` has been re-emitted — the one source whose media
  is still on disk. The other six carry relative `* FROM CLIP:` paths
  (`emwcon-panel-2018`, `hadhazy-protest`, `nasa360-ep5`, `ooi-vertical`,
  `pentagon-esper-briefing`, `telugu-tutorial`), and
  `tools/regenerate_timelines.py` refuses to run without the media, which is
  the correct refusal. Confirming them needs the media re-downloaded and the
  tool re-run, not a full pipeline re-run — but it does need the disk.
- **No NLE has opened any of this.** `edl media exists` passing is an audit
  statement about path resolution, not evidence an editor imported the
  sequence. It is stronger than it was — it now resolves the way an NLE does —
  but it is still a model of an import, not an import.

## Exact next actions, in priority order

1. Re-run the three non-English sources at `medium` and `large-v3` and
   measure whether the transcript quality that a human can verify improves.
   This is the cheapest test in this document and it addresses the finding
   that the audits cannot see.
2. Fix the no-speech error to distinguish an unreadable container from a
   speechless source — remux to a clean container and retry before reporting
   "no audible spoken content".
3. Re-download the six sources whose media is gone and re-emit their EDLs with
   `tools/regenerate_timelines.py`, so the shipped artifacts match the fixed
   emitter. No re-transcode needed; the media is the only thing missing, and
   it is needed one source at a time to stay inside the disk limit.
4. Score WER against `corpus/manifest.json`'s C9/C10 audio-only corpora
   (`docs/EVALUATION.md` §2.2), which are the ground truth the video sources
   lack.
5. Add a 23.976, a 50 and a 59.94 source, and one 4K/H.265 source.
