# Карта проекта: блоки по функционалу (front / back)

Десктоп-приложение на PySide6: **«фронт»** = всё, что рисует UI и обрабатывает ввод; **«бэк»** = данные, внешние процессы, сеть, бизнес-логика без привязки к виджетам.

---

## 1. Слой границы (entry)

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Точка входа** | `QApplication`, старт БД, демо-данные, окно | `app/main.py` |
| **Конфиг** | Версия, пути к БД/логам, таймауты проверок, бинарники ff*, флаги | `app/config.py` |
| **Сеть → GitHub (обновления)** | Переменные окружения: `RTSP_HTTP_TIMEOUT_SEC` (по умолчанию 60), `RTSP_HTTP_CONNECT_RETRIES` (3), `RTSP_HTTP_RETRY_DELAY_SEC` (2) — см. `app/services/release_service.py` |

---

## 2. Front-end (UI)

### 2.1 Главное окно (оркестрация)

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Main window** | Тулбар, сплиттеры, лог/ошибки, таймеры, горячие клавиши, закрытие, git/релизы, привязка сигналов | `app/ui/main_window.py` |
| **UI-константы** | Цвета, размеры, интервалы, QSS, лимиты логов | `app/ui/constants.py` |

### 2.2 Виджеты

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Таблица камер** | Колонки, сортировка, контекст-меню, копирование, отображение ping/статуса | `app/ui/widgets/camera_table.py` |
| **Сайдбар объектов** | Список объектов, выбор, переименование, удаление | `app/ui/widgets/object_sidebar.py` |
| **Бейдж статуса** (если используется) | Мелкие визуальные индикаторы | `app/ui/widgets/status_badge.py` |

### 2.3 Диалоги

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Импорт XLSX** | Маппинг колонок, превью, фоновый разбор файла | `app/ui/dialogs/import_dialog.py` |
| **Карта** | Leaflet / WebEngine, маркеры по GPS | `app/ui/dialogs/map_dialog.py` |
| **Камера** | Создание/редактирование камеры | `app/ui/dialogs/camera_dialog.py` |
| **Объект** | Создание/редактирование объекта | `app/ui/dialogs/object_dialog.py` |

---

## 3. Back-end: данные (persistence)

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Схема + миграции + путь к БД** | SQLite, WAL, `user_version`, разовые фиксы | `app/database/db.py` |
| **Репозиторий** | CRUD объектов/камер, списки, фильтры, обновление статусов | `app/database/repository.py` |
| **Модели (DTO)** | `ObjectModel`, `CameraModel` | `app/database/models.py` |

---

## 4. Back-end: доменные сервисы

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Проверка RTSP** | `ffprobe`, TCP/UDP, уровни таймаута, ping хоста, `CheckResult` | `app/services/camera_checker.py` |
| **Просмотр потока** | Запуск `ffplay`, учёт PID | `app/services/ffplay_service.py` |
| **Импорт форм** | pandas/openpyxl, превью, upsert в репозиторий | `app/services/import_service.py` |
| **Шаблон XLSX** | Генерация файла-шаблона | `app/services/template_service.py` |
| **Git pull (dev)** | `git fetch` + `merge --ff-only`, фоновая проверка «есть коммиты» | `app/services/git_service.py` |
| **Релизы / автообновление (.app)** | Версия с raw GitHub, релизы API, скачивание .dmg, установка | `app/services/release_service.py` |

---

## 5. Back-end: утилиты (общие, без UI)

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Процессы** | `run_command`, `resolve_binary` (PATH + Homebrew) | `app/utils/process_utils.py` |
| **Ping** | ICMP из `ping`, разбор вывода macOS | `app/utils/ping_utils.py` |
| **Валидация** | RTSP URL, `enabled`, маскирование URL | `app/utils/validators.py` |
| **Дата/время** | ISO для логов/БД | `app/utils/datetime_utils.py` |
| **GPS** | Парс координат для карты | `app/utils/gps_parse.py` |

---

## 6. Инструменты и сборка (вне runtime UI)

| Блок | Назначение | Ключевые файлы |
|------|------------|----------------|
| **Сборка .app/.dmg** | PyInstaller | `tools/build_dmg.sh` |
| **Публикация релиза** | GitHub Releases + загрузка .dmg | `tools/release.sh` |
| **Запуск с pull** | Обновление репо + venv + `python -m app.main` | `tools/lbck-rtsp` |
| **Разовые скрипты** | Импорт конкретных форм и т.п. | `tools/import_sas_form.py` |

---

## 7. Быстрый указатель: «что менять по задаче»

| Задача | Смотреть прежде всего |
|--------|------------------------|
| Колонки таблицы, меню, копирование, ping в UI | `app/ui/widgets/camera_table.py`, `app/ui/constants.py` |
| Главное меню, таймер проверок, лог, git-кнопка, закрытие | `app/ui/main_window.py` |
| Импорт Excel / превью / маппинг | `app/ui/dialogs/import_dialog.py`, `app/services/import_service.py` |
| Логика online/offline/unknown, ffprobe, таймауты | `app/services/camera_checker.py`, `app/config.py` |
| Карта, маркеры, Leaflet | `app/ui/dialogs/map_dialog.py`, `app/utils/gps_parse.py` |
| Схема БД, миграции | `app/database/db.py` |
| Запросы к SQLite, списки камер | `app/database/repository.py` |
| Автообновление .dmg | `app/services/release_service.py`, `_update_from_release` в `main_window.py` |
| Git pull из приложения (исходники) | `app/services/git_service.py` |
| ffplay не находится в .app | `app/utils/process_utils.py`, `app/services/ffplay_service.py` |
| Версия приложения | `app/config.py` |
| DMG / релиз | `tools/build_dmg.sh`, `tools/release.sh` |

---

## 8. Поток данных (упрощённо)

```
UI (main_window, dialogs, widgets)
    → вызывает Repository / ImportService / Checker / FFPlay / Git / Release
    → Repository → SQLite (db.py + repository.py)
Checker / FFPlay / ping → subprocess (process_utils, ffprobe/ffplay/ping)
Release / Git → urllib / subprocess (git)
```

Этот файл можно держать открытым при рефакторинге: сначала находишь **блок** в таблице §7, затем открываешь только перечисленные файлы.
