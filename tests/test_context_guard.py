"""Tests for the multilingual context guard."""
from models.core import Candidate, Word


def _make(hook: str, text: str, context_score: float = 0.8, duration: float = 10.0) -> Candidate:
    return Candidate(0, duration, text, "insight", hook, text[-50:],
                     0.7, 0.6, context_score, 0.9, "test", 0.5, [])


def test_rejects_english_absolute():
    from engine.context_guard import validate
    c = _make("Nobody knows this secret", "Nobody knows this secret and nobody cares.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_rejects_telugu_absolute():
    from engine.context_guard import validate
    c = _make("ఎవరూ ఈ రహస్యం తెలియదు", "ఎవరూ ఈ రహస్యం తెలియదు మరియు ఎవరూ పట్టించుకోరు.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_rejects_hindi_absolute():
    from engine.context_guard import validate
    c = _make("कोई नहीं जानता यह रहस्य", "कोई नहीं जानता यह रहस्य और कोई परवाह नहीं करता.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_rejects_marathi_absolute():
    from engine.context_guard import validate
    c = _make("कोणीही नाही जाणत हे रहस्य", "कोणीही नाही जाणत हे रहस्य आणि कोणीही काळजी घेत नाही.")
    ok, reason = validate(c)
    assert not ok


def test_rejects_tamil_absolute():
    from engine.context_guard import validate
    c = _make("யாரும் இந்த இரகசியத்தை அறிய மாட்டார்கள்", "யாரும் இந்த இரகசியத்தை அறிய மாட்டார்கள் மற்றும் யாரும் கவலைப்பட மாட்டார்கள்.")
    ok, reason = validate(c)
    assert not ok


def test_rejects_kannada_absolute():
    from engine.context_guard import validate
    c = _make("ಯಾರಿಗೂ ಈ ರಹಸ್ಯ ತಿಳಿದಿಲ್ಲ", "ಯಾರಿಗೂ ಈ ರಹಸ್ಯ ತಿಳಿದಿಲ್ಲ ಮತ್ತು ಯಾರಿಗೂ ಚಿಂತೆಯಿಲ್ಲ.")
    ok, reason = validate(c)
    assert not ok


def test_rejects_malayalam_absolute():
    from engine.context_guard import validate
    c = _make("ആർക്കും ഈ രഹസ്യം അറിയില്ല", "ആർക്കും ഈ രഹസ്യം അറിയില്ല ഒപ്പം ആർക്കും കാര്യമാക്കുന്നുമില്ല.")
    ok, reason = validate(c)
    assert not ok


def test_rejects_bengali_absolute():
    from engine.context_guard import validate
    c = _make("কেউ এই গোপন কথা জানে না", "কেউ এই গোপন কথা জানে না এবং কেউ চিন্তাও করে না.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_pass_absolute_with_sufficient_context():
    from engine.context_guard import validate
    c = _make("Always check your data", "Always check your data because the numbers move across every quarter and we have seen it happen repeatedly over the entire period we studied.")
    ok, reason = validate(c)
    assert ok, reason


def test_passes_normal_non_sensational():
    from engine.context_guard import validate
    c = _make("The real explanation", "Here is the real explanation with enough context for standalone comprehension.")
    ok, _ = validate(c)
    assert ok


def test_rejects_connective_start_depends_on_prior_context():
    from engine.context_guard import validate
    c = _make("Because of what we discussed", "Because of what we discussed earlier, the whole thing fell apart in the end.")
    ok, reason = validate(c)
    assert not ok and "context" in reason


def test_rejects_mid_sentence_conversation_start_telugu():
    from engine.context_guard import validate
    c = _make("అందుకే ఇది జరిగింది", "అందుకే ఇది జరిగింది మరియు దాని ప్రభావం చాలా పెద్దది.")
    ok, reason = validate(c)
    assert not ok and "context" in reason


def test_passes_code_switched_non_sensational():
    from engine.context_guard import validate
    c = _make("ఈ విషయం చాలా ముఖ్యం", "ఈ విషయం చాలా ముఖ్యం because it explains how the entire system behaves in practice.")
    ok, reason = validate(c)
    assert ok, reason


def test_code_switch_still_catches_absolute():
    from engine.context_guard import validate
    c = _make("ఎవరూ ఇది చేయలేరు", "ఎవరూ ఇది చేయలేరు and nobody has ever tried before in the history of the thing.")
    ok, reason = validate(c)
    assert not ok and "absolute" in reason


def test_rejects_low_context_score():
    from engine.context_guard import validate
    c = _make("quick", "A short fragment with no context whatsoever.", context_score=0.4)
    ok, reason = validate(c)
    assert not ok and "context" in reason


def test_rejects_when_context_outside_clip_changes_meaning():
    from engine.context_guard import validate
    c = _make("అది తర్వాత మారిపోయింది", "అది తర్వాత మారిపోయింది మరియు దాని వల్ల చాలా మంది ప్రభావితమయ్యారు.", duration=8.0)
    ok, reason = validate(c)
    assert not ok and "context" in reason

def test_rejects_incomplete_opening():
    from engine.context_guard import validate
    c = _make("modernization of the infrastructure",
              "modernization of the infrastructure or the introduction of "
              "a new protocol. Inland marine operations have historically "
              "struggled with limited connectivity.",
              context_score=0.9, duration=12.0)
    c.completeness = 0.35
    ok, reason = validate(c)
    assert not ok and "completeness" in reason


def test_rejects_dangling_subject_opening():
    # "modernization of the infrastructure…" is the tail of a cut-off
    # "could be a possible X or Y": no subject, no verb, no standalone value.
    from engine.context_guard import validate
    c = _make("modernization of the infrastructure",
              "modernization of the infrastructure or the introduction of "
              "a new protocol. Inland marine operations have historically "
              "struggled with limited connectivity.",
              context_score=0.9, duration=12.0)
    c.completeness = 0.9
    ok, reason = validate(c)
    assert not ok and "subjectless" in reason


def test_accepts_opening_with_a_verb():
    from engine.context_guard import validate
    c = _make("The origins of GPS began in the early 1970s.",
              "The origins of GPS began in the early 1970s. The agency then "
              "extended the operational contract across the continent.",
              context_score=0.9, duration=12.0)
    c.completeness = 0.9
    ok, reason = validate(c)
    assert ok, reason


def test_rejects_story_splice():
    from engine.context_guard import validate
    c = _make("The satellite network", "sourdough baking", context_score=0.9)
    c.coherence_score = 0.15
    ok, reason = validate(c)
    assert not ok and "coherence" in reason


def test_accepts_first_person_opening():
    # "we" is deictic, not anaphoric: the speaker grounds it, so the clip
    # needs no prior sentence. Third-person "it changed everything" does.
    from engine.context_guard import validate, _starts_with_anaphora

    assert _starts_with_anaphora("It moved forty percent overnight.")
    assert not _starts_with_anaphora("We shipped the feature on Tuesday morning.")
    assert not _starts_with_anaphora("You know the moment the metric moved.")

    c = _make("We shipped the feature on Tuesday morning.",
              "We shipped the feature on Tuesday morning. The metric moved "
              "forty percent overnight.", context_score=0.9, duration=9.0)
    c.completeness = 0.9
    ok, reason = validate(c)
    assert ok, reason

    c2 = _make("It moved forty percent overnight.",
               "It moved forty percent overnight. The team celebrated "
               "the launch.",
               context_score=0.9, duration=9.0)
    c2.completeness = 0.9
    ok2, reason2 = validate(c2)
    assert not ok2 and "anaphoric" in reason2
