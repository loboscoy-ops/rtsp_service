#!/usr/bin/env bash
# release.sh — собирает .app/.dmg и выкладывает GitHub Release с .dmg-ассетом.
#
# Тег и название релиза = "v$APP_VERSION" из app/config.py.
# Если тег уже существует — релиз дополнится недостающим ассетом
# (полезно, если соберёшь ту же версию ещё раз).
#
# Использование:
#   ./tools/release.sh                  # собрать + создать релиз
#   ./tools/release.sh --no-build       # пропустить сборку, только релиз
#   ./tools/release.sh --notes "txt"    # своя строка для notes
#
# Авторизация:
#   - GH_TOKEN или GITHUB_TOKEN из окружения, либо
#   - токен из git credential helper (osxkeychain) для https://github.com.
set -euo pipefail

cd "$(dirname "$0")/.."

REPO_SLUG="${RTSP_REPO_SLUG:-loboscoy-ops/rtsp_service}"
NOTES=""
DO_BUILD=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) DO_BUILD=0; shift ;;
    --notes) NOTES="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "release.sh: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done

VERSION="$(python3 -c 'from app import config; print(config.APP_VERSION)')"
ARCH="$(uname -m)"
TAG="v${VERSION}"
DMG_NAME="rtsp-camera-monitor-${VERSION}-${ARCH}.dmg"
DMG_PATH="dist/${DMG_NAME}"

echo "▶ Версия: ${VERSION}  тег: ${TAG}  arch: ${ARCH}"
echo "▶ Репозиторий: ${REPO_SLUG}"

if [ "${DO_BUILD}" = "1" ]; then
    echo "▶ Сборка .app/.dmg"
    bash tools/build_dmg.sh
fi

if [ ! -f "${DMG_PATH}" ]; then
    echo "✗ Не нашёл собранный ${DMG_PATH}. Запустите без --no-build или соберите вручную." >&2
    exit 1
fi

# --- получаем токен ---------------------------------------------------------
TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
if [ -z "${TOKEN}" ]; then
    if command -v git >/dev/null; then
        TOKEN="$(printf 'host=github.com\nprotocol=https\n\n' \
            | git credential fill 2>/dev/null \
            | awk -F= '/^password=/{print $2; exit}')" || true
    fi
fi
if [ -z "${TOKEN}" ]; then
    echo "✗ GitHub токен не найден. Задайте GH_TOKEN или авторизуйтесь в git" >&2
    echo "  через 'gh auth login' / 'git push' (он сохранит токен в osxkeychain)." >&2
    exit 1
fi

API="https://api.github.com/repos/${REPO_SLUG}"
UPLOADS="https://uploads.github.com/repos/${REPO_SLUG}"

if [ -z "${NOTES}" ]; then
    NOTES="RTSP Camera Monitor ${TAG} (${ARCH}). Установка: смонтируйте .dmg и перетащите .app в /Applications. Для существующих установок обновление произойдёт автоматически по нажатию зелёной кнопки «Обновить»."
fi

# --- ищем или создаём релиз -------------------------------------------------
echo "▶ Проверяю, есть ли релиз ${TAG}"
RELEASE_JSON="$(curl -sS \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${API}/releases/tags/${TAG}")"

RELEASE_ID="$(echo "${RELEASE_JSON}" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id") or "")')"

if [ -z "${RELEASE_ID}" ]; then
    echo "▶ Создаю релиз ${TAG}"
    BODY_JSON="$(python3 -c "
import json, sys
print(json.dumps({
    'tag_name': '${TAG}',
    'name': '${TAG}',
    'body': sys.stdin.read(),
    'draft': False,
    'prerelease': False,
}))" <<<"${NOTES}")"

    CREATE_JSON="$(curl -sS -X POST \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${API}/releases" \
        -d "${BODY_JSON}")"
    RELEASE_ID="$(echo "${CREATE_JSON}" \
        | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id") or "")')"

    if [ -z "${RELEASE_ID}" ]; then
        echo "✗ Не удалось создать релиз" >&2
        echo "${CREATE_JSON}" >&2
        exit 1
    fi
    echo "✓ Релиз создан: id=${RELEASE_ID}"
else
    echo "  релиз уже существует: id=${RELEASE_ID}"
fi

# --- удаляем старый ассет с тем же именем (если был) ------------------------
EXISTING_ASSET_ID="$(echo "${RELEASE_JSON}" \
    | python3 -c "
import sys, json
d = json.load(sys.stdin)
for a in (d.get('assets') or []):
    if a.get('name') == '${DMG_NAME}':
        print(a.get('id') or '')
        break
")"
if [ -n "${EXISTING_ASSET_ID}" ]; then
    echo "▶ Удаляю старый ассет ${DMG_NAME} (id=${EXISTING_ASSET_ID})"
    curl -sS -o /dev/null -X DELETE \
        -H "Accept: application/vnd.github+json" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${API}/releases/assets/${EXISTING_ASSET_ID}"
fi

# --- загружаем .dmg ---------------------------------------------------------
echo "▶ Загружаю ${DMG_NAME} ($(du -h "${DMG_PATH}" | cut -f1))"
UPLOAD_JSON="$(curl -sS -X POST \
    -H "Accept: application/vnd.github+json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/x-apple-diskimage" \
    --data-binary @"${DMG_PATH}" \
    "${UPLOADS}/releases/${RELEASE_ID}/assets?name=${DMG_NAME}")"

DOWNLOAD_URL="$(echo "${UPLOAD_JSON}" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("browser_download_url") or "")')"

if [ -z "${DOWNLOAD_URL}" ]; then
    echo "✗ Загрузка ассета не удалась" >&2
    echo "${UPLOAD_JSON}" >&2
    exit 1
fi

# --- синхронизируем локальный тег (GitHub мог создать тег при создании релиза)
if ! git rev-parse "${TAG}" >/dev/null 2>&1; then
    git fetch origin "refs/tags/${TAG}:refs/tags/${TAG}" 2>/dev/null || true
fi
if ! git rev-parse "${TAG}" >/dev/null 2>&1; then
    git tag -a "${TAG}" -m "${TAG}"
    git push origin "${TAG}" 2>/dev/null || true
fi

echo
echo "✓ Готово"
echo "  Release page: https://github.com/${REPO_SLUG}/releases/tag/${TAG}"
echo "  DMG:          ${DOWNLOAD_URL}"
