"""Multilingual context guard — validates standalone editorial suitability."""
from __future__ import annotations
import re
from models.core import Candidate
from engine.story_units import boilerplate_fraction

_SCRIPT_RANGES: dict[str, str] = {
    "telugu": r"[\u0C00-\u0C7F]",
    "hindi": r"[\u0900-\u097F]",
    "tamil": r"[\u0B80-\u0BFF]",
    "kannada": r"[\u0C80-\u0CFF]",
    "malayalam": r"[\u0D00-\u0D7F]",
    "bengali": r"[\u0980-\u09FF]",
}

_ABSOLUTES: dict[str, list[str]] = {
    "english": ["nobody", "no one", "everyone", "always", "never", "the world",
                "secret", "nothing", "everything", "forever"],
    "telugu": ["ఎవరూ", "అందరూ", "ఎప్పుడూ", "రహస్యం", "ఎప్పటికీ", "ఏమీ", "అన్నీ"],
    "hindi": ["कोई नहीं", "सब लोग", "हमेशा", "कभी नहीं", "रहस्य", "कुछ नहीं", "सब कुछ"],
    "marathi": ["कोणीही नाही", "सर्व", "नेहमी", "रहस्य", "काहीही नाही"],
    "tamil": ["யாரும்", "எல்லோரும்", "எப்போதும்", "ரகசியம்", "ஒன்றும்", "எல்லாம்"],
    "kannada": ["ಯಾರೂ", "ಎಲ್ಲರೂ", "ಯಾವಾಗಲೂ", "ರಹಸ್ಯ", "ಏನೂ", "ಎಲ್ಲವೂ"],
    "malayalam": ["ആരും", "എല്ലാവരും", "എപ്പോഴും", "രഹസ്യം", "ഒന്നും", "എല്ലാം"],
    "bengali": ["কেউ নেই", "সবাই", "সবসময়", "গোপন", "কিছুই", "সবকিছু"],
}

_CONNECTIVES: set[str] = {
    "because", "but", "and", "so", "then", "also", "which", "therefore", "however",
    "అందుకే", "అందువల్ల", "కానీ", "అయితే", "తర్వాత", "అందుకోసం", "అలా",
    "लेकिन", "और", "इसलिए", "तो", "क्योंकि", "अतः", "फिर",
    "पण", "आणि", "त्यामुळे", "म्हणून", "मात्र",
    "ஆனால்", "எனவே", "அதனால்", "பின்னர்", "மற்றும்",
    "ಆದರೆ", "ಆದ್ದರಿಂದ", "ಅಂದುಕೊಂಡು", "ನಂತರ", "ಆಮೇಲೆ",
    "എന്നാൽ", "അതിനാൽ", "അപ്പോൾ", "പക്ഷേ", "ഉത്തരം",
    "কিন্তু", "কারণ", "তাই", "অতএব", "তারপর",
}

# Third-person referents need an antecedent the clip does not supply: "It
# moved forty percent" or "They celebrated" is ungrounded without the sentence
# the clip cut off. First- and second-person pronouns are deictic — "we" and
# "you" point at the speaker/viewer, so "We shipped the feature" stands alone.
_ANAPHORA: set[str] = {
    "it", "he", "she", "they", "this", "that", "these", "those", "its", "their",
    "ఇది", "అది", "అతను", "ఆమె", "వారు", "ఇవి", "ఆవి",
    "यह", "वह", "वे", "इस", "उस", "ये",
    "हे", "ते", "ती", "ही",
    "அது", "இது", "அவர்", "அவள்", "இவை", "அவை",
    "ಅದು", "ಇದು", "ಅವರು", "ಅವಳು", "ಇವು",
    "അത്", "ഇത്", "അവൻ", "അവൾ",
    "এটা", "ও", "সে", "তারা", "এই", "ঐ",
}


def detect_languages(text: str) -> list[str]:
    """Detect which languages are present by Unicode script ranges."""
    counts: dict[str, int] = {}
    for lang, pattern in _SCRIPT_RANGES.items():
        c = len(re.findall(pattern, text))
        if c > 0:
            counts[lang] = c
    latin = len(re.findall(r"[A-Za-z]", text))
    if latin > 0:
        counts["english"] = latin
    result: set[str] = set()
    for lang, count in counts.items():
        if count < 2:
            continue
        if lang == "hindi":
            result.update({"hindi", "marathi"})
        else:
            result.add(lang)
    return sorted(result)


def _find_absolute(text: str) -> list[str]:
    """Find absolute-claim tokens present in text (language-agnostic)."""
    text_lower = text.lower()
    return sorted(
        {token for tokens in _ABSOLUTES.values() for token in tokens if token.lower() in text_lower}
    )


def _unsupported_absolute(c: Candidate) -> bool:
    """Is an absolute claim present but unsupported by sufficient context?"""
    return bool(_find_absolute(c.text) or _find_absolute(c.hook)) and len(c.text.split()) < 20


def _first_token(text: str) -> str:
    tokens = text.strip().split()
    return tokens[0].lower() if tokens else ""


def _starts_with_connective(text: str) -> str | None:
    first = _first_token(text)
    if first in _CONNECTIVES:
        return first
    parts = text.strip().split()[:2]
    two = " ".join(p.lower() for p in parts)
    return two if two in _CONNECTIVES else None


def _starts_with_anaphora(text: str) -> bool:
    return _first_token(text) in _ANAPHORA


_NP_HEADS = (
    "modernization", "introduction", "deployment", "implementation",
    "development", "creation", "establishment", "construction", "expansion",
    "adoption", "release", "launch", "rollout", "upgrade", "integration",
    "transition", "migration", "consolidation", "origins",
    "history", "beginnings", "beginning", "origin",
)
_NP_OPEN = re.compile(
    r"^(?:the\s+)?(?:" + "|".join(_NP_HEADS) + r")\s+of\b",
    re.IGNORECASE,
)
# A finite verb is the difference between a complete sentence and a noun
# phrase: "The origins of GPS began…" is a sentence, "modernization of the
# infrastructure…" is a fragment waiting for one.
_FINITE_VERB = re.compile(
    r"\b(?:is|are|was|were|has|have|had|began|started|changed|"
    r"created|introduced|deployed|implemented|developed|launched|released|"
    r"expanded|adopted|integrated|migrated|consolidated|established|"
    r"constructed|shipped|completed|improved|replaced|enabled|allowed|"
    r"required|caused|led|made|took|went|came|became|remained|seems|"
    r"appears|looks|feels|works|operates|runs|uses|needs|wants|"
    r"supports|provides|offers|delivers|powers|drives|shapes|forms)\b",
    re.IGNORECASE,
)


def _opens_with_dangling_subject(text: str) -> bool:
    """An opening noun phrase with no finite verb continues a prior sentence.

    "modernization of the infrastructure or the introduction of a new
    protocol." is the tail of "…could be a possible X or Y": the subject and
    verb live in the sentence the clip cut off. Such an opening cannot stand
    alone no matter how clean the rest of the clip is. "The origins of GPS
    began…" has a verb and is a sentence, so it is left alone.
    """
    first = text.strip().split(".")[0].strip()
    if not _NP_OPEN.match(first):
        return False
    return not _FINITE_VERB.search(first)


def validate(c: Candidate) -> tuple[bool, str]:
    """Validate a candidate for standalone editorial publication.

    Rules:
      - confidence and context_score thresholds
      - reject clips dominated by article boilerplate (byline, issue, date)
      - reject unsupported absolute claims (multilingual)
      - reject clips starting mid-conversation (connectives)
      - reject clips whose opening anaphoric reference depends on prior context
    """
    if c.confidence < 0.75:
        return False, "low factual confidence"
    if c.context_score < 0.70:
        return False, "insufficient standalone context"
    bp_frac, bp_kinds = boilerplate_fraction(c.text)
    if bp_frac >= 0.35:
        return False, f"dominated by article boilerplate ({', '.join(bp_kinds)})"
    if bp_frac >= 0.18 and c.coherence_score < 0.5:
        return False, f"boilerplate inside an incoherent span ({', '.join(bp_kinds)})"
    abs_found = _find_absolute(c.text) or _find_absolute(c.hook)
    if _unsupported_absolute(c):
        return False, f"unsupported absolute claim outside clip: {'; '.join(abs_found)}"
    connective = _starts_with_connective(c.text)
    if connective:
        return False, f"starts mid-conversation ({connective}) — depends on prior context"
    if _starts_with_anaphora(c.text) and len(c.text.split()) < 25:
        return False, "starts with anaphoric reference — requires prior context"
    if c.coherence_score < 0.20:
        return False, f"spans unrelated stories (coherence {c.coherence_score:.2f})"
    if c.completeness < 0.50:
        return False, (
            f"opens or closes inside a sentence (completeness {c.completeness:.2f}) "
            "— the clip inherits a subject it never introduces"
        )
    if _opens_with_dangling_subject(c.text):
        return False, "opens with a subjectless noun phrase — continuation of a cut-off sentence"
    return True, "ok"
