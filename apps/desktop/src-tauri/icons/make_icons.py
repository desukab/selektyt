#!/usr/bin/env python3
"""Generate the SelekyT desktop app icons (PNG / ICO / ICNS) with no third-party
dependencies.

The mark is the shell's own score-canvas idiom: a dark field with a row of
energy bars in the product teal, an amber threshold line, and a 9:16 crop
marker. Run from the repo root:

    python apps/desktop/src-tauri/icons/make_icons.py

ICO embeds PNG-encoded frames (valid since Windows Vista) and ICNS embeds PNG
payloads in the standard ``ic07``..``ic14`` slots, so no platform tooling is
needed to produce bundleable icon files. The writer round-trips every emitted
file: PNGs are re-decoded and their pixels compared against the source buffer,
and the ICO/ICNS containers are re-parsed and every embedded PNG re-decoded.
That self-check is what this host CAN verify; whether the icons render
correctly in the Windows Explorer / Finder shell is a native-verification gap
recorded in docs/packaging.md.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent

# Palette mirroring web/style.css (dark field, teal bars, amber threshold).
BG = (0x10, 0x13, 0x18)
BG_EDGE = (0x1B, 0x22, 0x2B)
TEAL = (0x4E, 0xC9, 0xA8)
TEAL_DIM = (0x2E, 0x6B, 0x59)
AMBER = (0xF0, 0xA6, 0x5A)
WHITE = (0xE8, 0xEC, 0xF1)

PNG_SLOTS = {16: "16x16.png", 32: "32x32.png", 64: "64x64.png",
             128: "128x128.png", 256: "128x128@2x.png", 512: "512x512.png",
             1024: "icon.png"}

# ICNS PNG ostypes by square size.
_ICNS_TYPE = {16: "ic11", 32: "ic12", 64: "ic13", 128: "ic07",
              256: "ic08", 512: "ic09", 1024: "ic10"}


# ── rasterisation ──

def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def render(size: int) -> bytes:
    """RGBA pixel buffer of the icon at ``size`` pixels square."""
    px = bytearray(size * size * 4)
    # Deterministic pseudo-random energy profile, stable across sizes.
    bars = 13

    def bar_height(i: int) -> float:
        x = (i * 0.7 + 1.0) % 1.6180339887
        return 0.18 + 0.62 * abs(((x * 3.1) % 1.0) - 0.5) * 2.0

    for y in range(size):
        for x in range(size):
            u, v = x / max(size - 1, 1), y / max(size - 1, 1)
            col = _lerp(BG, BG_EDGE, (u + v) / 2)
            # rounded-rect field: clear corners outside a 22% radius
            r = 0.22
            dx, dy = abs(u - 0.5) - (0.5 - r), abs(v - 0.5) - (0.5 - r)
            outside = dx > 0 and dy > 0 and (dx * dx + dy * dy) > r * r
            if outside:
                col = (0, 0, 0, 0)
            gap = size * 0.035
            bw = (size - gap * (bars + 1)) / bars
            for i in range(bars):
                bx0 = gap + i * (bw + gap)
                if bx0 <= x < bx0 + bw:
                    h = bar_height(i) * size * 0.66
                    by1 = size * 0.82 - h
                    by0 = size * 0.82
                    thr_y = size * 0.82 - 0.5 * size * 0.66
                    if by1 <= y <= by0:
                        col = TEAL if (y - by1) / max(h, 1) > 0.12 else TEAL_DIM
                        # tallest bar gets the amber "selected" cap
                        if i == 6 and y - by1 < size * 0.055:
                            col = AMBER
                    elif abs(y - thr_y) < max(size * 0.012, 1.0):
                        col = AMBER
            # 9:16 crop bracket, top-right
            cw, ch = size * 0.20, size * 0.34
            cx0, cy0 = size - size * 0.10 - cw, size * 0.10
            near = (abs(x - cx0) < max(size * 0.012, 1) and cy0 <= y <= cy0 + ch) or \
                   (abs(y - (cy0 + ch)) < max(size * 0.012, 1) and cx0 <= x <= cx0 + cw)
            if near and not outside:
                col = WHITE
            idx = (y * size + x) * 4
            px[idx:idx + 4] = bytes(col) if len(col) == 4 else bytes(col) + b"\xff"
            if len(col) == 3:
                px[idx + 3] = 255
    return bytes(px)


# ── PNG encode/decode ──

def encode_png(w: int, h: int, rgba: bytes) -> bytes:
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter type 0 (None)
        raw.extend(rgba[y * stride:(y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return sig + chunk(b"IHDR", ihdr + b"") + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) \
        + chunk(b"IEND", b"")


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Minimal decoder: verifies CRCs and reverses filter 0 (None) only.

    Our own encoder only ever emits filter type 0, so this is sufficient as a
    round-trip self-check; it is deliberately not a general PNG decoder.
    """
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    w = h = bitd = 0
    idat = bytearray()
    stride = 0
    out = bytearray()
    while pos < len(data):
        ln, = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + ln]
        crc, = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])
        if zlib.crc32(tag + payload) & 0xFFFFFFFF != crc:
            raise ValueError(f"bad CRC in {tag!r}")
        if tag == b"IHDR":
            w, h, bitd, ctype = struct.unpack(">IIBB", payload[:10])
            if bitd != 8 or ctype != 6:
                raise ValueError("only 8-bit RGBA is supported here")
            stride = w * 4
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(bytes(idat))
    for y in range(h):
        ftype = raw[y * (stride + 1)]
        if ftype != 0:
            raise ValueError(f"unexpected filter type {ftype} on row {y}")
        out.extend(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
    return w, h, bytes(out)


# ── ICO / ICNS ──

def encode_ico(frames: list[tuple[int, bytes]]) -> bytes:
    """PNG-in-ICO (valid on Windows Vista and later)."""
    header = struct.pack("<HHH", 0, 1, len(frames))
    entries = bytearray()
    data = bytearray()
    for size, png in frames:
        # 256 is stored as 0 in the width/height bytes, per the ICO spec.
        dim = 0 if size >= 256 else size
        offset = len(header) + 16 * len(frames) + len(data)
        entries += struct.pack(
            # width, height, colorCount, reserved, planes, bitCount, size, offset
            "<BBBBHHII", dim, dim, 0, 0, 0, 32, len(png), offset)
        data += png
    return header + bytes(entries) + bytes(data)


def encode_icns(frames: list[tuple[int, bytes]]) -> bytes:
    body = bytearray()
    for size, png in frames:
        body += _ICNS_TYPE[size].encode("ascii") + struct.pack(">I", len(png) + 8) + png
    return b"icns" + struct.pack(">I", len(body) + 8) + bytes(body)


def parse_ico(data: bytes) -> list[tuple[int, bytes]]:
    reserved, count = struct.unpack("<HH", data[:4])
    assert reserved == 0 and count >= 1
    out = []
    pos = 6  # 6-byte ICONDIR header, then 16-byte ICONDIRENTRY records
    for _ in range(count):
        w, _h, _c, _r, _planes, _bits, ln, off = struct.unpack(
            "<BBBBHHII", data[pos:pos + 16])
        out.append((w or 256, data[off:off + ln]))
        pos += 16
    return out


def parse_icns(data: bytes) -> list[tuple[str, bytes]]:
    assert data[:4] == b"icns"
    total, = struct.unpack(">I", data[4:8])
    assert total == len(data), f"icns length header {total} != file {len(data)}"
    out, pos = [], 8
    while pos < len(data):
        tag = data[pos:pos + 4].decode("ascii")
        ln, = struct.unpack(">I", data[pos + 4:pos + 8])
        assert ln >= 8 and pos + ln <= len(data), f"bad icns entry {tag}"
        out.append((tag, data[pos + 8:pos + ln]))
        pos += ln
    return out


# ── driver ──

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frames: dict[int, bytes] = {}
    for size, name in PNG_SLOTS.items():
        rgba = render(size)
        png = encode_png(size, size, rgba)
        # round-trip self-check: decode must reproduce the exact pixels
        dw, dh, drgba = decode_png(png)
        assert (dw, dh) == (size, size) and drgba == rgba, f"PNG round-trip failed at {size}"
        frames[size] = png
        (OUT / name).write_bytes(png)
        print(f"wrote {name} ({size}x{size}, {len(png)} bytes)")

    ico_frames = [(s, frames[s]) for s in (16, 32, 64, 128, 256)]
    ico = encode_ico(ico_frames)
    for size, png in parse_ico(ico):
        decode_png(png)  # every embedded frame must be a decodable PNG
    (OUT / "icon.ico").write_bytes(ico)
    print(f"wrote icon.ico ({len(ico)} bytes, {len(ico_frames)} frames)")

    icns_frames = [(s, frames[s]) for s in (32, 64, 128, 256, 512, 1024)]
    icns = encode_icns(icns_frames)
    parsed = parse_icns(icns)
    assert len(parsed) == len(icns_frames)
    for _tag, png in parsed:
        decode_png(png)
    (OUT / "icon.icns").write_bytes(icns)
    print(f"wrote icon.icns ({len(icns)} bytes, {len(parsed)} frames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
