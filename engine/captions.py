from __future__ import annotations
import os
from pathlib import Path
from models.core import Candidate


def ass_for_candidate(c: Candidate, out: Path, words_per_line: int = 6) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    words = c.text.split()
    duration = max(0.1, c.end - c.start)
    chunk = max(1, words_per_line)
    step = duration / max(1, (len(words) + chunk - 1) // chunk)
    lines = []
    for i in range(0, len(words), chunk):
        idx = i // chunk
        st = idx * step; en = min(duration, (idx + 1) * step)
        def ts(v: float) -> str:
            h = int(v // 3600); m = int(v % 3600 // 60); s = int(v % 60); cs = int((v - int(v))*100)
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
        text = " ".join(words[i:i+chunk]).replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ts(st)},{ts(en)},Default,,0,0,0,,{text}")
    # Noto Sans has broad Indic coverage. Override with SELEKYT_FONT if a local installation uses another font.
    font = os.getenv("SELEKYT_FONT", "Noto Sans")
    content = f"""[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},62,&H00FFFFFF,&H00FFFFFF,&H99000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,70,70,190,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n""" + "\n".join(lines) + "\n"
    out.write_text(content, encoding="utf-8")
    return out
