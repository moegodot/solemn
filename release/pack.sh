#!/bin/sh
set -eu

tar -c -f release.tar --format pax \
  SarasaMonoSC-Bold.ttf \
  SarasaMonoSC-BoldItalic.ttf \
  SarasaMonoSC-Italic.ttf \
  SarasaMonoSC-Regular.ttf
zstd --ultra -22 --force --format=zstd -o release.tar.zstd release.tar
rm -f \
  SarasaMonoSC-Bold.ttf \
  SarasaMonoSC-BoldItalic.ttf \
  SarasaMonoSC-Italic.ttf \
  SarasaMonoSC-Regular.ttf \
  release.tar
