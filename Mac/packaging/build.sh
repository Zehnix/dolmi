#!/bin/zsh
# Builds dist/Dolmi.app and dist/Dolmi-<version>.dmg (Apple Silicon or Intel, whichever builds it).
# Run from anywhere: zsh Mac/packaging/build.sh   (needs Xcode Command Line Tools: xcode-select --install)
# The program is the shared code at the repo root (see dolmi.spec); Mac/ holds the venv, the audio
# helper and the build output.
set -euo pipefail
MAC="${0:A:h:h}"
cd "$MAC"
VERSION=$(cat ../VERSION)          # one VERSION for Windows and Mac, at the repo root

echo "[1/5] Python environment"
[[ -x venv/bin/python ]] || /usr/bin/env python3.12 -m venv venv
venv/bin/python -m pip install -q --upgrade pip
venv/bin/python -m pip install -q -r requirements.txt pyinstaller

echo "[2/5] System audio helper"
swiftc -O audio/dolmi-audio.swift -o audio/dolmi-audio

echo "[3/5] Dolmi.app"
rm -rf build dist
venv/bin/pyinstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build packaging/dolmi.spec

echo "[4/5] Sign"
# A fixed local certificate keeps the System Audio Recording permission across rebuilds
# (ad-hoc signatures change every build). Not notarized: other Macs still ask once (right-click → Open).
# On CI (GitHub Actions sets CI=true) a throwaway runner certificate means nothing to users: sign ad hoc.
[[ -n "${CI:-}" ]] || ./packaging/make-signing-identity.sh || true
if [[ -z "${CI:-}" ]] && security find-certificate -c "Dolmi Local Signing" >/dev/null 2>&1; then
  codesign --force --deep --sign "Dolmi Local Signing" dist/Dolmi.app
else
  echo "    no local signing certificate: ad-hoc signing (macOS asks for audio permission again after rebuilds)"
  codesign --force --deep --sign - dist/Dolmi.app
fi
codesign --verify --deep --strict dist/Dolmi.app

echo "[5/5] Disk image"
STAGE=$(mktemp -d)
cp -R dist/Dolmi.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Dolmi $VERSION" -srcfolder "$STAGE" -ov -format UDZO "dist/Dolmi-$VERSION.dmg"
rm -rf "$STAGE"
echo "Done: dist/Dolmi.app and dist/Dolmi-$VERSION.dmg"
