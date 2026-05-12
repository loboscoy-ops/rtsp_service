#!/usr/bin/env bash
# Сборка .app + .dmg для текущей архитектуры Mac (arm64 на M1+, x86_64 на Intel).
# Запускать на каждом маке отдельно, если нужны обе сборки.
#
# Использование:
#   ./tools/build_dmg.sh         — обычная сборка
#   ./tools/build_dmg.sh clean   — снести dist/ build/ и пересобрать
set -euo pipefail

cd "$(dirname "$0")/.."

APP_NAME="Urus Camera Monitor"
ENTRY="app/main.py"
ARCH="$(uname -m)"
VERSION="$(python3 -c 'from app import config; print(config.APP_VERSION)')"
DMG_NAME="rtsp-camera-monitor-${VERSION}-${ARCH}.dmg"

echo "▶ Архитектура: ${ARCH}"
echo "▶ Версия:      ${VERSION}"

if [[ "${1:-}" == "clean" ]]; then
    echo "▶ clean: удаляю build/ dist/"
    rm -rf build dist
fi

if [[ ! -d ".venv" ]]; then
    echo "▶ Создаю .venv (python3 -m venv .venv)"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "▶ Обновляю pip и ставлю зависимости"
python -m pip install --upgrade pip wheel >/dev/null
pip install -r requirements.txt
pip install pyinstaller

if ! command -v ffprobe >/dev/null 2>&1; then
    echo "⚠  ffprobe не найден в PATH. На целевом маке поставь brew install ffmpeg," \
         "иначе .app не сможет проверять камеры."
fi

echo "▶ PyInstaller"
pyinstaller \
    --noconfirm \
    --windowed \
    --clean \
    --name "${APP_NAME}" \
    --add-data "resources:resources" \
    --hidden-import PySide6.QtWebEngineWidgets \
    --hidden-import PySide6.QtWebEngineCore \
    --hidden-import app.ui.widgets.camera_map \
    --osx-bundle-identifier "com.urus.rtsp-monitor" \
    "${ENTRY}"

APP_BUNDLE="dist/${APP_NAME}.app"
if [[ ! -d "${APP_BUNDLE}" ]]; then
    echo "✗ Не появился ${APP_BUNDLE}"
    exit 1
fi

echo "▶ Готовлю DMG: ${DMG_NAME}"
DMG_PATH="dist/${DMG_NAME}"
rm -f "${DMG_PATH}"
hdiutil create \
    -volname "${APP_NAME} ${VERSION}" \
    -srcfolder "${APP_BUNDLE}" \
    -ov \
    -format UDZO \
    "${DMG_PATH}" >/dev/null

echo "✓ Готово"
echo "  .app: ${APP_BUNDLE}"
echo "  .dmg: ${DMG_PATH}"
