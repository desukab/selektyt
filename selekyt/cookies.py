from __future__ import annotations

"""Convert a browser Cookie header into a local Netscape cookies.txt file.

The input is read from a local file or stdin and is never printed.  The
resulting cookies.txt is intended for yt-dlp's --cookies option.
"""

import argparse
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_DOMAIN = ".youtube.com"


def parse_cookie_header(header: str) -> list[tuple[str, str]]:
    """Parse a Cookie header into (name, value) pairs.

    Values are kept verbatim except surrounding whitespace.  Cookie headers
    are not decoded or reformatted beyond splitting on semicolons.
    """
    pairs: list[tuple[str, str]] = []
    for part in header.strip().split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name:
            pairs.append((name, value))
    return pairs


def to_netscape(pairs: list[tuple[str, str]], domain: str = DEFAULT_DOMAIN) -> str:
    """Create Netscape cookie-file text for yt-dlp."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated locally by selekyt. Keep this file private.",
        "",
    ]
    for name, value in pairs:
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def convert(input_path: Path, output_path: Path, domain: str = DEFAULT_DOMAIN) -> int:
    if not input_path.is_file():
        raise RuntimeError(f"Cookie header file not found: {input_path}")

    header = input_path.read_text(encoding="utf-8").strip()
    if header.lower().startswith("cookie:"):
        header = header.split(":", 1)[1].strip()

    pairs = parse_cookie_header(header)
    if not pairs:
        raise RuntimeError("No cookies found. Expected a browser Cookie header in the input file.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(to_netscape(pairs, domain), encoding="utf-8")
    try:
        output_path.chmod(0o600)
    except OSError:
        pass
    print(f"Converted {len(pairs)} cookies -> {output_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Convert a browser Cookie header to yt-dlp Netscape cookies.txt")
    p.add_argument("--input", help="Local text file containing the Cookie header")
    p.add_argument("--output", default="cookies.txt", help="Netscape output path (default: cookies.txt)")
    p.add_argument("--domain", default=DEFAULT_DOMAIN, help="Cookie domain (default: .youtube.com)")
    args = p.parse_args()

    input_path = Path(args.input).expanduser() if args.input else None
    if input_path is None:
        raw = input("Path to browser Cookie header file: ").strip()
        input_path = Path(raw).expanduser()

    output_path = Path(args.output).expanduser()
    return convert(input_path, output_path, args.domain)


if __name__ == "__main__":
    raise SystemExit(main())
