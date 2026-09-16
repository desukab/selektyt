"""Report system tests."""
import json
import time
from engine.report import (
    ReportBuilder, write_report_json, terminal_summary, write_terminal_summary,
)


def test_report_all_sections():
    b = ReportBuilder()
    b.set_provenance(source_path="a.mp4", whisper_model="small")
    b.set_config({"silence_threshold": 0.5})
    b.set_analysis(speech_ratio=0.6)
    b.set_candidates(count=5)
    b.set_editing(edited_duration=120.0)
    b.set_output(shorts="/tmp/x")
    b.set_qa(status="pass")
    d = b.to_dict()
    assert d["provenance"]["source_path"] == "a.mp4"
    assert d["config"]["silence_threshold"] == 0.5
    assert d["analysis"]["speech_ratio"] == 0.6
    assert d["candidates"]["count"] == 5
    assert d["editing"]["edited_duration"] == 120.0
    assert d["output"]["shorts"] == "/tmp/x"
    assert d["qa"]["status"] == "pass"


def test_report_provenance_autofill():
    b = ReportBuilder()
    d = b.to_dict()
    p = d["provenance"]
    assert p["started_at"]
    assert p["platform"]
    assert p["python"]
    assert p["software_version"]
    assert isinstance(p["duration_seconds"], float) or p["duration_seconds"] == 0.0


def test_finalize_records_duration():
    b = ReportBuilder(started_at=time.time() - 5)
    b.finalize(finished_at=time.time())
    d = b.to_dict()
    assert abs(d["provenance"]["duration_seconds"] - 5.0) < 2.0


def test_write_report_json(tmp_path):
    b = ReportBuilder()
    b.set_provenance(source_path="a.mp4")
    b.set_editing(edited_duration=10)
    out = write_report_json(b, tmp_path / "report.json")
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["provenance"]["source_path"] == "a.mp4"
    assert data["editing"]["edited_duration"] == 10


def test_write_report_creates_parent(tmp_path):
    b = ReportBuilder()
    out = write_report_json(b, tmp_path / "a" / "b" / "report.json")
    assert out.exists()


def test_terminal_summary_contains_key_fields():
    b = ReportBuilder()
    b.set_provenance(source_path="x.mp4", whisper_model="tiny")
    b.set_candidates(count=3)
    b.set_editing(edited_duration=50.0, compression_ratio=0.5)
    s = terminal_summary(b)
    assert "x.mp4" in s
    assert "tiny" in s
    assert "compression 0.5" in s


def test_write_terminal_summary(tmp_path):
    b = ReportBuilder()
    b.set_provenance(source_path="x.mp4")
    out = write_terminal_summary(b, tmp_path / "summary.txt")
    assert out.exists()
    assert "x.mp4" in out.read_text()


def test_unknown_setter_ignored():
    b = ReportBuilder()
    b.set_provenance(not_a_real_field=1)
    d = b.to_dict()
    assert "not_a_real_field" not in d["provenance"]