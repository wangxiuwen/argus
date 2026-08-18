#!/bin/sh
set -eu
dmg=$1
bundle=$2
mount=$(mktemp -d /tmp/mira-vpipe.XXXXXX)
cleanup() { hdiutil detach "$mount" >/dev/null 2>&1 || true; rmdir "$mount" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
hdiutil attach "$dmg" -readonly -nobrowse -mountpoint "$mount" >/dev/null
source_app="$mount/Vpipe Manager.app/Contents"
rm -f "$bundle/Contents/Helpers/vpipe"
rm -rf "$bundle/Contents/Frameworks" "$bundle/Contents/Resources/Licenses"
mkdir -p "$bundle/Contents/Helpers" "$bundle/Contents/Frameworks" "$bundle/Contents/Resources/Licenses"
cp "$source_app/Helpers/vpipe" "$bundle/Contents/Helpers/"
cp -R "$source_app/Frameworks/"*.dylib "$bundle/Contents/Frameworks/"
cp -R "$source_app/Resources/Licenses/"* "$bundle/Contents/Resources/Licenses/"
chmod 755 "$bundle/Contents/Helpers/vpipe" "$bundle/Contents/Frameworks/"*.dylib
