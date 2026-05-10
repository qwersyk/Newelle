#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BREW_PREFIX="${HOMEBREW_PREFIX:-$(brew --prefix)}"
PYTHON_VERSION="3.13"
VERSION="$(sed -n "s/.*version: '\\([^']*\\)'.*/\\1/p" "$ROOT/meson.build" | head -n 1)"
ARCH="${NEWELLE_ARCH:-$(uname -m)}"

APP_DIR="${NEWELLE_APP_DIR:-$ROOT/dist/Newelle.app}"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
FRAMEWORKS_DIR="$CONTENTS_DIR/Frameworks"
APP_ROOT="$RESOURCES_DIR/app"
SHARE_DIR="$RESOURCES_DIR/share"
LIB_DIR="$RESOURCES_DIR/lib"
BIN_DIR="$RESOURCES_DIR/bin"
ICON_SVG="$ROOT/data/icons/hicolor/scalable/apps/io.github.qwersyk.Newelle.svg"
ICON_NAME="Newelle"
LAUNCHER_SRC="$ROOT/macos/newelle_launcher.c"
BREW_PYTHON_BIN="$BREW_PREFIX/bin/python${PYTHON_VERSION}"
BREW_PYTHON_CONFIG_BIN="$BREW_PREFIX/bin/python${PYTHON_VERSION}-config"
RSVG_CONVERT_BIN="$BREW_PREFIX/bin/rsvg-convert"
read -r -a PYTHON_EMBED_CFLAGS <<< "$("$BREW_PYTHON_CONFIG_BIN" --embed --cflags)"
read -r -a PYTHON_EMBED_LDFLAGS <<< "$("$BREW_PYTHON_CONFIG_BIN" --embed --ldflags)"
PYTHON_FRAMEWORK_SOURCE="$BREW_PREFIX/opt/python@${PYTHON_VERSION}/Frameworks/Python.framework/Versions/${PYTHON_VERSION}"
PYTHON_DEST_VERSION_DIR="$FRAMEWORKS_DIR/Python.framework/Versions/$PYTHON_VERSION"
PYTHON_DEST_SITE_PACKAGES="$PYTHON_DEST_VERSION_DIR/lib/python${PYTHON_VERSION}/site-packages"
BREW_SITE_PACKAGES="$BREW_PREFIX/lib/python${PYTHON_VERSION}/site-packages"
VENV_DIR="$ROOT/.venv-macos"
VENV_SITE_PACKAGES="$VENV_DIR/lib/python${PYTHON_VERSION}/site-packages"
GLIB_COMPILE_SCHEMAS="$BREW_PREFIX/bin/glib-compile-schemas"
GLIB_COMPILE_RESOURCES="$BREW_PREFIX/bin/glib-compile-resources"
MSGFMT="$BREW_PREFIX/bin/msgfmt"

list_macho_files() {
  /usr/bin/python3 - "$1" <<'PY'
from pathlib import Path
import sys

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}

root = Path(sys.argv[1])
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    try:
        with path.open("rb") as handle:
            if handle.read(4) in MACHO_MAGICS:
                print(path, end="\0")
    except OSError:
        pass
PY
}

sign_bundle() {
  while IFS= read -r -d '' file; do
    /usr/bin/codesign --force --sign - --timestamp=none "$file"
  done < <(list_macho_files "$APP_DIR")

  /usr/bin/codesign --force --sign - --timestamp=none "$PYTHON_DEST_VERSION_DIR/Resources/Python.app"
  /usr/bin/codesign --force --sign - --timestamp=none "$PYTHON_DEST_VERSION_DIR"
  /usr/bin/codesign --force --sign - --timestamp=none "$FRAMEWORKS_DIR/Python.framework"
  /usr/bin/codesign --force --sign - --timestamp=none "$APP_DIR"
}

if [[ ! -d "$PYTHON_FRAMEWORK_SOURCE" ]]; then
  echo "Python framework not found at $PYTHON_FRAMEWORK_SOURCE"
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$BREW_PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --disable-pip-version-check -r "$ROOT/macos/requirements.txt"

if [[ -e "$APP_DIR" ]] && ! rm -rf "$APP_DIR" 2>/dev/null; then
  echo "Cannot replace $APP_DIR"
  echo "It is likely owned by root from an earlier sudo build. Remove it manually or run without sudo."
  exit 1
fi
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$FRAMEWORKS_DIR" "$APP_ROOT/macos" "$SHARE_DIR/newelle" "$SHARE_DIR/icons" "$SHARE_DIR/glib-2.0/schemas" "$SHARE_DIR/locale" "$LIB_DIR" "$BIN_DIR"

rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$ROOT/src/" "$APP_ROOT/src/"
rsync -a --delete "$ROOT/data/" "$APP_ROOT/data/"
cp "$ROOT/meson.build" "$APP_ROOT/meson.build"
cp "$ROOT/macos/run_newelle.py" "$APP_ROOT/macos/run_newelle.py"

rsync -aL --delete --exclude '__pycache__' "$PYTHON_FRAMEWORK_SOURCE/" "$PYTHON_DEST_VERSION_DIR/"
find "$PYTHON_DEST_VERSION_DIR" \( -name '_CodeSignature' -o -name 'CodeResources' \) -exec rm -rf {} +
rm -rf "$PYTHON_DEST_VERSION_DIR/lib/python${PYTHON_VERSION}/config-${PYTHON_VERSION}-darwin"
mkdir -p "$FRAMEWORKS_DIR/Python.framework/Versions"
ln -sfn "$PYTHON_VERSION" "$FRAMEWORKS_DIR/Python.framework/Versions/Current"
ln -sfn "Versions/Current/Python" "$FRAMEWORKS_DIR/Python.framework/Python"
ln -sfn "Versions/Current/Resources" "$FRAMEWORKS_DIR/Python.framework/Resources"

mkdir -p "$PYTHON_DEST_SITE_PACKAGES"
rsync -aL --exclude '__pycache__' --exclude '*.pyc' "$BREW_SITE_PACKAGES/" "$PYTHON_DEST_SITE_PACKAGES/"
if [[ -d "$VENV_SITE_PACKAGES" ]]; then
  rsync -aL --exclude '__pycache__' --exclude '*.pyc' "$VENV_SITE_PACKAGES/" "$PYTHON_DEST_SITE_PACKAGES/"
fi

rsync -aL "$BREW_PREFIX/share/glib-2.0/schemas/" "$SHARE_DIR/glib-2.0/schemas/"
cp "$ROOT/data/io.github.qwersyk.Newelle.gschema.xml" "$SHARE_DIR/glib-2.0/schemas/"
"$GLIB_COMPILE_SCHEMAS" "$SHARE_DIR/glib-2.0/schemas"

"$GLIB_COMPILE_RESOURCES" "$ROOT/src/newelle.gresource.xml" \
  --sourcedir "$ROOT/src" \
  --sourcedir "$ROOT/data" \
  --target "$SHARE_DIR/newelle/newelle.gresource"

for po_file in "$ROOT"/po/*.po; do
  lang="$(basename "${po_file%.po}")"
  mkdir -p "$SHARE_DIR/locale/$lang/LC_MESSAGES"
  "$MSGFMT" "$po_file" -o "$SHARE_DIR/locale/$lang/LC_MESSAGES/newelle.mo"
done

rsync -aL "$ROOT/data/icons/" "$SHARE_DIR/icons/"
rsync -aL "$BREW_PREFIX/share/icons/Adwaita" "$SHARE_DIR/icons/"
if [[ -d "$BREW_PREFIX/share/themes" ]]; then
  rsync -aL "$BREW_PREFIX/share/themes/" "$SHARE_DIR/themes/"
fi
if [[ -d "$BREW_PREFIX/share/gtk-4.0" ]]; then
  rsync -aL "$BREW_PREFIX/share/gtk-4.0" "$SHARE_DIR/"
fi
if [[ -d "$BREW_PREFIX/share/gtksourceview-5" ]]; then
  rsync -aL "$BREW_PREFIX/share/gtksourceview-5" "$SHARE_DIR/"
fi

rsync -aL "$BREW_PREFIX/lib/girepository-1.0/" "$LIB_DIR/girepository-1.0/"
rsync -aL "$BREW_PREFIX/lib/gdk-pixbuf-2.0" "$LIB_DIR/"
rm -f "$LIB_DIR/gdk-pixbuf-2.0/2.10.0/loaders.cache"
for tool in ffmpeg ffplay ffprobe; do
  if [[ -x "$BREW_PREFIX/bin/$tool" ]]; then
    cp "$BREW_PREFIX/bin/$tool" "$BIN_DIR/$tool"
  fi
done

ICONSET_PARENT="$(mktemp -d)"
ICON_PNG="$ICONSET_PARENT/$ICON_NAME.png"
trap 'rm -rf "$ICONSET_PARENT"' EXIT
"$RSVG_CONVERT_BIN" -w 1024 -h 1024 "$ICON_SVG" -o "$ICON_PNG"
ICON_PNG="$ICON_PNG" ICON_ICNS="$RESOURCES_DIR/$ICON_NAME.icns" "$VENV_DIR/bin/python" - <<'PY'
import os
from PIL import Image

source = os.environ["ICON_PNG"]
target = os.environ["ICON_ICNS"]
image = Image.open(source)
image.save(target, sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
PY

cat > "$CONTENTS_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>Newelle</string>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>Newelle</string>
  <key>CFBundleIconFile</key>
  <string>Newelle</string>
  <key>CFBundleIconName</key>
  <string>Newelle</string>
  <key>CFBundleIdentifier</key>
  <string>io.github.qwersyk.Newelle.macos</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Newelle</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION}</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>LSArchitecturePriority</key>
  <array>
    <string>${ARCH}</string>
  </array>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Newelle uses the microphone for speech-to-text recording.</string>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
PLIST

/usr/bin/clang -O2 -g0 -Wall -Wextra -Wl,-headerpad_max_install_names -o "$MACOS_DIR/Newelle" "$LAUNCHER_SRC" \
  "${PYTHON_EMBED_CFLAGS[@]}" \
  "${PYTHON_EMBED_LDFLAGS[@]}"

"$BREW_PYTHON_BIN" "$ROOT/macos/package_runtime.py" "$APP_DIR" "$BREW_PREFIX" "$PYTHON_VERSION"

sign_bundle

while IFS= read -r -d '' file; do
  /usr/bin/codesign --verify --strict "$file"
done < <(list_macho_files "$APP_DIR")

sign_bundle
/usr/bin/codesign --verify --deep --strict "$APP_DIR"
touch "$APP_DIR"

echo "Created $APP_DIR"
