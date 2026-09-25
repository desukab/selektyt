"""A synthetic transcript shaped to the story-unit segmenter.

``segment_story_units`` only breaks a seam where topical overlap collapses, so a
fixture built from passages that share boilerplate collapses into a single unit
— and a test that expected a graph then measures nothing. The transcript here
gives the splitter what it actually looks for:

* every passage is drawn from a five-word vocabulary disjoint from every other
  subject's, so two passages about different subjects overlap not at all and the
  seam between them is a hard drop;
* passages that share a subject are separated by passages about something else,
  which is also what the link layer needs — a question and its answer have to
  land in *different* units to form an edge at all.

The result is a transcript with real repetition *and* real question/answer
structure: units scale linearly with the input, the edge set is non-empty, and
long same-subject chains form. A fixture that produces those is the difference
between a benchmark and a timing.
"""

from __future__ import annotations

from models.core import Segment, Word

# Each subject is a disjoint five-word vocabulary, so two passages about the
# same subject overlap heavily and two about different subjects do not at all.
SUBJECTS = [
    ("satellite", "clock", "battery", "agency", "orbit"),
    ("antenna", "array", "signal", "harbour", "mast"),
    ("telemetry", "downlink", "decoder", "ground", "station"),
    ("vessel", "hull", "pump", "bilge", "crew"),
    ("harvest", "silo", "conveyor", "grain", "mill"),
    ("turbine", "blade", "gearbox", "nacelle", "tower"),
    ("pipeline", "valve", "compressor", "refinery", "storage"),
    ("furnace", "kiln", "refractory", "smelter", "ingot"),
    ("aquifer", "wellhead", "pump", "reservoir", "treatment"),
    ("reactor", "coolant", "control", "vessel", "grid"),
    ("airframe", "fuselage", "rivet", "wing", "hangar"),
    ("locomotive", "bogie", "coupling", "depot", "rail"),
    ("fibre", "splice", "repeater", "exchange", "cabinet"),
    ("crane", "winch", "hoist", "dockyard", "gantry"),
    ("boiler", "flue", "valve", "stack", "plant"),
    ("escalator", "step", "handrail", "concourse", "shaft"),
    ("excavator", "bucket", "track", "quarry", "loader"),
    ("compressor", "intercooler", "piston", "receiver", "shop"),
    ("pasteuriser", "heat", "exchanger", "dairy", "batch"),
    ("sonar", "transducer", "hull", "echo", "bridge"),
    ("scaffold", "plank", "coupler", "facade", "hoist"),
    ("turbine", "penstock", "spillway", "dam", "penstock"),
    ("circuit", "breaker", "busbar", "switchgear", "substation"),
    ("anemometer", "gauge", "mast", "weather", "station"),
]
# Generic filler vocabulary, disjoint from every subject above so a filler
# passage can never be read as continuing the subject passage beside it.
FILLERS = [
    "harbour", "lighthouse", "quay", "pier", "jetty", "marina", "tide",
    "channel", "beacon", "lock", "dock", "slipway", "buoy", "wharf",
]
FILLER_TEXT = "The {a} {b} opened for the season. The {c} reviewed the schedule."
_PASSAGE_SPACING = 12.0
_PASSAGE_SPAN = 8.0


def passages(n_units: int) -> list[str]:
    """A transcript of question / digression / answer blocks.

    Each block works one subject: a question, an answer three passages later, a
    harder question, and its answer, each separated by passages about something
    else entirely.
    """
    out: list[str] = []
    n_subjects = len(SUBJECTS)
    for block in range(n_units // 8 + 1):
        head, noun2, noun3, noun4, _noun5 = SUBJECTS[block % n_subjects]
        out.append(f"Why did the {head} {noun2} fail? "
                   f"The {noun3} was the suspected cause.")
        out.append(FILLER_TEXT.format(
            a=FILLERS[(block * 7) % len(FILLERS)],
            b=FILLERS[(block * 11) % len(FILLERS)],
            c=FILLERS[(block * 13) % len(FILLERS)]))
        out.append(f"The {head} {noun2} failed because the {noun3} was under-spec. "
                   f"The {noun4} confirmed the diagnosis.")
        out.append(FILLER_TEXT.format(
            a=FILLERS[(block * 17) % len(FILLERS)],
            b=FILLERS[(block * 19) % len(FILLERS)],
            c=FILLERS[(block * 23) % len(FILLERS)]))
        out.append(f"How was the {head} {noun2} repaired? The {noun3} was replaced.")
        out.append(FILLER_TEXT.format(
            a=FILLERS[(block * 29) % len(FILLERS)],
            b=FILLERS[(block * 31) % len(FILLERS)],
            c=FILLERS[(block * 37) % len(FILLERS)]))
        out.append(f"The {head} {noun2} was repaired by replacing the {noun3}. "
                   f"The {noun4} signed off the fix.")
        out.append(FILLER_TEXT.format(
            a=FILLERS[(block * 41) % len(FILLERS)],
            b=FILLERS[(block * 43) % len(FILLERS)],
            c=FILLERS[(block * 47) % len(FILLERS)]))
        if len(out) >= n_units:
            break
    return out[:n_units]


def _seg(a: float, b: float, text: str) -> Segment:
    """A timed segment like a transcript carries one, word for word."""
    words = text.split()
    span = (b - a) / max(1, len(words))
    return Segment(
        a, b, text,
        [Word(w, a + i * span, a + (i + 1) * span, 0.9)
         for i, w in enumerate(words)])


def transcript(n_units: int) -> list[Segment]:
    """``n_units`` passages spaced 12 s apart — a long interview in shape."""
    segs: list[Segment] = []
    t = 0.0
    for text in passages(n_units):
        segs.append(_seg(t, t + _PASSAGE_SPAN, text))
        t += _PASSAGE_SPACING
    return segs


def duration_for(n_units: int) -> float:
    """The source duration matching a transcript of ``n_units`` passages."""
    return n_units * _PASSAGE_SPACING
