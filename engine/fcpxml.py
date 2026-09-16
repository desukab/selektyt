"""FCPXML export — Final Cut Pro XML format from CutList.

Produces FCPXML 1.11 documents that can be imported into Final Cut Pro
to visualize or further edit the SelekyT cut decisions.

The export is optional and behind the --export-fcpxml flag.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from engine.jumpcut import CutList, SegmentType


def export_fcpxml(cutlist: CutList, output: str | Path, source_path: str = "") -> Path:
    """Export a CutList as FCPXML 1.11.

    Creates an event with a single project containing spine of KEEP
    segments and skip markers for REMOVE segments.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    fcpxml = ET.Element("fcpxml", version="1.11")
    resources = ET.SubElement(fcpxml, "resources")
    format_el = ET.SubElement(resources, "format",
                              id="r1", name="FFVideoFormat1080p2997",
                              frameDuration="1/30000s", width="1920", height="1080")
    asset = ET.SubElement(resources, "asset",
                          id="r2", name="source",
                          src=source_path,
                          hasAudio="1", hasVideo="1",
                          duration=f"{cutlist.original_duration}s")

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name="SelekyT Edit")
    project = ET.SubElement(event, "project", name="SelekyT Output")

    spine = ET.SubElement(project, "spine")

    for seg in cutlist.segments:
        if seg.segment_type == SegmentType.KEEP:
            clip = ET.SubElement(spine, "clip",
                                 name=f"keep_{seg.start:.2f}_{seg.end:.2f}",
                                 offset=f"{seg.start}s",
                                 duration=f"{seg.duration}s",
                                 start=f"{seg.start}s")
            clip.set("ref", "r2")
        else:
            marker = ET.SubElement(spine, "marker",
                                   value=seg.segment_type.value,
                                   start=f"{seg.start}s",
                                   duration=f"{seg.duration}s")
            comment = ET.SubElement(marker, "comment")
            comment.text = f"{seg.reason} ({seg.duration:.2f}s)"

    tree = ET.ElementTree(fcpxml)
    ET.indent(tree, space="  ")
    tree.write(str(output), encoding="unicode", xml_declaration=True)
    return output


def cutlist_to_simple_xml(cutlist: CutList, output: str | Path) -> Path:
    """Export a simplified XML cutlist for tool-agnostic consumption."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    root = ET.Element("cutlist", {
        "original_duration": f"{cutlist.original_duration:.3f}",
        "keep_duration": f"{cutlist.total_keep_duration:.3f}",
        "remove_duration": f"{cutlist.total_remove_duration:.3f}",
        "compression_ratio": f"{cutlist.compression_ratio:.3f}",
        "segments_removed": str(cutlist.segments_removed),
        "words_removed": str(cutlist.words_removed),
    })

    for seg in cutlist.segments:
        ET.SubElement(root, "segment", {
            "start": f"{seg.start:.3f}",
            "end": f"{seg.end:.3f}",
            "duration": f"{seg.duration:.3f}",
            "type": seg.segment_type.value,
            "reason": seg.reason,
        })

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output), encoding="unicode", xml_declaration=True)
    return output
