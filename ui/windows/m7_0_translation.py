# -*- coding: utf-8 -*-
# ui/windows/m7_0_translation.py

import os
import sys
import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import (Qt, Signal, QThreadPool, QTimer, QPointF, QMetaObject, Q_ARG, QSizeF, QRectF)
from PySide6.QtGui import (QPixmap, QFont, QColor, QBrush, QCursor)
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSpacerItem, QSizePolicy, QRadioButton,
                               QButtonGroup, QSlider, QLineEdit, QMessageBox, QComboBox,
                               QCheckBox, QWidget, QGroupBox, QApplication, QToolButton,
                               QColorDialog, QSpinBox, QProgressBar, QFileDialog)
from PySide6.QtCore import QTimer
# Импортируем наши модули
from ui.components.gradient_widget import GradientBackgroundWidget
from ui.windows.m7_1_image_viewer import (ImageViewer, NotesModifiedSignal,
                                          NoteItem, MovableRectItem, AnchorPointItem)
from ui.windows.m7_2_utils import (get_images_from_folder, show_message, PageChangeSignal)
from ui.windows.m7_3_ui_components import (ImageLoader, LoadingOverlay,
                                           ImageLoadedEvent, AllImagesLoadedEvent)
import datetime
import zipfile
from PySide6.QtWidgets import QProgressDialog

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class TranslationWindow(QDialog):
    """
    Окно "Перевод".
    Основная функциональность:
    - Загрузка и отображение изображений из предобработки или загрузки
    - Создание и редактирование заметок перевода
    - Сохранение состояния и переводов
    """
    back_requested = Signal()

    def __init__(self, chapter_folder, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("translation_window")

        # Сохраняем пути
        self.ch_folder = chapter_folder
        self.paths = paths or {}
        self.status_json_filename = "translation.json"

        # Определяем базовые папки
        self.ch_paths = {
            "translation": os.path.join(chapter_folder, "Перевод"),
            "preproc": os.path.join(chapter_folder, "Предобработка"),
            "upload": os.path.join(chapter_folder, "Загрузка"),
            "enhanced_folder": os.path.join(chapter_folder, "Предобработка"),
            "upload_folder": os.path.join(chapter_folder, "Загрузка"),
        }

        # Создаем папки
        for folder in self.ch_paths.values():
            os.makedirs(folder, exist_ok=True)

        # Инициализация окна
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Перевод")
        self.resize(1920, 1080)

        # Флаги состояния
        self.is_proc = False
        self.curr_op = None
        self.is_loading_complete = False

        # Определяем источник изображений с диалогом выбора
        self.image_paths = self._decide_img_source()
        if not self.image_paths:
            # Если пользователь отменил выбор, планируем закрытие
            QTimer.singleShot(100, self._handleCancelledInit)
            return

        # Создаем градиентный фон
        self.bg_widget = GradientBackgroundWidget(self)
        self.bg_widget.setObjectName("bg_widget")

        # Основной макет
        main_layout = QVBoxLayout(self.bg_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Сигнал изменения страницы
        self.page_change_signal = PageChangeSignal()

        # Создаем интерфейс
        self.initTopBar(main_layout)
        self.initContent(main_layout)

        # Основной лейаут диалога
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.bg_widget)

        # Два пула потоков для разных типов задач
        max_workers = min(4, max(os.cpu_count() - 1, 1))
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)

        # Загрузчик изображений
        self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
        self.image_loader.image_loaded.connect(self.onImageLoaded)
        self.image_loader.loading_progress.connect(self.updateLoadingProgress)
        self.image_loader.loading_complete.connect(self.onLoadingComplete)
        self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

        # Подключаем сигналы
        self.page_change_signal.page_changed.connect(self.updateActiveThumbnail)

        # Инициализируем настройки заметок по умолчанию
        self.viewer.note_settings.update({
            "note_bg_color": "#FFFFE0",
            "note_border_color": "#000000",
            "note_dotted_color": "#808080",
            "note_point_color": "#FF0000",
            "note_text_color": "#000000",
            "font_size": 12,
            "ocr_language": "Русский",
            "translation_language": "Английский",
            "point_radius": 5
        })

        # Загружаем статус
        self.loadStatus()

        # Подсвечиваем первую картинку
        self.updateActiveThumbnail(0)

        # Показываем загрузочный экран и начинаем загрузку изображений
        QTimer.singleShot(100, self.startLoading)

    def _decide_img_source(self):
        """Определение источника изображений с диалогом выбора"""
        translation_folder = os.path.join(self.ch_paths["translation"], "Images")
        os.makedirs(translation_folder, exist_ok=True)

        # Проверяем наличие сохраненной конфигурации
        config_path = os.path.join(translation_folder, "image_source_config.json")

        # Если в папке перевода уже есть изображения - используем их
        existing_images = get_images_from_folder(translation_folder)
        if existing_images:
            logger.info(f"Найдено {len(existing_images)} изображений в папке Перевод")
            return existing_images

        # Проверяем доступные источники
        sources = {}

        # Предобработка/Save
        save_folder = os.path.join(self.ch_paths["enhanced_folder"], "Save")
        if os.path.exists(save_folder):
            save_images = get_images_from_folder(save_folder)
            if save_images:
                sources["preprocess_save"] = {
                    "path": save_folder,
                    "images": save_images,
                    "name": "Предобработка (Save)",
                    "count": len(save_images)
                }

        # Загрузка
        upload_images = get_images_from_folder(self.ch_paths["upload_folder"])
        if upload_images:
            sources["upload"] = {
                "path": self.ch_paths["upload_folder"],
                "images": upload_images,
                "name": "Загрузка",
                "count": len(upload_images)
            }

        if not sources:
            show_message(self, "Ошибка", "Не найдены изображения для обработки")
            return []

        # Если есть сохраненная конфигурация
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    saved_source = config.get("source")
                    if saved_source in sources:
                        # Копируем изображения в папку перевода
                        self._copy_images_to_translation(sources[saved_source]["images"])
                        return get_images_from_folder(translation_folder)
            except:
                pass

        # Показываем диалог выбора источника
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник изображений для перевода:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()
        selected_source = None

        for key, source_info in sources.items():
            radio = QRadioButton(f"{source_info['name']} ({source_info['count']} изображений)")
            radio.setStyleSheet("margin: 10px 0;")
            button_group.addButton(radio)
            radio.toggled.connect(lambda checked, k=key: setattr(dialog, 'selected_source', k if checked else None))
            layout.addWidget(radio)

            # Выбираем первый по умолчанию
            if selected_source is None:
                radio.setChecked(True)
                dialog.selected_source = key

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        ok_btn.clicked.connect(dialog.accept)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #777;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if dialog.exec() == QDialog.Accepted and hasattr(dialog, 'selected_source'):
            selected = dialog.selected_source

            # Сохраняем выбор
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({"source": selected}, f, ensure_ascii=False, indent=4)

            # Копируем изображения в папку перевода
            self._copy_images_to_translation(sources[selected]["images"])

            return get_images_from_folder(translation_folder)

        # Если пользователь отменил выбор
        return []

    def _copy_images_to_translation(self, source_images):
        """Копирует изображения в папку перевода с сохранением имен"""
        translation_folder = os.path.join(self.ch_paths["translation"], "Images")
        os.makedirs(translation_folder, exist_ok=True)

        for i, src_path in enumerate(source_images):
            try:
                # Сохраняем оригинальное имя файла
                filename = os.path.basename(src_path)
                dst_path = os.path.join(translation_folder, filename)

                # Если файл уже существует, добавляем номер
                if os.path.exists(dst_path):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(dst_path):
                        dst_path = os.path.join(translation_folder, f"{base}_{counter}{ext}")
                        counter += 1

                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован файл: {filename}")

            except Exception as e:
                logger.error(f"Ошибка копирования {src_path}: {str(e)}")
    def ensureTranslationImages(self):
        """Копирует изображения из предобработки или загрузки в папку перевода"""
        translation_img_dir = os.path.join(self.ch_paths["translation"], "Images")
        os.makedirs(translation_img_dir, exist_ok=True)

        # Если в папке перевода уже есть изображения, не копируем
        existing_images = get_images_from_folder(translation_img_dir)
        if existing_images:
            logger.info(f"В папке перевода уже есть {len(existing_images)} изображений")
            return

        # Приоритет: 1) Предобработка, 2) Предобработка/Enhanced, 3) Загрузка/originals, 4) Загрузка
        source_folders = [
            os.path.join(self.ch_paths["preproc"]),
            os.path.join(self.ch_paths["preproc"], "Enhanced"),
            os.path.join(self.ch_paths["upload"], "originals"),
            os.path.join(self.ch_paths["upload"])
        ]

        copied = False
        for folder in source_folders:
            if os.path.isdir(folder):
                images = get_images_from_folder(folder)
                if images:
                    logger.info(f"Копирую {len(images)} изображений из {folder}")
                    for img_path in images:
                        img_name = os.path.basename(img_path)
                        dest_path = os.path.join(translation_img_dir, img_name)
                        shutil.copy2(img_path, dest_path)
                    copied = True
                    break

        if not copied:
            logger.warning("Не найдены изображения для копирования в папку перевода")

    def _getTranslationImages(self):
        """Получает список изображений из папки перевода"""
        translation_img_dir = os.path.join(self.ch_paths["translation"], "Images")
        if os.path.isdir(translation_img_dir):
            return get_images_from_folder(translation_img_dir)
        return []

    def refreshImages(self):
        """Обновляет изображения из источников с диалогом выбора"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Обновление изображений")

        # Определяем папку с текущими изображениями
        translation_img_dir = os.path.join(self.ch_paths["translation"], "Images")

        # Проверяем доступные источники
        sources = {}

        # Предобработка/Save
        save_folder = os.path.join(self.ch_paths["enhanced_folder"], "Save")
        if os.path.exists(save_folder):
            save_images = get_images_from_folder(save_folder)
            if save_images:
                sources["preprocess_save"] = {
                    "images": save_images,
                    "name": "Предобработка (Улучшенные изображения)"
                }

        # Загрузка
        upload_images = get_images_from_folder(self.ch_paths["upload_folder"])
        if upload_images:
            sources["upload"] = {
                "images": upload_images,
                "name": "Загрузка"
            }

        if not sources:
            show_message(self, "Предупреждение", "Не найдены оригинальные изображения")
            self.unlockInterface()
            return

        # Показываем диалог выбора
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника для обновления")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник для обновления изображений:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()

        for key, source_info in sources.items():
            radio = QRadioButton(f"{source_info['name']} ({len(source_info['images'])} изображений)")
            radio.setStyleSheet("margin: 10px 0;")
            button_group.addButton(radio)
            radio.toggled.connect(lambda checked, k=key: setattr(dialog, 'selected_source', k if checked else None))
            layout.addWidget(radio)

            # Выбираем первый по умолчанию
            if not hasattr(dialog, 'selected_source'):
                radio.setChecked(True)
                dialog.selected_source = key

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        ok_btn.clicked.connect(dialog.accept)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #777;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if dialog.exec() != QDialog.Accepted or not hasattr(dialog, 'selected_source'):
            self.unlockInterface()
            return

        # Получаем выбранные изображения
        source_images = sources[dialog.selected_source]["images"]

        # Сохраняем текущую страницу
        current_page_backup = self.viewer.current_page

        # Определение страниц для обработки
        if self.update_all_cb.isChecked():
            # Очищаем текущую папку с изображениями
            for file in os.listdir(translation_img_dir):
                file_path = os.path.join(translation_img_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)

            # Копируем новые изображения
            self._copy_images_to_translation(source_images)

            # Обновляем список изображений
            self.image_paths = get_images_from_folder(translation_img_dir)

            # Обновляем viewer
            self.viewer.pages = self.image_paths
            self.viewer.pixmaps = [QPixmap() for _ in self.image_paths]

            # Восстанавливаем страницу если возможно
            if current_page_backup < len(self.image_paths):
                self.viewer.current_page = current_page_backup
            else:
                self.viewer.current_page = 0

            # Обновляем загрузчик
            self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
            self.image_loader.image_loaded.connect(self.onImageLoaded)
            self.image_loader.loading_progress.connect(self.updateLoadingProgress)
            self.image_loader.loading_complete.connect(self.onLoadingComplete)
            self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

            # Запускаем полную загрузку
            self.startLoading()
        else:
            # Обновляем только текущее изображение
            current_page = self.viewer.current_page
            if current_page < 0 or current_page >= len(self.image_paths):
                show_message(self, "Предупреждение", "Текущее изображение недоступно для обновления")
                self.unlockInterface()
                return

            # Определяем имя текущего файла
            current_filename = os.path.basename(self.image_paths[current_page])
            base_name = os.path.splitext(current_filename)[0]

            # Ищем файл с похожим именем в источнике
            source_path = None
            for src_path in source_images:
                src_filename = os.path.basename(src_path)
                src_base = os.path.splitext(src_filename)[0]
                if src_base == base_name:
                    source_path = src_path
                    break

            if source_path and os.path.exists(source_path):
                # Копируем файл
                dst_path = os.path.join(translation_img_dir, current_filename)
                shutil.copy2(source_path, dst_path)

                # Обновляем путь
                self.image_paths[current_page] = dst_path

                # Обновляем изображение
                pm = QPixmap(dst_path)
                if not pm.isNull():
                    self.viewer.pixmaps[current_page] = pm
                    self.viewer.displayCurrentPage()

                    # Обновляем миниатюру
                    if 0 <= current_page < len(self.thumbnail_labels):
                        tw = 150
                        th = tw * 2
                        scaled_pix = pm.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.thumbnail_labels[current_page].setPixmap(scaled_pix)

            self.unlockInterface()

    def copyCurrentImage(self):
        """Копирует текущее изображение в буфер обмена"""
        if not hasattr(self.viewer, "pixmaps") or not self.viewer.pixmaps:
            show_message(self, "Предупреждение", "Нет доступных изображений для копирования")
            return

        current_page = self.viewer.current_page
        if current_page < 0 or current_page >= len(self.viewer.pixmaps):
            show_message(self, "Предупреждение", "Выбранная страница недоступна")
            return

        pixmap = self.viewer.pixmaps[current_page]
        if pixmap.isNull():
            show_message(self, "Предупреждение", "Изображение недоступно")
            return

        # Копируем в буфер обмена
        clipboard = QApplication.clipboard()
        clipboard.setPixmap(pixmap)
        show_message(self, "Информация", "Изображение скопировано в буфер обмена")

    def startLoading(self):
        """Начало загрузки изображений с блокировкой интерфейса"""
        # Показываем окно загрузки
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.loading_overlay.setStyleSheet("""
            QWidget#loading_overlay {
                background-color: #1E1E2A;
                border-radius: 35px;
                border: 2px solid #7E1E9F;
            }
        """)

        # Обновляем информацию о загрузке
        total_images = len(self.image_paths)
        self.loading_overlay.updateProgress(0, total_images)
        self.loading_overlay.info_label.setText(f"Загрузка изображений ({total_images} шт.)")

        # Центрируем окно загрузки
        self.loading_overlay.move(
            self.width() // 2 - self.loading_overlay.width() // 2,
            self.height() // 2 - self.loading_overlay.height() // 2
        )

        # Принудительно поднимаем окно над всеми виджетами
        self.loading_overlay.show()
        self.loading_overlay.raise_()

        # Форсируем обновление интерфейса
        QApplication.processEvents()

        # Подключаем кнопку отмены
        self.loading_overlay.cancel_button.clicked.connect(self.cancelLoading)

        # Запускаем загрузку изображений
        QTimer.singleShot(200, lambda: self.image_loader.start_loading(0))

    def cancelLoading(self):
        """Отмена загрузки изображений"""
        self.image_loader.cancel()

        # Обновляем интерфейс
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.info_label.setText("Отмена загрузки...")
            self.loading_overlay.cancel_button.setEnabled(False)

        # Посылаем сигнал возврата через 500мс
        QTimer.singleShot(500, self.back_requested.emit)

    def onImageLoaded(self, idx, pixmap, current_file):
        """Обработка загрузки изображения"""
        if self.viewer:
            self.viewer.pixmaps[idx] = pixmap

            if self.viewer.current_page == idx:
                self.viewer.displayCurrentPage()

            if self.preview_scroll_area and 0 <= idx < len(self.thumbnail_labels):
                tw = 150
                th = tw * 2
                scaled_pix = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumbnail_labels[idx].setPixmap(scaled_pix)

        QApplication.postEvent(self, ImageLoadedEvent(idx))

    def updateLoadingProgress(self, loaded, total, current_file):
        """Обновление прогресса загрузки"""
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.updateProgress(loaded, total, current_file)

    def onLoadingComplete(self):
        """Обработка завершения загрузки"""
        self.is_loading_complete = True

        # Обновляем интерфейс
        self.viewer.displayCurrentPage()

        # Скрываем окно загрузки
        QTimer.singleShot(500, self._finishLoadingCleanup)

    def _finishLoadingCleanup(self):
        """Завершающая очистка после загрузки"""
        # Скрываем окно загрузки
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.hide()
            self.loading_overlay.deleteLater()
            self.loading_overlay = None

        # Разблокируем интерфейс
        self.unlockInterface()

    def onLoadingCancelled(self):
        """Обработка отмены загрузки"""
        # Скрываем окно загрузки
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.hide()
            self.loading_overlay.deleteLater()
            self.loading_overlay = None

        # Закрываем окно
        self.close()

    def onCloseClicked(self):
        """Обработчик нажатия кнопки 'Назад'"""
        # Сигнализируем о запросе возврата и закрываем окно
        self.back_requested.emit()
        self.close()

    def initTopBar(self, main_layout):
        """Инициализация верхней панели с названием и кнопкой "Назад"."""
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 10, 20, 10)
        top_bar.setSpacing(10)

        # Заголовок
        title_label = QLabel("MangaLocalizer")
        title_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        top_bar.addWidget(title_label, 0, Qt.AlignVCenter | Qt.AlignLeft)

        top_bar.addStretch(1)

        # Кнопка "Назад"
        close_btn = QPushButton("Назад")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #6E6E9F;
            }
        """)
        close_btn.clicked.connect(self.onCloseClicked)
        top_bar.addWidget(close_btn, 0, Qt.AlignRight)

        main_layout.addLayout(top_bar)

    def initContent(self, main_layout):
        """Инициализация основного содержимого окна"""
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(20, 0, 20, 20)
        content_layout.setSpacing(20)

        # Левая панель с миниатюрами
        self.preview_scroll_area = self.createPreviewPanel()
        content_layout.addWidget(self.preview_scroll_area, stretch=1)

        # Центральная панель с просмотрщиком
        viewer_layout = QVBoxLayout()
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        viewer_layout.setSpacing(5)

        # Создаем просмотрщик
        self.viewer = ImageViewer(self.image_paths, parent=self)
        viewer_layout.addWidget(self.viewer, stretch=1)

        viewer_widget = QWidget()
        viewer_widget.setLayout(viewer_layout)
        content_layout.addWidget(viewer_widget, stretch=4)

        # Правая панель с настройками
        right_widget = self.createRightPanel()
        content_layout.addWidget(right_widget, stretch=0)

        main_layout.addLayout(content_layout, stretch=1)

    def createPreviewPanel(self):
        """Создает панель миниатюр для просмотра"""
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMaximumWidth(250)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #333333;
                border-top-left-radius: 0px;
                border-top-right-radius: 0px;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(5, 5, 5, 0)
        container_layout.setSpacing(5)

        self.thumbnail_labels = []
        self.index_labels = []

        thumbnail_width = 150
        thumbnail_height = thumbnail_width * 2

        for i, path in enumerate(self.image_paths):
            thumb_container = QWidget()
            thumb_layout = QVBoxLayout(thumb_container)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setSpacing(0)

            thumb_label = QLabel()
            pm = QPixmap(path)
            if not pm.isNull():
                scaled_pix = pm.scaled(
                    thumbnail_width, thumbnail_height,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                thumb_label.setPixmap(scaled_pix)
                thumb_label.setAlignment(Qt.AlignCenter)

            thumb_label.setStyleSheet("""
                QLabel {
                    background-color: #222222;
                    border: 2px solid #444444;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                }
            """)

            index_label = QLabel(str(i + 1))
            index_label.setAlignment(Qt.AlignCenter)
            index_label.setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #222222;
                    font-size: 14px;
                    font-weight: bold;
                    border: 2px solid #444444;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                }
            """)

            thumb_layout.addWidget(thumb_label)
            thumb_layout.addWidget(index_label)

            thumb_label.mousePressEvent = self.makePreviewClickHandler(i)

            self.thumbnail_labels.append(thumb_label)
            self.index_labels.append(index_label)

            container_layout.addWidget(thumb_container)

        container_layout.addStretch(1)
        scroll_area.setWidget(container)

        return scroll_area

    def makePreviewClickHandler(self, index):
        """Создает обработчик клика по миниатюре"""

        def handleMousePress(event):
            if event.button() == Qt.LeftButton:
                self.viewer.current_page = index
                self.viewer.displayCurrentPage()
                self.updateActiveThumbnail(index)
            event.accept()

        return handleMousePress

    def updateActiveThumbnail(self, active_index):
        """Обновляет подсветку активной миниатюры"""
        for i, (thumb_label, index_label) in enumerate(zip(self.thumbnail_labels, self.index_labels)):
            if i == active_index:
                # Активная миниатюра
                thumb_label.setStyleSheet("""
                    QLabel {
                        border: 2px solid #7E1E9F;
                        border-top-left-radius: 8px;
                        border-top-right-radius: 8px;
                        border-bottom-left-radius: 0px;
                        border-bottom-right-radius: 0px;
                    }
                """)
                index_label.setStyleSheet("""
                    QLabel {
                        color: white;
                        font-size: 14px;
                        font-weight: bold;
                        border: 2px solid #7E1E9F;
                        border-bottom-left-radius: 8px;
                        border-bottom-right-radius: 8px;
                    }
                """)
            else:
                # Неактивная миниатюра
                thumb_label.setStyleSheet("""
                    QLabel {
                        background-color: #222222;
                        border: 2px solid #444444;
                        border-top-left-radius: 8px;
                        border-top-right-radius: 8px;
                        border-bottom-left-radius: 0px;
                        border-bottom-right-radius: 0px;
                    }
                """)
                index_label.setStyleSheet("""
                    QLabel {
                        color: white;
                        background-color: #222222;
                        font-size: 14px;
                        font-weight: bold;
                        border: 2px solid #444444;
                        border-top-left-radius: 0px;
                        border-top-right-radius: 0px;
                        border-bottom-left-radius: 8px;
                        border-bottom-right-radius: 8px;
                    }
                """)

    def createRightPanel(self):
        """Создает правую панель с настройками"""
        right_widget = QWidget()
        right_widget.setStyleSheet("""
            QWidget {
                background: #5E0E7F;
                border-top-left-radius: 15px;
                border-bottom-left-radius: 15px;
            }
        """)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(10)

        # Группа обновления изображений
        update_group = QGroupBox("Обновление изображений")
        update_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #7E1E9F;
                border-radius: 10px;
                margin-top: 10px;
                color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        update_layout = QVBoxLayout()
        update_layout.setSpacing(5)

        # Чекбокс "Обновить все"
        self.update_all_cb = QCheckBox("Обновить все изображения")
        self.update_all_cb.setChecked(True)
        self.update_all_cb.setStyleSheet("color: white;")
        update_layout.addWidget(self.update_all_cb)

        # Кнопка обновления изображений
        self.refresh_btn = QPushButton("Обновить изображения")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #6E6E9F;
            }
        """)
        self.refresh_btn.clicked.connect(self.refreshImages)
        update_layout.addWidget(self.refresh_btn)

        # Кнопка копирования текущего изображения
        self.copy_current_image_btn = QPushButton("Копировать текущее изображение")
        self.copy_current_image_btn.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #6E6E9F;
            }
        """)
        self.copy_current_image_btn.clicked.connect(self.copyCurrentImage)
        update_layout.addWidget(self.copy_current_image_btn)

        update_group.setLayout(update_layout)
        right_layout.addWidget(update_group)

        # Панель настроек заметок
        self.initNoteSettingsPanel(right_layout)

        # Группа статуса обработки
        status_group = QGroupBox("Статус обработки")
        status_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #7E1E9F;
                border-radius: 10px;
                margin-top: 10px;
                color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        status_layout = QHBoxLayout()

        self.status_group = QButtonGroup(self)
        self.status_not = QRadioButton("Не начат")
        self.status_in_prog = QRadioButton("В работе")
        self.status_done = QRadioButton("Завершен")

        for btn in (self.status_not, self.status_in_prog, self.status_done):
            btn.setStyleSheet("color: white;")
            self.status_group.addButton(btn)
            status_layout.addWidget(btn)

        self.status_not.setChecked(True)
        self.status_group.buttonClicked.connect(self.onStatusChanged)
        status_group.setLayout(status_layout)
        right_layout.addWidget(status_group)

        # Группа выгрузки переводов
        export_group = QGroupBox("Экспорт переводов")
        export_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #7E1E9F;
                border-radius: 10px;
                margin-top: 10px;
                color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        export_layout = QVBoxLayout()

        self.export_btn = QPushButton("Экспортировать переводы")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #6E6E9F;
            }
        """)
        self.export_btn.clicked.connect(self.exportTranslations)
        export_layout.addWidget(self.export_btn)

        self.import_btn = QPushButton("Импортировать переводы")
        self.import_btn.setStyleSheet("""
            QPushButton {
                background-color: #4E4E6F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #6E6E9F;
            }
        """)
        self.import_btn.clicked.connect(self.importTranslations)
        export_layout.addWidget(self.import_btn)

        export_group.setLayout(export_layout)
        right_layout.addWidget(export_group)

        # Пространство для растяжения
        right_layout.addStretch(1)

        # Навигационные кнопки
        nav_buttons_layout = QHBoxLayout()
        nav_buttons_layout.setSpacing(10)

        self.prev_page_btn = QPushButton("Предыдущая")
        self.prev_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        self.prev_page_btn.clicked.connect(self.onPreviousPage)
        nav_buttons_layout.addWidget(self.prev_page_btn)

        self.next_page_btn = QPushButton("Следующая")
        self.next_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        self.next_page_btn.clicked.connect(self.onNextPage)
        nav_buttons_layout.addWidget(self.next_page_btn)

        right_layout.addLayout(nav_buttons_layout)

        return right_widget

    def initNoteSettingsPanel(self, parent_layout):
        """Инициализация панели настроек заметок"""
        note_group = QGroupBox("Настройки заметок")
        note_group.setStyleSheet("""
            QGroupBox {
                border: 2px solid #7E1E9F;
                border-radius: 10px;
                margin-top: 10px;
                color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        note_layout = QVBoxLayout()

        # Режим заметок
        mode_label = QLabel("Режим заметок:")
        mode_label.setStyleSheet("color: white;")
        note_layout.addWidget(mode_label)

        mode_layout = QHBoxLayout()
        self.note_mode_group = QButtonGroup(self)

        self.std_note_rb = QRadioButton("Стандартная")
        self.rect_note_rb = QRadioButton("Прямоугольная")
        self.simple_note_rb = QRadioButton("Простая")
        self.off_note_rb = QRadioButton("Выключено")

        self.std_note_rb.setChecked(True)

        for rb in (self.std_note_rb, self.rect_note_rb, self.simple_note_rb, self.off_note_rb):
            rb.setStyleSheet("color: white;")
            self.note_mode_group.addButton(rb)
            mode_layout.addWidget(rb)

        self.note_mode_group.buttonToggled.connect(
            lambda btn, checked: self.changeNoteMode(btn.text()) if checked else None
        )

        note_layout.addLayout(mode_layout)

        # Настройки цветов заметки
        self.note_color_settings = {}

        color_settings = [
            ("Фон", "note_bg_color", "#FFFFE0"),
            ("Рамка", "note_border_color", "#000000"),
            ("Пунктир", "note_dotted_color", "#808080"),
            ("Точка", "note_point_color", "#FF0000"),
            ("Текст", "note_text_color", "#000000")
        ]

        for label_text, key, default in color_settings:
            color_layout = QHBoxLayout()

            label = QLabel(label_text + ":")
            label.setStyleSheet("color: white;")
            color_layout.addWidget(label)

            line_edit = QLineEdit(default)
            color_layout.addWidget(line_edit)

            color_button = QToolButton()
            color_button.setStyleSheet(f"background-color: {default};")
            color_button.setFixedSize(20, 20)
            color_layout.addWidget(color_button)

            color_button.clicked.connect(lambda _, le=line_edit, btn=color_button: self.pickColor(le, btn))

            self.note_color_settings[key] = line_edit
            note_layout.addLayout(color_layout)

        # Размер шрифта заметки
        font_layout = QHBoxLayout()

        font_label = QLabel("Размер шрифта:")
        font_label.setStyleSheet("color: white;")
        font_layout.addWidget(font_label)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(12)
        self.font_size_spin.setMinimumWidth(60)
        self.font_size_spin.setButtonSymbols(QSpinBox.PlusMinus)
        self.font_size_spin.setStyleSheet("""
            QSpinBox {
                color: white;
                background-color: #4E4E6F;
                border-radius: 4px;
                padding: 2px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px;
                border-radius: 4px;
                background-color: #7E1E9F;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #9E3EBF;
            }
        """)
        font_layout.addWidget(self.font_size_spin)

        note_layout.addLayout(font_layout)

        # Выбор языка OCR
        ocr_lang_layout = QHBoxLayout()

        ocr_lang_label = QLabel("Язык OCR:")
        ocr_lang_label.setStyleSheet("color: white;")
        ocr_lang_layout.addWidget(ocr_lang_label)

        self.ocr_lang_combo = QComboBox()
        self.ocr_lang_combo.addItems(["Русский", "Английский", "Японский", "Китайский", "Корейский"])
        self.ocr_lang_combo.setCurrentText(self.viewer.note_settings.get("ocr_language", "Русский"))
        self.ocr_lang_combo.setStyleSheet("""
            QComboBox {
                color: white;
                background-color: #4E4E6F;
                border-radius: 4px;
                padding: 2px;
                min-height: 20px;
                min-width: 120px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox::down-arrow {
                width: 14px;
                height: 14px;
            }
            QComboBox QAbstractItemView {
                background-color: #4E4E6F;
                color: white;
                selection-background-color: #7E1E9F;
                selection-color: white;
                border: 1px solid #7E1E9F;
            }
        """)
        self.ocr_lang_combo.currentTextChanged.connect(lambda text: self.updateOcrLanguage(text))
        ocr_lang_layout.addWidget(self.ocr_lang_combo)

        note_layout.addLayout(ocr_lang_layout)

        # Сигналы при изменении настроек
        self.font_size_spin.valueChanged.connect(self.applyGlobalNoteSettings)

        for key, le in self.note_color_settings.items():
            le.textChanged.connect(self.applyGlobalNoteSettings)

        note_group.setLayout(note_layout)
        parent_layout.addWidget(note_group)

    def pickColor(self, line_edit, btn):
        """Выбор цвета через диалоговое окно"""
        current = line_edit.text()
        col = QColorDialog.getColor(QColor(current), self)
        if col.isValid():
            line_edit.setText(col.name())
            btn.setStyleSheet("background-color:" + col.name() + ";")
            self.applyGlobalNoteSettings()

    def updateOcrLanguage(self, language):
        """Обновление языка OCR в настройках"""
        self.viewer.note_settings["ocr_language"] = language
        self.saveStatus()

    def applyGlobalNoteSettings(self):
        """Применение глобальных настроек заметок"""
        font_size = max(6, abs(self.font_size_spin.value()))  # Гарантируем положительное значение

        settings = {
            "note_bg_color": self.note_color_settings["note_bg_color"].text(),
            "note_border_color": self.note_color_settings["note_border_color"].text(),
            "note_dotted_color": self.note_color_settings["note_dotted_color"].text(),
            "note_point_color": self.note_color_settings["note_point_color"].text(),
            "note_text_color": self.note_color_settings["note_text_color"].text(),
            "ocr_language": self.ocr_lang_combo.currentText(),
            "translation_language": "Английский",
            "font_size": font_size,
            "point_radius": self.viewer.note_settings.get("point_radius", 5)
        }

        self.viewer.note_settings.update(settings)

        # Обновляем стили у выделенных заметок
        for note in self.viewer.notes:
            if note.isSelected():
                note.updateStyles(settings)

        self.saveStatus()

    def onPreviousPage(self):
        """Обработчик кнопки 'Предыдущая'"""
        self.viewer.previousPage()
        self.updateActiveThumbnail(self.viewer.current_page)

        if self.viewer.current_page == 0:
            self.prev_page_btn.setEnabled(False)

        self.next_page_btn.setEnabled(True)

    def onNextPage(self):
        """Обработчик кнопки 'Следующая'"""
        self.viewer.nextPage()
        self.updateActiveThumbnail(self.viewer.current_page)

        if self.viewer.current_page == len(self.viewer.pages) - 1:
            self.next_page_btn.setEnabled(False)

        self.prev_page_btn.setEnabled(True)

    def onStatusChanged(self):
        """Обработчик изменения статуса обработки"""
        btn = self.status_group.checkedButton()
        if not btn:
            return

        status = btn.text()
        c_folder = self.ch_paths["translation"]

        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        data = {}

        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

        data["status"] = status

        # Обновляем chapter.json
        ch_json_path = os.path.join(self.ch_folder, "chapter.json")
        if os.path.exists(ch_json_path):
            try:
                with open(ch_json_path, 'r', encoding='utf-8') as f:
                    ch_data = json.load(f)

                if "stages" in ch_data:
                    if status == "Не начат":
                        ch_data["stages"]["Перевод"] = False
                    elif status == "В работе":
                        ch_data["stages"]["Перевод"] = "partial"
                    elif status == "Завершен":
                        ch_data["stages"]["Перевод"] = True

                with open(ch_json_path, 'w', encoding='utf-8') as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"Ошибка при обновлении chapter.json: {e}")

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def changeNoteMode(self, mode):
        """Изменение режима заметок"""
        self.viewer.note_mode = mode
        logger.info(f"Изменен режим заметок на: {mode}")

        # Обновляем курсор в зависимости от режима
        if mode == "Выключено":
            self.viewer.setCursor(Qt.ArrowCursor)
        else:
            self.viewer.setCursor(Qt.CrossCursor)

    def _handleCancelledInit(self):
        """Обработчик отмены инициализации"""
        self.back_requested.emit()
        self.close()
    def saveStatus(self):
        """Сохранение статуса обработки и разметки в реальном времени"""
        # Проверяем, что viewer существует
        if not hasattr(self, 'viewer'):
            return

        c_folder = self.ch_paths["translation"]
        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        data = {"note_settings": self.viewer.note_settings}

        # Сохраняем информацию о заметках
        notes_data = []
        for note in self.viewer.notes:
            note_data = {
                "page_index": note.page_index,
                "mode": note.mode,
                "position": {"x": note.pos().x(), "y": note.pos().y()},
                "size": {"width": note._width, "height": note._height},
                "text": note.text_edit.toHtml(),
                "is_visible": note.is_visible
            }

            # Если есть привязка (точка), сохраняем её
            # Если есть привязка (точка), сохраняем её
            if "p1" in note.extra:
                anchor = note.extra["p1"]
                if isinstance(anchor, QPointF):
                    note_data["anchor"] = {"x": anchor.x(), "y": anchor.y()}
                else:
                    try:
                        # Для AnchorPointItem нужно сохранять центр, а не позицию
                        if isinstance(anchor, AnchorPointItem):
                            # Получаем центр эллипса
                            center = anchor.mapToScene(anchor.boundingRect().center())
                            note_data["anchor"] = {"x": center.x(), "y": center.y()}
                            note_data["anchor_visible"] = getattr(anchor, "is_visible", True)
                        else:
                            # Для MovableRectItem сохраняем позицию как есть
                            anchor_pos = anchor.pos()
                            note_data["anchor"] = {"x": anchor_pos.x(), "y": anchor_pos.y()}
                            note_data["anchor_visible"] = getattr(anchor, "is_visible", True)

                            # Для прямоугольных заметок сохраняем размер прямоугольника
                            if hasattr(anchor, "rect"):
                                r = anchor.rect()
                                note_data["rect_size"] = {"width": r.width(), "height": r.height()}
                    except:
                        pass

                # Для OCR-заметок сохраняем результат OCR
                if hasattr(note, "ocr_result") and note.ocr_result:
                    note_data["ocr_result"] = note.ocr_result

            notes_data.append(note_data)

        data["notes"] = notes_data

        # Если там уже что-то было, подгрузим и обновим
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            # Сохраняем статус из старых данных
            if "status" in old_data:
                data["status"] = old_data["status"]

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def loadStatus(self):
        """Загрузка статуса обработки и восстановление заметок"""
        c_folder = self.ch_paths["translation"]
        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                if "status" in data:
                    st = data["status"]
                    if st == "Не начат":
                        self.status_not.setChecked(True)
                    elif st == "В работе":
                        self.status_in_prog.setChecked(True)
                    elif st == "Завершен":
                        self.status_done.setChecked(True)

                if "note_settings" in data:
                    s = data["note_settings"]
                    self.viewer.note_settings.update(s)

                    # Применяем настройки на UI
                    for key, le in self.note_color_settings.items():
                        if key in s:
                            le.setText(s[key])
                            le.parentWidget().findChild(QToolButton).setStyleSheet(f"background-color: {s[key]};")

                    fs = s.get("font_size", 12)
                    self.font_size_spin.setValue(fs)

                    # Обновляем выбор языка OCR
                    ocr_lang = s.get("ocr_language", "Русский")
                    self.ocr_lang_combo.setCurrentText(ocr_lang)

                # Восстанавливаем заметки
                if "notes" in data:
                    for note_data in data["notes"]:
                        # Создаем заметку в зависимости от режима
                        mode = note_data.get("mode", "Стандартная")
                        page_index = note_data.get("page_index", 0)
                        pos = QPointF(
                            note_data.get("position", {}).get("x", 0),
                            note_data.get("position", {}).get("y", 0)
                        )
                        is_visible = note_data.get("is_visible", True)

                        # Создаем точку привязки или прямоугольник если нужно
                        extra = {}
                        if "anchor" in note_data:
                            anchor_x = note_data["anchor"].get("x", 0)
                            anchor_y = note_data["anchor"].get("y", 0)
                            anchor_visible = note_data.get("anchor_visible", True)

                            if mode == "Прямоугольная":
                                # Создаем прямоугольник для режима OCR
                                rect_width = note_data.get("rect_size", {}).get("width", 100)
                                rect_height = note_data.get("rect_size", {}).get("height", 100)
                                rect = QRectF(anchor_x, anchor_y, rect_width, rect_height)
                                rect_item = MovableRectItem(rect, self.viewer.note_settings, True)
                                rect_item.page_index = page_index
                                rect_item.is_visible = anchor_visible
                                rect_item.notes_modified_signal.notes_modified.connect(self.viewer.onNotesModified)
                                self.viewer.scene_.addItem(rect_item)
                                extra["p1"] = rect_item
                            else:
                                # Создаем точку привязки
                                r = self.viewer.note_settings.get("point_radius", 5)
                                point = AnchorPointItem(anchor_x, anchor_y, r, self.viewer.note_settings)
                                point.page_index = page_index
                                point.is_visible = anchor_visible
                                point.notes_modified_signal.notes_modified.connect(self.viewer.onNotesModified)
                                self.viewer.scene_.addItem(point)
                                extra["p1"] = point

                        # Создаем заметку
                        note = NoteItem(pos, page_index, mode, self.viewer.note_settings, extra)
                        note.notes_modified_signal.notes_modified.connect(self.viewer.onNotesModified)
                        note.is_visible = is_visible

                        # Применяем размеры
                        width = note_data.get("size", {}).get("width", 150)
                        height = note_data.get("size", {}).get("height", 120)
                        note.setPreferredSize(width, height)
                        note.resize(QSizeF(width, height))
                        note._width, note._height = width, height
                        note.resize_handle.setRect(width - 10, height - 10, 10, 10)

                        # Устанавливаем текст
                        if "text" in note_data:
                            note.text_edit.setHtml(note_data["text"])

                        # Восстанавливаем результат OCR если есть
                        if "ocr_result" in note_data and mode == "Прямоугольная":
                            note.ocr_result = note_data["ocr_result"]
                            note.ocr_text.setPlainText(note_data["ocr_result"])
                            note.ocr_container.setVisible(True)

                        # Добавляем линию если есть привязка
                        if "p1" in extra and note.line:
                            self.viewer.scene_._lines.append(note.line)
                            self.viewer.scene_.addItem(note.line)
                            note.line.page_index = page_index
                            note.line.updateLine()

                        # Добавляем заметку на сцену
                        self.viewer.scene_.addItem(note)
                        self.viewer.notes.append(note)

                    # Обновляем отображение заметок для текущей страницы
                    self.viewer.displayCurrentPage()

    def exportTranslations(self):
        """Экспорт переводов вместе с изображениями в ZIP-архив"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Экспорт переводов")

        # Сохраняем текущее состояние
        self.saveStatus()

        try:
            import zipfile
            import datetime

            # Открываем диалог сохранения файла
            file_dialog = QFileDialog()
            file_dialog.setAcceptMode(QFileDialog.AcceptSave)
            file_dialog.setNameFilter("Translation Archives (*.mltz)")  # MangaLocalizer Translation Zip
            file_dialog.setDefaultSuffix("mltz")

            # Формируем имя файла по умолчанию
            chapter_name = os.path.basename(self.ch_folder)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"{chapter_name}_translation_{timestamp}.mltz"

            default_path = os.path.join(self.ch_paths["translation"], default_filename)
            file_dialog.selectFile(default_path)

            logger.info(f"Открытие диалога сохранения с путем по умолчанию: {default_path}")

            if file_dialog.exec():
                export_path = file_dialog.selectedFiles()[0]
                logger.info(f"Выбран путь для экспорта: {export_path}")

                # Показываем прогресс-диалог
                progress = QProgressDialog("Экспорт переводов...", "Отмена", 0, 100, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setWindowTitle("Экспорт переводов")
                progress.show()

                # Флаг для отслеживания отмены
                export_cancelled = False

                # Загружаем данные из файла статуса
                status_path = os.path.join(self.ch_paths["translation"], self.status_json_filename)
                logger.info(f"Загрузка данных из: {status_path}")

                if not os.path.exists(status_path):
                    show_message(self, "Ошибка", "Не найден файл с данными переводов")
                    progress.close()
                    self.unlockInterface()
                    return

                with open(status_path, 'r', encoding='utf-8') as f:
                    translation_data = json.load(f)

                logger.info(f"Загружено заметок: {len(translation_data.get('notes', []))}")

                # Создаем ZIP-архив
                logger.info(f"Создание архива: {export_path}")

                with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # Добавляем манифест с метаданными
                    manifest = {
                        "type": "manga_localizer_translation",
                        "version": "1.0",
                        "chapter_name": chapter_name,
                        "export_date": datetime.datetime.now().isoformat(),
                        "image_count": len(self.image_paths),
                        "notes_count": len(translation_data.get("notes", [])),
                        "status": translation_data.get("status", "В работе"),
                        "note_settings": translation_data.get("note_settings", {})
                    }

                    zipf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=4))
                    logger.info("Добавлен manifest.json")

                    # Добавляем данные перевода
                    zipf.writestr("translation_data.json", json.dumps(translation_data, ensure_ascii=False, indent=4))
                    logger.info("Добавлен translation_data.json")

                    # Добавляем изображения из папки Images
                    images_folder = os.path.join(self.ch_paths["translation"], "Images")
                    logger.info(f"Поиск изображений в: {images_folder}")

                    if os.path.exists(images_folder):
                        image_files = []
                        for filename in os.listdir(images_folder):
                            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                                image_files.append(filename)

                        # Сортируем файлы для правильного порядка
                        image_files.sort()
                        logger.info(f"Найдено изображений для экспорта: {len(image_files)}")

                        # Добавляем каждое изображение
                        for i, filename in enumerate(image_files):
                            if progress.wasCanceled():
                                logger.info("Экспорт отменен пользователем")
                                export_cancelled = True
                                break

                            file_path = os.path.join(images_folder, filename)
                            arc_name = os.path.join("Images", filename)
                            zipf.write(file_path, arc_name)

                            # Обновляем прогресс
                            progress_value = int((i + 1) / len(image_files) * 100)
                            progress.setValue(progress_value)
                            progress.setLabelText(f"Добавление изображения {i + 1} из {len(image_files)}: {filename}")
                            QApplication.processEvents()

                        logger.info(f"Добавлено изображений в архив: {len(image_files)}")

                # Проверяем статус ДО закрытия диалога
                if export_cancelled:
                    progress.close()
                    # Удаляем недозаписанный архив
                    if os.path.exists(export_path):
                        os.remove(export_path)
                        logger.info("Удален неполный архив из-за отмены")
                else:
                    progress.setValue(100)
                    progress.close()

                    # Проверяем, существует ли файл
                    if os.path.exists(export_path):
                        # Получаем размер архива
                        file_size = os.path.getsize(export_path)
                        size_mb = file_size / (1024 * 1024)

                        logger.info(f"Архив создан успешно. Размер: {size_mb:.2f} МБ")

                        show_message(
                            self,
                            "Успех",
                            f"Переводы экспортированы в архив:\n{export_path}\n\n"
                            f"Размер архива: {size_mb:.2f} МБ\n"
                            f"Изображений: {len(image_files)}\n"
                            f"Заметок: {len(translation_data.get('notes', []))}"
                        )
                    else:
                        logger.error(f"Файл не создан: {export_path}")
                        show_message(self, "Ошибка", f"Файл не был создан: {export_path}")
            else:
                logger.info("Диалог сохранения был отменен")

        except Exception as e:
            logger.error(f"Ошибка при экспорте: {e}", exc_info=True)
            show_message(self, "Ошибка", f"Не удалось экспортировать переводы: {str(e)}")

        self.unlockInterface()

    def importTranslations(self):
        """Импорт переводов вместе с изображениями из архива"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Импорт переводов")

        # Создаем резервные копии для отката
        backup_image_paths = self.image_paths.copy() if hasattr(self, 'image_paths') else []
        backup_current_page = self.viewer.current_page if hasattr(self, 'viewer') else 0

        # Сохраняем текущее состояние для возможного отката
        temp_backup_path = os.path.join(self.ch_paths["translation"], "backup_before_import.json")
        if os.path.exists(os.path.join(self.ch_paths["translation"], self.status_json_filename)):
            shutil.copy2(
                os.path.join(self.ch_paths["translation"], self.status_json_filename),
                temp_backup_path
            )

        try:
            import zipfile

            # Открываем диалог выбора файла
            file_dialog = QFileDialog()
            file_dialog.setAcceptMode(QFileDialog.AcceptOpen)
            file_dialog.setNameFilter("Translation Archives (*.mltz);;ZIP Archives (*.zip)")
            file_dialog.setFileMode(QFileDialog.ExistingFile)

            if file_dialog.exec():
                import_path = file_dialog.selectedFiles()[0]

                # Проверяем, что это наш архив
                with zipfile.ZipFile(import_path, 'r') as zipf:
                    # Проверяем наличие манифеста
                    if 'manifest.json' not in zipf.namelist():
                        show_message(self, "Ошибка", "Неверный формат архива. Отсутствует файл манифеста.")
                        self.unlockInterface()
                        return

                    # Читаем манифест
                    manifest_data = zipf.read('manifest.json')
                    manifest = json.loads(manifest_data.decode('utf-8'))

                    # Проверяем тип архива
                    if manifest.get('type') != 'manga_localizer_translation':
                        show_message(self, "Ошибка", "Неверный тип архива. Ожидается архив переводов MangaLocalizer.")
                        self.unlockInterface()
                        return

                # Показываем информацию об архиве и спрашиваем подтверждение
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Question)
                msg.setWindowTitle("Импорт переводов")
                msg.setText(
                    f"Информация об архиве:\n\n"
                    f"Глава: {manifest.get('chapter_name', 'Неизвестно')}\n"
                    f"Дата экспорта: {manifest.get('export_date', 'Неизвестно')}\n"
                    f"Изображений: {manifest.get('image_count', 0)}\n"
                    f"Заметок: {manifest.get('notes_count', 0)}\n"
                    f"Статус: {manifest.get('status', 'Неизвестно')}\n\n"
                    f"Это действие заменит все текущие изображения и заметки.\n"
                    f"Продолжить?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

                if msg.exec() != QMessageBox.Yes:
                    self.unlockInterface()
                    return

                # Показываем прогресс
                progress = QProgressDialog("Импорт переводов...", "Отмена", 0, 100, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setWindowTitle("Импорт переводов")
                progress.show()

                # Флаг успешного завершения
                import_success = False

                # Создаем временную папку для новых изображений
                temp_images_folder = os.path.join(self.ch_paths["translation"], "temp_import_images")
                os.makedirs(temp_images_folder, exist_ok=True)

                try:
                    # Удаляем все существующие заметки
                    self._clearAllNotes()

                    # Распаковываем архив
                    with zipfile.ZipFile(import_path, 'r') as zipf:
                        # Получаем список файлов для распаковки
                        files_to_extract = []
                        for filename in zipf.namelist():
                            if filename.startswith('Images/') and not filename.endswith('/'):
                                files_to_extract.append(filename)

                        # Извлекаем изображения во временную папку
                        for i, filename in enumerate(files_to_extract):
                            if progress.wasCanceled():
                                raise Exception("Импорт отменен пользователем")

                            # Извлекаем файл во временную папку
                            data = zipf.read(filename)
                            temp_file_path = os.path.join(temp_images_folder, os.path.basename(filename))
                            with open(temp_file_path, 'wb') as f:
                                f.write(data)

                            # Обновляем прогресс
                            progress_value = int((i + 1) / len(files_to_extract) * 80)  # 80% на изображения
                            progress.setValue(progress_value)
                            progress.setLabelText(f"Извлечение изображения {i + 1} из {len(files_to_extract)}")
                            QApplication.processEvents()

                        # Загружаем данные перевода
                        progress.setValue(85)
                        progress.setLabelText("Загрузка данных перевода...")
                        QApplication.processEvents()

                        translation_data_raw = zipf.read('translation_data.json')
                        translation_data = json.loads(translation_data_raw.decode('utf-8'))

                    # Если дошли до сюда - все файлы извлечены успешно
                    # Теперь заменяем старые файлы новыми

                    # Очищаем папку с изображениями
                    images_folder = os.path.join(self.ch_paths["translation"], "Images")
                    if os.path.exists(images_folder):
                        for file in os.listdir(images_folder):
                            file_path = os.path.join(images_folder, file)
                            if os.path.isfile(file_path):
                                os.remove(file_path)

                    # Перемещаем файлы из временной папки
                    for file in os.listdir(temp_images_folder):
                        src = os.path.join(temp_images_folder, file)
                        dst = os.path.join(images_folder, file)
                        shutil.move(src, dst)

                    # Сохраняем данные перевода
                    status_path = os.path.join(self.ch_paths["translation"], self.status_json_filename)
                    with open(status_path, 'w', encoding='utf-8') as f:
                        json.dump(translation_data, f, ensure_ascii=False, indent=4)

                    # Обновляем список изображений
                    progress.setValue(90)
                    progress.setLabelText("Обновление списка изображений...")
                    QApplication.processEvents()

                    self.image_paths = get_images_from_folder(images_folder)

                    # Обновляем viewer
                    self.viewer.pages = self.image_paths
                    self.viewer.pixmaps = [QPixmap() for _ in self.image_paths]

                    # Восстанавливаем страницу если возможно
                    if backup_current_page < len(self.image_paths):
                        self.viewer.current_page = backup_current_page
                    else:
                        self.viewer.current_page = 0

                    # Обновляем загрузчик
                    self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
                    self.image_loader.image_loaded.connect(self.onImageLoaded)
                    self.image_loader.loading_progress.connect(self.updateLoadingProgress)
                    self.image_loader.loading_complete.connect(self.onLoadingComplete)
                    self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

                    # Загружаем настройки и заметки
                    progress.setValue(98)
                    progress.setLabelText("Восстановление заметок...")
                    QApplication.processEvents()

                    self.loadStatus()

                    progress.setValue(100)
                    import_success = True

                except Exception as e:
                    logger.error(f"Ошибка во время импорта: {e}")
                    import_success = False

                    # Откатываем изменения
                    logger.info("Откат изменений после ошибки импорта")

                    # Восстанавливаем резервную копию статуса
                    if os.path.exists(temp_backup_path):
                        shutil.copy2(temp_backup_path,
                                     os.path.join(self.ch_paths["translation"], self.status_json_filename))

                    # Восстанавливаем пути к изображениям
                    self.image_paths = backup_image_paths
                    if hasattr(self, 'viewer'):
                        self.viewer.pages = backup_image_paths
                        self.viewer.current_page = backup_current_page

                    raise

                finally:
                    # Удаляем временную папку
                    if os.path.exists(temp_images_folder):
                        shutil.rmtree(temp_images_folder, ignore_errors=True)

                    # Удаляем временную резервную копию
                    if os.path.exists(temp_backup_path):
                        os.remove(temp_backup_path)

                    progress.close()

                if import_success:
                    # Запускаем загрузку изображений
                    self.startLoading()

                    show_message(
                        self,
                        "Успех",
                        f"Переводы успешно импортированы!\n\n"
                        f"Импортировано:\n"
                        f"- Изображений: {len(self.image_paths)}\n"
                        f"- Заметок: {len(translation_data.get('notes', []))}"
                    )
            else:
                self.unlockInterface()
                return

        except zipfile.BadZipFile:
            show_message(self, "Ошибка", "Выбранный файл не является корректным архивом")
        except Exception as e:
            logger.error(f"Ошибка при импорте: {e}")
            show_message(self, "Ошибка", f"Не удалось импортировать переводы: {str(e)}")

        self.unlockInterface()

    def _clearAllNotes(self):
        """Удаляет все существующие заметки перед импортом"""
        # Очищаем все заметки на сцене
        if hasattr(self.viewer, "notes"):
            for note in self.viewer.notes[:]:
                if hasattr(note, "line") and note.line and hasattr(self.viewer.scene_, "_lines"):
                    if note.line in self.viewer.scene_._lines:
                        self.viewer.scene_._lines.remove(note.line)
                    if note.line.scene():
                        self.viewer.scene_.removeItem(note.line)

                if "p1" in getattr(note, "extra", {}):
                    anchor = note.extra["p1"]
                    if anchor and anchor.scene():
                        self.viewer.scene_.removeItem(anchor)

                if note.scene():
                    self.viewer.scene_.removeItem(note)

            self.viewer.notes.clear()

        # Очищаем линии
        if hasattr(self.viewer.scene_, "_lines"):
            for line in self.viewer.scene_._lines[:]:
                if line.scene():
                    self.viewer.scene_.removeItem(line)
            self.viewer.scene_._lines.clear()

        # Очищаем все точки привязки
        for item in self.viewer.scene_.items()[:]:
            if isinstance(item, AnchorPointItem) and item.scene():
                self.viewer.scene_.removeItem(item)

    def checkEasyOcrInstalled(self):
        """Проверяет, установлен ли EasyOCR"""
        try:
            import easyocr
            return True
        except ImportError:
            show_message(
                self,
                "EasyOCR не установлен",
                "Для распознавания текста необходимо установить EasyOCR.\n"
                "Выполните команду: pip install easyocr\n\n"
                "Для поддержки распознавания русского языка также потребуется:\n"
                "pip install torch torchvision"
            )
            return False

    def lockInterface(self, operation):
        """Блокировка интерфейса при длительных операциях"""
        self.is_proc = True
        self.curr_op = operation

        self.refresh_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.copy_current_image_btn.setEnabled(False)

        QApplication.processEvents()

    def unlockInterface(self):
        """Разблокировка интерфейса после операций"""
        self.is_proc = False
        self.curr_op = None

        self.refresh_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.import_btn.setEnabled(True)
        self.copy_current_image_btn.setEnabled(True)

        QApplication.processEvents()

    def keyPressEvent(self, event):
        """Обработчик нажатий клавиш"""
        if event.key() == Qt.Key_Space:
            self.viewer.setDragMode(self.viewer.ScrollHandDrag)
            self.viewer.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.key() == Qt.Key_Left:
            self.onPreviousPage()
            event.accept()
            return
        elif event.key() == Qt.Key_Right:
            self.onNextPage()
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """Обработчик отпускания клавиш"""
        if event.key() == Qt.Key_Space:
            self.viewer.setDragMode(self.viewer.NoDrag)
            self.viewer.setCursor(Qt.ArrowCursor)
            event.accept()
            return

        super().keyReleaseEvent(event)

    def closeEvent(self, event):
        """Обработчик события закрытия окна"""
        # Сохраняем статус и заметки только если viewer существует
        if hasattr(self, 'viewer'):
            self.saveStatus()

        # Останавливаем загрузку изображений
        if hasattr(self, 'image_loader'):
            self.image_loader.cancel()

        # Закрываем пул потоков
        if hasattr(self, 'thread_pool'):
            self.thread_pool.shutdown(wait=False)

        super().closeEvent(event)

    def exportTranslationsWithImages(self):
        """Экспорт переводов вместе с изображениями в ZIP-архив"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Экспорт переводов с изображениями")

        # Сохраняем текущее состояние
        self.saveStatus()

        try:
            import zipfile
            import datetime

            # Открываем диалог сохранения файла
            file_dialog = QFileDialog()
            file_dialog.setAcceptMode(QFileDialog.AcceptSave)
            file_dialog.setNameFilter("ZIP Archives (*.zip)")
            file_dialog.setDefaultSuffix("zip")

            # Формируем имя файла по умолчанию
            chapter_name = os.path.basename(self.ch_folder)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"{chapter_name}_translation_{timestamp}.zip"

            default_path = os.path.join(self.ch_paths["translation"], default_filename)
            file_dialog.selectFile(default_path)

            if file_dialog.exec():
                export_path = file_dialog.selectedFiles()[0]

                # Показываем прогресс-диалог
                progress = QProgressDialog("Экспорт переводов...", "Отмена", 0, 100, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setWindowTitle("Экспорт")
                progress.show()

                # Загружаем данные из файла статуса
                status_path = os.path.join(self.ch_paths["translation"], self.status_json_filename)
                if not os.path.exists(status_path):
                    show_message(self, "Ошибка", "Не найден файл с данными переводов")
                    self.unlockInterface()
                    return

                with open(status_path, 'r', encoding='utf-8') as f:
                    translation_data = json.load(f)

                # Создаем ZIP-архив
                with zipfile.ZipFile(export_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    # Добавляем манифест с метаданными
                    manifest = {
                        "type": "manga_localizer_translation_export",
                        "version": "1.0",
                        "chapter_name": chapter_name,
                        "export_date": datetime.datetime.now().isoformat(),
                        "image_count": len(self.image_paths),
                        "notes_count": len(translation_data.get("notes", [])),
                        "status": translation_data.get("status", "В работе")
                    }

                    zipf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=4))

                    # Добавляем данные перевода
                    zipf.writestr("translation_data.json", json.dumps(translation_data, ensure_ascii=False, indent=4))

                    # Добавляем изображения из папки Images
                    images_folder = os.path.join(self.ch_paths["translation"], "Images")
                    if os.path.exists(images_folder):
                        image_files = []
                        for filename in os.listdir(images_folder):
                            if filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                                image_files.append(filename)

                        # Сортируем файлы для правильного порядка
                        image_files.sort()

                        # Добавляем каждое изображение
                        for i, filename in enumerate(image_files):
                            if progress.wasCanceled():
                                break

                            file_path = os.path.join(images_folder, filename)
                            arc_name = os.path.join("Images", filename)
                            zipf.write(file_path, arc_name)

                            # Обновляем прогресс
                            progress_value = int((i + 1) / len(image_files) * 100)
                            progress.setValue(progress_value)
                            progress.setLabelText(f"Добавление изображения {i + 1} из {len(image_files)}: {filename}")
                            QApplication.processEvents()

                progress.setValue(100)
                progress.close()

                if not progress.wasCanceled():
                    # Получаем размер архива
                    file_size = os.path.getsize(export_path)
                    size_mb = file_size / (1024 * 1024)

                    show_message(
                        self,
                        "Успех",
                        f"Переводы и изображения экспортированы в архив:\n{export_path}\n\n"
                        f"Размер архива: {size_mb:.2f} МБ\n"
                        f"Изображений: {len(image_files)}\n"
                        f"Заметок: {len(translation_data.get('notes', []))}"
                    )
                else:
                    # Удаляем недозаписанный архив
                    if os.path.exists(export_path):
                        os.remove(export_path)

        except Exception as e:
            logger.error(f"Ошибка при экспорте: {e}")
            show_message(self, "Ошибка", f"Не удалось экспортировать переводы: {str(e)}")

        self.unlockInterface()

    def importTranslationsWithImages(self):
        """Импорт переводов вместе с изображениями из ZIP-архива"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Импорт переводов с изображениями")

        try:
            import zipfile

            # Открываем диалог выбора файла
            file_dialog = QFileDialog()
            file_dialog.setAcceptMode(QFileDialog.AcceptOpen)
            file_dialog.setNameFilter("ZIP Archives (*.zip)")
            file_dialog.setFileMode(QFileDialog.ExistingFile)

            if file_dialog.exec():
                import_path = file_dialog.selectedFiles()[0]

                # Проверяем, что это наш архив
                with zipfile.ZipFile(import_path, 'r') as zipf:
                    # Проверяем наличие манифеста
                    if 'manifest.json' not in zipf.namelist():
                        show_message(self, "Ошибка", "Неверный формат архива. Отсутствует файл манифеста.")
                        self.unlockInterface()
                        return

                    # Читаем манифест
                    manifest_data = zipf.read('manifest.json')
                    manifest = json.loads(manifest_data.decode('utf-8'))

                    # Проверяем тип архива
                    if manifest.get('type') != 'manga_localizer_translation_export':
                        show_message(self, "Ошибка", "Неверный тип архива. Ожидается архив экспорта переводов.")
                        self.unlockInterface()
                        return

                # Показываем информацию об архиве и спрашиваем подтверждение
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Question)
                msg.setWindowTitle("Импорт переводов")
                msg.setText(
                    f"Информация об архиве:\n\n"
                    f"Глава: {manifest.get('chapter_name', 'Неизвестно')}\n"
                    f"Дата экспорта: {manifest.get('export_date', 'Неизвестно')}\n"
                    f"Изображений: {manifest.get('image_count', 0)}\n"
                    f"Заметок: {manifest.get('notes_count', 0)}\n"
                    f"Статус: {manifest.get('status', 'Неизвестно')}\n\n"
                    f"Это действие заменит все текущие изображения и заметки.\n"
                    f"Продолжить?"
                )
                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

                if msg.exec() != QMessageBox.Yes:
                    self.unlockInterface()
                    return

                # Показываем прогресс
                progress = QProgressDialog("Импорт переводов...", "Отмена", 0, 100, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setWindowTitle("Импорт")
                progress.show()

                # Сохраняем текущую страницу
                current_page_backup = self.viewer.current_page

                # Удаляем все существующие заметки
                self._clearAllNotes()

                # Очищаем папку с изображениями
                images_folder = os.path.join(self.ch_paths["translation"], "Images")
                if os.path.exists(images_folder):
                    for file in os.listdir(images_folder):
                        file_path = os.path.join(images_folder, file)
                        if os.path.isfile(file_path):
                            os.remove(file_path)

                # Распаковываем архив
                with zipfile.ZipFile(import_path, 'r') as zipf:
                    # Получаем список файлов для распаковки
                    files_to_extract = []
                    for filename in zipf.namelist():
                        if filename.startswith('Images/') and not filename.endswith('/'):
                            files_to_extract.append(filename)

                    # Извлекаем изображения
                    for i, filename in enumerate(files_to_extract):
                        if progress.wasCanceled():
                            break

                        # Извлекаем файл
                        zipf.extract(filename, self.ch_paths["translation"])

                        # Обновляем прогресс
                        progress_value = int((i + 1) / len(files_to_extract) * 80)  # 80% на изображения
                        progress.setValue(progress_value)
                        progress.setLabelText(f"Извлечение изображения {i + 1} из {len(files_to_extract)}")
                        QApplication.processEvents()

                    if not progress.wasCanceled():
                        # Загружаем данные перевода
                        progress.setValue(85)
                        progress.setLabelText("Загрузка данных перевода...")
                        QApplication.processEvents()

                        translation_data_raw = zipf.read('translation_data.json')
                        translation_data = json.loads(translation_data_raw.decode('utf-8'))

                        # Сохраняем данные перевода
                        status_path = os.path.join(self.ch_paths["translation"], self.status_json_filename)
                        with open(status_path, 'w', encoding='utf-8') as f:
                            json.dump(translation_data, f, ensure_ascii=False, indent=4)

                        # Обновляем список изображений
                        progress.setValue(90)
                        progress.setLabelText("Обновление списка изображений...")
                        QApplication.processEvents()

                        self.image_paths = get_images_from_folder(images_folder)

                        # Обновляем viewer
                        self.viewer.pages = self.image_paths
                        self.viewer.pixmaps = [QPixmap() for _ in self.image_paths]

                        # Восстанавливаем страницу если возможно
                        if current_page_backup < len(self.image_paths):
                            self.viewer.current_page = current_page_backup
                        else:
                            self.viewer.current_page = 0

                        # Обновляем миниатюры
                        progress.setValue(95)
                        progress.setLabelText("Обновление миниатюр...")
                        QApplication.processEvents()

                        # Обновляем загрузчик
                        self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
                        self.image_loader.image_loaded.connect(self.onImageLoaded)
                        self.image_loader.loading_progress.connect(self.updateLoadingProgress)
                        self.image_loader.loading_complete.connect(self.onLoadingComplete)
                        self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

                        # Загружаем настройки и заметки
                        progress.setValue(98)
                        progress.setLabelText("Восстановление заметок...")
                        QApplication.processEvents()

                        self.loadStatus()

                progress.setValue(100)
                progress.close()

                if not progress.wasCanceled():
                    # Запускаем загрузку изображений
                    self.startLoading()

                    show_message(
                        self,
                        "Успех",
                        f"Переводы успешно импортированы!\n\n"
                        f"Импортировано:\n"
                        f"- Изображений: {len(self.image_paths)}\n"
                        f"- Заметок: {len(translation_data.get('notes', []))}"
                    )
                else:
                    show_message(self, "Предупреждение",
                                 "Импорт был отменен. Некоторые данные могли быть импортированы частично.")
                    self.unlockInterface()

        except zipfile.BadZipFile:
            show_message(self, "Ошибка", "Выбранный файл не является корректным ZIP-архивом")
            self.unlockInterface()
        except Exception as e:
            logger.error(f"Ошибка при импорте: {e}")
            show_message(self, "Ошибка", f"Не удалось импортировать переводы: {str(e)}")
            self.unlockInterface()