#!/usr/bin/env bash
# Сборка .app + .dmg для macOS Apple Silicon.
# Требования: установленный Python 3 с PySide6 в .venv проекта, ffmpeg (опционально).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="RTSPCameraMonitor"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "venv не найден: $VENV_PY"
  echo "сначала: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

"$VENV_PY" -m pip install -q pyinstaller

rm -rf build dist "$APP_NAME.dmg"

"$VENV_PY" -m PyInstaller \
  --name "$APP_NAME" \
  --windowed \
  --noconfirm \
  --osx-bundle-identifier "com.loboscoy.rtspmonitor" \
  --add-data "data:data" \
  --hidden-import "openpyxl.cell._writer" \
  app/main.py

APP_PATH="dist/$APP_NAME.app"
if [ ! -d "$APP_PATH" ]; then
  echo "Сборка .app не удалась"
  exit 2
fi

DMG_DIR="$(mktemp -d)"
cp -R "$APP_PATH" "$DMG_DIR/"
ln -s /Applications "$DMG_DIR/Applications"

DMG_PATH="$ROOT_DIR/dist/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_DIR" -ov -format UDZO "$DMG_PATH"
rm -rf "$DMG_DIR"

echo "==> Готово:"
echo "    $APP_PATH"
echo "    $DMG_PATH"
