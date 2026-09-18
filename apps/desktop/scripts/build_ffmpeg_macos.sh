#!/usr/bin/env bash
# Build LGPL ffmpeg/ffprobe for macOS from the pinned FFmpeg source tarball.
#
# Why from source: every published *static* macOS ffmpeg binary we could
# verify (osxexperts.net, evermeet.cx) is configured --enable-gpl. SelekyT is
# MIT-licensed, and shipping a GPL ffmpeg would force the combined work under
# the GPL. Building the LGPL configuration ourselves is the only way to keep
# the app redistributable on the terms it claims. Windows uses a prebuilt
# LGPL static build (fetch_ffmpeg.py) because a trustworthy one exists there.
#
# Run on macOS (arm64 or x86_64) from the repo root:
#   bash apps/desktop/scripts/build_ffmpeg_macos.sh
# CI runs this in .github/workflows/desktop-macos.yml.
set -euo pipefail

FFMPEG_VERSION="8.1.2"
FFMPEG_URL="https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz"
FFMPEG_SHA256="REPLACE_WITH_VERIFIED_SHA256"

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESOURCES="${DESKTOP}/resources/bin"
CACHE="${HOME}/.cache/selekyt-packaging"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

mkdir -p "${CACHE}" "${RESOURCES}"

echo "== ffmpeg ${FFMPEG_VERSION} (LGPL) for $(uname -m) =="

TARBALL="${CACHE}/ffmpeg-${FFMPEG_VERSION}.tar.xz"
if [[ ! -f "${TARBALL}" ]]; then
  echo "downloading ${FFMPEG_URL}"
  curl -fSL --retry 3 -o "${TARBALL}.part" "${FFMPEG_URL}"
  mv "${TARBALL}.part" "${TARBALL}"
fi

ACTUAL="$(shasum -a 256 "${TARBALL}" | cut -d' ' -f1)"
if [[ "${ACTUAL}" != "${FFMPEG_SHA256}" ]]; then
  echo "FATAL: sha256 mismatch for the ffmpeg source tarball" >&2
  echo "  recorded: ${FFMPEG_SHA256}" >&2
  echo "  actual:   ${ACTUAL}" >&2
  exit 1
fi
echo "sha256 verified: ${ACTUAL}"

BUILD="${CACHE}/ffmpeg-${FFMPEG_VERSION}-build"
rm -rf "${BUILD}"
mkdir -p "${BUILD}"
tar -xJf "${TARBALL}" -C "${BUILD}"
SRC="${BUILD}/ffmpeg-${FFMPEG_VERSION}"

# LGPL and nothing else: no GPL libraries, no version3 GPL code, no nonfree.
# --enable-shared would need dylibs next to the binary; static keeps PATH
# discovery sufficient.
cd "${SRC}"
./configure \
  --prefix="${RESOURCES}/ffmpeg-prefix" \
  --disable-gpl \
  --disable-version3 \
  --disable-nonfree \
  --disable-doc \
  --disable-programs \
  --enable-ffmpeg \
  --enable-ffprobe \
  --disable-ffplay \
  --disable-network \
  --enable-static \
  --disable-shared \
  --enable-pic \
  CFLAGS="-O2"

make -j"${JOBS}"
make install

for tool in ffmpeg ffprobe; do
  BIN="${RESOURCES}/ffmpeg-prefix/bin/${tool}"
  if [[ ! -x "${BIN}" ]]; then
    echo "FATAL: ${tool} was not produced by the build" >&2
    exit 1
  fi
  install -m 0755 "${BIN}" "${RESOURCES}/${tool}"
  echo "installed resources/bin/${tool} ($(du -h "${BIN}" | cut -f1))"
done

rm -rf "${RESOURCES}/ffmpeg-prefix"

{
  echo "# Bundled ffmpeg/ffprobe provenance (macOS, built from source)."
  echo "# SelekyT is MIT-licensed; these binaries are FFmpeg LGPL builds."
  echo "source = ${FFMPEG_URL}"
  echo "version = ${FFMPEG_VERSION}"
  echo "configure = --disable-gpl --disable-version3 --disable-nonfree --disable-network (LGPL)"
  echo "archive_sha256 = ${ACTUAL}"
  echo "host = $(uname -s) $(uname -m)"
} > "${RESOURCES}/ffmpeg.sources.txt"

echo "smoke: $(("${RESOURCES}/ffmpeg" -version | head -n1))"
