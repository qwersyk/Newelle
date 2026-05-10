#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(sed -n "s/.*version: '\\([^']*\\)'.*/\\1/p" "$ROOT/meson.build" | head -n 1)"
ARCH="${NEWELLE_ARCH:-$(uname -m)}"
APP_DIR="${NEWELLE_APP_DIR:-$ROOT/dist/Newelle.app}"
DMG_PATH="${NEWELLE_DMG_PATH:-$ROOT/dist/Newelle-${VERSION}-macos-${ARCH}.dmg}"
TEMP_BUILD_DIR=""
TEMP_DMG_DIR=""

cleanup() {
  if [[ -n "$TEMP_BUILD_DIR" ]] && [[ -d "$TEMP_BUILD_DIR" ]]; then
    rm -rf "$TEMP_BUILD_DIR"
  fi
  if [[ -n "$TEMP_DMG_DIR" ]] && [[ -d "$TEMP_DMG_DIR" ]]; then
    rm -rf "$TEMP_DMG_DIR"
  fi
}
trap cleanup EXIT

if [[ -z "${NEWELLE_APP_DIR:-}" ]] && [[ -e "$APP_DIR" ]] && [[ ! -w "$APP_DIR" ]]; then
  TEMP_BUILD_DIR="$(mktemp -d "$ROOT/.macos-app-build.XXXXXX")"
  APP_DIR="$TEMP_BUILD_DIR/Newelle.app"
  export NEWELLE_APP_DIR="$APP_DIR"
  echo "Using temporary app build dir because dist/Newelle.app is not writable"
fi

if [[ "${NEWELLE_SKIP_APP_BUILD:-0}" != "1" ]]; then
  /usr/bin/env bash "$ROOT/macos/build_newelle_app.sh"
fi

if [[ -e "$DMG_PATH" ]] && ! rm -f "$DMG_PATH" 2>/dev/null; then
  echo "Cannot replace $DMG_PATH"
  echo "It is likely owned by root from an earlier sudo build. Remove it manually or run without sudo."
  exit 1
fi

TEMP_DMG_DIR="$(mktemp -d "$ROOT/.macos-dmg.XXXXXX")"
/usr/bin/ditto "$APP_DIR" "$TEMP_DMG_DIR/Newelle.app"
ln -s /Applications "$TEMP_DMG_DIR/Applications"

/usr/bin/hdiutil create \
  -volname "Newelle" \
  -srcfolder "$TEMP_DMG_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Created $DMG_PATH"
