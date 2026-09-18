import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
for _f in list(sys.meta_path):
    if type(_f).__name__ == "_EditableFinder" and hasattr(_f, "MAPPING"):
        for _k in list(_f.MAPPING):
            _f.MAPPING[_k] = str(ROOT / _k)
        if hasattr(_f, "NAMESPACES"):
            for _k in list(_f.NAMESPACES):
                _f.NAMESPACES[_k] = [str(ROOT / _k)]
