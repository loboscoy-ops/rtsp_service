# RTSP Camera Monitor (macOS MVP)

Локальное desktop-приложение для macOS (Apple Silicon), сделанное на `Python + PySide6`.

## Возможности MVP

- Несколько объектов и список камер внутри каждого объекта
- Поиск по камерам и фильтр по статусу
- Статусы камер: `online/offline/unknown`
- Хранение:
  - `status`
  - `last_seen_online_at`
  - `last_checked_at`
  - `last_error`
- Фоновая автопроверка камер по RTSP через `ffprobe`
- Ручная проверка одной камеры и кнопка "Проверить все"
- Открытие потока через внешний `ffplay`
- Импорт камер из XLSX с предпросмотром и валидацией
- Генерация шаблона XLSX
- Локальная SQLite база с автосозданием при первом запуске

## Технологии

- Python 3.12 (рекомендуется)
- PySide6
- SQLite (`sqlite3`)
- pandas + openpyxl
- ffplay + ffprobe (из ffmpeg)

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
requirements.txt
README.md
```

## Подготовка окружения

### 1) Python 3.12 и venv

```bash
cd /path/to/rtsp-camera-service
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2) Установка ffmpeg (ffplay/ffprobe) на macOS

```bash
brew install ffmpeg
which ffplay
which ffprobe
```

## Запуск приложения

```bash
source .venv/bin/activate
python -m app.main
```

При первом запуске автоматически создается база:

- `data/rtsp_monitor.db`

И (если база пустая) добавляются демо-данные.

## Импорт XLSX

В приложении нажмите **"Импорт XLSX"**:

1. **"Скачать шаблон XLSX"** (опционально)
2. Заполните файл
3. **"Выбрать XLSX"**
4. **"Предпросмотр"** (валидация)
5. **"Импортировать"**

### Ожидаемые колонки

- `object_name`
- `camera_identifier`
- `camera_name`
- `rtsp_url`
- `group_name`
- `enabled`

`enabled` поддерживает значения: `1/0`, `true/false`, `yes/no`, `да/нет`.

## Как работает проверка статуса

- Фоновый таймер запускает проверку каждые `CHECK_INTERVAL_SEC` секунд (см. `app/config.py`)
- Используется `ffprobe` по RTSP URL
- Результат сохраняется в БД
- UI обновляется без блокировки интерфейса

## Ошибки ffplay

При нажатии **"Открыть"**:

- если `ffplay` не найден — показывается понятная ошибка
- если URL пустой/некорректный — показывается ошибка
- если процесс не стартовал — показывается ошибка запуска

## Подготовка к сборке `.app` (далее)

MVP уже структурирован для упаковки в приложение.
Следующий шаг — использовать, например, `pyinstaller`:

```bash
pip install pyinstaller
pyinstaller --windowed --name RTSPCameraMonitor app/main.py
```

(Нужно отдельно настроить иконку, entitlements и доп. параметры для production-сборки.)

