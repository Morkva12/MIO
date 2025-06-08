# -*- coding: utf-8 -*-
# ui/windows/m10_0_quality_check.py

import os
import sys
import json
import logging
import shutil
import threading
import datetime
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import (Qt, Signal, QThreadPool, QTimer, QPointF, QMetaObject, Q_ARG)
from PySide6.QtGui import (QPixmap, QFont, QColor, QBrush, QCursor, QPainter)
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSpacerItem, QSizePolicy, QRadioButton,
                               QButtonGroup, QMessageBox, QComboBox,
                               QCheckBox, QWidget, QGroupBox, QApplication,
                               QProgressBar, QFileDialog)

# Импортируем наши модули
from ui.components.gradient_widget import GradientBackgroundWidget
from ui.windows.m10_1_image_viewer import ImageViewer
from ui.windows.m9_2_utils import (get_images_from_folder, show_message, PageChangeSignal)
from ui.windows.m9_3_ui_components import (ImageLoader, LoadingOverlay,
                                           ImageLoadedEvent, AllImagesLoadedEvent)

# Настройка логгера
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('quality_check_debug.log')
    ]
)
logger = logging.getLogger(__name__)


class QualityCheckWindow(QDialog):
    """
    Окно "Контроль качества".
    Основная функциональность:
    - Загрузка и отображение изображений из модуля тайпсеттинга
    - Просмотр готовых результатов с наложенным текстом
    - Управление статусом обработки
    - Экспорт финальных изображений
    """
    back_requested = Signal()

    def __init__(self, chapter_folder, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("quality_check_window")

        # Сохраняем пути
        self.ch_folder = chapter_folder
        self.paths = paths or {}
        self.status_json_filename = "quality_check.json"

        # Определяем базовые папки
        self.ch_paths = {
            "quality_check": os.path.join(chapter_folder, "Контроль качества"),
            "typesetting": os.path.join(chapter_folder, "Тайпсеттинг"),
            "translation": os.path.join(chapter_folder, "Перевод"),
            "cleaning": os.path.join(chapter_folder, "Клининг"),
            "preproc": os.path.join(chapter_folder, "Предобработка"),
            "upload": os.path.join(chapter_folder, "Загрузка"),
        }

        # Создаем папки
        for folder in self.ch_paths.values():
            os.makedirs(folder, exist_ok=True)

        # Инициализация окна
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Контроль качества")
        self.resize(1920, 1080)

        # Создаем градиентный фон
        self.bg_widget = GradientBackgroundWidget(self)
        self.bg_widget.setObjectName("bg_widget")

        # Основной макет
        main_layout = QVBoxLayout(self.bg_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Сигнал изменения страницы
        self.page_change_signal = PageChangeSignal()

        # Флаги состояния
        self.is_proc = False  # Идет ли процесс
        self.curr_op = None  # Текущая операция
        self.is_loading_complete = False  # Завершена ли загрузка изображений

        # Данные тайпсеттинга
        self.typesetting_data = None

        # Определяем источник изображений с диалогом при первом запуске
        self.image_paths = self._decideImageSource()

        if not self.image_paths:
            show_message(self, "Ошибка", "Не найдены изображения для контроля качества.")
            QApplication.processEvents()
            self.close()
            return

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

        # Загружаем статус
        self.loadStatus()

        # Подсвечиваем первую картинку
        self.updateActiveThumbnail(0)

        # Показываем загрузочный экран и начинаем загрузку изображений
        QTimer.singleShot(100, self.startLoading)

    def _decideImageSource(self):
        """Определяет источник изображений при первом запуске"""
        quality_check_img_dir = os.path.join(self.ch_paths["quality_check"], "Images")
        os.makedirs(quality_check_img_dir, exist_ok=True)

        # Если в папке уже есть изображения, используем их
        existing_images = get_images_from_folder(quality_check_img_dir)
        if existing_images:
            logger.info(f"Найдено {len(existing_images)} изображений в папке контроля качества")
            # Проверяем наличие данных тайпсеттинга
            self._loadTypesettingData()
            return existing_images

        # При первом запуске проверяем тайпсеттинг
        typesetting_cleaned = os.path.join(self.ch_paths["typesetting"], "Cleaned")
        typesetting_json = os.path.join(self.ch_paths["typesetting"], "typesetting.json")

        # Если есть очищенные изображения и данные тайпсеттинга
        if os.path.exists(typesetting_cleaned) and os.path.exists(typesetting_json):
            cleaned_images = get_images_from_folder(typesetting_cleaned)
            if cleaned_images:
                # Копируем изображения и загружаем данные
                self._copyImagesToQualityCheck(cleaned_images)
                self._loadTypesettingData()
                return get_images_from_folder(quality_check_img_dir)

        # Если нет готовых данных из тайпсеттинга, показываем диалог выбора
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(500)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник изображений для контроля качества:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()
        sources = []

        # 1. Очищенные изображения из клининга
        cleaning_folders = [
            os.path.join(self.ch_paths["cleaning"], "Save"),
            os.path.join(self.ch_paths["cleaning"], "Results"),
            os.path.join(self.ch_paths["cleaning"], "Pages")
        ]

        for cleaning_path in cleaning_folders:
            if os.path.exists(cleaning_path):
                cleaning_images = get_images_from_folder(cleaning_path)
                if cleaning_images:
                    sources.append({
                        "path": cleaning_path,
                        "images": cleaning_images,
                        "name": "Клининг - Очищенные изображения",
                        "description": f"({len(cleaning_images)} изображений)"
                    })
                    break

        # 2. Оригинальные изображения
        original_folders = [
            os.path.join(self.ch_paths["translation"], "Images"),
            os.path.join(self.ch_paths["upload"])
        ]

        for orig_path in original_folders:
            if os.path.exists(orig_path):
                orig_images = get_images_from_folder(orig_path)
                if orig_images:
                    sources.append({
                        "path": orig_path,
                        "images": orig_images,
                        "name": "Оригинальные изображения",
                        "description": f"({len(orig_images)} изображений)"
                    })
                    break

        if not sources:
            show_message(self, "Ошибка", "Не найдены источники изображений")
            return []

        # Создаем радиокнопки для каждого источника
        for i, source_info in enumerate(sources):
            radio = QRadioButton(f"{source_info['name']} {source_info['description']}")
            radio.setStyleSheet("margin: 10px 0;")
            button_group.addButton(radio)
            radio.toggled.connect(lambda checked, idx=i: setattr(dialog, 'selected_source', idx if checked else None))
            layout.addWidget(radio)

            # Выбираем первый по умолчанию
            if i == 0:
                radio.setChecked(True)
                dialog.selected_source = 0

        # Информация о текстовых блоках
        if os.path.exists(typesetting_json):
            info_label = QLabel("✓ Найдены данные о текстовых блоках из тайпсеттинга")
            info_label.setStyleSheet("color: #4CAF50; margin-top: 10px;")
            layout.addWidget(info_label)

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
            selected = sources[dialog.selected_source]

            # Копируем изображения
            self._copyImagesToQualityCheck(selected["images"])

            # Загружаем данные тайпсеттинга если есть
            self._loadTypesettingData()

            return get_images_from_folder(quality_check_img_dir)

        return []

    def _loadTypesettingData(self):
        """Загружает данные о текстовых блоках из тайпсеттинга"""
        typesetting_json = os.path.join(self.ch_paths["typesetting"], "typesetting.json")

        if os.path.exists(typesetting_json):
            try:
                with open(typesetting_json, 'r', encoding='utf-8') as f:
                    self.typesetting_data = json.load(f)
                    logger.info("Загружены данные тайпсеттинга")

                    # Передаем данные в viewer если он уже создан
                    if hasattr(self, 'viewer'):
                        self.viewer.setTypesettingData(self.typesetting_data)
            except Exception as e:
                logger.error(f"Ошибка загрузки данных тайпсеттинга: {str(e)}")
                self.typesetting_data = None

    def _copyImagesToQualityCheck(self, source_images):
        """Копирует изображения в папку контроля качества"""
        quality_check_img_dir = os.path.join(self.ch_paths["quality_check"], "Images")

        # Очищаем папку
        for file in os.listdir(quality_check_img_dir):
            file_path = os.path.join(quality_check_img_dir, file)
            if os.path.isfile(file_path):
                os.remove(file_path)

        # Копируем изображения
        for src_path in source_images:
            filename = os.path.basename(src_path)
            dst_path = os.path.join(quality_check_img_dir, filename)
            try:
                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован файл: {filename}")
            except Exception as e:
                logger.error(f"Ошибка копирования {filename}: {str(e)}")

    def refreshImages(self):
        """Обновляет изображения с диалогом выбора источника"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Обновление изображений")

        # Показываем диалог выбора типа обновления
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Обновление изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(450)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите что обновить:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()

        # Опции обновления
        update_images_radio = QRadioButton("Обновить изображения из тайпсеттинга")
        update_text_radio = QRadioButton("Обновить только текстовые блоки")
        update_both_radio = QRadioButton("Обновить изображения и текст")

        update_both_radio.setChecked(True)
        dialog.update_type = "both"

        button_group.addButton(update_images_radio)
        button_group.addButton(update_text_radio)
        button_group.addButton(update_both_radio)

        update_images_radio.toggled.connect(
            lambda checked: setattr(dialog, 'update_type', 'images' if checked else None))
        update_text_radio.toggled.connect(
            lambda checked: setattr(dialog, 'update_type', 'text' if checked else None))
        update_both_radio.toggled.connect(
            lambda checked: setattr(dialog, 'update_type', 'both' if checked else None))

        layout.addWidget(update_images_radio)
        layout.addWidget(update_text_radio)
        layout.addWidget(update_both_radio)

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("Обновить")
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

        if dialog.exec() == QDialog.Accepted:
            update_type = dialog.update_type

            if update_type in ["images", "both"]:
                # Обновляем изображения из тайпсеттинга
                typesetting_cleaned = os.path.join(self.ch_paths["typesetting"], "Cleaned")
                if os.path.exists(typesetting_cleaned):
                    cleaned_images = get_images_from_folder(typesetting_cleaned)
                    if cleaned_images:
                        # Сохраняем текущую страницу
                        current_page_backup = self.viewer.current_page

                        # Копируем изображения
                        self._copyImagesToQualityCheck(cleaned_images)

                        # Обновляем список путей
                        new_image_paths = get_images_from_folder(
                            os.path.join(self.ch_paths["quality_check"], "Images"))

                        if new_image_paths:
                            # Полностью обновляем интерфейс
                            self._updateImagesAndUI(new_image_paths, current_page_backup)

                            # Обновляем текстовые блоки если нужно
                            if update_type == "both":
                                self._loadTypesettingData()
                        else:
                            show_message(self, "Ошибка", "Не удалось загрузить обновленные изображения")
                            self.unlockInterface()
                    else:
                        show_message(self, "Предупреждение",
                                     "Не найдены очищенные изображения в модуле тайпсеттинга")
                        self.unlockInterface()
                else:
                    show_message(self, "Предупреждение",
                                 "Не найдена папка с очищенными изображениями в тайпсеттинге")
                    self.unlockInterface()

            elif update_type == "text":
                # Обновляем только текстовые блоки
                self._loadTypesettingData()

                # Перерисовываем текущую страницу
                if hasattr(self, 'viewer'):
                    self.viewer.displayCurrentPage()

                self.unlockInterface()
        else:
            self.unlockInterface()

    def _updateImagesAndUI(self, new_image_paths, preserve_page=0):
        """Полностью обновляет интерфейс с новыми изображениями"""
        # Останавливаем текущую загрузку если есть
        if hasattr(self, 'image_loader'):
            self.image_loader.cancel()

        # Обновляем пути к изображениям
        self.image_paths = new_image_paths

        # Пересоздаем панель миниатюр
        self._recreatePreviewPanel()

        # Обновляем viewer
        self.viewer.pages = self.image_paths
        self.viewer.pixmaps = [QPixmap() for _ in self.image_paths]

        # Восстанавливаем страницу если возможно
        if preserve_page < len(self.image_paths):
            self.viewer.current_page = preserve_page
        else:
            self.viewer.current_page = 0

        # Создаем новый загрузчик изображений
        self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
        self.image_loader.image_loaded.connect(self.onImageLoaded)
        self.image_loader.loading_progress.connect(self.updateLoadingProgress)
        self.image_loader.loading_complete.connect(self.onLoadingComplete)
        self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

        # Обновляем активную миниатюру
        self.updateActiveThumbnail(self.viewer.current_page)

        # Запускаем загрузку
        self.startLoading()

    def _recreatePreviewPanel(self):
        """Пересоздает панель миниатюр с новыми изображениями"""
        # Получаем контейнер из scroll area
        container = self.preview_scroll_area.widget()
        if not container:
            return

        # Получаем layout контейнера
        container_layout = container.layout()
        if not container_layout:
            return

        # Удаляем все старые виджеты
        while container_layout.count() > 0:
            item = container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Очищаем списки
        self.thumbnail_labels = []
        self.index_labels = []

        # Создаем новые миниатюры
        thumbnail_width = 150
        thumbnail_height = thumbnail_width * 2

        for i, path in enumerate(self.image_paths):
            thumb_container = QWidget()
            thumb_layout = QVBoxLayout(thumb_container)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setSpacing(0)

            thumb_label = QLabel()
            # Создаем пустую миниатюру, она обновится при загрузке
            empty_pixmap = QPixmap(thumbnail_width, thumbnail_height)
            empty_pixmap.fill(Qt.darkGray)
            thumb_label.setPixmap(empty_pixmap)
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

        # Обновляем навигационные кнопки
        self._updateNavigationButtons()

    def _updateNavigationButtons(self):
        """Обновляет состояние навигационных кнопок"""
        if hasattr(self, 'prev_page_btn') and hasattr(self, 'next_page_btn'):
            self.prev_page_btn.setEnabled(self.viewer.current_page > 0)
            self.next_page_btn.setEnabled(self.viewer.current_page < len(self.viewer.pages) - 1)

    def ensureQualityCheckImages(self):
        """Этот метод больше не используется"""
        pass

    def _getQualityCheckImages(self):
        """Получает список изображений из папки контроля качества"""
        quality_check_img_dir = os.path.join(self.ch_paths["quality_check"], "Images")
        if os.path.isdir(quality_check_img_dir):
            return get_images_from_folder(quality_check_img_dir)
        return []

    def exportAsImages(self):
        """Экспортирует финальные изображения с наложенным текстом"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Экспорт изображений")

        try:
            # Открываем диалог выбора папки
            export_dir = QFileDialog.getExistingDirectory(self, "Выберите папку для экспорта",
                                                          self.ch_paths["quality_check"])

            if export_dir:
                # Создаем папку Final
                final_dir = os.path.join(export_dir, "Final")
                os.makedirs(final_dir, exist_ok=True)

                # Прогресс диалог
                from PySide6.QtWidgets import QProgressDialog
                progress = QProgressDialog("Экспорт изображений...", "Отмена", 0, len(self.image_paths), self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setWindowTitle("Экспорт")
                progress.show()

                exported_count = 0

                for i, path in enumerate(self.image_paths):
                    if progress.wasCanceled():
                        break

                    progress.setValue(i)
                    progress.setLabelText(f"Экспорт изображения {i + 1} из {len(self.image_paths)}...")
                    QApplication.processEvents()

                    filename = os.path.basename(path)
                    export_path = os.path.join(final_dir, filename)

                    # Экспортируем через viewer чтобы включить текстовые блоки
                    if self.viewer.exportPageWithText(i, export_path):
                        exported_count += 1

                progress.setValue(len(self.image_paths))
                progress.close()

                show_message(self, "Готово",
                             f"Экспорт завершен. Экспортировано {exported_count} файлов в папку:\n{final_dir}")

                # Открываем папку с результатами
                if sys.platform == "win32":
                    os.startfile(final_dir)
                elif sys.platform == "darwin":
                    os.system(f"open '{final_dir}'")
                else:
                    os.system(f"xdg-open '{final_dir}'")

        except Exception as e:
            logger.error(f"Ошибка при экспорте: {e}")
            show_message(self, "Ошибка", f"Произошла ошибка при экспорте: {str(e)}")

        self.unlockInterface()

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
            # Сохраняем изображение
            self.viewer.pixmaps[idx] = pixmap

            # Обновляем текущую страницу если это текущий индекс
            if self.viewer.current_page == idx:
                self.viewer.displayCurrentPage()

            # Обновляем миниатюру с учетом текстовых блоков
            if self.preview_scroll_area and 0 <= idx < len(self.thumbnail_labels):
                # Создаем миниатюру с текстом
                thumb_pixmap = self._createThumbnailWithText(pixmap, idx)
                tw = 150
                th = tw * 2
                scaled_pix = thumb_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumbnail_labels[idx].setPixmap(scaled_pix)

        # Отправляем событие об успешной загрузке
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

        # Обновляем навигационные кнопки
        self._updateNavigationButtons()

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
        # Сохраняем статус перед закрытием
        self.saveStatus()

        # Сигнализируем о запросе возврата и закрываем окно
        self.back_requested.emit()
        self.close()

    def initTopBar(self, main_layout):
        """Инициализация верхней панели с названием и кнопкой "Назад"."""
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 10, 20, 10)
        top_bar.setSpacing(10)

        # Заголовок
        title_label = QLabel("MangaLocalizer - Контроль качества")
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

        # Передаем данные тайпсеттинга если уже загружены
        if self.typesetting_data:
            self.viewer.setTypesettingData(self.typesetting_data)

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
                self.updatePageInfo()
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

        update_group.setLayout(update_layout)
        right_layout.addWidget(update_group)

        # Группа статуса обработки (горизонтально)
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
        status_layout = QHBoxLayout()  # Горизонтальный layout

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

        # Кнопка экспорта
        self.export_btn = QPushButton("Экспортировать финальные изображения")
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #2EA44F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #36CC57;
            }
        """)
        self.export_btn.clicked.connect(self.exportAsImages)
        right_layout.addWidget(self.export_btn)

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

    def onPreviousPage(self):
        """Обработчик кнопки 'Предыдущая'"""
        if self.viewer.previousPage():
            self.updateActiveThumbnail(self.viewer.current_page)
            self.updatePageInfo()

        self.prev_page_btn.setEnabled(self.viewer.current_page > 0)
        self.next_page_btn.setEnabled(self.viewer.current_page < len(self.viewer.pages) - 1)

    def onNextPage(self):
        """Обработчик кнопки 'Следующая'"""
        if self.viewer.nextPage():
            self.updateActiveThumbnail(self.viewer.current_page)
            self.updatePageInfo()

        self.prev_page_btn.setEnabled(self.viewer.current_page > 0)
        self.next_page_btn.setEnabled(self.viewer.current_page < len(self.viewer.pages) - 1)

    def updatePageInfo(self):
        """Обновляет информацию о текущей странице"""
        # Этот метод вызывается из обработчиков, но сама функциональность
        # может быть реализована позже при необходимости
        pass

    def onStatusChanged(self):
        """Обработчик изменения статуса обработки"""
        btn = self.status_group.checkedButton()
        if not btn:
            return

        status = btn.text()

        # Сохраняем статус в JSON файл модуля
        self.saveStatus()

        # Обновляем chapter.json
        ch_json_path = os.path.join(self.ch_folder, "chapter.json")
        if os.path.exists(ch_json_path):
            try:
                with open(ch_json_path, 'r', encoding='utf-8') as f:
                    ch_data = json.load(f)

                if "stages" in ch_data:
                    if status == "Не начат":
                        ch_data["stages"]["Контроль качества"] = False
                    elif status == "В работе":
                        ch_data["stages"]["Контроль качества"] = "partial"
                    elif status == "Завершен":
                        ch_data["stages"]["Контроль качества"] = True

                with open(ch_json_path, 'w', encoding='utf-8') as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)

                logger.info(f"Обновлен статус 'Контроль качества' на '{status}' в chapter.json")

            except Exception as e:
                logger.error(f"Ошибка при обновлении chapter.json: {e}")

    def saveStatus(self):
        """Сохранение статуса обработки"""
        c_folder = self.ch_paths["quality_check"]
        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)

        # Определяем текущий статус
        status = "Не начат"
        if self.status_in_prog.isChecked():
            status = "В работе"
        elif self.status_done.isChecked():
            status = "Завершен"

        data = {
            "status": status,
            "images_count": len(self.image_paths),
            "last_updated": datetime.datetime.now().isoformat()
        }

        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            logger.info(f"Сохранен статус контроля качества: {status}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении статуса: {e}")

    def loadStatus(self):
        """Загрузка статуса обработки"""
        c_folder = self.ch_paths["quality_check"]
        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        if os.path.exists(json_path):
            try:
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

                logger.info(f"Загружен статус контроля качества: {st}")

            except Exception as e:
                logger.error(f"Ошибка при загрузке статуса: {e}")

    def lockInterface(self, operation):
        """Блокировка интерфейса при длительных операциях"""
        self.is_proc = True
        self.curr_op = operation

        self.refresh_btn.setEnabled(False)
        self.export_btn.setEnabled(False)

        QApplication.processEvents()

    def unlockInterface(self):
        """Разблокировка интерфейса после операций"""
        self.is_proc = False
        self.curr_op = None

        self.refresh_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

        QApplication.processEvents()

    def _createThumbnailWithText(self, pixmap, page_index):
        """Создает миниатюру с наложенным текстом"""
        if not self.typesetting_data or "text_blocks" not in self.typesetting_data:
            return pixmap

        # Создаем копию для отрисовки
        result = QPixmap(pixmap.size())
        result.fill(Qt.white)

        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, pixmap)

        # Здесь можно добавить упрощенную отрисовку текстовых блоков для миниатюр
        # Но для производительности лучше показывать только изображение в миниатюрах

        painter.end()
        return pixmap  # Возвращаем оригинал для миниатюр

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
        # Сохраняем статус перед закрытием
        self.saveStatus()

        # Останавливаем загрузку изображений
        if hasattr(self, 'image_loader'):
            self.image_loader.cancel()

        # Закрываем пул потоков
        if hasattr(self, 'thread_pool'):
            self.thread_pool.shutdown(wait=False)

        super().closeEvent(event)