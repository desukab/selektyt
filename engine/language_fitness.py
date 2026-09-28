"""Language fitness: is the model that just ran one that can transcribe this
language?

The corpus measured faster-whisper ``small`` on reference corpora in seven
languages (``tools/wer_bench.py``, records in ``corpus/wer/runs/``):

    en   3.38%        es   8.58%        de  10.66%
    pl  11.01%        fr  13.59%        it  20.11%
    te  151.08%       te on medium  91.54%

English is the exception, not the rule, and Telugu is not a little worse — at
151% the output carries more error words than the reference has words, so a
transcript produced this way is worse than no transcript, and every deliverable
downstream of it (captions, selection, cut points) inherits the wrongness.

Nothing else in the pipeline sees this. The coverage gate compares the
transcript against the *audio* and is the right instrument for a different
question — it caught ``telugu-tutorial`` because that source happens to be
sparse, but a dense Telugu source would have a high coverage ratio, a
well-formed transcript and a garbage one, and ship with a success message. The
editorial gates read the transcript's text and cannot tell a fluent
hallucination from a faithful one. This module is the one check that asks
whether the *model* can do this *language*, and it asks it of a table measured
offline rather than of the transcript itself, because the transcript is the
thing under suspicion.

The bar is a cut point in a measured empty interval, not a calibration. Seven
languages measured, and nothing falls between Italian's 20.11% and Telugu's
91.54% — a 4.5x gap with no data in it. ``WER_BAR`` sits at 25%, inside that
gap with 1.2x of room below and 3.7x above. With seven points this is not
calibrated and is not presented as one; it is the same basis the coverage gate
uses, and it will move when more languages are measured. Italian is the
language the bar decides against, and that is stated rather than buried: at
20.11% WER and 4.53% CER its characters are mostly right even where its words
are not, which is why it lands on the usable side of a bar drawn on words.

Three properties the table forced on the loader:

* **A record is only a measurement if it has enough clips.** ``nasa-c4-small``
  scores English at WER 1.0000 on 2 clips — a reference corpus whose alignment
  failed, not a statement about English. Loaded naively it makes the most
  verified language in the table look unusable, and it would have been the
  difference between a gate that helps and one that breaks on its first real
  user. Records under ``MIN_CLIP_COUNT`` are excluded, and the exclusion is
  reported rather than silently averaged away.
* **A record is only a measurement of the decode the pipeline runs.**
  ``c9-small-beam5`` is a beam-size sweep, and its 4.05% is not the number a
  ``--model small`` user gets with the default beam. It won the en/small slot
  on filename order before this rule existed and made English look 20% worse
  than it is — an optimistic-looking table built from a record answering a
  different question. Records off ``PRODUCTION_BEAM`` are excluded too.
* **Duplicates are resolved by clip count.** ``verify-current-small`` and
  ``redteam-small-verify`` are re-runs of ``c9-small`` on the same corpus; they
  agree, but the loader takes the record with the most clips so a partial
  re-run cannot overwrite a full measurement — and the loser is named in the
  report's ``superseded`` list, because a re-run that *disagrees* with the
  measurement it replaces is the one record an auditor most needs to see, and
  naming only the records the rules refused would let it vanish.
* **No measurement is never a pass.** An unmeasured language yields
  ``not_measured``, which is reported as a caveat and never as ``usable``. A
  gate that reports *usable* for every language it has never heard of is the
  failure mode this module exists to remove.

What this still cannot see: WER is a property of the language-model pair, not
of this file. A source in a well-measured language can still be transcribed
wrong — heavy accent, crosstalk, music, a domain the references did not cover.
The verdict is a prior, and the coverage gate and the confidence gate are the
checks that listen to *this* audio. They are complements, not substitutes, and
this module deliberately does not try to do their jobs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The cut point. See the module docstring for the measured interval it sits in.
WER_BAR = 0.25

# Above this the transcript carries more error than the reference has words, so
# captions burned from it are strictly worse than captions absent. Refusing
# here is deliberate: the deliverable is worthless and the user should have to
# override consciously, not discover it in the captions.
WER_REFUSE = 1.0

# A record needs this many clips to be a language-level measurement. The record
# that forced this exists at 2.
MIN_CLIP_COUNT = 10

RUNS_DIR = "corpus/wer/runs"


@dataclass
class FitnessReport:
    verdict: str            # usable | poor | unusable | not_measured
    language: str
    model: str
    wer: float | None = None
    cer: float | None = None
    clip_count: int = 0
    record: str = ""        # the file the number came from
    # Other models measured on this language, best first, so a routing
    # suggestion is a measured fact and not a guess that a bigger model helps.
    alternatives: list[dict] = field(default_factory=list)
    # Records skipped for being too small to be a measurement, or for measuring
    # a decode the pipeline does not run, named so the exclusion is auditable
    # rather than silent.
    excluded: list[str] = field(default_factory=list)
    # Duplicates that lost the clip-count race to the record the table keeps.
    # Named separately from ``excluded`` because they passed every rule and were
    # still not used: a re-run that disagrees with the measurement it replaces
    # is the one record a reader most needs to see, and without this list it
    # vanishes the moment the table is built.
    superseded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def detail(self) -> str:
        """One sentence a user can act on, quoting only measured numbers."""
        if self.verdict == "not_measured":
            return (
                f"no WER measurement exists for language {self.language} on "
                f"model {self.model}, so this transcript is unverified against "
                f"any reference corpus — verify it by hand before shipping "
                f"captions derived from it."
            )
        measured = (
            f"model {self.model} measures WER {self.wer:.1%} (CER "
            f"{self.cer:.1%}, {self.clip_count} clips) on language "
            f"{self.language}"
        )
        if self.verdict == "usable":
            return f"{measured}, inside the {WER_BAR:.0%} usable bar."
        if self.verdict == "poor":
            return (
                f"{measured}, above the {WER_BAR:.0%} bar but not unusable — "
                f"the words are wrong often enough that the transcript needs "
                f"a human check before it ships."
            )
        # unusable: name the alternative only if a measured model clears the bar
        usable = [a for a in self.alternatives if a["wer"] <= WER_BAR]
        if usable:
            best = usable[0]
            return (
                f"{measured}, above {WER_REFUSE:.0%}: the transcript carries "
                f"more error than the source has words, so captions burned "
                f"from it would be wrong on balance. Re-run with --model "
                f"{best['model']}, which measures WER {best['wer']:.1%} on "
                f"this language."
            )
        return (
            f"{measured}, above {WER_REFUSE:.0%}: the transcript carries more "
            f"error than the source has words, so captions burned from it "
            f"would be wrong on balance. No model measured on this language "
            f"clears the bar ({', '.join(a['model'] + ' ' + format(a['wer'], '.1%') for a in self.alternatives) or 'none'}), "
            f"so a larger model is a hope rather than a fix — this language "
            f"needs a different transcription path, not a bigger one."
        )


# Records are only table entries when they measured the decode the pipeline
# actually runs. ``c9-small-beam5`` is a beam-size experiment, not a language
# measurement: it answers "does beam 5 help" and its 4.05% is not the number a
# ``--model small`` user will get. Loaded naively it won the en/small slot on
# filename order and made English look 20% worse than it is.
PRODUCTION_BEAM = 1

def _model_name(raw: str) -> str:
    """Normalise a record's model field to the size the CLI takes.

    ``c9-large-v3`` records the directory the model was loaded from rather than
    a size, so the table key would not match the ``--model large-v3`` a user
    would pass to get the same behaviour.
    """
    name = str(raw).strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    name = name.removeprefix("faster-whisper-")
    return name


def _scan_runs(runs_dir: Path | None = None) -> tuple[dict, list[str], list[str]]:
    """One pass over the records directory: the table, and the two audit lists.

    Both lists come out of the same walk as the table rather than from a second
    pass, so a rule added here cannot be applied in one place and forgotten in
    the other — which is how ``_excluded_records`` came to re-implement the
    loader's conditions by hand.
    """
    directory = Path(runs_dir) if runs_dir is not None else Path(RUNS_DIR)
    if not directory.is_dir():
        return {}, [], []
    table: dict[tuple[str, str], dict] = {}
    excluded: list[str] = []
    superseded: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # The coverage records in the same directory are lists of per-source
        # measurements with no language-level WER; the decode-experiment records
        # carry no ``wer`` at all. Either way they are not table entries, and
        # were never candidates, so they are not named in either list.
        if not isinstance(data, dict) or "wer" not in data:
            continue
        language = str(data.get("language") or "").strip().lower()
        model = _model_name(data.get("model"))
        clip_count = int(data.get("clip_count") or 0)
        beam_size = int(data.get("beam_size") or 0)
        if not language or not model:
            continue
        if clip_count < MIN_CLIP_COUNT:
            excluded.append(path.name)
            continue
        if beam_size != PRODUCTION_BEAM:
            excluded.append(path.name)
            continue
        key = (language, model)
        existing = table.get(key)
        # Resolve duplicates by clip count, not by filename order, so a partial
        # re-run of a corpus cannot overwrite the full measurement it replaced.
        if existing is None:
            table[key] = _entry(path.name, language, model, data, clip_count)
        elif clip_count > existing["clip_count"]:
            superseded.append(existing["record"])
            table[key] = _entry(path.name, language, model, data, clip_count)
        else:
            # A record that passed every rule and still did not make the table.
            # It is named rather than dropped, because a re-run that disagrees
            # with the number it replaces is the record an auditor most needs.
            superseded.append(path.name)
    return table, excluded, superseded


def _entry(record: str, language: str, model: str, data: dict, clip_count: int) -> dict:
    return {
        "language": language,
        "model": model,
        "wer": float(data["wer"]),
        "cer": float(data.get("cer") or 0.0),
        "clip_count": clip_count,
        "record": record,
    }


def load_table(runs_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Every ``(language, model)`` pair that has a real WER measurement.

    Records without a ``wer`` key are decode experiments, not quality
    measurements, and are skipped; records under ``MIN_CLIP_COUNT`` are probes
    and appear in the report's ``excluded`` list instead of the table; records
    off ``PRODUCTION_BEAM`` measure a decode the pipeline does not run. All of
    it is visible to the caller rather than folded in silently — a loader that
    quietly drops the records that disagree with it is how a table comes to
    support any conclusion.
    """
    return _scan_runs(runs_dir)[0]


def _excluded_records(runs_dir: Path | None = None) -> list[str]:
    """Records the rules refused: too few clips to be a language-level
    measurement, or a beam size other than the decode the pipeline runs."""
    return _scan_runs(runs_dir)[1]


def _superseded_records(runs_dir: Path | None = None) -> list[str]:
    """Duplicates that lost the clip-count race to the record the table keeps."""
    return _scan_runs(runs_dir)[2]


def fitness(
    language: str | None,
    model: str | None,
    runs_dir: Path | None = None,
) -> FitnessReport:
    """The measured verdict for the language-model pair that just ran.

    ``language`` is what the transcriber detected or was forced to; ``model`` is
    the size that actually ran, not the one requested — a silent downgrade to a
    smaller model can move a language from usable to unusable and the verdict
    has to follow the model that produced the words.
    """
    lang = (language or "").strip().lower()
    size = _model_name(model or "")
    _, excluded, superseded = _scan_runs(runs_dir)
    report = FitnessReport(
        verdict="not_measured", language=lang or "unknown",
        model=size or "unknown", excluded=excluded, superseded=superseded,
    )
    table, _, _ = _scan_runs(runs_dir)
    if not lang or not size or not table:
        return report

    # Every model measured on this language, best WER first, so the routing
    # suggestion in ``detail`` is a measured fact.
    alternatives = sorted(
        (v for (l, _m), v in table.items() if l == lang),
        key=lambda v: v["wer"],
    )
    report.alternatives = [
        {"model": v["model"], "wer": v["wer"], "cer": v["cer"],
         "clip_count": v["clip_count"], "record": v["record"]}
        for v in alternatives
    ]

    entry = table.get((lang, size))
    if entry is None:
        # The language is measured, just not on this model — the alternatives
        # list is the useful content, and the verdict stays not_measured rather
        # than inheriting another model's number.
        return report

    report.wer = entry["wer"]
    report.cer = entry["cer"]
    report.clip_count = entry["clip_count"]
    report.record = entry["record"]
    if entry["wer"] <= WER_BAR:
        report.verdict = "usable"
    elif entry["wer"] <= WER_REFUSE:
        report.verdict = "poor"
    else:
        report.verdict = "unusable"
    return report


def is_refusable(report: FitnessReport) -> bool:
    """Only an unusable verdict stops the run.

    ``poor`` warns rather than refusing, and the reason is not the usual one.
    The ``poor`` band — above the 25% bar, at or below 100% — has exactly one
    measured member: Telugu on ``medium`` at 91.54%. That transcript is badly
    wrong, not "mostly right", so the comforting version of this rationale
    (``poor`` is near enough for an editor) is not available here. Two things
    are:

    * the one measured ``poor`` pair has no measured escape. ``small`` is worse
      on the same language at 151.08%, so the alternatives list offers nothing
      above the bar — a refusal would be a dead end, not an actionable error,
      and a gate whose only ``poor`` case cannot be fixed by the fix it
      suggests is a gate that stops work for nothing.
    * the rest of the band is unmeasured, so placing a second cut point inside
      it would be guessing where the data is absent. Refusing on ground no
      measurement covers is the same error as passing on it; the bar is drawn
      where a measured empty interval lets it be drawn, and nowhere else.

    ``not_measured`` never refuses either, because refusing on absence would
    block every language the table has not covered, which is most of them.
    """
    return report.verdict == "unusable"
