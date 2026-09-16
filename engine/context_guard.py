"""Multilingual context guard — validates standalone editorial suitability."""
from __future__ import annotations
import re
from models.core import Candidate

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

_ANAPHORA: set[str] = {
    "it", "he", "she", "they", "we", "you", "this", "that", "these", "those",
    "ఇది", "అది", "అతను", "ఆమె", "వారు", "మనం", "నువ్వు", "ఇవి", "ఆవి",
    "यह", "वह", "वे", "हम", "तुम", "इस", "उस", "ये",
    "हे", "ते", "ती", "ही", "आम्ही", "तू", "तुम्ही",
    "அது", "இது", "அவர்", "அவள்", "நாங்கள்", "நீங்கள்", "இவை", "அவை",
    "ಅದು", "ಇದು", "ಅವರು", "ಅವಳು", "ನಾವು", "ನೀವು", "ಇವು",
    "അത്", "ഇത്", "അവൻ", "അവൾ", "ഞങ്ങൾ", "നിങ്ങൾ",
    "এটা", "ও", "সে", "তারা", "আমরা", "আপনারা", "এই", "ঐ",
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


def validate(c: Candidate) -> tuple[bool, str]:
    """Validate a candidate for standalone editorial publication.

    Rules:
      - confidence and context_score thresholds
      - reject unsupported absolute claims (multilingual)
      - reject clips starting mid-conversation (connectives)
      - reject clips whose opening anaphoric reference depends on prior context
    """
    if c.confidence < 0.75:
        return False, "low factual confidence"
    if c.context_score < 0.70:
        return False, "insufficient standalone context"
    abs_found = _find_absolute(c.text) or _find_absolute(c.hook)
    if _unsupported_absolute(c):
        return False, f"unsupported absolute claim outside clip: {'; '.join(abs_found)}"
    connective = _starts_with_connective(c.text)
    if connective:
        return False, f"starts mid-conversation ({connective}) — depends on prior context"
    if _starts_with_anaphora(c.text) and len(c.text.split()) < 25:
        return False, "starts with anaphoric reference — requires prior context"
    return True, "ok"
