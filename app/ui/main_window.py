from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut

import shutil
import subprocess
from PySide6.QtWidgets import (
    QComboBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import config
from app.database.models import CameraModel, ObjectModel
from app.database.repository import Repository
from app.services.camera_checker import CameraChecker, CheckResult
from app.services.ffplay_service import FFPlayService
from app.services.import_service import ImportService
from app.services.template_service import TemplateService
from app.ui.dialogs.camera_dialog import CameraDialog
from app.ui.dialogs.import_dialog import ImportDialog
from app.ui.dialogs.object_dialog import ObjectDialog
from app.ui.widgets.camera_table import CameraTable
from app.ui.widgets.object_sidebar import ObjectSidebar


class MainWindow(QMainWindow):
    def __init__(self, repository: Repository):
        super().__init__()
        self.repo = repository
        self.ffplay = FFPlayService()
        self.checker = CameraChecker()
        self.import_service = ImportService(self.repo)
        self.template_service = TemplateService()

        self.current_object_id: int | None = None
        self.objects_cache: list[ObjectModel] = []
        self.cameras_cache: list[CameraModel] = []
        self._sort_column = 0
        self._sort_order = Qt.SortOrder.AscendingOrder

        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.resize(1450, 880)

        self._setup_ui()
        self._bind_signals()
        self._refresh_objects()
        self._refresh_cameras()

        self.timer = QTimer(self)
        self.timer.setInterval(max(15, config.CHECK_INTERVAL_SEC) * 1000)
        self.timer.timeout.connect(self._auto_check_all_enabled)
        self.timer.start()

        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(250)
        self._refresh_debounce.timeout.connect(self._refresh_views_after_checks)

        self._setup_shortcuts()

    def _setup_shortcuts(self) -> None:
        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(self._open_selected_camera)
        sc_r = QShortcut(QKeySequence("Ctrl+R"), self)
        sc_r.setContext(Qt.ShortcutContext.ApplicationShortcut)
        sc_r.activated.connect(self._manual_check_all)

    def _open_selected_camera(self) -> None:
        cam_id = self.table.selected_camera_id()
        if cam_id is None:
            self._log("Открыть камеру: не выбрана строка в таблице")
            return
        self._open_camera_stream(cam_id)

    def _setup_ui(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        add_object_btn = QPushButton("Добавить объект")
        add_object_btn.clicked.connect(self._add_object)
        self.add_object_btn = add_object_btn
        toolbar.addWidget(add_object_btn)

        add_camera_btn = QPushButton("Добавить камеру")
        add_camera_btn.clicked.connect(self._add_camera)
        self.add_camera_btn = add_camera_btn
        toolbar.addWidget(add_camera_btn)

        import_btn = QPushButton("Импорт формы")
        import_btn.setToolTip("Загрузить .xls / .xlsx и сопоставить колонки")
        import_btn.clicked.connect(self._open_import_dialog)
        self.import_btn = import_btn
        toolbar.addWidget(import_btn)

        check_all_btn = QPushButton("Проверить все (⌘R)")
        check_all_btn.setToolTip("Проверить все камеры всех объектов (⌘R)")
        check_all_btn.clicked.connect(self._manual_check_all)
        self.check_all_btn = check_all_btn
        toolbar.addWidget(check_all_btn)

        git_btn = QPushButton("Обновить из GitHub")
        git_btn.setToolTip("git pull --ff-only origin main")
        git_btn.clicked.connect(self._git_pull_from_github)
        self.git_btn = git_btn
        toolbar.addWidget(git_btn)

        toolbar.addSeparator()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск камер: ID, имя, группа, объект")
        self.search_input.textChanged.connect(self._refresh_cameras)
        toolbar.addWidget(self.search_input)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["all", "online", "offline", "unknown"])
        self.status_filter.currentIndexChanged.connect(self._refresh_cameras)
        toolbar.addWidget(QLabel("Статус:"))
        toolbar.addWidget(self.status_filter)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Объекты"))
        self.sidebar = ObjectSidebar()
        left_layout.addWidget(self.sidebar)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Камеры"))
        self.table = CameraTable()
        right_layout.addWidget(self.table)

        right_layout.addWidget(QLabel("Лог"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(160)
        right_layout.addWidget(self.log_text)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar())
        self._setup_logo()

    def _setup_logo(self) -> None:
        if not config.LOGO_PATH.exists():
            return
        pix = QPixmap(str(config.LOGO_PATH))
        if pix.isNull():
            return
        target_h = 84  # ~3× от прежних 28 px
        pix = pix.scaledToHeight(target_h, Qt.TransformationMode.SmoothTransformation)
        self.logo_label = QLabel()
        self.logo_label.setPixmap(pix)
        self.logo_label.setToolTip("УРУС")
        self.logo_label.setContentsMargins(8, 0, 8, 0)
        self.logo_label.setFixedHeight(target_h)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setFixedHeight(target_h + 8)
        self.statusBar().addPermanentWidget(self.logo_label)

    def _bind_signals(self) -> None:
        self.sidebar.object_selected.connect(self._on_object_selected)
        self.sidebar.delete_requested.connect(self._delete_object)
        self.sidebar.rename_requested.connect(self._rename_object)
        self.table.open_requested.connect(self._open_camera_stream)
        self.table.check_requested.connect(self._check_single_camera)
        self.table.edit_requested.connect(self._edit_camera)
        self.table.delete_requested.connect(self._delete_camera)
        self.table.coordinates_copied.connect(self._on_coords_copied)
        self.table.rtsp_copied.connect(self._on_rtsp_copied)
        self.table.sort_changed.connect(self._on_sort_changed)
        self.table.bulk_check_requested.connect(self._bulk_check)
        self.table.bulk_delete_requested.connect(self._bulk_delete)
        self.table.bulk_edit_requested.connect(self._bulk_edit)
        self.checker.camera_checked.connect(self._on_camera_checked)

    def _log(self, message: str) -> None:
        self.log_text.append(message)
        self.statusBar().showMessage(message, 5000)

    def _refresh_objects(self) -> None:
        current_id = self.sidebar.current_object_id()
        objects = self.repo.list_objects()
        self.objects_cache = objects
        self.sidebar.populate(objects)
        if current_id is not None:
            self.sidebar.select_object(current_id)
        elif objects:
            self.current_object_id = objects[0].id

    def _refresh_cameras(self) -> None:
        status_filter = self.status_filter.currentText()
        search = self.search_input.text()
        cameras = self.repo.list_cameras(
            object_id=self.current_object_id,
            search=search,
            status_filter=status_filter,
        )
        cameras = self._apply_sort(cameras)
        self.cameras_cache = cameras
        self.table.populate(cameras)

    def _apply_sort(self, cameras: list[CameraModel]) -> list[CameraModel]:
        col = self._sort_column
        order = self._sort_order
        T = self.table
        status_rank = {"online": 0, "offline": 1, "unknown": 2}

        def key(cam: CameraModel):
            if col == T.COL_NUM:
                return cam.id
            if col == T.COL_OBJECT:
                return (cam.object_name or "").lower()
            if col == T.COL_UIN:
                return (cam.uin or "").lower()
            if col == T.COL_NAME:
                return (cam.camera_name or "").lower()
            if col == T.COL_TYPE:
                return (cam.group_name or "").lower()
            if col == T.COL_GPS:
                return (cam.gps_coords or "").lower()
            if col == T.COL_STATUS:
                return status_rank.get(cam.status, 9)
            if col == T.COL_CHECKED:
                return cam.last_checked_at or ""
            if col == T.COL_ERR:
                return cam.last_error or ""
            if col == T.COL_RTSP:
                return (cam.rtsp_url or "").lower()
            return cam.camera_name or ""

        return sorted(cameras, key=key, reverse=(order == Qt.SortOrder.DescendingOrder))

    def _on_sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self._sort_column = column
        self._sort_order = order
        self._refresh_cameras()

    def _on_object_selected(self, object_id: int) -> None:
        self.current_object_id = object_id
        self._refresh_cameras()

    def _selected_object(self) -> ObjectModel | None:
        for obj in self.objects_cache:
            if obj.id == self.current_object_id:
                return obj
        return None

    def _add_object(self) -> None:
        dlg = ObjectDialog(self)
        if dlg.exec():
            try:
                self.repo.add_object(dlg.name)
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка", f"Не удалось создать объект:\n{exc}")
                return
            self._refresh_objects()
            self._log(f"Создан объект: {dlg.name}")

    def _add_camera(self) -> None:
        if not self.objects_cache:
            QMessageBox.information(self, "Камера", "Сначала добавьте хотя бы один объект")
            return
        dlg = CameraDialog(self.objects_cache, parent=self)
        if dlg.exec():
            d = dlg.form_data()
            try:
                self.repo.add_camera(
                    object_id=d.object_id,
                    camera_identifier=d.camera_identifier,
                    camera_name=d.camera_name,
                    group_name=d.group_name,
                    gps_coords=d.gps_coords,
                    uin=d.uin,
                    rtsp_url=d.rtsp_url,
                    enabled=d.enabled,
                )
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка", f"Не удалось добавить камеру:\n{exc}")
                return
            self._refresh_objects()
            self._refresh_cameras()
            self._log(f"Добавлена камера: {d.camera_name}")

    def _edit_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        dlg = CameraDialog(self.objects_cache, parent=self, camera=cam)
        if dlg.exec():
            d = dlg.form_data()
            try:
                self.repo.update_camera(
                    camera_id=cam.id,
                    object_id=d.object_id,
                    camera_identifier=d.camera_identifier,
                    camera_name=d.camera_name,
                    group_name=d.group_name,
                    gps_coords=d.gps_coords,
                    uin=d.uin,
                    rtsp_url=d.rtsp_url,
                    enabled=d.enabled,
                )
            except Exception as exc:
                QMessageBox.critical(self, "Ошибка", f"Не удалось обновить камеру:\n{exc}")
                return
            self._refresh_objects()
            self._refresh_cameras()
            self._log(f"Обновлена камера: {d.camera_name}")

    def _delete_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        resp = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить камеру «{cam.camera_name}»?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_camera(camera_id)
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удалена камера: {cam.camera_name}")

    def _rename_object(self, object_id: int) -> None:
        obj = next((o for o in self.objects_cache if o.id == object_id), None)
        if not obj:
            return
        new_name, ok = QInputDialog.getText(
            self,
            "Переименовать объект",
            "Новое название объекта:",
            QLineEdit.EchoMode.Normal,
            obj.name,
        )
        if not ok:
            return
        new_name = (new_name or "").strip()
        if not new_name:
            QMessageBox.warning(self, "Переименование", "Название не может быть пустым")
            return
        if new_name == obj.name:
            return
        clash = next(
            (o for o in self.objects_cache if o.id != object_id and o.name.lower() == new_name.lower()),
            None,
        )
        if clash:
            QMessageBox.warning(
                self,
                "Переименование",
                f"Объект с названием «{new_name}» уже существует.",
            )
            return
        try:
            self.repo.update_object(object_id, new_name)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось переименовать объект:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Объект переименован: «{obj.name}» → «{new_name}»")

    def _delete_object(self, object_id: int) -> None:
        obj = next((o for o in self.objects_cache if o.id == object_id), None)
        if not obj:
            return
        resp = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить объект «{obj.name}» и все его камеры ({obj.camera_count})?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.repo.delete_object(object_id)
        if self.current_object_id == object_id:
            self.current_object_id = None
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удален объект: {obj.name}")

    def _bulk_check(self, camera_ids: list[int]) -> None:
        cams = [c for c in (self.repo.get_camera(cid) for cid in camera_ids) if c]
        self.checker.check_many(cams)
        self._log(f"Запущена проверка выделенных: {len(cams)} камер")

    def _bulk_delete(self, camera_ids: list[int]) -> None:
        if not camera_ids:
            return
        resp = QMessageBox.question(
            self,
            "Удаление",
            f"Удалить выделенные камеры ({len(camera_ids)})?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.repo.bulk_delete_cameras(camera_ids)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось удалить камеры:\n{exc}")
            return
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Удалено камер: {removed}")

    def _bulk_edit(self, camera_ids: list[int], field: str) -> None:
        if not camera_ids:
            return

        labels = {
            "group_name": ("Тип (группа)", "Новый тип/группа для выделенных камер:"),
            "gps_coords": ("Координаты (GPS)", "Новые координаты для выделенных камер (пусто = очистить):"),
            "uin": ("УИН", "Новый УИН для выделенных камер (пусто = очистить):"),
        }

        try:
            if field in labels:
                title, prompt = labels[field]
                value, ok = QInputDialog.getText(
                    self,
                    f"Массовое изменение: {title}",
                    f"{prompt}\nЗатронет камер: {len(camera_ids)}",
                    QLineEdit.EchoMode.Normal,
                    "",
                )
                if not ok:
                    return
                affected = self.repo.bulk_update_field(camera_ids, field, value.strip())
                self._log(f"Массово обновлено «{title}» у {affected} камер")

            elif field == "object_id":
                if not self.objects_cache:
                    QMessageBox.information(self, "Перенос", "Нет доступных объектов")
                    return
                names = [o.name for o in self.objects_cache]
                name, ok = QInputDialog.getItem(
                    self,
                    "Перенести в объект",
                    f"Выберите объект для {len(camera_ids)} камер:",
                    names,
                    0,
                    False,
                )
                if not ok:
                    return
                target = next((o for o in self.objects_cache if o.name == name), None)
                if not target:
                    return
                affected = self.repo.bulk_update_field(camera_ids, "object_id", target.id)
                self._log(f"Перенесено камер в «{target.name}»: {affected}")

            elif field in ("enable", "disable"):
                value = 1 if field == "enable" else 0
                affected = self.repo.bulk_update_field(camera_ids, "enabled", value)
                state = "включено" if value else "выключено"
                self._log(f"Массово {state} камер: {affected}")
            else:
                return
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось применить массовое изменение:\n{exc}")
            return

        self._refresh_objects()
        self._refresh_cameras()

    def _open_camera_stream(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        result = self.ffplay.launch(cam.rtsp_url, f"{cam.object_name} / {cam.camera_name}")
        if not result.ok:
            QMessageBox.warning(self, "FFplay", result.error or "Не удалось открыть поток")
            return
        self._log(f"Открыт поток: {cam.camera_name}")
        self.lower()
        self.clearFocus()
        QTimer.singleShot(300, self._activate_ffplay_window)

    def _activate_ffplay_window(self) -> None:
        osa = shutil.which("osascript")
        if not osa:
            return
        try:
            subprocess.Popen(
                [
                    osa,
                    "-e",
                    'tell application "System Events" to set frontmost of (first process whose name is "ffplay") to true',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def _check_single_camera(self, camera_id: int) -> None:
        cam = self.repo.get_camera(camera_id)
        if not cam:
            return
        self.checker.check_camera(cam)
        self._log(f"Проверка камеры: {cam.camera_name}")

    def _manual_check_all(self) -> None:
        cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        enabled = [c for c in cameras if c.enabled]
        self.checker.check_many(enabled)
        self._log(f"Запущена ручная проверка всех объектов: {len(enabled)} камер")

    def _auto_check_all_enabled(self) -> None:
        cameras = self.repo.list_cameras(object_id=None, search="", status_filter="all")
        enabled = [c for c in cameras if c.enabled]
        self.checker.check_many(enabled)
        self._log(f"Автопроверка: {len(enabled)} камер")

    def _on_coords_copied(self, coords: str) -> None:
        self._log(f"Координаты скопированы: {coords}")

    def _on_rtsp_copied(self, url: str) -> None:
        from app.utils.validators import mask_rtsp_url

        self._log(f"RTSP-ссылка скопирована: {mask_rtsp_url(url)}")

    def _on_camera_checked(self, result: CheckResult) -> None:
        self.repo.update_camera_status(
            camera_id=result.camera_id,
            status=result.status,
            last_checked_at=result.checked_at,
            last_error=result.error,
            last_seen_online_at=result.seen_online_at,
        )
        self._log(
            f"Проверка завершена camera_id={result.camera_id}: {result.status}"
            + (f" ({result.error})" if result.error else "")
        )
        if not self._refresh_debounce.isActive():
            self._refresh_debounce.start()

    def _refresh_views_after_checks(self) -> None:
        self._refresh_objects()
        self._refresh_cameras()

    def _open_import_dialog(self) -> None:
        import traceback

        try:
            dlg = ImportDialog(self.import_service, self.template_service, self)
        except Exception as exc:
            tb = traceback.format_exc()
            self._log(f"Ошибка открытия импорта: {exc}")
            QMessageBox.critical(self, "Импорт", f"Не удалось открыть окно импорта:\n{exc}\n\n{tb}")
            return
        dlg.import_completed.connect(self._on_import_completed)
        dlg.exec()

    def _on_import_completed(self, created: int, updated: int) -> None:
        self._refresh_objects()
        self._refresh_cameras()
        self._log(f"Импорт завершен. Создано={created}, обновлено={updated}")

    def closeEvent(self, event) -> None:
        try:
            killed = self.ffplay.terminate_all()
            if killed:
                self._log(f"Закрыто окон ffplay: {killed}")
        except Exception as exc:
            self._log(f"Не удалось закрыть ffplay: {exc}")
        super().closeEvent(event)

    def _git_pull_from_github(self) -> None:
        from app.services.git_service import GitPullService

        if config.PROJECT_GIT_DIR is None:
            QMessageBox.information(
                self,
                "GitHub",
                (
                    "Не найден git-репозиторий проекта на этом Mac.\n\n"
                    "Чтобы кнопка обновляла код, выполните в терминале один раз:\n\n"
                    f"  git clone {config.GITHUB_REPO_URL} ~/rtsp-camera-service\n\n"
                    "После этого кнопка будет делать `git pull` в этом каталоге."
                ),
            )
            return

        if not hasattr(self, "_git_service"):
            self._git_service = GitPullService(self)
            self._git_service.finished.connect(self._on_git_pull_done)
        self.git_btn.setEnabled(False)
        self._log(f"git pull в {config.PROJECT_GIT_DIR}...")
        self._git_service.start()

    def _on_git_pull_done(self, ok: bool, message: str) -> None:
        self.git_btn.setEnabled(True)
        log_message = ("OK: " if ok else "ERROR: ") + message.strip()
        self._log(log_message)
        if not ok:
            QMessageBox.warning(self, "GitHub", message.strip() or "Не удалось обновить из GitHub")
        else:
            QMessageBox.information(
                self,
                "GitHub",
                "Обновление из GitHub выполнено.\n\n" + message.strip()[:1500] +
                "\n\nДля применения новых изменений перезапустите приложение.",
            )

