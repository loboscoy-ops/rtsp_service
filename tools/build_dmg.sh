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

# PySide6/PyInstaller на macOS кладёт QtWebEngineProcess.app и chromium-паки
# в QtWebEngineCore.framework/Versions/Resources/{Helpers,Resources},
# а корневые симлинки framework'а указывают на Versions/Current/* (их нет).
# Без этого Qt падает с SIGABRT при инициализации QWebEnginePage.
WEBENGINE_FW="${APP_BUNDLE}/Contents/Frameworks/PySide6/Qt/lib/QtWebEngineCore.framework"
if [[ -d "${WEBENGINE_FW}" ]]; then
    # Helpers
    REAL_HELPERS=""
    for cand in \
        "${WEBENGINE_FW}/Versions/Resources/Helpers" \
        "${WEBENGINE_FW}/Versions/A/Helpers"; do
        if [[ -d "${cand}/QtWebEngineProcess.app" ]]; then
            REAL_HELPERS="${cand#${WEBENGINE_FW}/}"
            break
        fi
    done
    if [[ -n "${REAL_HELPERS}" ]]; then
        rm -f "${WEBENGINE_FW}/Helpers"
        ln -s "${REAL_HELPERS}" "${WEBENGINE_FW}/Helpers"
        echo "▶ QtWebEngineCore.framework/Helpers → ${REAL_HELPERS}"
    else
        echo "⚠  Не нашёл QtWebEngineProcess.app в QtWebEngineCore.framework"
    fi
    # Resources (.pak / icudtl.dat / qtwebengine_locales)
    REAL_RES=""
    for cand in \
        "${WEBENGINE_FW}/Versions/Resources/Resources" \
        "${WEBENGINE_FW}/Versions/A/Resources"; do
        if [[ -f "${cand}/qtwebengine_resources.pak" ]]; then
            REAL_RES="${cand#${WEBENGINE_FW}/}"
            break
        fi
    done
    if [[ -n "${REAL_RES}" ]]; then
        rm -f "${WEBENGINE_FW}/Resources"
        ln -s "${REAL_RES}" "${WEBENGINE_FW}/Resources"
        echo "▶ QtWebEngineCore.framework/Resources → ${REAL_RES}"
    else
        echo "⚠  Не нашёл qtwebengine_resources.pak в QtWebEngineCore.framework"
    fi
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
