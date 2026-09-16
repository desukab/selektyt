"""Cookie-header -> Netscape conversion, and the privacy contract.

The converter's promise is that credentials stay local: the header is read
from a file/stdin and written to a 0600 cookies.txt, never echoed to stdout
or logs. These pin the parsing rules and that promise.
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest

from selekyt.cookies import convert, parse_cookie_header, to_netscape, main


def test_parse_splits_on_semicolons():
    header = "SID=abc123; HSID=def456;  APISID=ghi789"
    assert parse_cookie_header(header) == [
        ("SID", "abc123"), ("HSID", "def456"), ("APISID", "ghi789"),
    ]


def test_parse_keeps_values_verbatim():
    """Values are not URL-decoded or reformatted — only whitespace trimmed."""
    assert parse_cookie_header("a=b%3Dc==") == [("a", "b%3Dc==")]
    assert parse_cookie_header("a= spaced ") == [("a", "spaced")]


def test_parse_skips_malformed_entries():
    assert parse_cookie_header("=novalue; novalue; ; a=b; ") == [("a", "b")]


def test_parse_empty_header():
    assert parse_cookie_header("") == []


def test_to_netscape_shape():
    text = to_netscape([("SID", "abc")], domain=".youtube.com")
    lines = text.splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert lines[2] == ""
    assert lines[3] == ".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tabc"


def test_to_netscape_neutralizes_field_breaking_chars():
    """A tab or newline in a value would add spurious fields to the
    tab-delimited line and make yt-dlp misparse the cookie."""
    for name, value in [("a", "b\tc"), ("a\nx", "ok"), ("a", "b\nc")]:
        line = to_netscape([(name, value)]).splitlines()[3]
        assert line.count("\t") == 6, f"field count broke for {value!r}"
        assert "\n" not in line


def test_convert_strips_the_cookie_prefix(tmp_path):
    src = tmp_path / "header.txt"
    src.write_text("Cookie: SID=abc; HSID=def", encoding="utf-8")
    out = tmp_path / "cookies.txt"
    assert convert(src, out) == 0
    body = out.read_text(encoding="utf-8")
    assert "SID\tabc" in body
    assert "HSID\tdef" in body


def test_convert_sets_0600_permissions(tmp_path):
    src = tmp_path / "header.txt"
    src.write_text("SID=abc", encoding="utf-8")
    out = tmp_path / "sub" / "cookies.txt"
    convert(src, out)
    mode = stat.S_IMODE(out.stat().st_mode)
    assert mode == 0o600, f"credentials must be 0600, got {oct(mode)}"


def test_convert_never_prints_values(tmp_path, capsys):
    """The privacy contract: the report line names counts and paths only."""
    src = tmp_path / "header.txt"
    src.write_text("SECRET_SESSION_VALUE=do-not-leak", encoding="utf-8")
    convert(src, tmp_path / "cookies.txt")
    captured = capsys.readouterr()
    assert "do-not-leak" not in captured.out
    assert "1 cookies" in captured.out


def test_convert_missing_input_raises(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        convert(tmp_path / "nope.txt", tmp_path / "cookies.txt")


def test_convert_empty_header_raises(tmp_path):
    src = tmp_path / "header.txt"
    src.write_text("Cookie:", encoding="utf-8")
    with pytest.raises(RuntimeError, match="No cookies found"):
        convert(src, tmp_path / "cookies.txt")


def test_cli_main_end_to_end(tmp_path, monkeypatch, capsys):
    src = tmp_path / "header.txt"
    src.write_text("SID=abc; HSID=def", encoding="utf-8")
    out = tmp_path / "cookies.txt"
    monkeypatch.setattr("sys.argv", ["selekyt-cookies", "--input", str(src), "--output", str(out)])
    assert main() == 0
    assert out.is_file()
    assert "do-not-leak" not in capsys.readouterr().out
