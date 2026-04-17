# RTSP Camera Monitor (macOS)

Локальное desktop-приложение для macOS Apple Silicon на `Python + PySide6`.
Запуск — из терминала командой `lbck-rtsp`, всегда с актуальной версией кода из GitHub.

## Возможности

- Несколько объектов и список камер внутри каждого
- Поиск, фильтр по статусу, сортировка по любой колонке
- Скрытие/показ колонок через ПКМ по заголовку (сохраняется между запусками)
- Колонки таблицы: `Объект`, `ID камеры`, `Имя камеры`, `Тип`, `Координаты`, `Статус`, `Последний online`, `Последняя проверка`, `Ошибка`, `RTSP`, `Действия`
- Копирование GPS: клик по ячейке координат или `⌘+C` на выделенной строке
- Открытие потока через `ffplay` (`⌘+Return`); главное окно уходит назад, окно `ffplay` — на передний план
- Хранение по каждой камере: `status`, `last_seen_online_at`, `last_checked_at`, `last_error`
- Фоновая авто-проверка каждые **60 секунд**, тайм-аут не показывает «offline» и не пишет ошибку
- Ручная проверка камеры и **`⌘+R`** — проверить все камеры всех объектов
- Импорт XLSX с выбором листа, строкой заголовков и **маппингом колонок** (с авто-подбором)
- Шаблон XLSX генерируется одной кнопкой
- Локальная SQLite база (создаётся автоматически), путь:
  - в режиме разработки: `data/rtsp_monitor.db` в репозитории
  - в режиме сборки: `~/Library/Application Support/RTSPCameraMonitor/`

## Стек

- Python 3.12 (рекомендуется; работает и с 3.9)
- PySide6, SQLite, pandas, openpyxl
- `ffplay` + `ffprobe` (из ffmpeg)

## Структура проекта

```text
app/
  main.py
  config.py
  database/
    db.py
    models.py
    repository.py
  services/
    camera_checker.py
    ffplay_service.py
    git_service.py
    import_service.py
    template_service.py
  ui/
    main_window.py
    dialogs/
      camera_dialog.py
      import_dialog.py
      object_dialog.py
    widgets/
      camera_table.py
      object_sidebar.py
      status_badge.py
  utils/
    datetime_utils.py
    process_utils.py
    validators.py
tools/
  lbck-rtsp           # лаунчер: git pull + venv + python -m app.main
  import_sas_form.py  # адаптер формы «САС» в наш формат и заливка в БД
requirements.txt
README.md
```

## Установка (один раз)

### 1) Клонирование

```bash
git clone https://github.com/loboscoy-ops/rtsp_service.git ~/rtsp-camera-service
```

### 2) ffmpeg (ffplay / ffprobe)

```bash
brew install ffmpeg
which ffplay
which ffprobe
```

### 3) Установить лаунчер `lbck-rtsp` в `PATH`

```bash
ln -sfn ~/rtsp-camera-service/tools/lbck-rtsp /opt/homebrew/bin/lbck-rtsp
```

После этого в любом терминале команда `lbck-rtsp` будет:

1. делать `git fetch + git merge --ff-only origin/main` в `~/rtsp-camera-service`,
2. при необходимости создавать `.venv` и ставить зависимости из `requirements.txt`,
3. запускать `python -m app.main`.

## Запуск

```bash
lbck-rtsp                 # обновить из GitHub и запустить (терминал держит процесс)
lbck-rtsp --background    # отвязать от терминала, лог: /tmp/rtsp_app.log
lbck-rtsp --no-pull       # запустить без обращения к GitHub
lbck-rtsp --no-deps       # пропустить pip install
lbck-rtsp --update-only   # только обновить, без запуска
lbck-rtsp -h              # справка
```

Переменные окружения:

- `RTSP_PROJECT_DIR` — путь к репозиторию, по умолчанию `~/rtsp-camera-service`
- `RTSP_BRANCH` — ветка, по умолчанию `main`
- `RTSP_PYTHON` — какой `python` использовать для создания venv (например, `python3.12`)
- `RTSP_LOG_FILE` — куда писать лог при `--background` (по умолчанию `/tmp/rtsp_app.log`)

## Импорт XLSX

В приложении нажмите **«Импорт XLSX»**:

1. (опционально) **«Скачать шаблон XLSX»**
2. **«Выбрать XLSX»** — читаются все листы файла
3. Выберите лист, укажите номер строки заголовков
4. Сопоставьте колонки своими полями (есть авто-подбор по подписям)
5. **«Предпросмотр»** — валидация и просмотр данных
6. **«Импортировать»** — `upsert` по `(object_name + camera_identifier)`

Поля приложения:

- `object_name` (объект)
- `camera_identifier` (ID камеры)
- `camera_name` (имя камеры)
- `rtsp_url` (RTSP)
- `group_name` (тип/зона)
- `gps_coords` (координаты `lat, lon`)
- `enabled` (`1/0`, `true/false`, `yes/no`, `да/нет`)

Для типовой формы «САС» (УИН/Наименование объекта/GPS/Ссылка на видеотрансляцию) есть отдельный адаптер:

```bash
cd ~/rtsp-camera-service
.venv/bin/python -m tools.import_sas_form "/path/to/Forma_*.xlsx"
```

## Горячие клавиши

- `⌘ + Return / ⌘ + Enter` — открыть выделенную камеру через `ffplay`
- `⌘ + R` — проверить все камеры всех объектов
- `⌘ + C` (фокус в таблице) — скопировать GPS выделенной камеры
- `Backspace / Delete` — удалить камеру (в таблице) или объект (в сайдбаре, каскадно)
- `Esc` — закрыть открытый диалог
- ПКМ по заголовку таблицы — меню скрытия/показа колонок
- Клик по заголовку — сортировка по возрастанию/убыванию

## Проверка камер

- Авто-проверка каждые `CHECK_INTERVAL_SEC` секунд (по умолчанию **60**, см. `app/config.py`)
- Используется `ffprobe` по RTSP URL (`-rtsp_transport tcp`)
- Тайм-аут проверки **не пишется** в БД и UI: статус не меняется, поля не обновляются
- Ошибки соединения (`Connection refused`, неверный URL и т. п.) → статус `offline` + текст ошибки
- Кнопка «Обновить из GitHub» в тулбаре выполняет `git pull` в каталоге репозитория (`~/rtsp-camera-service`)

## Где хранятся данные

- `data/rtsp_monitor.db` — SQLite база (репо)
- `data/logs/` — каталог под логи
- Можно переопределить через `RTSP_DATA_DIR=/path` или `RTSP_APP_DB_PATH=/path/db.sqlite`
