# ui/windows/m5_0_upload_images.py
# Есть следующие баги
# При выходе он не очищает лишнее
# -*- coding: utf-8 -*-
import os
import shutil
import json
import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QSize, QEvent, QRect, QMimeData, QIODevice, Signal, QAbstractItemModel,
    QThread, QObject, Slot, QMetaObject, Q_ARG, QPoint
)
from PySide6.QtGui import (
    QPixmap, QImage, QAction, QDrag
)
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QFileDialog, QMessageBox,
    QProgressBar, QStackedWidget, QAbstractItemView, QScrollArea,
    QStackedLayout, QApplication, QSizePolicy
)

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


##############################################################################
# Вспомогательные функции
##############################################################################
def loadImageConvertToPNG(source_path: str, target_path: str) -> bool:
    """
    Пытается конвертировать (source_path) -> PNG (target_path).
    Возвращает True, если успех, иначе False.
    """
    if PIL_AVAILABLE:
        try:
            with Image.open(source_path) as im:
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGB")
                im.save(target_path, "PNG")
            print(f"[loadImageConvertToPNG] Конвертация {source_path} -> {target_path} (PIL).")
            return True
        except Exception as e:
            print(f"[loadImageConvertToPNG] Ошибка PIL: {e}")
            return False

    # Если нет PIL или произошла ошибка
    img = QImage(source_path)
    if not img.isNull():
        if img.save(target_path, "PNG"):
            print(f"[loadImageConvertToPNG] Конвертация {source_path} -> {target_path} (QImage).")
            return True
        else:
            print("[loadImageConvertToPNG] Ошибка сохранения QImage.")
            return False
    else:
        # Если и QImage не смог, тогда просто копируем
        try:
            shutil.copy2(source_path, target_path)
            print(f"[loadImageConvertToPNG] Копирование {source_path} -> {target_path}.")
            return True
        except Exception as e:
            print(f"[loadImageConvertToPNG] Ошибка копирования: {e}")
            return False


def formatFileSize(size_bytes: int) -> str:
    """Человекочитаемое представление размера файла."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024**2:
        return f"{round(size_bytes/1024, 1)} КБ"
    elif size_bytes < 1024**3:
        return f"{round(size_bytes/(1024**2), 1)} МБ"
    else:
        return f"{round(size_bytes/(1024**3), 2)} ГБ"


##############################################################################
# Класс Worker для добавления файлов (многопоточность)
##############################################################################
class Worker(QObject):
    processed_file = Signal(dict)  # Сигнал при успешной обработке одного файла
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.upload_folder = ""

    @Slot(str)
    def add_file_slot(self, file_path: str):
        """
        Слот для добавления файла. Вызывается в потоке воркера.
        """
        if not os.path.exists(self.upload_folder):
            err_msg = f"Папка загрузки не существует: {self.upload_folder}"
            self.error.emit(err_msg)
            print("[Worker]", err_msg)
            return

        original_name = os.path.basename(file_path)
        now_str = datetime.datetime.now().isoformat()
        timestamp = int(datetime.datetime.now().timestamp())
        temp_name = f"temp_{timestamp}_{original_name}.png"
        target_path = os.path.join(self.upload_folder, temp_name)

        print(f"[Worker] Обработка файла: {file_path} => {target_path}")
        ok = loadImageConvertToPNG(file_path, target_path)
        if not ok:
            err_msg = f"Не удалось обработать файл: {original_name}"
            self.error.emit(err_msg)
            print("[Worker]", err_msg)
            return

        try:
            sz = os.path.getsize(target_path)
            rec = {
                "original_name": original_name,
                "current_name": temp_name,
                "size": sz,
                "updated_at": now_str,
                "added_at": now_str
            }
            # Уведомляем основной поток
            self.processed_file.emit(rec)
        except Exception as e:
            err_msg = f"Ошибка при получении размера файла: {e}"
            self.error.emit(err_msg)
            print("[Worker]", err_msg)


##############################################################################
# Класс Worker для асинхронной загрузки миниатюр (многопоточность)
##############################################################################
class ImageLoaderWorker(QObject):
    finished = Signal()
    imageLoaded = Signal(int, object)  # int - индекс, object - QImage
    error = Signal(str)
    loadImages = Signal(list, str)  # внешний сигнал, который запускает startLoading

    def __init__(self):
        super().__init__()
        self.running = True
        self.images_data = []
        self.upload_folder = ""
        self.target_size = QSize(140, 140)

    @Slot(list, str)
    def startLoading(self, images_data, upload_folder):
        """
        Слот, чтобы запустить загрузку миниатюр.
        """
        if not self.running:
            print("[ImageLoaderWorker] Операция прервана.")
            return

        self.images_data = images_data
        self.upload_folder = upload_folder
        self.run()  # запускаем непосредственно

    def run(self):
        """
        Основная логика загрузки миниатюр. Вызывается в потоке воркера.
        """
        total = len(self.images_data)
        print(f"[ImageLoaderWorker] Загружаем миниатюры, всего: {total} шт.")

        for idx, info in enumerate(self.images_data):
            if not self.running:
                break

            current_name = info.get("current_name", "")
            full_path = os.path.join(self.upload_folder, current_name)
            if not os.path.isfile(full_path):
                print(f"[ImageLoaderWorker] Файл не найден: {full_path}")
                # Формируем серую заглушку
                img = QImage(self.target_size, QImage.Format_RGB32)
                img.fill(Qt.darkGray)
                self.imageLoaded.emit(idx, img)
                continue

            # Загружаем картинку
            img = QImage(full_path)
            if img.isNull():
                print(f"[ImageLoaderWorker] Не удалось загрузить {full_path}, формируем заглушку.")
                img = QImage(self.target_size, QImage.Format_RGB32)
                img.fill(Qt.darkGray)
            self.imageLoaded.emit(idx, img)

        self.finished.emit()
        print("[ImageLoaderWorker] Загрузка миниатюр завершена.")

    def stop(self):
        self.running = False
        print("[ImageLoaderWorker] Остановка загрузки миниатюр.")


##############################################################################
# Класс виджета с "плитками"
##############################################################################
class TileGridWidget(QScrollArea):
    orderChanged = Signal()
    selectionChanged = Signal()
    externalFilesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.setWidget(self.container)

        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(10)

        self.tile_items = []
        self.selected_indexes = set()

        # Разрешаем дроп внешних файлов
        self.setAcceptDrops(True)

    def showNoImagesMessage(self, show: bool):
        if show:
            if not hasattr(self, 'no_images_label'):
                self.no_images_label = QLabel("Нет изображений. Добавьте новые файлы.", self)
                self.no_images_label.setStyleSheet("color: #AAA; font-size: 14px;")
                self.no_images_label.setAlignment(Qt.AlignCenter)
                self.grid_layout.addWidget(self.no_images_label, 0, 0)
            self.no_images_label.show()
        else:
            if hasattr(self, 'no_images_label'):
                self.no_images_label.hide()

    def clearTiles(self):
        """Удаляем все плитки с layout'а."""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w and w != getattr(self, 'no_images_label', None):
                w.deleteLater()
        self.tile_items.clear()
        self.selected_indexes.clear()

    def addTile(self, tile_widget):
        idx = len(self.tile_items)
        row = idx // 5  # 5 плиток в ряд
        col = idx % 5
        self.grid_layout.addWidget(tile_widget, row, col)
        self.tile_items.append(tile_widget)
        tile_widget.clicked.connect(self.onTileClicked)

    def rebuildGrid(self):
        """Перестраиваем сетку (обычно после изменения порядка)."""
        for i in reversed(range(self.grid_layout.count())):
            item = self.grid_layout.takeAt(i)
            w = item.widget()
            if w and w != getattr(self, 'no_images_label', None):
                w.setParent(None)

        for idx, tile in enumerate(self.tile_items):
            row = idx // 5
            col = idx % 5
            self.grid_layout.addWidget(tile, row, col)
            tile.setIndex(idx)
            tile.setSelected(idx in self.selected_indexes)

    # ------------------------------------------------------------------------
    # Drag & Drop
    # ------------------------------------------------------------------------
    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls():
            event.acceptProposedAction()
        elif md.hasFormat("application/x-uploadwindow-tile"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasFormat("application/x-uploadwindow-tile"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        md = event.mimeData()
        if md.hasUrls():
            # Внешние файлы
            file_paths = []
            for url in md.urls():
                local_path = url.toLocalFile()
                if local_path:
                    file_paths.append(local_path)
            if file_paths:
                self.externalFilesDropped.emit(file_paths)
            event.acceptProposedAction()

        elif md.hasFormat("application/x-uploadwindow-tile"):
            # Перетаскивание внутри приложения
            drag_data = md.data("application/x-uploadwindow-tile").data()
            try:
                oldIndex = int(drag_data.decode())
            except ValueError:
                oldIndex = None

            if oldIndex is None or oldIndex < 0 or oldIndex >= len(self.tile_items):
                event.ignore()
                return

            # Точка сброса в координатах QScrollArea.viewport()
            scroll_pos = event.position().toPoint()
            # Переводим в координаты self.container
            containerPos = self.container.mapFrom(self.viewport(), scroll_pos)

            dropIndex = self._findInsertIndex(containerPos)
            if dropIndex is None:
                dropIndex = len(self.tile_items)

            if dropIndex != oldIndex:
                tile = self.tile_items.pop(oldIndex)
                if dropIndex > len(self.tile_items):
                    dropIndex = len(self.tile_items)
                self.tile_items.insert(dropIndex, tile)
                self.rebuildGrid()
                self.orderChanged.emit()
            event.acceptProposedAction()
        else:
            event.ignore()

    def _findInsertIndex(self, containerPos: QPoint):
        """
        Ищем индекс плитки, перед которой нужно вставить.
        Учитываем, что координаты уже переведены в систему self.container.
        """
        for i, tile in enumerate(self.tile_items):
            wpos = tile.pos()  # Позиция внутри self.container
            rect = QRect(wpos, tile.size())
            if rect.contains(containerPos):
                return i
        return None

    # ------------------------------------------------------------------------
    # Выделение плиток
    # ------------------------------------------------------------------------
    def onTileClicked(self, tile_index: int, modifiers):
        if modifiers & Qt.ControlModifier:
            # Тоггл
            if tile_index in self.selected_indexes:
                self.selected_indexes.remove(tile_index)
            else:
                self.selected_indexes.add(tile_index)
        elif modifiers & Qt.ShiftModifier:
            if self.selected_indexes:
                mn = min(self.selected_indexes)
                mx = max(self.selected_indexes)
                if tile_index < mn:
                    for i in range(tile_index, mn + 1):
                        self.selected_indexes.add(i)
                elif tile_index > mx:
                    for i in range(mx, tile_index + 1):
                        self.selected_indexes.add(i)
            else:
                self.selected_indexes.add(tile_index)
        else:
            # Сбрасываем всё и выделяем одну
            self.selected_indexes.clear()
            self.selected_indexes.add(tile_index)

        self.rebuildGrid()
        self.selectionChanged.emit()

    def getSelectedTiles(self):
        return sorted(list(self.selected_indexes))

    def selectAllTiles(self):
        self.selected_indexes = set(range(len(self.tile_items)))
        self.rebuildGrid()
        self.selectionChanged.emit()


class TileItem(QWidget):
    clicked = Signal(int, object)

    def __init__(self, index=0, image_name="", pixmap=None, parent=None):
        super().__init__(parent)
        self._index = index
        self.image_name = image_name
        self.selected = False

        self.setFixedSize(180, 200)
        layout_ = QVBoxLayout(self)
        layout_.setContentsMargins(0, 0, 0, 0)
        layout_.setSpacing(0)

        # Иконка
        self.icon_container = QWidget()
        self.icon_container.setStyleSheet("background: transparent;")
        self.icon_container.setFixedSize(180, 150)
        self.icon_layout = QGridLayout(self.icon_container)
        self.icon_layout.setContentsMargins(0, 0, 0, 0)
        self.icon_layout.setSpacing(0)

        self.label_icon = QLabel()
        self.label_icon.setAlignment(Qt.AlignCenter)

        if pixmap is not None:
            self.label_icon.setPixmap(pixmap.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.label_icon.setStyleSheet("background: #666;")

        self.icon_layout.addWidget(self.label_icon, 0, 0)

        # Номер
        self.label_index = QLabel(str(index + 1), self.icon_container)
        self.label_index.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.8);
            color: #FFF;
            font-weight: bold;
            font-size: 12px;
            padding: 1px 3px;
            border-top-left-radius: 10px;
            border-bottom-right-radius: 10px;
        """)
        self.icon_layout.addWidget(self.label_index, 0, 0, alignment=Qt.AlignLeft | Qt.AlignTop)

        # Имя файла
        self.label_name = QLabel(image_name)
        self.label_name.setAlignment(Qt.AlignCenter)
        self.label_name.setStyleSheet("color: white; padding: 3px;")

        layout_.addWidget(self.icon_container, alignment=Qt.AlignCenter)
        layout_.addWidget(self.label_name, alignment=Qt.AlignCenter)

        self.updateStyle()

    def setIndex(self, idx):
        self._index = idx
        self.label_index.setText(str(idx + 1))

    def setSelected(self, sel):
        self.selected = sel
        self.updateStyle()

    def updatePixmap(self, pixmap):
        if pixmap is None:
            self.label_icon.setStyleSheet("background: #666;")
        else:
            self.label_icon.setPixmap(pixmap.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def updateStyle(self):
        if self.selected:
            # Белая рамка при выделении
            self.setStyleSheet("""
                background-color: #3E3E5F;
                border: 2px solid #FFFFFF;
                border-radius: 8px;
            """)
        else:
            self.setStyleSheet("""
                background-color: transparent;
                border: none;
                border-radius: 8px;
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._index, event.modifiers())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Запуск драга
        if event.buttons() & Qt.LeftButton:
            drag = QDrag(self)
            md = QMimeData()
            md.setData("application/x-uploadwindow-tile", str(self._index).encode())
            drag.setMimeData(md)
            drag.exec_(Qt.MoveAction)
        super().mouseMoveEvent(event)


##############################################################################
# Основное окно UploadWindow
##############################################################################
class UploadWindow(QDialog):
    def __init__(self, chapter_path: str, stage_folder: str = "Загрузка", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузка изображений")
        self.resize(1000, 700)

        # Безрамочное окно
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # Пути
        self.chapter_path = chapter_path
        self.stage_folder = stage_folder
        self.upload_folder = os.path.join(chapter_path, stage_folder)
        os.makedirs(self.upload_folder, exist_ok=True)
        print(f"[UploadWindow] Папка загрузки: {self.upload_folder}")

        self.images_json = os.path.join(self.upload_folder, "images.json")
        self.images_data = []
        self.modified = False
        self.loading_now = False

        # Кэш
        self.pixmaps_cache = {}

        # Сразу почистим старые temp_*
        self.deleteObsoleteTempFiles()

        # Сборка UI
        self.initUI()

        # Загрузка существующих
        self.loadExistingData()

        # Создаём интерфейс
        self.refreshUI()

    def deleteObsoleteTempFiles(self):
        """
        Удаляем только temp_*-файлы, которых нет в images.json.
        """
        print("[UploadWindow] Удаляем неактуальные временные (temp_*) файлы.")
        if not os.path.isdir(self.upload_folder):
            return

        old_records = []
        if os.path.isfile(self.images_json):
            try:
                with open(self.images_json, "r", encoding="utf-8") as f:
                    old_records = json.load(f)
            except:
                old_records = []

        needed_temp_files = set()
        for rec in old_records:
            cn = rec.get("current_name", "")
            if cn.startswith("temp_"):
                needed_temp_files.add(cn)

        for fname in os.listdir(self.upload_folder):
            if fname.startswith("temp_") and fname not in needed_temp_files:
                full_path = os.path.join(self.upload_folder, fname)
                try:
                    os.remove(full_path)
                    print(f"[UploadWindow] Удалён старый temp-файл: {full_path}")
                except Exception as e:
                    print(f"[UploadWindow] Не удалось удалить {full_path}: {e}")

    def initUI(self):
        main_widget = QWidget(self)
        main_widget.setObjectName("main_widget")
        main_widget.setStyleSheet("""
            #main_widget {
                background-color: #3E3E5F;
                border-radius: 15px;
            }
        """)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)
        self.setLayout(QVBoxLayout())
        self.layout().addWidget(main_widget)

        # Заголовок
        title_bar = QHBoxLayout()
        self.lbl_title = QLabel("Загрузка изображений")
        self.lbl_title.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        title_bar.addWidget(self.lbl_title)

        title_bar.addStretch(1)

        # Кнопка закрытия
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(30, 30)
        btn_close.setStyleSheet("""
            QPushButton {
                color: white;
                background: transparent;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                color: #FF5C5C;
            }
        """)
        btn_close.clicked.connect(self.onCloseClicked)
        title_bar.addWidget(btn_close)

        main_layout.addLayout(title_bar)

        # Полоса для перетаскивания окна
        self.dragWidget = QWidget()
        self.dragWidget.setFixedHeight(5)
        drag_layout = QVBoxLayout(self.dragWidget)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.dragWidget)
        self.dragWidget.mousePressEvent = self.dragWidget_mousePressEvent
        self.dragWidget.mouseMoveEvent = self.dragWidget_mouseMoveEvent

        # Инструменты
        tool_bar = QHBoxLayout()

        self.btn_select = QPushButton("Добавить...")
        self.btn_select.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F; 
                color: white;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #5E5E7F;
            }
        """)
        self.btn_select.clicked.connect(self.selectFiles)
        tool_bar.addWidget(self.btn_select)

        self.view_combo = QComboBox()
        self.view_combo.setStyleSheet("""
            QComboBox {
                background-color: #4E4E6F; 
                color: white;
                border-radius: 6px;
                padding: 4px;
            }
            QComboBox:hover {
                background-color: #5E5E7F;
            }
        """)
        self.view_combo.addItem("Плитки")
        self.view_combo.addItem("Список")
        self.view_combo.currentIndexChanged.connect(self.onViewModeChanged)
        tool_bar.addWidget(self.view_combo)

        self.sort_combo = QComboBox()
        self.sort_combo.setStyleSheet("""
            QComboBox {
                background-color: #4E4E6F; 
                color: white;
                border-radius: 6px;
                padding: 4px;
            }
            QComboBox:hover {
                background-color: #5E5E7F;
            }
        """)
        self.sort_combo.addItem("Без сортировки (перетаскивание)")
        self.sort_combo.addItem("По времени добавления (новые в начале)")
        self.sort_combo.addItem("По времени добавления (старые в начале)")
        self.sort_combo.addItem("По имени (А → Я)")
        self.sort_combo.addItem("По имени (Я → А)")
        self.sort_combo.addItem("По размеру (возрастание)")
        self.sort_combo.addItem("По размеру (убывание)")
        self.sort_combo.currentIndexChanged.connect(self.applySort)
        tool_bar.addWidget(self.sort_combo)

        tool_bar.addStretch(1)

        self.btn_delete = QPushButton("Удалить выбранные")
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: #803333; 
                color: white;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #994444;
            }
        """)
        self.btn_delete.setVisible(False)
        self.btn_delete.clicked.connect(self.deleteSelected)
        tool_bar.addWidget(self.btn_delete)

        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F; 
                color: white;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #8E2EBF;
            }
        """)
        self.btn_save.clicked.connect(self.saveChanges)
        tool_bar.addWidget(self.btn_save)

        main_layout.addLayout(tool_bar)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #333;
                border-radius: 6px;
                background-color: #2E2E4F; 
            }
            QProgressBar::chunk {
                background-color: #7E1E9F;
                border: none;
                border-radius: 6px;
            }
        """)
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)

        # Основная область: плиточный / табличный вид
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget, 1)

        # 1) Плитки
        self.tileGrid = TileGridWidget()
        self.tileGrid.orderChanged.connect(self.onTilesOrderChanged)
        self.tileGrid.selectionChanged.connect(self.onTileSelectionChanged)
        self.tileGrid.externalFilesDropped.connect(self.onExternalFilesDropped)
        self.stacked_widget.addWidget(self.tileGrid)

        # 2) Таблица
        self.tableWidget = QTableWidget()
        self.tableWidget.setStyleSheet("""
            QTableWidget {
                background-color: #2E2E4F;
                color: white;
                border: none;
            }
            QHeaderView::section {
                background-color: #4E4E6F;
                color: white;
            }
            QTableWidget::item:selected {
                background-color: rgba(255, 255, 255, 0.2);
            }
        """)
        self.tableWidget.setColumnCount(4)
        self.tableWidget.setHorizontalHeaderLabels(["№", "Изображение", "Имя", "Размер"])
        self.tableWidget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tableWidget.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tableWidget.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tableWidget.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tableWidget.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tableWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tableWidget.itemSelectionChanged.connect(self.onTableSelectionChanged)
        self.stacked_widget.addWidget(self.tableWidget)

        # Горячие клавиши
        del_shortcut = QAction(self)
        del_shortcut.setShortcut("Delete")
        del_shortcut.triggered.connect(self.deleteSelected)
        self.addAction(del_shortcut)

        back_shortcut = QAction(self)
        back_shortcut.setShortcut("Backspace")
        back_shortcut.triggered.connect(self.deleteSelected)
        self.addAction(back_shortcut)

        select_all_shortcut = QAction(self)
        select_all_shortcut.setShortcut("Ctrl+A")
        select_all_shortcut.triggered.connect(self.onCtrlA)
        self.addAction(select_all_shortcut)

        # Первый поток (Worker)
        self.worker_thread = QThread()
        self.worker = Worker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.processed_file.connect(self._addNewFileThreadSafe)
        self.worker.error.connect(self.onWorkerError)
        self.worker_thread.started.connect(lambda: print("[UploadWindow] Поток Worker запущен."))
        self.worker_thread.start()
        self.worker.upload_folder = self.upload_folder

        # Второй поток (ImageLoaderWorker)
        self.imageLoaderThread = QThread()
        self.imageLoader = ImageLoaderWorker()
        self.imageLoader.moveToThread(self.imageLoaderThread)
        self.imageLoader.finished.connect(self.onImageLoaderFinished)
        self.imageLoader.imageLoaded.connect(self.onImageLoaded)
        self.imageLoader.error.connect(self.onImageLoaderError)
        self.imageLoader.loadImages.connect(self.imageLoader.startLoading)
        self.imageLoaderThread.started.connect(lambda: print("[UploadWindow] Поток ImageLoader запущен."))
        self.imageLoaderThread.start()

    def onViewModeChanged(self, idx: int):
        """Переключаем стек на (0 = плитки, 1 = список)."""
        self.stacked_widget.setCurrentIndex(idx)
        self.updateDeleteButtonVisibility()

    def onWorkerError(self, message: str):
        QMessageBox.critical(self, "Ошибка обработки файла", message)

    def loadExistingData(self):
        """
        Загружаем (если есть) список файлов из images.json.
        Удаляем фантомные записи, которых нет на диске.
        """
        if not os.path.isfile(self.images_json):
            print("[UploadWindow] images.json не найден, начинаем с пустого списка.")
            return
        try:
            with open(self.images_json, "r", encoding="utf-8") as f:
                self.images_data = json.load(f)
            print(f"[UploadWindow] Загружено {len(self.images_data)} файлов из JSON.")
        except Exception as e:
            print(f"[UploadWindow] Ошибка загрузки JSON: {e}")
            self.images_data = []

        # Убедимся, что у всех есть added_at
        for img in self.images_data:
            if "added_at" not in img:
                img["added_at"] = img.get("updated_at", datetime.datetime.now().isoformat())

        # Убираем фантомные записи, которых нет на диске
        existing_files = []
        for info in self.images_data:
            cur_name = info.get("current_name", "")
            full_path = os.path.join(self.upload_folder, cur_name)
            if os.path.isfile(full_path):
                existing_files.append(info)
            else:
                print(f"[UploadWindow] Файл {cur_name} отсутствует на диске и будет удалён из списка.")

        self.images_data = existing_files

    def refreshUI(self):
        """Пересоздаём плитки и таблицу, запускаем загрузку миниатюр."""
        self.pixmaps_cache.clear()

        # Плитки
        self.tileGrid.clearTiles()
        if not self.images_data:
            self.tileGrid.showNoImagesMessage(True)
        else:
            self.tileGrid.showNoImagesMessage(False)
            for i, info in enumerate(self.images_data):
                tile = TileItem(i, info["original_name"], None)
                self.tileGrid.addTile(tile)
            self.tileGrid.rebuildGrid()

        # Таблица
        self.tableWidget.setRowCount(0)
        if self.images_data:
            self.tableWidget.setRowCount(len(self.images_data))
            for i, info in enumerate(self.images_data):
                it_index = QTableWidgetItem(str(i + 1))
                it_index.setFlags(it_index.flags() ^ Qt.ItemIsEditable)

                icon_label = QLabel()
                icon_label.setAlignment(Qt.AlignCenter)

                it_name = QTableWidgetItem(info["original_name"])
                it_size = QTableWidgetItem(formatFileSize(info["size"]))
                it_size.setFlags(it_size.flags() ^ Qt.ItemIsEditable)

                self.tableWidget.setItem(i, 0, it_index)
                self.tableWidget.setCellWidget(i, 1, icon_label)
                self.tableWidget.setItem(i, 2, it_name)
                self.tableWidget.setItem(i, 3, it_size)

        self.modified = False
        self.updateDeleteButtonVisibility()

        # Запуск загрузки миниатюр (асинхронно)
        if self.images_data:
            self.progress_bar.setVisible(True)
            self.imageLoader.loadImages.emit(self.images_data, self.upload_folder)
        else:
            self.progress_bar.setVisible(False)

    @Slot(int, object)
    def onImageLoaded(self, index, qimage):
        """Когда картинка загружена (QImage) в потоке."""
        if index < 0 or index >= len(self.images_data):
            return
        px = QPixmap.fromImage(qimage)
        self.pixmaps_cache[index] = px

        # Обновляем плитку
        if index < len(self.tileGrid.tile_items):
            tile_item = self.tileGrid.tile_items[index]
            tile_item.updatePixmap(px)

        # Обновляем таблицу
        lbl = self.tableWidget.cellWidget(index, 1)
        if isinstance(lbl, QLabel):
            scaled = px.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl.setPixmap(scaled)

    @Slot()
    def onImageLoaderFinished(self):
        """Завершили загрузку миниатюр."""
        self.progress_bar.setVisible(False)

    @Slot(str)
    def onImageLoaderError(self, message):
        QMessageBox.warning(self, "Ошибка загрузки миниатюр", message)

    @Slot(dict)
    def _addNewFileThreadSafe(self, file_info: dict):
        """Добавление нового файла из воркера (в главном потоке)."""
        self.images_data.append(file_info)
        self.modified = True
        self.refreshUI()
        self.loading_now = False

    def onExternalFilesDropped(self, file_paths):
        """DnD внешних файлов на плитки."""
        if self.loading_now:
            print("[UploadWindow] Уже идёт загрузка, игнорируем.")
            return
        self.loading_now = True
        self.progress_bar.setVisible(True)
        for path in file_paths:
            if os.path.isfile(path):
                QMetaObject.invokeMethod(
                    self.worker,
                    "add_file_slot",
                    Qt.QueuedConnection,
                    Q_ARG(str, path)
                )

    def selectFiles(self):
        """Кнопка 'Добавить...'"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы",
            "", "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp);;Все файлы (*.*)"
        )
        if not files:
            return
        if self.loading_now:
            print("[UploadWindow] Уже идёт загрузка, игнорируем.")
            return
        self.loading_now = True
        self.progress_bar.setVisible(True)
        for f in files:
            if os.path.isfile(f):
                QMetaObject.invokeMethod(
                    self.worker,
                    "add_file_slot",
                    Qt.QueuedConnection,
                    Q_ARG(str, f)
                )

    def applySort(self, idx):
        """Сортируем images_data."""
        if idx == 0:
            return

        def by_added(desc=False):
            return sorted(self.images_data, key=lambda x: x["added_at"], reverse=desc)

        def by_name(desc=False):
            return sorted(self.images_data, key=lambda x: x["original_name"].lower(), reverse=desc)

        def by_size(desc=False):
            return sorted(self.images_data, key=lambda x: x["size"], reverse=desc)

        if idx == 1:  # новые в начале
            self.images_data = by_added(desc=True)
        elif idx == 2:  # старые в начале
            self.images_data = by_added(desc=False)
        elif idx == 3:  # имя: А → Я
            self.images_data = by_name(desc=False)
        elif idx == 4:  # имя: Я → А
            self.images_data = by_name(desc=True)
        elif idx == 5:  # размер: возрастание
            self.images_data = by_size(desc=False)
        elif idx == 6:  # размер: убывание
            self.images_data = by_size(desc=True)

        self.refreshUI()
        self.modified = True

    def onTilesOrderChanged(self):
        """
        Когда пользователь перетащил плитки, меняем порядок self.images_data
        в соответствии с порядком плиток.
        """
        newData = []
        for tile_widget in self.tileGrid.tile_items:
            name_label = tile_widget.label_name.text()
            found = None
            for info in self.images_data:
                if info["original_name"] == name_label:
                    found = info
                    break
            if found:
                newData.append(found)

        if len(newData) == len(self.images_data):
            self.images_data = newData
            self.refreshUI()
            self.modified = True

    def onTileSelectionChanged(self):
        self.updateDeleteButtonVisibility()

    def onTableSelectionChanged(self):
        self.updateDeleteButtonVisibility()

    def deleteSelected(self):
        """
        Удаляем выбранные плитки или строки. Физически удаляем файл,
        затем удаляем запись из self.images_data.
        """
        idx = self.stacked_widget.currentIndex()
        removed_any = False

        if idx == 0:
            # Удаляем выбранные плитки
            sel_indexes = self.tileGrid.getSelectedTiles()
            if not sel_indexes:
                return
            for tile_idx in reversed(sel_indexes):
                if 0 <= tile_idx < len(self.images_data):
                    info = self.images_data[tile_idx]
                    full_path = os.path.join(self.upload_folder, info["current_name"])
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception as e:
                            QMessageBox.warning(self, "Ошибка удаления", str(e))
                    del self.images_data[tile_idx]
            removed_any = True

        else:
            # Удаляем выбранные строки таблицы
            rows = self.tableWidget.selectionModel().selectedRows()
            selected_rows = [r.row() for r in rows]
            if not selected_rows:
                return
            for ridx in reversed(selected_rows):
                if 0 <= ridx < len(self.images_data):
                    info = self.images_data[ridx]
                    full_path = os.path.join(self.upload_folder, info["current_name"])
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception as e:
                            QMessageBox.warning(self, "Ошибка удаления", str(e))
                    del self.images_data[ridx]
            removed_any = True

        if removed_any:
            self.modified = True
            self.refreshUI()

    def saveChanges(self):
        """
        1) Собираем порядок из плиток (актуальный).
        2) Переименовываем файлы в 0001.png, 0002.png, ...
           с учётом нового порядка.
        3) Сохраняем JSON.
        """
        print("[UploadWindow] Сохраняем изменения.")

        # Сначала берём актуальный порядок из плиток
        ordered_data = []
        for tile_widget in self.tileGrid.tile_items:
            name_label = tile_widget.label_name.text()
            found = None
            for info in self.images_data:
                if info["original_name"] == name_label:
                    found = info
                    break
            if found:
                ordered_data.append(found)

        counter = 1
        new_data = []
        for info in ordered_data:
            old_name = info["current_name"]
            # Формируем имя с ведущими нулями: 4 знака (0001, 0002, ...)
            new_name = f"{counter:04d}.png"
            old_path = os.path.join(self.upload_folder, old_name)
            new_path = os.path.join(self.upload_folder, new_name)

            if old_name != new_name:  # Если имя уже совпадает, ничего не делаем
                # Если новый файл уже существует, удаляем его
                if os.path.exists(new_path):
                    try:
                        os.remove(new_path)
                    except Exception as e:
                        QMessageBox.warning(self, "Ошибка", f"Не удалось удалить {new_name}: {e}")
                        continue

                # Переименовываем
                if os.path.exists(old_path):
                    try:
                        os.rename(old_path, new_path)
                    except Exception as e:
                        QMessageBox.warning(self, "Ошибка",
                                            f"Не удалось переименовать {old_name} -> {new_name}: {e}")
                        continue

            # Обновляем данные
            size_ = 0
            if os.path.exists(new_path):
                size_ = os.path.getsize(new_path)
            info["current_name"] = new_name
            info["size"] = size_
            info["updated_at"] = datetime.datetime.now().isoformat()
            new_data.append(info)
            counter += 1

        # Обновляем self.images_data по новому порядку
        self.images_data = new_data

        # Пишем JSON
        try:
            with open(self.images_json, "w", encoding="utf-8") as f:
                json.dump(self.images_data, f, ensure_ascii=False, indent=4)
            print(f"[UploadWindow] JSON сохранён: {self.images_json}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))
            return

        self.modified = False
        self.close()
        print("[UploadWindow] Изменения успешно сохранены и окно закрыто.")

    def updateDeleteButtonVisibility(self):
        idx = self.stacked_widget.currentIndex()
        if idx == 0:
            sel = self.tileGrid.getSelectedTiles()
            self.btn_delete.setVisible(len(sel) > 0)
        else:
            rows = self.tableWidget.selectionModel().selectedRows()
            self.btn_delete.setVisible(len(rows) > 0)

    def onCloseClicked(self):
        self.deleteObsoleteTempFiles()
        self.close()

    def closeEvent(self, event):
        if self.modified:
            resp = QMessageBox.question(
                self, "Несохранённые изменения",
                "Сохранить изменения перед выходом?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if resp == QMessageBox.Yes:
                self.saveChanges()
                self.deleteObsoleteTempFiles()
                event.ignore()  # окно закроется внутри saveChanges()
                return
            elif resp == QMessageBox.No:
                self.deleteTempFiles()
                event.accept()
            else:
                self.deleteObsoleteTempFiles()
                event.ignore()
                return
        else:
            event.accept()

        # Останавливаем потоки
        if self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()
        if self.imageLoaderThread.isRunning():
            self.imageLoader.stop()
            self.imageLoaderThread.quit()
            self.imageLoaderThread.wait()

    def deleteTempFiles(self):
        """
        Удаляем все temp_*-файлы, которые остались в self.images_data,
        если пользователь выбрал "Не сохранять".
        """
        for info in self.images_data:
            cn = info.get("current_name", "")
            if cn.startswith("temp_"):
                full_path = os.path.join(self.upload_folder, cn)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except:
                        pass

    # Перетаскивание окна
    def dragWidget_mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragPos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        else:
            self.dragPos = None
        event.accept()

    def dragWidget_mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.dragPos is not None:
            self.move(event.globalPosition().toPoint() - self.dragPos)
            event.accept()

    def onCtrlA(self):
        """Горячая клавиша Ctrl+A."""
        idx = self.stacked_widget.currentIndex()
        if idx == 0:
            self.tileGrid.selectAllTiles()
        else:
            self.tableWidget.selectAll()


# Для тестового запуска
if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    test_chapter = "data/projects/ТестовыйПроект/chapters/1/Загрузка"
    os.makedirs(test_chapter, exist_ok=True)
    wnd = UploadWindow(chapter_path=test_chapter, stage_folder="Загрузка")
    wnd.show()
    sys.exit(app.exec())

