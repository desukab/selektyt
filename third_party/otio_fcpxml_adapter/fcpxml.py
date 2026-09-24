# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project

"""OpenTimelineIO FCPXML Adapter."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import subprocess
from fractions import Fraction
from urllib.parse import unquote
from xml.dom import minidom
from xml.etree import cElementTree

import opentimelineio as otio

META_NAMESPACE = "fcpx"
SUPPORTED_VERSIONS = tuple(f"1.{minor}" for minor in range(15))
SUPPORTED_WRITE_VERSION = "1.14"
DEFAULT_FRAME_RATE = 24.0
PROJECT_RESOURCES_VERSION_MAX = "1.3"
GENERIC_FILTER_VERSION_MAX = "1.2"
LEGACY_TRANSITION_VERSION_MAX = "1.2"
LIBRARY_ROOT_ONLY_VERSIONS = {"1.4", "1.5"}
ASSET_FORMAT_VERSION_MIN = "1.3"
REF_CLIP_VERSION_MIN = "1.2"
ASSET_CLIP_VERSION_MIN = "1.6"
SYNC_CLIP_VERSION_MIN = "1.6"
MEDIA_REP_VERSION_MIN = "1.9"
FCPXMLD_INFO_FILENAME = "Info.fcpxml"

STORY_ITEM_TAGS = {
    "clip",
    "mc-clip",
    "audition",
    "asset-clip",
    "ref-clip",
    "sync-clip",
    "title",
    "video",
    "audio",
    "gap",
    "transition",
    "live-drawing",
}
TIMELINE_ITEM_TAGS = STORY_ITEM_TAGS - {"transition"}
UNSUPPORTED_ACTIVE_STORY_TAGS = set()
MARKER_TAGS = {
    "marker",
    "keyword",
    "rating",
    "chapter-marker",
    "analysis-marker",
    "hidden-clip-marker",
}
FILTER_TAG_PREFIXES = ("filter-", "adjust-")
FRAME_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)s")
FORMAT_RATE_RE = re.compile(r"p(\d+(?:\.\d+)?)$")
LANE_CAPABLE_TAGS = {
    "clip",
    "mc-clip",
    "audition",
    "asset-clip",
    "ref-clip",
    "sync-clip",
    "title",
    "video",
    "audio",
    "live-drawing",
}
AUDIO_SOURCE_ENHANCEMENT_ORDER = (
    "adjust-loudness",
    "adjust-noiseReduction",
    "adjust-humReduction",
    "adjust-EQ",
    "adjust-matchEQ",
    "adjust-voiceIsolation",
)
AUDIO_SOURCE_INTRINSIC_ORDER = ("adjust-volume", "adjust-panner")

FRAMERATE_FRAMEDURATION = {
    23.98: "1001/24000s",
    24.0: "25/600s",
    25.0: "1/25s",
    29.97: "1001/30000s",
    30.0: "100/3000s",
    47.95: "1001/48000s",
    48.0: "1/48s",
    50.0: "1/50s",
    59.94: "1001/60000s",
    60.0: "1/60s",
    90.0: "1/90s",
    100.0: "1/100s",
    119.88: "1001/120000s",
    120.0: "1/120s",
}


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def _validate_version(version):
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Unsupported FCPXML version '{version}'. Supported versions: "
            f"{', '.join(SUPPORTED_VERSIONS)}."
        )
    return version


def _version_at_least(version, minimum_version):
    return _version_key(version) >= _version_key(minimum_version)


def _version_at_most(version, maximum_version):
    return _version_key(version) <= _version_key(maximum_version)


def _package_info_path(package_path):
    if not package_path.exists():
        raise FileNotFoundError(f"FCPXML package does not exist: '{package_path}'.")
    if not package_path.is_dir():
        raise NotADirectoryError(
            f"FCPXML package path is not a directory: '{package_path}'."
        )

    info_path = package_path / FCPXMLD_INFO_FILENAME
    if not info_path.is_file():
        raise FileNotFoundError(
            f"FCPXML package is missing '{FCPXMLD_INFO_FILENAME}': '{package_path}'."
        )

    return info_path


def _readable_fcpx_path(filepath):
    path = Path(filepath)
    if path.is_dir() or path.suffix.lower() == ".fcpxmld":
        return _package_info_path(path)
    return path


def format_name(frame_rate, path):
    """Probe a path with ffprobe and build an FCP format name."""

    path = path.replace("file://", "")
    path = unquote(path)
    if not os.path.exists(path):
        return ""

    try:
        frame_size = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height,width",
                "-of",
                "csv=s=x:p=0",
                path,
            ]
        ).decode("utf-8")
    except (subprocess.CalledProcessError, OSError):
        return ""

    frame_size = frame_size.rstrip()
    if not frame_size:
        return ""

    if "1920" in frame_size:
        frame_size = "1080"
    if frame_size.endswith("1280"):
        frame_size = "720"

    rate = int(frame_rate) if int(frame_rate) == float(frame_rate) else frame_rate
    return f"FFVideoFormat{frame_size}p{rate}"


def _to_fraction_seconds(value):
    if value in (None, "", "0s"):
        return Fraction(0, 1)
    if isinstance(value, (int, float)):
        return Fraction(value).limit_denominator()
    if value.endswith("s"):
        value = value[:-1]
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return Fraction(numerator) / Fraction(denominator)
    return Fraction(value)


def to_rational_time(rational_number, fps):
    """Convert an FCP time string into an OTIO RationalTime."""

    rate = float(fps or DEFAULT_FRAME_RATE)
    frames = float(_to_fraction_seconds(rational_number) * Fraction(str(rate)))
    return otio.opentime.RationalTime(frames, rate)


def from_rational_time(rational_time):
    """Convert an OTIO RationalTime into an FCP time string."""

    if rational_time is None:
        return "0s"

    value = Fraction(str(float(rational_time.value)))
    rate = Fraction(str(float(rational_time.rate or DEFAULT_FRAME_RATE)))
    if value == 0:
        return "0s"
    result = (value / rate).limit_denominator()
    if result.denominator == 1:
        return f"{result.numerator}s"
    return f"{result.numerator}/{result.denominator}s"


def _time_range_from_values(start_value, duration_value, rate):
    return otio.opentime.TimeRange(
        start_time=to_rational_time(start_value, rate),
        duration=to_rational_time(duration_value, rate),
    )


def _raw_xml(element):
    return cElementTree.tostring(element, encoding="unicode")


def _append_raw_xml(parent, raw_xml_strings):
    for raw_xml in raw_xml_strings or []:
        parent.append(cElementTree.fromstring(raw_xml))


def _normalize_audio_source_xml(raw_xml_strings):
    normalized_xml = []
    for raw_xml in raw_xml_strings or []:
        element = cElementTree.fromstring(raw_xml)
        if element.tag not in {"audio-channel-source", "audio-role-source"}:
            normalized_xml.append(raw_xml)
            continue

        children = list(element)
        if not children:
            normalized_xml.append(raw_xml)
            continue

        ordered_children = []
        remaining = list(children)

        for tag in AUDIO_SOURCE_ENHANCEMENT_ORDER + AUDIO_SOURCE_INTRINSIC_ORDER:
            matching = [child for child in remaining if child.tag == tag]
            ordered_children.extend(matching)
            remaining = [child for child in remaining if child.tag != tag]

        filter_audio = [child for child in remaining if child.tag == "filter-audio"]
        mute = [child for child in remaining if child.tag == "mute"]
        leftovers = [
            child
            for child in remaining
            if child.tag not in {"filter-audio", "mute"}
        ]
        ordered_children.extend(filter_audio)
        ordered_children.extend(mute)
        ordered_children.extend(leftovers)

        if [child.tag for child in ordered_children] == [child.tag for child in children]:
            normalized_xml.append(raw_xml)
            continue

        normalized_element = cElementTree.Element(element.tag, dict(element.attrib))
        for child in ordered_children:
            normalized_element.append(copy.deepcopy(child))
        normalized_xml.append(_raw_xml(normalized_element))

    return normalized_xml


def _copy_attrib(element, *excluded_keys):
    return {
        key: value
        for key, value in element.attrib.items()
        if key not in excluded_keys and value != ""
    }


def _with_attrs(tag, attrs=None):
    return cElementTree.Element(tag, {k: v for k, v in (attrs or {}).items() if v != ""})


def _parse_text_rate_from_name(name):
    if not name:
        return None
    match = FORMAT_RATE_RE.search(name)
    if not match:
        return None
    return float(match.group(1))


def _rate_to_audio_rate(rate):
    mapping = {
        "32000": "32k",
        "44100": "44.1k",
        "48000": "48k",
        "88200": "88.2k",
        "96000": "96k",
        "176400": "176.4k",
        "192000": "192k",
    }
    return mapping.get(str(rate), "48k")


class _ResourceIndex:
    def __init__(self, resources_element):
        self.resources_element = resources_element
        self.formats = {}
        self.assets = {}
        self.effects = {}
        self.media = {}
        if resources_element is None:
            return
        for child in resources_element:
            child_id = child.get("id")
            if not child_id:
                continue
            if child.tag == "format":
                self.formats[child_id] = child
            elif child.tag == "asset":
                self.assets[child_id] = child
            elif child.tag == "effect":
                self.effects[child_id] = child
            elif child.tag == "media":
                self.media[child_id] = child

    def format_rate(self, format_id, fallback=DEFAULT_FRAME_RATE):
        if not format_id:
            return fallback
        format_element = self.formats.get(format_id)
        if format_element is None:
            return fallback
        frame_duration = format_element.get("frameDuration")
        if frame_duration:
            match = FRAME_DURATION_RE.match(frame_duration)
            if match:
                total = Fraction(match.group(1))
                rate = Fraction(match.group(2))
                if total != 0:
                    return float(rate / total)
        return _parse_text_rate_from_name(format_element.get("name")) or fallback

    def format_attrs(self, format_id):
        format_element = self.formats.get(format_id)
        if format_element is None:
            return {}
        return dict(format_element.attrib)

    def asset_source(self, asset_element):
        source = asset_element.get("src")
        if source:
            return source
        original_media = asset_element.find("./media-rep[@kind='original-media']")
        if original_media is None:
            original_media = asset_element.find("./media-rep")
        if original_media is None:
            return ""
        return original_media.get("src", "")


class FcpxXml:
    """Convert FCPXML into OTIO."""

    def __init__(self, xml_string):
        self.fcpx_xml = cElementTree.fromstring(xml_string)
        self.version = _validate_version(
            self.fcpx_xml.get("version", SUPPORTED_WRITE_VERSION)
        )
        self.child_parent_map = {child: parent for parent in self.fcpx_xml.iter() for child in parent}
        resources_element = self.fcpx_xml.find("./resources")
        if resources_element is None:
            project_element = self.fcpx_xml.find("./project")
            if project_element is not None:
                resources_element = project_element.find("./resources")
        self.resources = _ResourceIndex(resources_element)

    def to_otio(self):
        if self.fcpx_xml.find("./library") is not None:
            return self._from_library(self.fcpx_xml.find("./library"))
        root_events = self.fcpx_xml.findall("./event")
        if root_events:
            if len(root_events) == 1:
                return self._from_event(root_events[0])
            container = otio.schema.SerializableCollection()
            container.metadata.setdefault(META_NAMESPACE, {})["events_root"] = {}
            for event in root_events:
                container.append(self._from_event(event))
            return container
        project_element = self.fcpx_xml.find("./project")
        if project_element is not None:
            return self._from_project(project_element)
        return self._from_collection_items(self.fcpx_xml)

    def _from_library(self, library_element):
        container = otio.schema.SerializableCollection(
            name=library_element.get("location", "")
        )
        container.metadata.setdefault(META_NAMESPACE, {})["library"] = {
            "attrs": dict(library_element.attrib),
            "raw_items": [
                _raw_xml(child)
                for child in library_element
                if child.tag != "event"
            ],
        }
        for event_element in library_element.findall("./event"):
            container.append(self._from_event(event_element))
        return container

    def _from_event(self, event_element):
        container = otio.schema.SerializableCollection(
            name=event_element.get("name", "")
        )
        container.metadata.setdefault(META_NAMESPACE, {})["event"] = {
            "attrs": dict(event_element.attrib),
            "raw_items": [],
        }
        for child in event_element:
            if child.tag == "project":
                container.append(self._from_project(child))
                continue
            if child.tag in STORY_ITEM_TAGS:
                container.append(self._parse_collection_item(child, None))
                continue
            container.metadata[META_NAMESPACE]["event"]["raw_items"].append(_raw_xml(child))
        return container

    def _from_project(self, project_element):
        timeline = otio.schema.Timeline(name=project_element.get("name", ""))
        timeline.metadata.setdefault(META_NAMESPACE, {})["project"] = {
            "attrs": dict(project_element.attrib)
        }
        sequence_element = project_element.find("./sequence")
        if sequence_element is None:
            timeline.tracks = otio.schema.Stack()
            return timeline
        timeline.metadata[META_NAMESPACE]["sequence"] = {
            "attrs": dict(sequence_element.attrib)
        }
        timeline.tracks = self._sequence_to_stack(sequence_element)
        return timeline

    def _from_collection_items(self, parent_element):
        collection = otio.schema.SerializableCollection()
        for child in parent_element:
            if child.tag == "resources":
                continue
            if child.tag in STORY_ITEM_TAGS:
                collection.append(self._parse_collection_item(child, None))
                continue
        return collection

    def _sequence_to_stack(self, sequence_element, name="", source_range=None):
        stack = otio.schema.Stack(name=name, source_range=source_range)
        stack.metadata.setdefault(META_NAMESPACE, {})["sequence"] = {
            "attrs": dict(sequence_element.attrib)
        }
        spine_element = sequence_element.find("./spine")
        if spine_element is None:
            return stack

        items = []
        self._collect_story_items(
            spine_element,
            sequence_element.get("format"),
            items,
            include_direct_av=True,
        )

        lanes = sorted({int(item["lane"]) for item in items})
        for lane in lanes:
            lane_items = [item for item in items if int(item["lane"]) == lane]
            lane_items.sort(key=lambda item: (item["offset"].value, 0 if item["is_transition"] else 1))
            track = otio.schema.Track(
                name=str(lane),
                kind=self._track_kind(lane_items),
            )
            for item in lane_items:
                if item["is_transition"]:
                    track.append(item["composable"])
                    continue
                frame_diff = item["offset"].value - track.duration().value
                if frame_diff > 0:
                    track.append(
                        otio.schema.Gap(
                            source_range=otio.opentime.TimeRange(
                                start_time=otio.opentime.RationalTime(0, item["offset"].rate),
                                duration=otio.opentime.RationalTime(frame_diff, item["offset"].rate),
                            )
                        )
                    )
                track.append(item["composable"])
            stack.append(track)
        return stack

    def _collect_story_items(self, parent_element, default_format_id, items, include_direct_av):
        for child in list(parent_element):
            if child.tag in UNSUPPORTED_ACTIVE_STORY_TAGS:
                raise NotImplementedError(
                    f"'{child.tag}' elements are not supported yet."
                )
            if child.tag == "transition":
                items.append(
                    {
                        "lane": self._lane_for_element(child),
                        "offset": self._absolute_offset(child, default_format_id),
                        "composable": self._build_transition(child, default_format_id),
                        "audio_only": False,
                        "is_transition": True,
                    }
                )
                continue
            if child.tag == "spine":
                self._collect_story_items(child, default_format_id, items, include_direct_av=True)
                continue
            if child.tag not in TIMELINE_ITEM_TAGS:
                continue
            if child.tag in {"video", "audio"} and not include_direct_av:
                continue
            item = self._build_story_item(child, default_format_id)
            items.append(
                {
                    "lane": self._lane_for_element(child),
                    "offset": self._absolute_offset(child, default_format_id),
                    "composable": item,
                    "audio_only": self._audio_only(child),
                    "is_transition": False,
                }
            )
            if child.tag in {"clip", "asset-clip", "gap", "title", "ref-clip", "live-drawing"}:
                self._collect_story_items(child, default_format_id, items, include_direct_av=False)

    def _build_story_item(self, element, default_format_id):
        if element.tag == "gap":
            gap = otio.schema.Gap(
                source_range=self._time_range(
                    element,
                    self._format_rate_for_element(element, default_format_id),
                )
            )
            self._apply_common_metadata(gap, element, default_format_id)
            return gap

        if element.tag == "title":
            return self._build_title(element, default_format_id)

        if element.tag == "audition":
            return self._build_audition(element, default_format_id)

        if element.tag == "mc-clip":
            return self._build_mc_clip(element, default_format_id)

        if element.tag == "ref-clip":
            return self._build_ref_clip(element, default_format_id)

        if element.tag == "sync-clip":
            return self._build_sync_clip(element, default_format_id)

        source_range = self._time_range(
            element,
            self._format_rate_for_element(element, default_format_id),
        )
        clip = otio.schema.Clip(
            name=element.get("name", ""),
            media_reference=self._media_reference_for_element(element, default_format_id),
            source_range=source_range,
        )
        self._apply_common_metadata(clip, element, default_format_id)
        return clip

    def _build_audition(self, element, default_format_id):
        alternatives = [
            child
            for child in list(element)
            if child.tag in STORY_ITEM_TAGS and child.tag != "transition"
        ]
        if not alternatives:
            clip = otio.schema.Clip(
                name=element.get("name", ""),
                media_reference=otio.schema.MissingReference(),
                source_range=self._time_range(
                    element,
                    self._format_rate_for_element(element, default_format_id),
                ),
            )
            clip.metadata.setdefault(META_NAMESPACE, {})["audition"] = {
                "attrs": dict(element.attrib),
                "alternatives_xml": [],
            }
            self._apply_common_metadata(clip, element, default_format_id)
            return clip

        active_element = alternatives[0]
        active_item = self._build_story_item(active_element, default_format_id)
        active_item.metadata.setdefault(META_NAMESPACE, {})["audition"] = {
            "attrs": dict(element.attrib),
            "alternatives_xml": [_raw_xml(child) for child in alternatives[1:]],
            "resource_xml": self._resource_xml_for_elements(alternatives[1:]),
        }
        return active_item

    def _build_mc_clip(self, element, default_format_id):
        clip = otio.schema.Clip(
            name=element.get("name", ""),
            media_reference=otio.schema.MissingReference(),
            source_range=self._time_range(
                element,
                self._format_rate_for_element(element, default_format_id),
            ),
        )
        fcpx_meta = clip.metadata.setdefault(META_NAMESPACE, {})
        fcpx_meta["mc_clip"] = {
            "attrs": dict(element.attrib),
            "mc_source_xml": [
                _raw_xml(child) for child in element if child.tag == "mc-source"
            ],
            "anchor_xml": [
                _raw_xml(child)
                for child in element
                if child.tag in {"spine", "clip", "asset-clip", "ref-clip", "sync-clip", "title", "video", "audio", "gap"}
            ],
        }
        fcpx_meta["mc_source_xml"] = list(fcpx_meta["mc_clip"]["mc_source_xml"])
        fcpx_meta["anchor_xml"] = list(fcpx_meta["mc_clip"]["anchor_xml"])
        fcpx_meta["mc_clip"]["resource_xml"] = self._resource_xml_for_elements([element])
        fcpx_meta["resource_xml"] = list(fcpx_meta["mc_clip"]["resource_xml"])

        media_element = self.resources.media.get(element.get("ref", ""))
        if media_element is not None:
            fcpx_meta["media"] = {"attrs": dict(media_element.attrib)}
            sequence_element = media_element.find("./sequence")
            multicam_element = media_element.find("./multicam")
            if sequence_element is not None:
                fcpx_meta["media_sequence_xml"] = _raw_xml(sequence_element)
            if multicam_element is not None:
                fcpx_meta["multicam_xml"] = _raw_xml(multicam_element)

        self._apply_common_metadata(clip, element, default_format_id)
        return clip

    def _build_title(self, element, default_format_id):
        effect_element = self.resources.effects.get(element.get("ref", ""))
        parameters = {
            "text_xml": [
                _raw_xml(child) for child in element.findall("./text")
            ],
            "text_style_def_xml": [
                _raw_xml(child) for child in element.findall("./text-style-def")
            ],
            "param_xml": [
                _raw_xml(child) for child in element.findall("./param")
            ],
        }
        if effect_element is not None:
            parameters.update(
                {
                    "effect_name": effect_element.get("name", ""),
                    "effect_uid": effect_element.get("uid", ""),
                    "effect_src": effect_element.get("src", ""),
                }
            )
        generator = otio.schema.GeneratorReference(
            name=parameters.get("effect_name", element.get("name", "")),
            generator_kind="fcpx.title",
            parameters=parameters,
            metadata={META_NAMESPACE: {"title_effect_ref": element.get("ref", "")}},
        )
        clip = otio.schema.Clip(
            name=element.get("name", ""),
            media_reference=generator,
            source_range=self._time_range(
                element,
                self._format_rate_for_element(element, default_format_id),
            ),
        )
        self._apply_common_metadata(clip, element, default_format_id)
        return clip

    def _build_ref_clip(self, element, default_format_id):
        media_element = self.resources.media.get(element.get("ref", ""))
        sequence_element = None if media_element is None else media_element.find("./sequence")
        stack = otio.schema.Stack(
            name=element.get("name", ""),
            source_range=self._time_range(
                element,
                self._format_rate_for_element(element, default_format_id),
            ),
        )
        stack.metadata.setdefault(META_NAMESPACE, {})["container"] = "ref-clip"
        if media_element is not None:
            stack.metadata[META_NAMESPACE]["media"] = {"attrs": dict(media_element.attrib)}
        if sequence_element is not None:
            inner = self._sequence_to_stack(sequence_element, name=element.get("name", ""))
            for child in inner:
                stack.append(copy.deepcopy(child))
            for effect in inner.effects:
                stack.effects.append(copy.deepcopy(effect))
            stack.metadata[META_NAMESPACE]["media_sequence"] = {
                "attrs": dict(sequence_element.attrib)
            }
        self._apply_common_metadata(stack, element, default_format_id)
        return stack

    def _build_sync_clip(self, element, default_format_id):
        stack = otio.schema.Stack(
            name=element.get("name", ""),
            source_range=self._time_range(
                element,
                self._format_rate_for_element(element, default_format_id),
            ),
        )
        stack.metadata.setdefault(META_NAMESPACE, {})["container"] = "sync-clip"
        stack.metadata[META_NAMESPACE]["sync_clip"] = {"attrs": dict(element.attrib)}
        fake_sequence = _with_attrs(
            "sequence",
            {"format": element.get("format", default_format_id) or default_format_id},
        )
        spine_element = element.find("./spine")
        if spine_element is None:
            spine_element = cElementTree.SubElement(fake_sequence, "spine")
            for child in list(element):
                if child.tag in STORY_ITEM_TAGS:
                    spine_element.append(cElementTree.fromstring(_raw_xml(child)))
        else:
            fake_sequence.append(cElementTree.fromstring(_raw_xml(spine_element)))
        inner = self._sequence_to_stack(
            fake_sequence,
            name=element.get("name", ""),
            source_range=stack.source_range,
        )
        for child in inner:
            stack.append(copy.deepcopy(child))
        for effect in inner.effects:
            stack.effects.append(copy.deepcopy(effect))
        self._apply_common_metadata(stack, element, default_format_id)
        return stack

    def _build_transition(self, element, default_format_id):
        rate = self._format_rate_for_element(element, default_format_id)
        duration = to_rational_time(element.get("duration"), rate)
        half_duration = otio.opentime.RationalTime(duration.value / 2.0, duration.rate)
        transition_type = otio.schema.TransitionTypes.Custom
        filter_video = element.find("./filter-video")
        if filter_video is not None and "Dissolve" in filter_video.get("name", ""):
            transition_type = otio.schema.TransitionTypes.SMPTE_Dissolve
        elif element.get("ref"):
            resource = self.resources.effects.get(element.get("ref", ""))
            if resource is not None and "Dissolve" in resource.get("name", ""):
                transition_type = otio.schema.TransitionTypes.SMPTE_Dissolve
        transition = otio.schema.Transition(
            name=element.get("name", ""),
            transition_type=transition_type,
            in_offset=half_duration,
            out_offset=half_duration,
            metadata={META_NAMESPACE: {"transition": {"attrs": dict(element.attrib)}}},
        )
        payload = self._extract_fcpx_payload(element)
        transition.metadata[META_NAMESPACE]["transition"].update(payload)
        transition.metadata[META_NAMESPACE].update(payload)
        return transition

    def _apply_common_metadata(self, item, element, default_format_id):
        fcpx_meta = item.metadata.setdefault(META_NAMESPACE, {})
        fcpx_meta.setdefault("story", {})
        fcpx_meta["story"]["tag"] = element.tag
        fcpx_meta["story"]["attrs"] = dict(element.attrib)

        if element.get("enabled", "1") == "0":
            item.enabled = False

        note = element.find("./note")
        if note is not None and note.text:
            fcpx_meta["note"] = note.text

        metadata_entries = self._metadata_entries(element.find("./metadata"))
        if metadata_entries:
            fcpx_meta["metadata"] = metadata_entries

        keywords = [dict(keyword.attrib) for keyword in element.findall("./keyword")]
        if keywords:
            fcpx_meta["keywords"] = keywords

        ratings = [dict(rating.attrib) for rating in element.findall("./rating")]
        if ratings:
            fcpx_meta["ratings"] = ratings

        raw_marker_items = [
            _raw_xml(child)
            for child in element
            if child.tag in {"chapter-marker", "analysis-marker", "hidden-clip-marker"}
        ]
        if raw_marker_items:
            fcpx_meta["raw_marker_items"] = raw_marker_items

        for marker in element.findall("./marker"):
            item.markers.append(self._marker(marker, default_format_id))

        raw_audio_channel_source = [
            _raw_xml(child)
            for child in element
            if child.tag == "audio-channel-source"
        ]
        if raw_audio_channel_source:
            fcpx_meta["audio_channel_source_xml"] = raw_audio_channel_source

        raw_audio_role_source = [
            _raw_xml(child)
            for child in element
            if child.tag == "audio-role-source"
        ]
        if raw_audio_role_source:
            fcpx_meta["audio_role_source_xml"] = raw_audio_role_source

        legacy_audio_source = [
            _raw_xml(child)
            for child in element
            if child.tag in {"audio-source", "audio-aux-source"}
        ]
        if legacy_audio_source:
            fcpx_meta["legacy_audio_source_xml"] = legacy_audio_source

        raw_sync_source = [
            _raw_xml(child)
            for child in element
            if child.tag == "sync-source"
        ]
        if raw_sync_source:
            fcpx_meta["sync_source_xml"] = raw_sync_source

        effects, extra_meta = self._effects_for_element(element, default_format_id)
        item.effects.extend(effects)
        fcpx_meta.update(extra_meta)

    def _effects_for_element(self, element, default_format_id):
        effects = []
        extra_meta = {}
        sequence_rate = self._sequence_rate_for_element(element, default_format_id)

        conform_rate_element = element.find("./conform-rate")
        if conform_rate_element is not None:
            conform_attrs = dict(conform_rate_element.attrib)
            extra_meta["conform_rate"] = conform_attrs
            src_frame_rate = conform_attrs.get("srcFrameRate")
            if src_frame_rate and conform_attrs.get("scaleEnabled", "1") != "0":
                scalar = sequence_rate / float(src_frame_rate)
                effects.append(
                    otio.schema.LinearTimeWarp(
                        name="conform-rate",
                        time_scalar=scalar,
                        metadata={META_NAMESPACE: {"conform_rate": conform_attrs}},
                    )
                )

        time_map_element = element.find("./timeMap")
        if time_map_element is not None:
            time_map_payload = {
                "attrs": dict(time_map_element.attrib),
                "points": [dict(point.attrib) for point in time_map_element.findall("./timept")],
            }
            parsed = self._time_effect_from_time_map(time_map_payload)
            if parsed is not None:
                effects.append(parsed)
            else:
                extra_meta["time_map"] = time_map_payload

        for child in list(element):
            if child.tag == "filter" or child.tag.startswith(FILTER_TAG_PREFIXES):
                if child.tag in {"conform-rate", "timeMap"}:
                    continue
                effect = self._effect_from_xml(child)
                if effect is not None:
                    effects.append(effect)
        return effects, extra_meta

    def _time_effect_from_time_map(self, payload):
        points = payload.get("points") or []
        if len(points) < 2:
            return None
        try:
            first_time = _to_fraction_seconds(points[0]["time"])
            last_time = _to_fraction_seconds(points[-1]["time"])
            first_value = _to_fraction_seconds(points[0]["value"])
            last_value = _to_fraction_seconds(points[-1]["value"])
        except (KeyError, ZeroDivisionError, ValueError):
            return None

        adjusted_duration = last_time - first_time
        source_duration = last_value - first_value
        if adjusted_duration == 0:
            return None
        if source_duration == 0:
            return otio.schema.FreezeFrame(
                name="FreezeFrame",
                metadata={META_NAMESPACE: {"time_map": payload}},
            )
        if len(points) != 2:
            return None
        scalar = float(source_duration / adjusted_duration)
        return otio.schema.LinearTimeWarp(
            name="LinearTimeWarp",
            time_scalar=scalar,
            metadata={META_NAMESPACE: {"time_map": payload}},
        )

    def _effect_from_xml(self, element):
        if element.tag == "filter":
            resource = self.resources.effects.get(element.get("ref", ""))
            effect_name = (
                resource.get("name")
                if resource is not None and resource.get("name")
                else element.get("name", "")
            )
            return otio.schema.Effect(
                name=element.get("name", effect_name),
                effect_name=effect_name or element.tag,
                metadata={
                    META_NAMESPACE: {
                        "element": element.tag,
                        "attrs": dict(element.attrib),
                        "params": [dict(param.attrib) for param in element.findall("./param")],
                        "resource": {} if resource is None else dict(resource.attrib),
                    }
                },
            )

        if element.tag == "filter-video":
            resource = self.resources.effects.get(element.get("ref", ""))
            effect_name = (
                resource.get("name")
                if resource is not None and resource.get("name")
                else element.get("name", "")
            )
            return otio.schema.Effect(
                name=element.get("name", effect_name),
                effect_name=effect_name or element.tag,
                metadata={
                    META_NAMESPACE: {
                        "element": element.tag,
                        "attrs": dict(element.attrib),
                        "params": [dict(param.attrib) for param in element.findall("./param")],
                        "data": [child.text or "" for child in element.findall("./data")],
                        "resource": {} if resource is None else dict(resource.attrib),
                    }
                },
            )

        if element.tag == "filter-audio":
            resource = self.resources.effects.get(element.get("ref", ""))
            effect_name = (
                resource.get("name")
                if resource is not None and resource.get("name")
                else element.get("name", "")
            )
            return otio.schema.Effect(
                name=element.get("name", effect_name),
                effect_name=effect_name or element.tag,
                metadata={
                    META_NAMESPACE: {
                        "element": element.tag,
                        "attrs": dict(element.attrib),
                        "params": [dict(param.attrib) for param in element.findall("./param")],
                        "resource": {} if resource is None else dict(resource.attrib),
                    }
                },
            )

        if element.tag.startswith("adjust-"):
            return otio.schema.Effect(
                name=element.tag,
                effect_name=element.tag,
                metadata={
                    META_NAMESPACE: {
                        "element": element.tag,
                        "attrs": dict(element.attrib),
                        "params": [dict(param.attrib) for param in element.findall("./param")],
                        "raw_children": [
                            _raw_xml(child) for child in list(element) if child.tag != "param"
                        ],
                    }
                },
            )
        return None

    def _extract_fcpx_payload(self, element):
        payload = {}
        if element.get("ref"):
            resource = self.resources.effects.get(element.get("ref", ""))
            payload["resource"] = {} if resource is None else dict(resource.attrib)
        filter_video = element.find("./filter-video")
        if filter_video is not None:
            resource = self.resources.effects.get(filter_video.get("ref", ""))
            payload["filter_video"] = {
                "name": filter_video.get("name", ""),
                "attrs": dict(filter_video.attrib),
                "params": [dict(param.attrib) for param in filter_video.findall("./param")],
                "resource": {} if resource is None else dict(resource.attrib),
            }
        filter_audio = element.find("./filter-audio")
        if filter_audio is not None:
            resource = self.resources.effects.get(filter_audio.get("ref", ""))
            payload["filter_audio"] = {
                "name": filter_audio.get("name", ""),
                "attrs": dict(filter_audio.attrib),
                "params": [dict(param.attrib) for param in filter_audio.findall("./param")],
                "resource": {} if resource is None else dict(resource.attrib),
            }
        return payload

    def _marker(self, element, default_format_id):
        if element.get("completed") == "1":
            color = otio.schema.MarkerColor.GREEN
        elif element.get("completed") == "0":
            color = otio.schema.MarkerColor.RED
        else:
            color = otio.schema.MarkerColor.PURPLE
        rate = self._sequence_rate_for_element(element, default_format_id)
        return otio.schema.Marker(
            name=element.get("value", ""),
            marked_range=_time_range_from_values(
                element.get("start", "0s"),
                element.get("duration", "0s"),
                rate,
            ),
            color=color,
            metadata={META_NAMESPACE: {"attrs": dict(element.attrib)}},
        )

    def _metadata_entries(self, metadata_element):
        if metadata_element is None:
            return []
        entries = []
        for md in metadata_element.findall("./md"):
            entry = dict(md.attrib)
            array = md.find("./array")
            if array is not None:
                entry["array"] = [
                    {
                        "tag": child.tag,
                        "text": child.text or "",
                        "attrs": dict(child.attrib),
                    }
                    for child in list(array)
                ]
            entries.append(entry)
        return entries

    def _media_reference_for_element(self, element, default_format_id):
        asset_id = self._reference_id_for_story(element)
        if not asset_id:
            return otio.schema.MissingReference()

        asset = self.resources.assets.get(asset_id)
        if asset is None:
            return otio.schema.MissingReference()

        source = self.resources.asset_source(asset)
        if not source:
            return otio.schema.MissingReference()

        format_id = asset.get("format", default_format_id)
        rate = self.resources.format_rate(format_id)
        media_reference = otio.schema.ExternalReference(
            target_url=source,
            available_range=_time_range_from_values(
                asset.get("start", "0s"),
                asset.get("duration", "0s"),
                rate,
            ),
            metadata={
                META_NAMESPACE: {
                    "asset": {
                        "attrs": dict(asset.attrib),
                        "format": self.resources.format_attrs(format_id),
                        "media_reps": [dict(media_rep.attrib) for media_rep in asset.findall("./media-rep")],
                        "bookmark_xml": [
                            _raw_xml(bookmark) for bookmark in asset.findall("./bookmark")
                        ],
                        "metadata": self._metadata_entries(asset.find("./metadata")),
                    }
                }
            },
        )

        top_level_asset_clip = self._top_level_asset_clip_by_ref(asset_id)
        if top_level_asset_clip is not None:
            media_reference.metadata[META_NAMESPACE]["asset_clip"] = self._collection_item_payload(
                top_level_asset_clip
            )
        return media_reference

    def _build_title_effect_payload(self, effect_element):
        payload = {}
        if effect_element is None:
            return payload
        payload["effect_name"] = effect_element.get("name", "")
        payload["effect_uid"] = effect_element.get("uid", "")
        payload["effect_src"] = effect_element.get("src", "")
        return payload

    def _collection_item_payload(self, element):
        payload = {"attrs": dict(element.attrib)}
        note = element.find("./note")
        if note is not None and note.text:
            payload["note"] = note.text
        metadata_entries = self._metadata_entries(element.find("./metadata"))
        if metadata_entries:
            payload["metadata"] = metadata_entries
        keywords = [dict(keyword.attrib) for keyword in element.findall("./keyword")]
        if keywords:
            payload["keywords"] = keywords
        ratings = [dict(rating.attrib) for rating in element.findall("./rating")]
        if ratings:
            payload["ratings"] = ratings
        return payload

    def _resource_xml_for_elements(self, elements):
        resource_xml = []
        seen_ids = set()

        def append_resource(resource_element):
            if resource_element is None:
                return
            resource_id = resource_element.get("id")
            if not resource_id or resource_id in seen_ids:
                return
            seen_ids.add(resource_id)
            resource_xml.append(_raw_xml(resource_element))

        def collect_resource_refs(element):
            if element.get("format"):
                append_resource(self.resources.formats.get(element.get("format")))

            if element.tag in {"asset-clip", "video", "audio"} and element.get("ref"):
                asset = self.resources.assets.get(element.get("ref"))
                if asset is not None:
                    append_resource(asset)
                    if asset.get("format"):
                        append_resource(self.resources.formats.get(asset.get("format")))
                else:
                    append_resource(self.resources.effects.get(element.get("ref")))
            elif element.tag == "clip":
                asset_id = self._reference_id_for_story(element)
                asset = self.resources.assets.get(asset_id or "")
                append_resource(asset)
                if asset is not None and asset.get("format"):
                    append_resource(self.resources.formats.get(asset.get("format")))
            elif element.tag in {"ref-clip", "mc-clip"} and element.get("ref"):
                media = self.resources.media.get(element.get("ref"))
                append_resource(media)
                if media is not None:
                    sequence = media.find("./sequence")
                    multicam = media.find("./multicam")
                    if sequence is not None and sequence.get("format"):
                        append_resource(self.resources.formats.get(sequence.get("format")))
                    if multicam is not None and multicam.get("format"):
                        append_resource(self.resources.formats.get(multicam.get("format")))
                    for child in media:
                        collect_resource_refs(child)

            if element.tag in {"filter", "filter-video", "filter-audio", "transition", "title"} and element.get("ref"):
                append_resource(self.resources.effects.get(element.get("ref")))

            for child in list(element):
                collect_resource_refs(child)

        for element in elements:
            collect_resource_refs(element)
        return resource_xml

    def _parse_collection_item(self, element, default_format_id):
        if element.tag == "transition":
            return self._build_transition(element, default_format_id)
        return self._build_story_item(element, default_format_id)

    def _top_level_asset_clip_by_ref(self, asset_id):
        for parent in (self.fcpx_xml, self.fcpx_xml.find("./event")):
            if parent is None:
                continue
            asset_clip = parent.find(f"./asset-clip[@ref='{asset_id}']")
            if asset_clip is not None:
                return asset_clip
        return None

    def _reference_id_for_story(self, element):
        if element.tag in {"asset-clip", "video", "audio"}:
            return element.get("ref")
        if element.tag == "clip":
            direct_video = element.find("./video")
            if direct_video is not None and direct_video.get("ref"):
                return direct_video.get("ref")
            direct_audio = element.find("./audio")
            if direct_audio is not None and direct_audio.get("ref"):
                return direct_audio.get("ref")
        if element.tag == "live-drawing":
            return element.get("dataLocator")
        return None

    def _time_range(self, element, rate):
        return _time_range_from_values(
            element.get("start", "0s"),
            element.get("duration", "0s"),
            rate,
        )

    def _lane_for_element(self, element):
        parent = self.child_parent_map.get(element)
        if parent is not None and parent.tag == "spine" and parent.get("lane") is not None:
            return parent.get("lane")
        return element.get("lane", "0")

    def _absolute_offset(self, element, default_format_id):
        element_format_id = self._format_id_for_element(element, default_format_id)
        element_rate = self.resources.format_rate(element_format_id)
        parent = self.child_parent_map.get(element)
        if parent is None:
            return otio.opentime.RationalTime(0, element_rate)

        parent_story = parent
        ignore_parent_start = False
        if parent.tag == "spine" and parent.get("lane") is not None:
            parent_story = self.child_parent_map.get(parent)
            ignore_parent_start = True

        parent_format_id = self._format_id_for_element(parent_story, default_format_id)
        parent_rate = self.resources.format_rate(parent_format_id)
        clip_offset = to_rational_time(element.get("offset", "0s"), element_rate)
        parent_start = (
            otio.opentime.RationalTime(0, parent_rate)
            if ignore_parent_start or parent_story is None
            else to_rational_time(parent_story.get("start", "0s"), parent_rate)
        )
        parent_offset = (
            otio.opentime.RationalTime(0, parent_rate)
            if parent_story is None
            else to_rational_time(parent_story.get("offset", "0s"), parent_rate)
        )
        return clip_offset - parent_start + parent_offset

    def _format_id_for_element(self, element, default_format_id):
        if element is None:
            return default_format_id
        if element.tag == "sequence":
            return element.get("format", default_format_id)
        if element.tag == "mc-clip":
            media = self.resources.media.get(element.get("ref", ""))
            if media is not None:
                multicam = media.find("./multicam")
                if multicam is not None:
                    return multicam.get("format", default_format_id)
                sequence = media.find("./sequence")
                if sequence is not None:
                    return sequence.get("format", default_format_id)
        if element.tag in {"video", "audio", "asset-clip"} and element.get("ref"):
            asset = self.resources.assets.get(element.get("ref"))
            if asset is not None:
                return asset.get("format", default_format_id)
        if element.tag == "clip":
            ref_id = self._reference_id_for_story(element)
            if ref_id:
                asset = self.resources.assets.get(ref_id)
                if asset is not None:
                    return asset.get("format", default_format_id)
        if element.tag == "ref-clip":
            media = self.resources.media.get(element.get("ref", ""))
            sequence = None if media is None else media.find("./sequence")
            if sequence is not None:
                return sequence.get("format", default_format_id)
        return element.get("format", default_format_id)

    def _format_rate_for_element(self, element, default_format_id):
        return self.resources.format_rate(
            self._format_id_for_element(element, default_format_id),
            fallback=DEFAULT_FRAME_RATE,
        )

    def _sequence_rate_for_element(self, element, default_format_id):
        current = element
        while current is not None and current.tag != "sequence":
            current = self.child_parent_map.get(current)
        if current is not None:
            return self.resources.format_rate(current.get("format"), DEFAULT_FRAME_RATE)
        return self.resources.format_rate(default_format_id, DEFAULT_FRAME_RATE)

    @staticmethod
    def _audio_only(element):
        if element.tag == "audio":
            return True
        if element.tag == "video":
            return False
        if element.tag == "audition":
            lane = element.get("lane")
            if lane is not None:
                try:
                    return int(lane) < 0
                except ValueError:
                    return False
            active = next(
                (child for child in list(element) if child.tag in TIMELINE_ITEM_TAGS),
                None,
            )
            return active is not None and FcpxXml._audio_only(active)
        if element.tag == "mc-clip":
            if element.get("srcEnable") == "audio":
                return True
            lane = element.get("lane")
            if lane is not None:
                try:
                    return int(lane) < 0
                except ValueError:
                    return False
            return False
        if element.tag == "asset-clip":
            lane = element.get("lane")
            if lane is not None:
                try:
                    if int(lane) < 0:
                        return True
                except ValueError:
                    pass
            return element.get("srcEnable") == "audio"
        if element.tag == "clip":
            if element.find("./video") is not None:
                return False
            if element.find("./audio") is not None:
                return True
            return element.find(".//audio") is not None and element.find(".//video") is None
        if element.tag == "title":
            return False
        if element.tag == "ref-clip":
            if element.get("srcEnable") == "audio":
                return True
            lane = element.get("lane")
            if lane is not None:
                try:
                    return int(lane) < 0
                except ValueError:
                    return False
        return False

    @staticmethod
    def _track_kind(lane_items):
        editorial_items = [
            item
            for item in lane_items
            if not item["is_transition"]
            and not isinstance(item["composable"], otio.schema.Gap)
        ]
        if editorial_items and all(item["audio_only"] for item in editorial_items):
            return otio.schema.TrackKind.Audio
        return otio.schema.TrackKind.Video


class FcpxOtio:
    """Convert OTIO into FCPXML."""

    def __init__(self, input_otio, fcpxml_version=SUPPORTED_WRITE_VERSION):
        self.fcpxml_version = _validate_version(fcpxml_version)
        self.input_otio = input_otio
        self.fcpx_xml = cElementTree.Element("fcpxml", version=self.fcpxml_version)
        self.resource_element = None
        self.resource_count = 0
        self.used_resource_ids = set()
        self.format_ids = {}
        self.asset_ids = {}
        self.effect_ids = {}
        self.media_ids = {}
        self.text_style_id_count = 0

    def to_xml(self):
        if isinstance(self.input_otio, otio.schema.Timeline):
            if self.fcpxml_version in LIBRARY_ROOT_ONLY_VERSIONS:
                self._append_collection(self._wrap_timeline_in_library(self.input_otio), self.fcpx_xml)
            else:
                self.fcpx_xml.append(self._project_element(self.input_otio))
        elif isinstance(self.input_otio, otio.schema.SerializableCollection):
            if _version_at_most(self.fcpxml_version, PROJECT_RESOURCES_VERSION_MAX):
                self.fcpx_xml.append(self._legacy_project_for_collection(self.input_otio))
            elif self.fcpxml_version in LIBRARY_ROOT_ONLY_VERSIONS:
                self._append_collection(self._wrap_collection_in_library(self.input_otio), self.fcpx_xml)
            else:
                self._append_collection(self.input_otio, self.fcpx_xml)
        else:
            raise TypeError("Unsupported OTIO root type for fcpxml adapter.")

        xml = cElementTree.tostring(self.fcpx_xml, encoding="UTF-8", method="xml")
        dom = minidom.parseString(xml)
        pretty = dom.toprettyxml(indent="    ")
        return pretty.replace(
            '<?xml version="1.0" ?>',
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n',
        )

    def _ensure_resource_element(self, project_element=None):
        if self.resource_element is not None:
            return self.resource_element
        if _version_at_most(self.fcpxml_version, PROJECT_RESOURCES_VERSION_MAX):
            if project_element is None:
                raise RuntimeError("Project-scoped resources require a project element.")
            self.resource_element = cElementTree.SubElement(project_element, "resources")
        else:
            self.resource_element = cElementTree.SubElement(self.fcpx_xml, "resources")
        return self.resource_element

    def _wrap_timeline_in_library(self, timeline):
        collection = otio.schema.SerializableCollection(name=timeline.name)
        collection.metadata[META_NAMESPACE] = {"library": {"attrs": {}}}
        event_collection = otio.schema.SerializableCollection(name=timeline.name or "Event")
        event_collection.metadata[META_NAMESPACE] = {
            "event": {"attrs": {"name": timeline.name or "Event"}}
        }
        event_collection.append(timeline)
        collection.append(event_collection)
        return collection

    def _wrap_collection_in_library(self, collection):
        fcpx_meta = collection.metadata.get(META_NAMESPACE, {})
        if "library" in fcpx_meta or self._looks_like_library(collection):
            return collection
        library = otio.schema.SerializableCollection(name=collection.name)
        library.metadata[META_NAMESPACE] = {"library": {"attrs": {}}}
        if "event" in fcpx_meta:
            library.append(collection)
            return library
        event = otio.schema.SerializableCollection(name=collection.name or "Event")
        event.metadata[META_NAMESPACE] = {
            "event": {"attrs": {"name": collection.name or "Event"}}
        }
        for child in collection:
            event.append(child)
        library.append(event)
        return library

    def _legacy_project_for_collection(self, collection):
        timelines = [child for child in collection if isinstance(child, otio.schema.Timeline)]
        if timelines:
            if len(collection) != 1:
                raise NotImplementedError(
                    "FCPXML versions 1.0-1.3 can only write a single top-level timeline."
                )
            return self._project_element(timelines[0])
        project_attrs = {
            "name": collection.name,
        }
        project_element = cElementTree.Element(
            "project",
            {k: v for k, v in project_attrs.items() if v},
        )
        self._ensure_resource_element(project_element)
        for child in collection:
            project_element.append(self._element_for_collection_item(child, top_level=True))
        return project_element

    def _append_collection(self, collection, parent_element):
        if not _version_at_most(self.fcpxml_version, PROJECT_RESOURCES_VERSION_MAX):
            self._ensure_resource_element()
        fcpx_meta = collection.metadata.get(META_NAMESPACE, {})
        if "library" in fcpx_meta or self._looks_like_library(collection):
            library_attrs = dict(fcpx_meta.get("library", {}).get("attrs", {}))
            library_element = cElementTree.SubElement(parent_element, "library", library_attrs)
            for child in collection:
                if isinstance(child, otio.schema.SerializableCollection):
                    self._append_event(child, library_element)
                elif isinstance(child, otio.schema.Timeline):
                    event_collection = otio.schema.SerializableCollection(name=collection.name)
                    event_collection.metadata[META_NAMESPACE] = {
                        "event": {"attrs": {"name": collection.name}}
                    }
                    event_collection.append(child)
                    self._append_event(event_collection, library_element)
                else:
                    library_element.append(self._element_for_collection_item(child, top_level=True))
            _append_raw_xml(library_element, fcpx_meta.get("library", {}).get("raw_items"))
            return

        if "event" in fcpx_meta:
            self._append_event(collection, parent_element)
            return

        for child in collection:
            parent_element.append(self._element_for_collection_item(child, top_level=True))

    def _append_event(self, collection, parent_element):
        event_attrs = dict(collection.metadata.get(META_NAMESPACE, {}).get("event", {}).get("attrs", {}))
        if not event_attrs.get("name") and collection.name:
            event_attrs["name"] = collection.name
        event_element = cElementTree.SubElement(parent_element, "event", event_attrs)
        for child in collection:
            if isinstance(child, otio.schema.Timeline):
                event_element.append(self._project_element(child))
            else:
                event_element.append(self._element_for_collection_item(child, top_level=True))
        _append_raw_xml(
            event_element,
            collection.metadata.get(META_NAMESPACE, {}).get("event", {}).get("raw_items"),
        )

    def _looks_like_library(self, collection):
        if not len(collection):
            return False
        return all(
            isinstance(child, otio.schema.SerializableCollection)
            and META_NAMESPACE in child.metadata
            and "event" in child.metadata[META_NAMESPACE]
            for child in collection
        )

    def _project_element(self, timeline):
        project_attrs = dict(timeline.metadata.get(META_NAMESPACE, {}).get("project", {}).get("attrs", {}))
        project_attrs.setdefault("name", timeline.name)
        project_element = cElementTree.Element("project", {k: v for k, v in project_attrs.items() if v != ""})
        self._ensure_resource_element(project_element if _version_at_most(self.fcpxml_version, PROJECT_RESOURCES_VERSION_MAX) else None)
        project_element.append(self._stack_to_sequence(timeline.tracks, timeline))
        return project_element

    def _stack_to_sequence(self, stack, timeline=None):
        sequence_meta = {}
        if timeline is not None:
            sequence_meta = timeline.metadata.get(META_NAMESPACE, {}).get("sequence", {}).get("attrs", {})
        elif stack.metadata.get(META_NAMESPACE, {}).get("sequence"):
            sequence_meta = stack.metadata[META_NAMESPACE]["sequence"]["attrs"]

        format_id = self._ensure_format_for_stack(stack)
        sequence_attrs = dict(sequence_meta)
        sequence_attrs["format"] = format_id
        sequence_attrs["duration"] = from_rational_time(stack.duration())
        sequence_attrs.setdefault("tcStart", "0s")
        sequence_attrs.setdefault("tcFormat", "NDF")
        if any(track.kind == otio.schema.TrackKind.Audio for track in stack):
            sequence_attrs.setdefault("audioLayout", "stereo")
            sequence_attrs.setdefault("audioRate", "48k")
        sequence_element = cElementTree.Element("sequence", sequence_attrs)
        spine = cElementTree.SubElement(sequence_element, "spine")
        self._append_tracks_to_spine(stack, spine, format_id)
        return sequence_element

    def _append_tracks_to_spine(self, stack, spine_element, default_format_id):
        video_tracks = [track for track in stack if track.kind == otio.schema.TrackKind.Video]
        audio_tracks = [track for track in stack if track.kind == otio.schema.TrackKind.Audio]

        if not video_tracks:
            video_tracks = [otio.schema.Track(kind=otio.schema.TrackKind.Video)]

        self._append_track_items(video_tracks[0], spine_element, 0, default_format_id)
        for index, track in enumerate(video_tracks[1:], 1):
            self._append_track_items(track, spine_element, index, default_format_id)
        for index, track in enumerate(audio_tracks, 1):
            self._append_track_items(track, spine_element, -index, default_format_id)

    def _append_track_items(self, track, spine_element, lane_id, default_format_id):
        items = list(track)
        if not items:
            return

        if lane_id == 0:
            for item in items:
                spine_element.append(self._element_for_track_item(item, default_format_id))
            return

        for segment in self._non_gap_track_segments(items):
            if not any(isinstance(item, otio.schema.Transition) for item in segment):
                for item in segment:
                    item_element = self._element_for_track_item(item, default_format_id)
                    if item_element.tag not in LANE_CAPABLE_TAGS:
                        continue
                    item_element.set("lane", str(lane_id))
                    spine_element.append(item_element)
                continue
            self._append_storyline_segment(
                track,
                segment,
                spine_element,
                lane_id,
                default_format_id,
            )

    @staticmethod
    def _non_gap_track_segments(items):
        segments = []
        current_segment = []
        for item in items:
            if isinstance(item, otio.schema.Gap):
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
                continue
            current_segment.append(item)
        if current_segment:
            segments.append(current_segment)
        return segments

    def _append_storyline_segment(
        self,
        track,
        segment,
        spine_element,
        lane_id,
        default_format_id,
    ):
        anchor_start = self._segment_anchor_time(track, segment)
        parent_element = self._find_parent_element(
            spine_element,
            anchor_start,
            default_format_id,
        )
        if parent_element is None:
            parent_element = self._find_fallback_parent_element(
                spine_element,
                anchor_start,
                default_format_id,
            )

        if parent_element is None:
            for item in segment:
                if isinstance(item, otio.schema.Transition):
                    continue
                item_element = self._element_for_track_item(item, default_format_id)
                if item_element.tag not in LANE_CAPABLE_TAGS:
                    continue
                item_element.set("lane", str(lane_id))
                spine_element.append(item_element)
            return

        nested_spine = self._find_or_create_nested_spine(parent_element, lane_id)
        for item in segment:
            item_element = self._element_for_track_item(item, default_format_id)
            item_element.attrib.pop("lane", None)
            self._set_storyline_child_offset(
                item_element,
                item,
                track,
                parent_element,
                anchor_start,
                default_format_id,
            )
            nested_spine.append(item_element)

    @staticmethod
    def _track_item_start(track, item):
        return track.trimmed_range_of_child(item).start_time

    def _segment_anchor_time(self, track, segment):
        anchor_item = next(
            (item for item in segment if not isinstance(item, otio.schema.Transition)),
            segment[0],
        )
        return self._track_item_start(track, anchor_item)

    def _find_fallback_parent_element(self, spine_element, absolute_start, default_format_id):
        fallback = None
        first_candidate = None
        for item in spine_element:
            if item.tag == "transition" or item.get("lane") is not None:
                continue
            item_rate = self._element_rate(item, default_format_id)
            item_offset = to_rational_time(item.get("offset", "0s"), item_rate)
            if first_candidate is None:
                first_candidate = item
            if item_offset <= absolute_start:
                fallback = item
                continue
            if fallback is not None:
                break
        return fallback or first_candidate

    def _set_storyline_child_offset(
        self,
        item_element,
        item,
        track,
        parent_element,
        anchor_start,
        default_format_id,
    ):
        if isinstance(item, otio.schema.Transition):
            preserved_offset = (
                item.metadata.get(META_NAMESPACE, {})
                .get("transition", {})
                .get("attrs", {})
                .get("offset")
            )
            if preserved_offset:
                item_element.set("offset", preserved_offset)
                return

        absolute_start = self._track_item_start(track, item)
        if isinstance(item, otio.schema.Transition) and absolute_start < anchor_start:
            absolute_start = anchor_start
        self._set_relative_offset(
            item_element,
            absolute_start,
            parent_element,
            default_format_id,
        )

    def _find_parent_element(self, spine_element, absolute_start, default_format_id):
        for item in spine_element:
            if item.tag == "transition":
                continue
            if item.get("lane") is not None:
                continue
            item_format_rate = self._element_rate(item, default_format_id)
            offset = to_rational_time(item.get("offset", "0s"), item_format_rate)
            duration = to_rational_time(item.get("duration", "0s"), item_format_rate)
            if offset <= absolute_start < (offset + duration):
                return item
        return None

    def _find_or_create_nested_spine(self, parent_element, lane_id):
        for child in parent_element.findall("./spine"):
            if child.get("lane") == str(lane_id):
                return child
        nested_spine = cElementTree.Element(
            "spine",
            {
                "lane": str(lane_id),
                "offset": parent_element.get("start", "0s"),
            },
        )
        insert_at = len(parent_element)
        for index, child in enumerate(list(parent_element)):
            if child.tag in {
                "marker",
                "chapter-marker",
                "rating",
                "keyword",
                "analysis-marker",
                "hidden-clip-marker",
                "audio-channel-source",
                "filter-video",
                "filter-video-mask",
                "filter-audio",
                "metadata",
            }:
                insert_at = index
                break
        parent_element.insert(insert_at, nested_spine)
        return nested_spine

    def _set_relative_offset(self, item_element, absolute_start, parent_element, default_format_id):
        parent_rate = self._element_rate(parent_element, default_format_id)
        parent_offset = to_rational_time(parent_element.get("offset", "0s"), parent_rate)
        parent_start = to_rational_time(parent_element.get("start", "0s"), parent_rate)
        relative_offset = absolute_start - parent_offset + parent_start
        item_element.set("offset", from_rational_time(relative_offset))

    def _element_for_collection_item(self, item, top_level=False):
        if isinstance(item, otio.schema.Timeline):
            return self._project_element(item)
        return self._element_for_track_item(item, None, top_level=top_level)

    def _element_for_track_item(self, item, default_format_id, top_level=False):
        if item.metadata.get(META_NAMESPACE, {}).get("audition"):
            return self._element_for_audition(item, default_format_id, top_level=top_level)
        if isinstance(item, otio.schema.Transition):
            return self._element_for_transition(item)
        if isinstance(item, otio.schema.Gap):
            return self._element_for_gap(item)
        if isinstance(item, otio.schema.Stack):
            return self._element_for_stack(item)
        if isinstance(item, otio.schema.Clip):
            return self._element_for_clip(item, top_level=top_level)
        raise TypeError(f"Unsupported OTIO item: {type(item)}")

    def _element_for_transition(self, transition):
        attrs = dict(transition.metadata.get(META_NAMESPACE, {}).get("transition", {}).get("attrs", {}))
        attrs.setdefault("name", transition.name)
        attrs["duration"] = from_rational_time(transition.in_offset + transition.out_offset)
        if _version_at_most(self.fcpxml_version, LEGACY_TRANSITION_VERSION_MAX):
            payload = transition.metadata.get(META_NAMESPACE, {}).get("transition", {})
            resource = (
                payload.get("resource")
                or payload.get("filter_video", {}).get("resource")
                or {"name": transition.name or "Cross Dissolve"}
            )
            attrs["ref"] = self._ensure_effect_resource(
                resource.get("name", transition.name or "Cross Dissolve"),
                resource,
            )
            return cElementTree.Element("transition", attrs)
        element = cElementTree.Element("transition", attrs)

        payload = transition.metadata.get(META_NAMESPACE, {}).get("transition", {})
        filter_video = payload.get("filter_video")
        if filter_video:
            element.append(self._effect_xml_from_payload("filter-video", filter_video))
        filter_audio = payload.get("filter_audio")
        if filter_audio:
            element.append(self._effect_xml_from_payload("filter-audio", filter_audio))
        return element

    def _effect_xml_from_payload(self, tag, payload):
        attrs = dict(payload.get("attrs", {}))
        attrs.setdefault("name", payload.get("name", ""))
        attrs["ref"] = self._ensure_effect_resource(
            attrs.get("name", ""),
            payload.get("resource", {}),
        )
        element = cElementTree.Element(tag, {k: v for k, v in attrs.items() if v != ""})
        for datum in payload.get("data", []):
            data_element = cElementTree.SubElement(element, "data")
            data_element.text = datum
        for param in payload.get("params", []):
            cElementTree.SubElement(element, "param", dict(param))
        return element

    def _element_for_gap(self, gap):
        attrs = {
            "name": "Gap",
            "offset": from_rational_time(gap.trimmed_range_in_parent().start_time if gap.parent() else otio.opentime.RationalTime(0, gap.duration().rate)),
            "start": from_rational_time(gap.source_range.start_time),
            "duration": from_rational_time(gap.duration()),
        }
        element = cElementTree.Element("gap", attrs)
        self._append_common_children(element, gap)
        return element

    def _element_for_stack(self, stack):
        container_type = stack.metadata.get(META_NAMESPACE, {}).get("container", "ref-clip")
        if container_type == "sync-clip":
            return self._element_for_sync_clip(stack)
        if _version_at_most(self.fcpxml_version, "1.1"):
            raise NotImplementedError(
                "FCPXML versions 1.0-1.1 do not support ref-clip containers."
            )
        if container_type in UNSUPPORTED_ACTIVE_STORY_TAGS:
            raise NotImplementedError(
                f"Writing '{container_type}' containers is not supported yet."
            )
        return self._element_for_ref_clip(stack)

    def _element_for_ref_clip(self, stack):
        media_id = self._ensure_media_resource(stack)
        attrs = dict(stack.metadata.get(META_NAMESPACE, {}).get("story", {}).get("attrs", {}))
        attrs["ref"] = media_id
        attrs.setdefault("name", stack.name)
        attrs["duration"] = from_rational_time(stack.duration())
        attrs.setdefault("offset", from_rational_time(stack.trimmed_range_in_parent().start_time if stack.parent() else otio.opentime.RationalTime(0, stack.duration().rate or DEFAULT_FRAME_RATE)))
        if stack.source_range is not None:
            attrs["start"] = from_rational_time(stack.source_range.start_time)
        element = cElementTree.Element("ref-clip", {k: v for k, v in attrs.items() if v != ""})
        self._append_common_children(element, stack)
        return element

    def _element_for_sync_clip(self, stack):
        if not _version_at_least(self.fcpxml_version, SYNC_CLIP_VERSION_MIN):
            raise NotImplementedError(
                f"FCPXML v{self.fcpxml_version} does not support sync-clip items."
            )
        attrs = dict(stack.metadata.get(META_NAMESPACE, {}).get("sync_clip", {}).get("attrs", {}))
        attrs.setdefault("name", stack.name)
        attrs["duration"] = from_rational_time(stack.duration())
        attrs.setdefault("offset", from_rational_time(stack.trimmed_range_in_parent().start_time if stack.parent() else otio.opentime.RationalTime(0, stack.duration().rate or DEFAULT_FRAME_RATE)))
        if stack.source_range is not None:
            attrs["start"] = from_rational_time(stack.source_range.start_time)
        format_id = self._ensure_format_for_stack(stack)
        attrs.setdefault("format", format_id)
        element = cElementTree.Element("sync-clip", {k: v for k, v in attrs.items() if v != ""})
        inner_spine = cElementTree.SubElement(element, "spine")
        self._append_tracks_to_spine(stack, inner_spine, format_id)
        self._append_common_children(element, stack, include_markers=False)
        return element

    def _element_for_clip(self, clip, top_level=False):
        if clip.metadata.get(META_NAMESPACE, {}).get("mc_clip"):
            return self._element_for_mc_clip(clip)
        if isinstance(clip.media_reference, otio.schema.GeneratorReference) and clip.media_reference.generator_kind == "fcpx.title":
            return self._element_for_title(clip)

        story_tag = clip.metadata.get(META_NAMESPACE, {}).get("story", {}).get("tag", "clip")
        if top_level and story_tag == "asset-clip" and _version_at_least(
            self.fcpxml_version,
            ASSET_CLIP_VERSION_MIN,
        ):
            return self._element_for_asset_clip(clip)

        element = cElementTree.Element(
            "clip",
            {
                "name": clip.name,
                "offset": from_rational_time(
                    clip.trimmed_range_in_parent().start_time
                    if clip.parent()
                    else otio.opentime.RationalTime(0, clip.duration().rate or DEFAULT_FRAME_RATE)
                ),
                "duration": from_rational_time(clip.duration()),
            },
        )
        if clip.source_range is not None and clip.source_range.start_time.value != 0:
            element.set("start", from_rational_time(clip.source_range.start_time))

        ref_id = self._ensure_asset_resource(clip)
        media_duration = from_rational_time(self._available_range_for_clip(clip).duration)
        if clip.parent() is not None and getattr(clip.parent(), "kind", None) == otio.schema.TrackKind.Audio:
            cElementTree.SubElement(
                element,
                "audio",
                {
                    "ref": ref_id,
                    "offset": "0s",
                    "duration": media_duration,
                },
            )
        else:
            cElementTree.SubElement(
                element,
                "video",
                {
                    "ref": ref_id,
                    "offset": "0s",
                    "duration": media_duration,
                },
            )
        self._append_common_children(element, clip)
        return element

    def _element_for_mc_clip(self, clip):
        if not _version_at_least(self.fcpxml_version, "1.1"):
            raise NotImplementedError(
                "FCPXML v1.0 does not support mc-clip items."
            )
        self._append_preserved_resource_xml(
            clip.metadata.get(META_NAMESPACE, {}).get("resource_xml")
        )
        media_id = self._ensure_raw_media_resource(clip)
        attrs = dict(clip.metadata.get(META_NAMESPACE, {}).get("mc_clip", {}).get("attrs", {}))
        attrs.setdefault("name", clip.name)
        attrs["ref"] = media_id
        attrs["duration"] = from_rational_time(clip.duration())
        attrs.setdefault(
            "offset",
            from_rational_time(
                clip.trimmed_range_in_parent().start_time
                if clip.parent()
                else otio.opentime.RationalTime(0, clip.duration().rate or DEFAULT_FRAME_RATE)
            ),
        )
        if clip.source_range is not None:
            attrs["start"] = from_rational_time(clip.source_range.start_time)
        element = cElementTree.Element("mc-clip", {k: v for k, v in attrs.items() if v != ""})
        self._append_common_children(
            element,
            clip,
            anchor_xml_keys=("mc_source_xml", "anchor_xml"),
        )
        return element

    def _element_for_audition(self, item, default_format_id, top_level=False):
        attrs = dict(item.metadata.get(META_NAMESPACE, {}).get("audition", {}).get("attrs", {}))
        self._append_preserved_resource_xml(
            item.metadata.get(META_NAMESPACE, {}).get("audition", {}).get("resource_xml")
        )
        element = cElementTree.Element("audition", {k: v for k, v in attrs.items() if v != ""})
        active_item = copy.deepcopy(item)
        active_item.metadata.setdefault(META_NAMESPACE, {}).pop("audition", None)
        active_element = self._element_for_track_item(
            active_item,
            default_format_id,
            top_level=top_level,
        )
        active_element.attrib.pop("offset", None)
        active_element.attrib.pop("lane", None)
        element.append(active_element)
        _append_raw_xml(
            element,
            item.metadata.get(META_NAMESPACE, {}).get("audition", {}).get("alternatives_xml"),
        )
        return element

    def _element_for_asset_clip(self, clip):
        ref_id = self._ensure_asset_resource(clip)
        attrs = dict(clip.media_reference.metadata.get(META_NAMESPACE, {}).get("asset_clip", {}).get("attrs", {}))
        attrs.setdefault("name", clip.name)
        attrs["ref"] = ref_id
        attrs["duration"] = from_rational_time(clip.duration())
        if _version_at_least(self.fcpxml_version, ASSET_CLIP_VERSION_MIN):
            attrs.setdefault("format", self._ensure_format_for_clip(clip))
        element = cElementTree.Element("asset-clip", {k: v for k, v in attrs.items() if v != ""})
        self._append_common_children(element, clip)
        return element

    def _element_for_title(self, clip):
        generator = clip.media_reference
        parameters = generator.parameters or {}
        effect_resource = {
            "name": parameters.get("effect_name", generator.name or clip.name),
            "uid": parameters.get("effect_uid", parameters.get("effect_name", generator.generator_kind)),
            "src": parameters.get("effect_src", ""),
        }
        effect_id = self._ensure_effect_resource(effect_resource["name"], effect_resource)
        attrs = dict(clip.metadata.get(META_NAMESPACE, {}).get("story", {}).get("attrs", {}))
        attrs.setdefault("name", clip.name)
        attrs["ref"] = effect_id
        attrs["duration"] = from_rational_time(clip.duration())
        attrs.setdefault(
            "offset",
            from_rational_time(
                clip.trimmed_range_in_parent().start_time
                if clip.parent()
                else otio.opentime.RationalTime(0, clip.duration().rate or DEFAULT_FRAME_RATE)
            ),
        )
        if clip.source_range is not None and clip.source_range.start_time.value != 0:
            attrs["start"] = from_rational_time(clip.source_range.start_time)
        element = cElementTree.Element("title", {k: v for k, v in attrs.items() if v != ""})
        text_xml, text_style_def_xml = self._uniquify_title_text_style_ids(
            parameters.get("text_xml"),
            parameters.get("text_style_def_xml"),
        )
        if _version_at_least(self.fcpxml_version, "1.3"):
            _append_raw_xml(element, parameters.get("param_xml"))
        _append_raw_xml(element, text_xml)
        if _version_at_least(self.fcpxml_version, "1.3"):
            _append_raw_xml(element, text_style_def_xml)
        self._append_common_children(
            element,
            clip,
            leading_children_position="append",
        )
        return element

    def _uniquify_title_text_style_ids(self, text_xml, text_style_def_xml):
        text_xml = list(text_xml or [])
        text_style_def_xml = list(text_style_def_xml or [])
        if not text_xml or not text_style_def_xml:
            return text_xml, text_style_def_xml

        id_map = {}
        remapped_defs = []
        for raw_xml in text_style_def_xml:
            text_style_def = cElementTree.fromstring(raw_xml)
            original_id = text_style_def.get("id")
            if original_id:
                self.text_style_id_count += 1
                new_id = f"ts_otio_{self.text_style_id_count}"
                id_map[original_id] = new_id
                text_style_def.set("id", new_id)
            remapped_defs.append(_raw_xml(text_style_def))

        if not id_map:
            return text_xml, remapped_defs

        remapped_text = []
        for raw_xml in text_xml:
            text_element = cElementTree.fromstring(raw_xml)
            for text_style in text_element.findall(".//text-style"):
                ref = text_style.get("ref")
                if ref in id_map:
                    text_style.set("ref", id_map[ref])
            remapped_text.append(_raw_xml(text_element))
        return remapped_text, remapped_defs

    def _append_common_children(
        self,
        element,
        item,
        include_markers=True,
        anchor_xml_keys=(),
        leading_children_position="prepend",
    ):
        fcpx_meta = item.metadata.get(META_NAMESPACE, {})
        leading_children = []
        trailing_filter_children = []

        if not getattr(item, "enabled", True):
            element.set("enabled", "0")

        note = fcpx_meta.get("note")
        if note:
            note_element = cElementTree.SubElement(element, "note")
            note_element.text = note
            leading_children.append(note_element)
            element.remove(note_element)

        for effect in getattr(item, "effects", []):
            category, effect_element = self._effect_element(effect)
            if effect_element is None:
                continue
            if category == "pre_story":
                leading_children.append(effect_element)
            else:
                trailing_filter_children.append(effect_element)

        if leading_children_position == "prepend":
            for child in reversed(leading_children):
                element.insert(0, child)
        else:
            for child in leading_children:
                element.append(child)

        for key in anchor_xml_keys:
            _append_raw_xml(element, fcpx_meta.get(key))

        if include_markers:
            for marker in getattr(item, "markers", []):
                element.append(self._marker_element(marker))
            _append_raw_xml(element, fcpx_meta.get("raw_marker_items"))
            if fcpx_meta.get("keywords"):
                for keyword in fcpx_meta["keywords"]:
                    cElementTree.SubElement(element, "keyword", dict(keyword))
            if fcpx_meta.get("ratings"):
                for rating in fcpx_meta["ratings"]:
                    cElementTree.SubElement(element, "rating", dict(rating))

        _append_raw_xml(element, fcpx_meta.get("legacy_audio_source_xml"))
        _append_raw_xml(
            element,
            _normalize_audio_source_xml(fcpx_meta.get("audio_channel_source_xml")),
        )
        _append_raw_xml(
            element,
            _normalize_audio_source_xml(fcpx_meta.get("audio_role_source_xml")),
        )
        _append_raw_xml(element, fcpx_meta.get("sync_source_xml"))

        for filter_element in trailing_filter_children:
            element.append(filter_element)

        metadata_entries = fcpx_meta.get("metadata")
        if metadata_entries:
            element.append(self._metadata_element(metadata_entries))

    def _append_preserved_resource_xml(self, raw_resource_xml):
        resources_element = self._ensure_resource_element()
        for raw_xml in raw_resource_xml or []:
            resource_element = cElementTree.fromstring(raw_xml)
            resource_id = resource_element.get("id")
            if resource_id and resources_element.find(f"./*[@id='{resource_id}']") is not None:
                self.used_resource_ids.add(resource_id)
                continue
            resources_element.append(resource_element)
            if resource_id:
                self.used_resource_ids.add(resource_id)

    def _resource_with_id(self, resource_id):
        if not resource_id:
            return None
        return self._ensure_resource_element().find(f"./*[@id='{resource_id}']")

    def _effect_element(self, effect):
        if isinstance(effect, otio.schema.FreezeFrame):
            return "pre_story", self._time_map_element(
                effect.metadata.get(META_NAMESPACE, {}).get("time_map")
            )
        if isinstance(effect, otio.schema.LinearTimeWarp):
            fcpx_meta = effect.metadata.get(META_NAMESPACE, {})
            if fcpx_meta.get("conform_rate"):
                return "pre_story", cElementTree.Element(
                    "conform-rate",
                    dict(fcpx_meta["conform_rate"]),
                )
            return "pre_story", self._time_map_element(fcpx_meta.get("time_map"))

        fcpx_meta = effect.metadata.get(META_NAMESPACE, {})
        tag = fcpx_meta.get("element")
        if tag in {"filter", "filter-video", "filter-audio"}:
            if _version_at_most(self.fcpxml_version, GENERIC_FILTER_VERSION_MAX):
                return "filter", self._legacy_filter_element(fcpx_meta)
            return "filter", self._effect_xml_from_payload(tag, fcpx_meta)
        if tag and tag.startswith("adjust-"):
            adjust_element = cElementTree.Element(
                tag,
                dict(fcpx_meta.get("attrs", {})),
            )
            for param in fcpx_meta.get("params", []):
                cElementTree.SubElement(adjust_element, "param", dict(param))
            _append_raw_xml(adjust_element, fcpx_meta.get("raw_children"))
            return "pre_story", adjust_element
        return None, None

    def _legacy_filter_element(self, payload):
        resource = payload.get("resource", {})
        attrs = dict(payload.get("attrs", {}))
        attrs["ref"] = self._ensure_effect_resource(
            resource.get("name", attrs.get("name", "")),
            resource,
        )
        element = cElementTree.Element("filter", {k: v for k, v in attrs.items() if k != "name" and v != ""})
        if self.fcpxml_version == "1.0":
            for param in payload.get("params", []):
                cElementTree.SubElement(element, "param", dict(param))
        return element

    def _time_map_element(self, payload):
        if not payload:
            return None
        time_map = cElementTree.Element("timeMap", dict(payload.get("attrs", {})))
        for point in payload.get("points", []):
            cElementTree.SubElement(time_map, "timept", dict(point))
        return time_map

    def _marker_element(self, marker):
        attrs = {
            "start": from_rational_time(marker.marked_range.start_time),
            "duration": from_rational_time(marker.marked_range.duration),
            "value": marker.name,
        }
        if marker.color == otio.schema.MarkerColor.GREEN:
            attrs["completed"] = "1"
        elif marker.color == otio.schema.MarkerColor.RED:
            attrs["completed"] = "0"
        return cElementTree.Element("marker", attrs)

    def _metadata_element(self, metadata_entries):
        metadata_element = cElementTree.Element("metadata")
        for entry in metadata_entries:
            md_attrs = {k: v for k, v in entry.items() if k != "array"}
            md_element = cElementTree.SubElement(metadata_element, "md", md_attrs)
            if entry.get("array"):
                array_element = cElementTree.SubElement(md_element, "array")
                for child in entry["array"]:
                    child_element = cElementTree.SubElement(
                        array_element,
                        child.get("tag", "string"),
                        dict(child.get("attrs", {})),
                    )
                    child_element.text = child.get("text", "")
        return metadata_element

    def _ensure_media_resource(self, stack):
        if id(stack) in self.media_ids:
            return self.media_ids[id(stack)]
        media_attrs = dict(stack.metadata.get(META_NAMESPACE, {}).get("media", {}).get("attrs", {}))
        preserved_media_id = media_attrs.pop("id", "")
        media_id = preserved_media_id or self._resource_id_generator()
        existing_media = self._resource_with_id(media_id)
        if existing_media is not None and existing_media.tag == "media":
            self.media_ids[id(stack)] = media_id
            self.used_resource_ids.add(media_id)
            return media_id
        if existing_media is not None:
            media_id = self._resource_id_generator()
        media_attrs["id"] = media_id
        media_attrs.setdefault("name", stack.name)
        self.used_resource_ids.add(media_id)
        media_element = cElementTree.SubElement(self._ensure_resource_element(), "media", media_attrs)
        media_element.append(self._stack_to_sequence(stack))
        self.media_ids[id(stack)] = media_id
        return media_id

    def _ensure_raw_media_resource(self, clip):
        if id(clip) in self.media_ids:
            return self.media_ids[id(clip)]
        fcpx_meta = clip.metadata.get(META_NAMESPACE, {})
        media_attrs = dict(fcpx_meta.get("media", {}).get("attrs", {}))
        preserved_media_id = media_attrs.pop("id", "")
        media_id = preserved_media_id or self._resource_id_generator()
        existing_media = self._resource_with_id(media_id)
        if existing_media is not None and existing_media.tag == "media":
            self.media_ids[id(clip)] = media_id
            self.used_resource_ids.add(media_id)
            return media_id
        if existing_media is not None:
            media_id = self._resource_id_generator()
        media_attrs["id"] = media_id
        media_attrs.setdefault("name", clip.name)
        self.used_resource_ids.add(media_id)
        media_element = cElementTree.SubElement(self._ensure_resource_element(), "media", media_attrs)
        if fcpx_meta.get("multicam_xml"):
            media_element.append(cElementTree.fromstring(fcpx_meta["multicam_xml"]))
        elif fcpx_meta.get("media_sequence_xml"):
            media_element.append(cElementTree.fromstring(fcpx_meta["media_sequence_xml"]))
        else:
            raise NotImplementedError(
                "mc-clip items require preserved media or multicam metadata to write."
            )
        self.media_ids[id(clip)] = media_id
        return media_id

    def _ensure_effect_resource(self, name, resource_payload):
        resource_payload = dict(resource_payload or {})
        key = (
            name,
            resource_payload.get("uid", ""),
            resource_payload.get("src", ""),
        )
        if key in self.effect_ids:
            return self.effect_ids[key]
        effect_id = resource_payload.pop("id", "") or self._resource_id_generator()
        existing_effect = self._resource_with_id(effect_id)
        if existing_effect is not None and existing_effect.tag == "effect":
            self.effect_ids[key] = effect_id
            self.used_resource_ids.add(effect_id)
            return effect_id
        if existing_effect is not None:
            effect_id = self._resource_id_generator()
        effect_attrs = {
            "id": effect_id,
            "name": name,
            "uid": resource_payload.get("uid", name or effect_id),
        }
        if resource_payload.get("src"):
            effect_attrs["src"] = resource_payload["src"]
        cElementTree.SubElement(self._ensure_resource_element(), "effect", effect_attrs)
        self.effect_ids[key] = effect_id
        self.used_resource_ids.add(effect_id)
        return effect_id

    def _ensure_asset_resource(self, clip):
        media_reference = clip.media_reference
        if media_reference is None or media_reference.is_missing_reference:
            key = (clip.name, "missing")
            target_url = f"file:///tmp/{clip.name}"
            asset_meta = {}
        else:
            asset_meta = media_reference.metadata.get(META_NAMESPACE, {}).get("asset", {})
            target_url = media_reference.target_url
            key = (
                target_url,
                from_rational_time(self._available_range_for_clip(clip).start_time),
                from_rational_time(self._available_range_for_clip(clip).duration),
                self._ensure_format_for_clip(clip),
            )

        if key in self.asset_ids:
            return self.asset_ids[key]

        format_id = self._ensure_format_for_clip(clip)
        available_range = self._available_range_for_clip(clip)
        asset_attrs = dict(asset_meta.get("attrs", {}))
        preserved_asset_id = asset_attrs.pop("id", "")
        asset_attrs.pop("src", None)
        asset_id = preserved_asset_id or self._resource_id_generator()
        existing_asset = self._resource_with_id(asset_id)
        if existing_asset is not None and existing_asset.tag == "asset":
            self.asset_ids[key] = asset_id
            self.used_resource_ids.add(asset_id)
            return asset_id
        if existing_asset is not None:
            asset_id = self._resource_id_generator()
        asset_attrs["id"] = asset_id
        asset_attrs.setdefault("name", clip.name)
        if _version_at_least(self.fcpxml_version, ASSET_FORMAT_VERSION_MIN):
            asset_attrs["format"] = format_id
        else:
            asset_attrs.pop("format", None)
        asset_attrs["start"] = from_rational_time(available_range.start_time)
        asset_attrs["duration"] = from_rational_time(available_range.duration)
        asset_attrs.setdefault("hasVideo", "0")
        asset_attrs.setdefault("hasAudio", "0")
        if clip.parent() is None:
            asset_attrs["hasVideo"] = "1"
            asset_attrs["hasAudio"] = "1"
        elif getattr(clip.parent(), "kind", None) == otio.schema.TrackKind.Audio:
            asset_attrs["hasAudio"] = "1"
        else:
            asset_attrs["hasVideo"] = "1"
        asset_element = cElementTree.SubElement(self._ensure_resource_element(), "asset", asset_attrs)

        if _version_at_least(self.fcpxml_version, MEDIA_REP_VERSION_MIN):
            media_reps = asset_meta.get("media_reps") or [
                {
                    "kind": "original-media",
                    "src": target_url or f"file:///tmp/{clip.name}",
                }
            ]
            for media_rep in media_reps:
                rep_attrs = dict(media_rep)
                rep_attrs.setdefault("kind", "original-media")
                if not rep_attrs.get("src"):
                    rep_attrs["src"] = target_url or f"file:///tmp/{clip.name}"
                cElementTree.SubElement(asset_element, "media-rep", rep_attrs)
        else:
            asset_element.set("src", target_url or f"file:///tmp/{clip.name}")
            if _version_at_least(self.fcpxml_version, "1.2"):
                _append_raw_xml(asset_element, asset_meta.get("bookmark_xml"))

        metadata_entries = asset_meta.get("metadata")
        if metadata_entries and _version_at_least(self.fcpxml_version, "1.2"):
            asset_element.append(self._metadata_element(metadata_entries))

        self.asset_ids[key] = asset_id
        self.used_resource_ids.add(asset_id)
        return asset_id

    def _ensure_format_for_stack(self, stack):
        for track in stack:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    return self._ensure_format_for_clip(item)
                if isinstance(item, otio.schema.Stack):
                    return self._ensure_format_for_stack(item)
        return self._ensure_format({}, DEFAULT_FRAME_RATE, f"FFVideoFormatRateUndefined")

    def _ensure_format_for_clip(self, clip):
        format_attrs = {}
        if clip.media_reference is not None and not clip.media_reference.is_missing_reference:
            format_attrs = clip.media_reference.metadata.get(META_NAMESPACE, {}).get("asset", {}).get("format", {})
        if not format_attrs:
            format_attrs = clip.metadata.get(META_NAMESPACE, {}).get("format", {})
        rate = clip.duration().rate or DEFAULT_FRAME_RATE
        name = format_attrs.get("name") or format_name(rate, getattr(clip.media_reference, "target_url", ""))
        return self._ensure_format(format_attrs, rate, name)

    def _ensure_format(self, format_attrs, rate, fallback_name):
        attrs = dict(format_attrs)
        preserved_format_id = attrs.pop("id", "")
        attrs.setdefault("name", fallback_name or "FFVideoFormatRateUndefined")
        if "frameDuration" not in attrs and rate in FRAMERATE_FRAMEDURATION:
            attrs["frameDuration"] = FRAMERATE_FRAMEDURATION[float(rate)]
        key = tuple(sorted(attrs.items()))
        if key in self.format_ids:
            return self.format_ids[key]
        format_id = preserved_format_id or self._resource_id_generator()
        existing_format = self._resource_with_id(format_id)
        if existing_format is not None and existing_format.tag == "format":
            self.format_ids[key] = format_id
            self.used_resource_ids.add(format_id)
            return format_id
        if existing_format is not None:
            format_id = self._resource_id_generator()
        attrs["id"] = format_id
        cElementTree.SubElement(self._ensure_resource_element(), "format", attrs)
        self.format_ids[key] = format_id
        self.used_resource_ids.add(format_id)
        return format_id

    def _available_range_for_clip(self, clip):
        media_reference = clip.media_reference
        if (
            media_reference is not None
            and not media_reference.is_missing_reference
            and media_reference.available_range is not None
        ):
            return media_reference.available_range
        return clip.source_range or otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, clip.duration().rate or DEFAULT_FRAME_RATE),
            duration=clip.duration(),
        )

    def _element_rate(self, element, default_format_id):
        format_id = element.get("format", default_format_id)
        if element.tag in {"asset-clip", "video", "audio"} and element.get("ref"):
            format_id = self._asset_format_id_for_resource(element.get("ref")) or format_id
        elif element.tag == "clip":
            child = element.find("./video") or element.find("./audio")
            if child is not None and child.get("ref"):
                format_id = self._asset_format_id_for_resource(child.get("ref")) or format_id
        return self._format_rate_from_id(format_id)

    def _asset_format_id_for_resource(self, asset_id):
        asset_element = self.resource_element.find(f"./asset[@id='{asset_id}']")
        if asset_element is None:
            return None
        return asset_element.get("format")

    def _format_rate_from_id(self, format_id):
        format_element = self.resource_element.find(f"./format[@id='{format_id}']")
        if format_element is None:
            return DEFAULT_FRAME_RATE
        frame_duration = format_element.get("frameDuration")
        if not frame_duration:
            return _parse_text_rate_from_name(format_element.get("name")) or DEFAULT_FRAME_RATE
        match = FRAME_DURATION_RE.match(frame_duration)
        if not match:
            return DEFAULT_FRAME_RATE
        total = Fraction(match.group(1))
        rate = Fraction(match.group(2))
        return float(rate / total)

    def _resource_id_generator(self):
        while True:
            self.resource_count += 1
            candidate = f"r{self.resource_count}"
            if candidate in self.used_resource_ids:
                continue
            return candidate


def read_from_string(input_str):
    """OTIO adapter entrypoint."""

    return FcpxXml(input_str).to_otio()


def read_from_file(filepath):
    """OTIO adapter entrypoint."""

    return read_from_string(_readable_fcpx_path(filepath).read_text(encoding="utf-8"))


def write_to_string(input_otio, fcpxml_version=SUPPORTED_WRITE_VERSION):
    """OTIO adapter entrypoint."""

    return FcpxOtio(input_otio, fcpxml_version=fcpxml_version).to_xml()


def write_to_file(input_otio, filepath, fcpxml_version=SUPPORTED_WRITE_VERSION):
    """OTIO adapter entrypoint."""

    output_path = Path(filepath)
    xml_string = write_to_string(input_otio, fcpxml_version=fcpxml_version)

    if output_path.suffix.lower() == ".fcpxmld":
        if output_path.exists():
            raise FileExistsError(f"'{output_path}' exists, will not overwrite.")
        if not output_path.parent.exists():
            raise FileNotFoundError(
                f"Directory '{output_path.parent}' does not exist, cannot create "
                f"'{output_path}'."
            )
        if not output_path.parent.is_dir():
            raise NotADirectoryError(
                f"'{output_path.parent}' is not a directory, cannot create "
                f"'{output_path}'."
            )

        output_path.mkdir()
        (output_path / FCPXMLD_INFO_FILENAME).write_text(xml_string, encoding="utf-8")
        return str(output_path)

    output_path.write_text(xml_string, encoding="utf-8")
    return str(output_path)
