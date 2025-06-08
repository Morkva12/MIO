# ui/windows/m5_0_upload_images.py

# -*- coding: utf-8 -*-
import os
import shutil
import json
import datetime
from pathlib import Path
import gc
import time

from PySide6.QtCore import (
    Qt, QSize, QEvent, QRect, QMimeData, QIODevice, Signal, QAbstractItemModel,
    QThread, QObject, Slot, QMetaObject, Q_ARG, QPoint, QMutex, QMutexLocker
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
    try:
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
    except Exception as e:
        print(f"[loadImageConvertToPNG] Неожиданная ошибка: {e}")
        return False


def formatFileSize(size_bytes: int) -> str:
    """Человекочитаемое представление размера файла."""
    if size_bytes < 1024:
        return f"{size_bytes} Б"
    elif size_bytes < 1024 ** 2:
        return f"{round(size_bytes / 1024, 1)} КБ"
    elif size_bytes < 1024 ** 3:
        return f"{round(size_bytes / (1024 ** 2), 1)} МБ"
    else:
        return f"{round(size_bytes / (1024 ** 3), 2)} ГБ"


##############################################################################
# Класс Worker для добавления файлов (многопоточность)
##############################################################################
class Worker(QObject):
    processed_file = Signal(dict)  # Сигнал при успешной обработке одного файла
    error = Signal(str)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.upload_folder = ""
        self.is_running = True
        self.mutex = QMutex()

    def stop(self):
        """Безопасная остановка воркера"""
        with QMutexLocker(self.mutex):
            self.is_running = False

    # В классе Worker измените метод add_file_slot:

    @Slot(str)
    def add_file_slot(self, file_path: str):
        """
        Слот для добавления файла. Вызывается в потоке воркера.
        """
        with QMutexLocker(self.mutex):
            if not self.is_running:
                return

        try:
            if not os.path.exists(self.upload_folder):
                err_msg = f"Папка загрузки не существует: {self.upload_folder}"
                self.error.emit(err_msg)
                print("[Worker]", err_msg)
                return

            original_name = os.path.basename(file_path)
            now_str = datetime.datetime.now().isoformat()

            # Используем микросекунды и случайное число для уникальности
            import random
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            random_suffix = random.randint(1000, 9999)

            # Добавляем хэш файла для различения одинаковых имен
            import hashlib
            with open(file_path, 'rb') as f:
                file_hash = hashlib.md5(f.read(1024)).hexdigest()[:8]

            temp_name = f"temp_{timestamp}_{random_suffix}_{file_hash}_{original_name}.png"
            target_path = os.path.join(self.upload_folder, temp_name)

            # Дополнительная проверка на существование файла
            counter = 1
            while os.path.exists(target_path):
                temp_name = f"temp_{timestamp}_{random_suffix}_{file_hash}_{counter}_{original_name}.png"
                target_path = os.path.join(self.upload_folder, temp_name)
                counter += 1

            print(f"[Worker] Обработка файла: {file_path} => {target_path}")

            with QMutexLocker(self.mutex):
                if not self.is_running:
                    return

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
                with QMutexLocker(self.mutex):
                    if self.is_running:
                        self.processed_file.emit(rec)
                        print(f"[Worker] Файл добавлен: {temp_name}")
            except Exception as e:
                err_msg = f"Ошибка при получении размера файла: {e}"
                self.error.emit(err_msg)
                print("[Worker]", err_msg)
        except Exception as e:
            print(f"[Worker] Критическая ошибка: {e}")
            self.error.emit(str(e))


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
        self.mutex = QMutex()

    @Slot(list, str)
    def startLoading(self, images_data, upload_folder):
        """
        Слот, чтобы запустить загрузку миниатюр.
        """
        with QMutexLocker(self.mutex):
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
            with QMutexLocker(self.mutex):
                if not self.running:
                    break

            try:
                current_name = info.get("current_name", "")
                full_path = os.path.join(self.upload_folder, current_name)

                if not os.path.isfile(full_path):
                    print(f"[ImageLoaderWorker] Файл не найден: {full_path}")
                    # Формируем серую заглушку
                    img = QImage(self.target_size, QImage.Format_RGB32)
                    img.fill(Qt.darkGray)
                else:
                    # Загружаем картинку
                    img = QImage(full_path)
                    if img.isNull():
                        print(f"[ImageLoaderWorker] Не удалось загрузить {full_path}, формируем заглушку.")
                        img = QImage(self.target_size, QImage.Format_RGB32)
                        img.fill(Qt.darkGray)

                with QMutexLocker(self.mutex):
                    if self.running:
                        self.imageLoaded.emit(idx, img)

            except Exception as e:
                print(f"[ImageLoaderWorker] Ошибка при загрузке изображения {idx}: {e}")

        with QMutexLocker(self.mutex):
            if self.running:
                self.finished.emit()
        print("[ImageLoaderWorker] Загрузка миниатюр завершена.")

    def stop(self):
        """Безопасная остановка загрузки"""
        with QMutexLocker(self.mutex):
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
    images_updated = Signal(bool)  # Сигнал о том, что изображения были обновлены

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
        self.saving_now = False  # Флаг для блокировки во время сохранения

        # Добавляем счетчик для уникальных ID
        self.next_image_id = 1

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
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setStyleSheet("""
            QPushButton {
                color: white;
                background: transparent;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                color: #FF5C5C;
            }
            QPushButton:disabled {
                color: #666;
            }
        """)
        self.btn_close.clicked.connect(self.onCloseClicked)
        title_bar.addWidget(self.btn_close)

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
            QPushButton:disabled {
                background-color: #3E3E4F;
                color: #666;
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
            QPushButton:disabled {
                background-color: #5E1E7F;
                color: #AAA;
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

    def showEvent(self, event):
        """При показе окна перезагружаем данные"""
        super().showEvent(event)
        # Перезагружаем существующие данные
        self.images_data = []
        self.loadExistingData()
        self.refreshUI()

    def onViewModeChanged(self, idx: int):
        """Переключаем стек на (0 = плитки, 1 = список)."""
        self.stacked_widget.setCurrentIndex(idx)
        self.updateDeleteButtonVisibility()

    def onWorkerError(self, message: str):
        if not self.saving_now:  # Не показываем ошибки во время сохранения
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

        # Убедимся, что у всех есть added_at и уникальный ID
        max_id = 0
        for img in self.images_data:
            if "added_at" not in img:
                img["added_at"] = img.get("updated_at", datetime.datetime.now().isoformat())

            # Добавляем ID если его нет
            if "id" not in img:
                img["id"] = self.next_image_id
                self.next_image_id += 1
            else:
                max_id = max(max_id, img["id"])

        # Обновляем счетчик ID
        if max_id > 0:
            self.next_image_id = max_id + 1

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
        # Очищаем кэш изображений перед обновлением
        for pixmap in self.pixmaps_cache.values():
            if pixmap and not pixmap.isNull():
                del pixmap
        self.pixmaps_cache.clear()
        gc.collect()

        # Плитки
        self.tileGrid.clearTiles()
        if not self.images_data:
            self.tileGrid.showNoImagesMessage(True)
        else:
            self.tileGrid.showNoImagesMessage(False)
            for i, info in enumerate(self.images_data):
                # Используем ID вместе с именем для уникальности
                display_name = info["original_name"]
                tile = TileItem(i, display_name, None)
                # Сохраняем ID в tile для идентификации
                tile.image_id = info.get("id", -1)
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
        try:
            if index < 0 or index >= len(self.images_data):
                return

            # Проверяем, что виджеты еще существуют
            if not hasattr(self, 'tileGrid') or not hasattr(self, 'tableWidget'):
                return

            px = QPixmap.fromImage(qimage)
            self.pixmaps_cache[index] = px

            # Обновляем плитку
            if hasattr(self.tileGrid, 'tile_items') and index < len(self.tileGrid.tile_items):
                tile_item = self.tileGrid.tile_items[index]
                tile_item.updatePixmap(px)

            # Обновляем таблицу
            if self.tableWidget.rowCount() > index:
                lbl = self.tableWidget.cellWidget(index, 1)
                if isinstance(lbl, QLabel):
                    scaled = px.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    lbl.setPixmap(scaled)
        except Exception as e:
            print(f"[onImageLoaded] Ошибка: {e}")

    @Slot()
    def onImageLoaderFinished(self):
        """Завершили загрузку миниатюр."""
        self.progress_bar.setVisible(False)

    @Slot(str)
    def onImageLoaderError(self, message):
        if not self.saving_now:  # Не показываем ошибки во время сохранения
            QMessageBox.warning(self, "Ошибка загрузки миниатюр", message)

    @Slot(dict)
    def _addNewFileThreadSafe(self, file_info: dict):
        """Добавление нового файла из воркера (в главном потоке)."""
        try:
            # Добавляем уникальный ID
            file_info["id"] = self.next_image_id
            self.next_image_id += 1

            self.images_data.append(file_info)
            self.modified = True
            self.refreshUI()
            self.loading_now = False
            # Сигнализируем об обновлении
            self.images_updated.emit(len(self.images_data) > 0)
        except Exception as e:
            print(f"[_addNewFileThreadSafe] Ошибка: {e}")
            self.loading_now = False

    def onExternalFilesDropped(self, file_paths):
        """DnD внешних файлов на плитки."""
        if self.loading_now or self.saving_now:
            print("[UploadWindow] Операция в процессе, игнорируем.")
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
        if self.saving_now:
            return

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
            # Используем ID для идентификации
            tile_id = getattr(tile_widget, 'image_id', -1)
            found = None
            for info in self.images_data:
                if info.get("id", -1) == tile_id:
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
        if self.saving_now:
            return

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
                            print(f"[deleteSelected] Удален файл: {full_path}")
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
                            print(f"[deleteSelected] Удален файл: {full_path}")
                        except Exception as e:
                            QMessageBox.warning(self, "Ошибка удаления", str(e))
                    del self.images_data[ridx]
            removed_any = True

        if removed_any:
            self.modified = True
            self.refreshUI()
            # Сигнализируем об обновлении
            self.images_updated.emit(len(self.images_data) > 0)

    def setUIEnabled(self, enabled):
        """Включает/выключает весь интерфейс во время сохранения"""
        self.btn_close.setEnabled(enabled)
        self.btn_select.setEnabled(enabled)
        self.btn_save.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)
        self.view_combo.setEnabled(enabled)
        self.sort_combo.setEnabled(enabled)
        self.tileGrid.setEnabled(enabled)
        self.tableWidget.setEnabled(enabled)

        # Изменяем курсор
        if enabled:
            self.setCursor(Qt.ArrowCursor)
        else:
            self.setCursor(Qt.WaitCursor)

    def saveChanges(self):
        """
        Сохраняет порядок изображений и переименовывает файлы с обработкой блокировок
        """
        if self.saving_now:
            return

        print("[UploadWindow] Сохраняем изменения.")
        self.saving_now = True

        # Блокируем интерфейс
        self.setUIEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Индикатор неопределенного прогресса

        try:
            # СНАЧАЛА получаем актуальный порядок из плиток (до очистки!)
            ordered_data = []
            for tile_widget in self.tileGrid.tile_items:
                # Используем ID для идентификации
                tile_id = getattr(tile_widget, 'image_id', -1)
                found = None
                for info in self.images_data:
                    if info.get("id", -1) == tile_id:
                        found = info
                        break
                if found:
                    ordered_data.append(found)

            # Останавливаем потоки
            self.worker.stop()
            self.imageLoader.stop()

            # Ждем завершения потоков
            if self.imageLoaderThread.isRunning():
                self.imageLoaderThread.quit()
                if not self.imageLoaderThread.wait(2000):  # Ждем максимум 2 секунды
                    print("[UploadWindow] Предупреждение: поток изображений не завершился вовремя")

            # Очищаем кэш изображений и виджеты
            self.clearImageCache()

            # Принудительная сборка мусора
            gc.collect()

            counter = 1
            new_data = []
            rename_errors = []

            # Используем микросекунды для уникальности временных имен
            import random
            base_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            random_base = random.randint(10000, 99999)

            # Пытаемся переименовать все файлы с использованием временных имен
            for idx, info in enumerate(ordered_data):
                old_name = info["current_name"]
                # Уникальное временное имя с индексом
                temp_name = f"temp_{base_timestamp}_{random_base}_{idx}_{counter}.png"
                final_name = f"{counter:04d}.png"
                old_path = os.path.join(self.upload_folder, old_name)
                temp_path = os.path.join(self.upload_folder, temp_name)
                final_path = os.path.join(self.upload_folder, final_name)

                # Сначала переименовываем в уникальное временное имя
                if os.path.exists(old_path):
                    success = self.safeRenameFile(old_path, temp_path)
                    if success:
                        # Сохраняем информацию для второго прохода
                        info["temp_name"] = temp_name
                        info["final_name"] = final_name
                        new_data.append(info)
                    else:
                        rename_errors.append(f"Не удалось переименовать {old_name}")
                else:
                    print(f"[saveChanges] Файл не существует: {old_path}")
                    rename_errors.append(f"Файл не существует: {old_name}")

                counter += 1

            # Второй проход: переименовываем из временных в финальные имена
            for info in new_data:
                temp_name = info.get("temp_name")
                final_name = info.get("final_name")

                if not temp_name or not final_name:
                    continue

                temp_path = os.path.join(self.upload_folder, temp_name)
                final_path = os.path.join(self.upload_folder, final_name)

                success = self.safeRenameFile(temp_path, final_path)
                if success:
                    # Обновляем информацию о файле
                    info["current_name"] = final_name
                    info["size"] = os.path.getsize(final_path)
                    info["updated_at"] = datetime.datetime.now().isoformat()

                    # Убираем временные поля
                    if "temp_name" in info:
                        del info["temp_name"]
                    if "final_name" in info:
                        del info["final_name"]
                else:
                    rename_errors.append(f"Не удалось переименовать {temp_name} в {final_name}")

            # Обновляем список изображений
            self.images_data = new_data

            # Сохраняем JSON
            try:
                with open(self.images_json, "w", encoding="utf-8") as f:
                    json.dump(self.images_data, f, ensure_ascii=False, indent=4)
                print(f"[UploadWindow] JSON сохранён: {self.images_json}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка сохранения JSON", str(e))
                return

            # Показываем ошибки, если были
            if rename_errors:
                error_msg = "\n".join(rename_errors[:5])
                if len(rename_errors) > 5:
                    error_msg += f"\n...и ещё {len(rename_errors) - 5} ошибок"
                QMessageBox.warning(self, "Предупреждение", f"Некоторые файлы не удалось переименовать:\n{error_msg}")

            self.modified = False
            # Сигнализируем об успешном сохранении
            self.images_updated.emit(len(self.images_data) > 0)
            self.accept()  # Используем accept() вместо close()

        except Exception as e:
            print(f"[saveChanges] Критическая ошибка: {e}")
            QMessageBox.critical(self, "Ошибка", f"Произошла ошибка при сохранении: {str(e)}")
        finally:
            self.saving_now = False
            self.setUIEnabled(True)
            self.progress_bar.setVisible(False)
            self.setCursor(Qt.ArrowCursor)

    def clearImageCache(self):
        """Очищает кэш изображений и освобождает память"""
        # Очищаем изображения из таблицы
        for i in range(self.tableWidget.rowCount()):
            widget = self.tableWidget.cellWidget(i, 1)
            if widget and isinstance(widget, QLabel):
                widget.clear()
                widget.setPixmap(QPixmap())  # Устанавливаем пустой pixmap

        # Очищаем кэш
        for pixmap in self.pixmaps_cache.values():
            if pixmap and not pixmap.isNull():
                del pixmap
        self.pixmaps_cache.clear()

    def reject(self):
        """Переопределяем reject для выполнения очистки перед закрытием"""
        if self.saving_now:
            return  # Не позволяем закрыть во время сохранения

        self.deleteObsoleteTempFiles()
        self.stopThreads()
        super().reject()

    def stopThreads(self):
        """Останавливает все потоки"""
        # Останавливаем воркеры
        if hasattr(self, 'worker'):
            self.worker.stop()
        if hasattr(self, 'imageLoader'):
            self.imageLoader.stop()

        # Останавливаем потоки
        if hasattr(self, 'worker_thread') and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait(1000)

        if hasattr(self, 'imageLoaderThread') and self.imageLoaderThread.isRunning():
            self.imageLoaderThread.quit()
            self.imageLoaderThread.wait(1000)

    def safeRenameFile(self, src_path, dst_path, max_attempts=3, wait_time=0.1):
        """
        Безопасное переименование файла с повторными попытками
        """
        import time

        for attempt in range(max_attempts):
            try:
                # Если целевой файл существует, удаляем его
                if os.path.exists(dst_path):
                    os.remove(dst_path)

                # Пытаемся переименовать
                os.rename(src_path, dst_path)
                return True

            except PermissionError as e:
                print(f"[safeRenameFile] Попытка {attempt + 1}/{max_attempts} не удалась: {e}")

                # Принудительно очищаем кэши и собираем мусор
                if hasattr(self, 'pixmaps_cache'):
                    self.pixmaps_cache.clear()

                gc.collect()
                time.sleep(wait_time)

            except Exception as e:
                print(f"[safeRenameFile] Неожиданная ошибка: {e}")
                return False

        return False

    def updateDeleteButtonVisibility(self):
        idx = self.stacked_widget.currentIndex()
        if idx == 0:
            sel = self.tileGrid.getSelectedTiles()
            self.btn_delete.setVisible(len(sel) > 0)
        else:
            rows = self.tableWidget.selectionModel().selectedRows()
            self.btn_delete.setVisible(len(rows) > 0)

    def onCloseClicked(self):
        if self.saving_now:
            return  # Не позволяем закрыть во время сохранения

        self.deleteObsoleteTempFiles()
        self.reject()

    def closeEvent(self, event):
        if self.saving_now:
            event.ignore()  # Не позволяем закрыть во время сохранения
            return

        if self.modified:
            resp = QMessageBox.question(
                self, "Несохранённые изменения",
                "Сохранить изменения перед выходом?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if resp == QMessageBox.Yes:
                self.saveChanges()
                event.ignore()  # saveChanges вызовет accept()
            elif resp == QMessageBox.No:
                self.deleteTempFiles()
                event.ignore()
                self.reject()
            else:
                event.ignore()
        else:
            event.ignore()
            self.reject()

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