# -*- coding: utf-8 -*-
# ui/windows/m9_0_typesetting.py

import os
import sys
import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import (Qt, Signal, QThreadPool, QTimer, QPointF, QMetaObject, Q_ARG,QSizeF,QRectF)
from PySide6.QtGui import (QPixmap, QFont, QColor, QBrush, QCursor,QPen)
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSpacerItem, QSizePolicy, QRadioButton,
                               QButtonGroup, QSlider, QLineEdit, QMessageBox, QComboBox,
                               QCheckBox, QWidget, QGroupBox, QApplication, QToolButton,
                               QColorDialog, QSpinBox, QProgressBar, QFileDialog, QGraphicsItem,
                               QProgressDialog,QStyleOptionGraphicsItem)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QStyle
# Импортируем наши модули
from ui.components.gradient_widget import GradientBackgroundWidget
from ui.windows.m9_1_image_viewer import (ImageViewer, TextBlockModifiedSignal,
                                          TextBlockItem)
from ui.windows.m9_2_utils import (get_images_from_folder, show_message, PageChangeSignal)
from ui.windows.m9_3_ui_components import (ImageLoader, LoadingOverlay,
                                           ImageLoadedEvent, AllImagesLoadedEvent)
from ui.windows.m9_4_text_block import AVAILABLE_FONTS

# Импортируем классы из модуля перевода для отображения заметок
from ui.windows.m7_1_image_viewer import (NoteItem, LinkLineItem, AnchorPointItem,
                                          MovableRectItem, NotesModifiedSignal)

# Настройка логгера
# Настройка детального логгирования для отладки
logging.basicConfig(
    level=logging.DEBUG,  # Установим уровень DEBUG для более подробных сообщений
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('typesetting_debug.log')  # Запись в файл для анализа
    ]
)
logger = logging.getLogger(__name__)


class TypesettingWindow(QDialog):
    """
    Окно "Тайпсеттинг".
    Основная функциональность:
    - Загрузка и отображение изображений из модулей очистки и перевода
    - Работа с текстовыми блоками на изображении
    - Поддержка разных шрифтов, размеров и стилей
    - Копирование текста из заметок перевода
    """
    back_requested = Signal()

    def __init__(self, chapter_folder, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("typesetting_window")

        # Сохраняем пути
        self.ch_folder = chapter_folder
        self.paths = paths or {}
        self.status_json_filename = "typesetting.json"

        # Определяем базовые папки
        self.ch_paths = {
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
        self.setWindowTitle("Тайпсеттинг")
        self.resize(1920, 1080)

        # Флаги состояния
        self.is_proc = False
        self.curr_op = None
        self.is_loading_complete = False
        self.show_cleaned = True

        # Определяем источник изображений с диалогом выбора
        self.image_paths = self._decide_img_source()
        if not self.image_paths:
            # Если пользователь отменил выбор, планируем закрытие
            QTimer.singleShot(100, self._handleCancelledInit)
            return

        # Создаем градиентный фон только после успешного выбора источника
        self.bg_widget = GradientBackgroundWidget(self)
        self.bg_widget.setObjectName("bg_widget")

        # Основной макет
        main_layout = QVBoxLayout(self.bg_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Сигнал изменения страницы
        self.page_change_signal = PageChangeSignal()

        self.translation_json = self._getTranslationData()

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

        # Инициализируем настройки текстовых блоков по умолчанию
        self.viewer.text_settings.update({
            "font_family": "Arial",
            "font_size": 16,
            "text_color": "#FFFFFF",
            "outline_color": "#000000",
            "outline_width": 2,
            "background_color": "transparent",
            "alignment": Qt.AlignCenter,
            "bold": False,
            "italic": False,
        })

        # Загружаем статус
        self.loadStatus()

        # Подсвечиваем первую картинку
        self.updateActiveThumbnail(0)

        # Показываем загрузочный экран и начинаем загрузку изображений
        QTimer.singleShot(100, self.startLoading)

    def _handleCancelledInit(self):
        """Обработчик отмены инициализации"""
        # Не вызываем close() напрямую, а планируем закрытие через событие
        QTimer.singleShot(0, lambda: self.back_requested.emit())

    def _decide_img_source(self):
        """Определение источника изображений с диалогом выбора"""
        typesetting_folder = os.path.join(self.ch_paths["typesetting"], "Images")
        cleaned_folder = os.path.join(self.ch_paths["typesetting"], "Cleaned")
        os.makedirs(typesetting_folder, exist_ok=True)
        os.makedirs(cleaned_folder, exist_ok=True)

        # Проверяем наличие сохраненной конфигурации
        config_path = os.path.join(typesetting_folder, "image_source_config.json")

        # Если в папке тайпсеттинга уже есть изображения - используем их
        existing_images = get_images_from_folder(typesetting_folder)
        if existing_images:
            logger.info(f"Найдено {len(existing_images)} изображений в папке Тайпсеттинг")

            # Автоматически проверяем и копируем очищенные если их еще нет
            self._auto_copy_cleaned_images()

            return existing_images

        # Проверяем доступные источники для ОРИГИНАЛОВ
        sources = {}

        # Перевод/Images (приоритет для оригиналов)
        translation_images_folder = os.path.join(self.ch_paths["translation"], "Images")
        if os.path.exists(translation_images_folder):
            translation_images = get_images_from_folder(translation_images_folder)
            if translation_images:
                sources["translation"] = {
                    "path": translation_images_folder,
                    "images": translation_images,
                    "name": "Перевод",
                    "count": len(translation_images)
                }

        # Предобработка/Save
        preproc_save_folder = os.path.join(self.ch_paths["preproc"], "Save")
        if os.path.exists(preproc_save_folder):
            preproc_save_images = get_images_from_folder(preproc_save_folder)
            if preproc_save_images:
                sources["preprocess_save"] = {
                    "path": preproc_save_folder,
                    "images": preproc_save_images,
                    "name": "Предобработка (Save)",
                    "count": len(preproc_save_images)
                }

        # Загрузка
        upload_images = get_images_from_folder(self.ch_paths["upload"])
        if upload_images:
            sources["upload"] = {
                "path": self.ch_paths["upload"],
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
                        # Копируем изображения в папку тайпсеттинга
                        self._copy_images_to_typesetting(sources[saved_source]["images"])

                        # Автоматически копируем очищенные
                        self._auto_copy_cleaned_images()

                        return get_images_from_folder(typesetting_folder)
            except:
                pass

        # Показываем диалог выбора источника ДЛЯ ОРИГИНАЛОВ
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника оригинальных изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(450)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник ОРИГИНАЛЬНЫХ изображений для тайпсеттинга:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        info_label = QLabel("(Очищенные изображения будут загружены автоматически из модуля Клининг)")
        info_label.setStyleSheet("font-size: 12px; color: #AAA; font-style: italic;")
        layout.addWidget(info_label)

        button_group = QButtonGroup()
        selected_source = None

        # Приоритет: перевод -> предобработка -> загрузка
        priority_order = ["translation", "preprocess_save", "upload"]

        for key in priority_order:
            if key in sources:
                source_info = sources[key]
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

            # Копируем оригинальные изображения
            self._copy_images_to_typesetting(sources[selected]["images"])

            # Автоматически копируем очищенные изображения
            self._auto_copy_cleaned_images()

            return get_images_from_folder(typesetting_folder)

        # Если пользователь отменил выбор
        return []

    def _auto_copy_cleaned_images(self):
        """Автоматически копирует очищенные изображения из клининга"""
        cleaned_folder = os.path.join(self.ch_paths["typesetting"], "Cleaned")

        # Проверяем возможные источники очищенных изображений
        cleaning_sources = [
            os.path.join(self.ch_paths["cleaning"], "Save"),
            os.path.join(self.ch_paths["cleaning"], "Results"),
            os.path.join(self.ch_paths["cleaning"])
        ]

        cleaned_images = []
        for source in cleaning_sources:
            if os.path.exists(source):
                images = get_images_from_folder(source)
                if images:
                    cleaned_images = images
                    logger.info(f"Найдено {len(images)} очищенных изображений в {source}")
                    break

        if cleaned_images:
            # Очищаем папку Cleaned
            for file in os.listdir(cleaned_folder):
                file_path = os.path.join(cleaned_folder, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)

            # Копируем очищенные изображения
            for src_path in cleaned_images:
                filename = os.path.basename(src_path)
                dst_path = os.path.join(cleaned_folder, filename)
                try:
                    shutil.copy2(src_path, dst_path)
                    logger.info(f"Скопирован очищенный файл: {filename}")
                except Exception as e:
                    logger.error(f"Ошибка копирования очищенного файла {filename}: {str(e)}")

            self.show_cleaned = True
        else:
            logger.info("Очищенные изображения не найдены в модуле Клининг")
            self.show_cleaned = False
    def _copy_images_to_typesetting(self, source_images):
        """Копирует изображения в папку тайпсеттинга с сохранением имен"""
        typesetting_folder = os.path.join(self.ch_paths["typesetting"], "Images")
        os.makedirs(typesetting_folder, exist_ok=True)

        for i, src_path in enumerate(source_images):
            try:
                # Сохраняем оригинальное имя файла
                filename = os.path.basename(src_path)
                dst_path = os.path.join(typesetting_folder, filename)

                # Если файл уже существует, добавляем номер
                if os.path.exists(dst_path):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(dst_path):
                        dst_path = os.path.join(typesetting_folder, f"{base}_{counter}{ext}")
                        counter += 1

                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован файл: {filename}")

            except Exception as e:
                logger.error(f"Ошибка копирования {src_path}: {str(e)}")

    def _copy_cleaned_images(self, source_images):
        """Копирует очищенные изображения из клининга в папку Cleaned"""
        cleaned_folder = os.path.join(self.ch_paths["typesetting"], "Cleaned")
        os.makedirs(cleaned_folder, exist_ok=True)

        # Очищаем папку
        for file in os.listdir(cleaned_folder):
            file_path = os.path.join(cleaned_folder, file)
            if os.path.isfile(file_path):
                os.remove(file_path)

        for i, src_path in enumerate(source_images):
            try:
                filename = os.path.basename(src_path)
                dst_path = os.path.join(cleaned_folder, filename)
                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован очищенный файл: {filename}")

            except Exception as e:
                logger.error(f"Ошибка копирования очищенного {src_path}: {str(e)}")

    def _copy_cleaned_images_to_typesetting(self, source_images):
        """Копирует очищенные изображения в папку Cleaned"""
        cleaned_folder = os.path.join(self.ch_paths["typesetting"], "Cleaned")
        os.makedirs(cleaned_folder, exist_ok=True)

        for i, src_path in enumerate(source_images):
            try:
                filename = os.path.basename(src_path)
                dst_path = os.path.join(cleaned_folder, filename)

                if os.path.exists(dst_path):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(dst_path):
                        dst_path = os.path.join(cleaned_folder, f"{base}_{counter}{ext}")
                        counter += 1

                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован очищенный файл: {filename}")

            except Exception as e:
                logger.error(f"Ошибка копирования очищенного {src_path}: {str(e)}")

    def ensureTypesettingImages(self):
        """Копирует изображения из очистки или перевода в папку тайпсеттинга"""
        typesetting_img_dir = os.path.join(self.ch_paths["typesetting"], "Images")
        cleaned_img_dir = os.path.join(self.ch_paths["typesetting"], "Cleaned")

        os.makedirs(typesetting_img_dir, exist_ok=True)
        os.makedirs(cleaned_img_dir, exist_ok=True)

        # Если в папке тайпсеттинга уже есть изображения, не копируем
        existing_images = get_images_from_folder(typesetting_img_dir)
        if existing_images:
            logger.info(f"В папке тайпсеттинга уже есть {len(existing_images)} изображений")
            return

        # Приоритет источников
        source_folders = [
            os.path.join(self.ch_paths["cleaning"], "Results"),
            os.path.join(self.ch_paths["cleaning"], "Pages"),
            os.path.join(self.ch_paths["translation"], "Images"),
            os.path.join(self.ch_paths["preproc"]),
            os.path.join(self.ch_paths["upload"], "originals")
        ]

        # Ищем папку с изображениями
        source_images = []
        source_folder = None

        for folder in source_folders:
            if os.path.isdir(folder):
                images = get_images_from_folder(folder)
                if images:
                    source_images = images
                    source_folder = folder
                    logger.info(f"Найдено {len(images)} изображений в {folder}")
                    break

        if not source_images:
            logger.warning("Не найдены изображения для копирования в папку тайпсеттинга")
            return

        # Копируем основные изображения с правильными именами
        for idx, img_path in enumerate(sorted(source_images)):
            img_name = os.path.basename(img_path)

            # Если это из папки клининга, используем индекс для имени
            if "cleaning" in source_folder.lower() and img_name.startswith(
                    ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')):
                # Сохраняем с индексом
                dest_name = f"{idx:04d}.png"
            else:
                dest_name = img_name

            dest_path = os.path.join(typesetting_img_dir, dest_name)
            shutil.copy2(img_path, dest_path)
            logger.info(f"Скопирован файл {img_name} -> {dest_name}")

        # Копируем очищенные изображения
        cleaned_folders = [
            os.path.join(self.ch_paths["cleaning"], "Results"),
            os.path.join(self.ch_paths["cleaning"], "Pages")
        ]

        for folder in cleaned_folders:
            if os.path.isdir(folder):
                images = get_images_from_folder(folder)
                if images:
                    logger.info(f"Копирую {len(images)} очищенных изображений из {folder}")
                    for idx, img_path in enumerate(sorted(images)):
                        img_name = os.path.basename(img_path)

                        # Используем индекс для имени
                        if img_name.startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')):
                            dest_name = f"{idx:04d}.png"
                        else:
                            dest_name = img_name

                        dest_path = os.path.join(cleaned_img_dir, dest_name)
                        shutil.copy2(img_path, dest_path)
                    break

    def _getTypesettingImages(self):
        """Получает список изображений из папки тайпсеттинга"""
        typesetting_img_dir = os.path.join(self.ch_paths["typesetting"], "Images")
        if os.path.isdir(typesetting_img_dir):
            return get_images_from_folder(typesetting_img_dir)
        return []

    def _getCleanedImages(self):
        """Получает список очищенных изображений"""
        cleaned_img_dir = os.path.join(self.ch_paths["typesetting"], "Cleaned")
        if os.path.isdir(cleaned_img_dir):
            return get_images_from_folder(cleaned_img_dir)
        return []

    def _getTranslationData(self):
        """Получает данные о заметках перевода"""
        translation_json_path = os.path.join(self.ch_paths["translation"], "translation.json")
        if os.path.isfile(translation_json_path):
            try:
                with open(translation_json_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка при чтении файла перевода: {e}")
        return {}

    def refreshImages(self):
        """Обновляет изображения из источников с диалогом выбора"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Обновление изображений")

        # Показываем диалог выбора типа обновления
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        type_dialog = QDialog(self)
        type_dialog.setWindowTitle("Тип обновления")
        type_dialog.setModal(True)
        type_dialog.setFixedWidth(350)

        layout = QVBoxLayout(type_dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Что вы хотите обновить?")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        type_group = QButtonGroup()

        update_original = QRadioButton("Обновить оригиналы")
        update_cleaned = QRadioButton("Обновить очищенные")

        update_original.setChecked(True)
        type_dialog.update_type = "original"

        type_group.addButton(update_original)
        type_group.addButton(update_cleaned)

        update_original.toggled.connect(
            lambda checked: setattr(type_dialog, 'update_type', 'original' if checked else None))
        update_cleaned.toggled.connect(
            lambda checked: setattr(type_dialog, 'update_type', 'cleaned' if checked else None))

        layout.addWidget(update_original)
        layout.addWidget(update_cleaned)

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("Далее")
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
        ok_btn.clicked.connect(type_dialog.accept)

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
        cancel_btn.clicked.connect(type_dialog.reject)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if type_dialog.exec() != QDialog.Accepted:
            self.unlockInterface()
            return

        update_type = type_dialog.update_type

        # Определяем папки
        typesetting_img_dir = os.path.join(self.ch_paths["typesetting"], "Images")
        cleaned_img_dir = os.path.join(self.ch_paths["typesetting"], "Cleaned")

        # Проверяем доступные источники в зависимости от типа обновления
        sources = {}

        if update_type == "original":
            # Источники для оригиналов
            # Перевод/Images
            translation_images_folder = os.path.join(self.ch_paths["translation"], "Images")
            if os.path.exists(translation_images_folder):
                translation_images = get_images_from_folder(translation_images_folder)
                if translation_images:
                    sources["translation"] = {
                        "images": translation_images,
                        "name": "Перевод",
                        "type": "original"
                    }

            # Предобработка/Save
            preproc_save_folder = os.path.join(self.ch_paths["preproc"], "Save")
            if os.path.exists(preproc_save_folder):
                preproc_save_images = get_images_from_folder(preproc_save_folder)
                if preproc_save_images:
                    sources["preprocess_save"] = {
                        "images": preproc_save_images,
                        "name": "Предобработка (Save)",
                        "type": "original"
                    }

            # Загрузка
            upload_images = get_images_from_folder(self.ch_paths["upload"])
            if upload_images:
                sources["upload"] = {
                    "images": upload_images,
                    "name": "Загрузка",
                    "type": "original"
                }

        elif update_type == "cleaned":
            # Источники для очищенных
            cleaning_save_folder = os.path.join(self.ch_paths["cleaning"], "Save")
            if not os.path.exists(cleaning_save_folder):
                cleaning_save_folder = os.path.join(self.ch_paths["cleaning"])

            if os.path.exists(cleaning_save_folder):
                cleaning_save_images = get_images_from_folder(cleaning_save_folder)
                if cleaning_save_images:
                    sources["cleaning_save"] = {
                        "images": cleaning_save_images,
                        "name": "Клининг (Очищенные)",
                        "type": "cleaned"
                    }

        if not sources:
            show_message(self, "Предупреждение", "Не найдены источники для обновления")
            self.unlockInterface()
            return

        # Показываем диалог выбора источника
        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника для обновления")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник для обновления:")
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
        selected_source = sources[dialog.selected_source]
        source_images = selected_source["images"]

        # Сохраняем текущую страницу
        current_page_backup = self.viewer.current_page

        # Определение страниц для обработки
        if self.update_all_cb.isChecked():
            # Обновляем все изображения
            if update_type == "original":
                # Обновляем только оригиналы
                for file in os.listdir(typesetting_img_dir):
                    file_path = os.path.join(typesetting_img_dir, file)
                    if os.path.isfile(file_path):
                        os.remove(file_path)

                self._copy_images_to_typesetting(source_images)

            elif update_type == "cleaned":
                # Обновляем только очищенные
                for file in os.listdir(cleaned_img_dir):
                    file_path = os.path.join(cleaned_img_dir, file)
                    if os.path.isfile(file_path):
                        os.remove(file_path)

                self._copy_cleaned_images(source_images)

            # Обновляем список изображений
            self.image_paths = get_images_from_folder(typesetting_img_dir)

            # Обновляем viewer
            self.viewer.pages = self.image_paths
            self.viewer.pixmaps = [QPixmap() for _ in self.image_paths]
            self.viewer.cleaned_pixmaps = [QPixmap() for _ in self.image_paths]

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
                if selected_source["type"] == "original":
                    # Обновляем оригинал
                    dst_path = os.path.join(typesetting_img_dir, current_filename)
                    shutil.copy2(source_path, dst_path)

                    # Обновляем путь
                    self.image_paths[current_page] = dst_path

                    # Обновляем изображение
                    pm = QPixmap(dst_path)
                    if not pm.isNull():
                        self.viewer.pixmaps[current_page] = pm

                elif selected_source["type"] == "cleaned":
                    # Обновляем очищенное
                    dst_path = os.path.join(cleaned_img_dir, current_filename)
                    shutil.copy2(source_path, dst_path)

                    # Обновляем изображение
                    pm = QPixmap(dst_path)
                    if not pm.isNull():
                        self.viewer.cleaned_pixmaps[current_page] = pm

                self.viewer.displayCurrentPage()

                # Обновляем миниатюру
                if 0 <= current_page < len(self.thumbnail_labels):
                    display_pm = self.viewer.cleaned_pixmaps[current_page] if self.show_cleaned and not \
                        self.viewer.cleaned_pixmaps[current_page].isNull() else self.viewer.pixmaps[current_page]
                    tw = 150
                    th = tw * 2
                    scaled_pix = display_pm.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.thumbnail_labels[current_page].setPixmap(scaled_pix)

            self.unlockInterface()

    def exportAsImages(self):
        """Экспортирует изображения с текстовыми блоками"""
        if self.is_proc:
            show_message(self, "Предупреждение", "Дождитесь завершения текущей операции")
            return

        self.lockInterface("Экспорт изображений")

        try:
            # Получаем название главы из пути
            chapter_name = os.path.basename(self.ch_folder)

            # Диалог выбора папки для сохранения
            export_base_dir = QFileDialog.getExistingDirectory(
                self,
                "Выберите папку для экспорта",
                self.ch_paths["typesetting"],
                QFileDialog.ShowDirsOnly
            )

            if not export_base_dir:
                self.unlockInterface()
                return

            # Создаем папку с названием главы
            export_dir = os.path.join(export_base_dir, chapter_name)

            # Если папка существует, спрашиваем о перезаписи
            if os.path.exists(export_dir):
                reply = QMessageBox.question(
                    self,
                    "Папка существует",
                    f"Папка '{chapter_name}' уже существует.\n\nПерезаписать содержимое?",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
                )

                if reply == QMessageBox.Cancel:
                    self.unlockInterface()
                    return
                elif reply == QMessageBox.No:
                    # Добавляем суффикс к папке
                    counter = 1
                    while os.path.exists(f"{export_dir}_{counter}"):
                        counter += 1
                    export_dir = f"{export_dir}_{counter}"

            # Создаем папку для экспорта
            os.makedirs(export_dir, exist_ok=True)

            # Экспортируем все страницы
            progress = QProgressDialog("Экспорт изображений...", "Отмена", 0, len(self.image_paths), self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setWindowTitle("Экспорт")
            progress.show()

            exported_count = 0
            errors = []

            for i in range(len(self.image_paths)):
                if progress.wasCanceled():
                    break

                progress.setValue(i)
                progress.setLabelText(f"Экспорт изображения {i + 1} из {len(self.image_paths)}...")
                QApplication.processEvents()

                try:
                    # Используем оригинальное имя файла
                    filename = os.path.basename(self.image_paths[i])
                    # Меняем расширение на .png для качества
                    filename = os.path.splitext(filename)[0] + ".png"
                    export_path = os.path.join(export_dir, filename)

                    # Автоматически выбираем очищенное если есть, иначе оригинал
                    base_pixmap = None
                    if (self.viewer.cleaned_pixmaps and
                            i < len(self.viewer.cleaned_pixmaps) and
                            not self.viewer.cleaned_pixmaps[i].isNull()):
                        base_pixmap = self.viewer.cleaned_pixmaps[i].copy()
                    elif i < len(self.viewer.pixmaps) and not self.viewer.pixmaps[i].isNull():
                        base_pixmap = self.viewer.pixmaps[i].copy()

                    if not base_pixmap or base_pixmap.isNull():
                        errors.append(f"Пустое изображение: {filename}")
                        continue

                    # Создаем новое изображение того же размера
                    result = QPixmap(base_pixmap.size())
                    result.fill(Qt.white)  # Заполняем белым вместо прозрачного

                    # Создаем painter и рисуем
                    painter = QPainter()
                    if not painter.begin(result):
                        errors.append(f"Не удалось начать рисование: {filename}")
                        continue

                    try:
                        painter.setRenderHint(QPainter.Antialiasing, True)
                        painter.setRenderHint(QPainter.TextAntialiasing, True)
                        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

                        # Рисуем базовое изображение
                        painter.drawPixmap(0, 0, base_pixmap)

                        # Рисуем текстовые блоки для этой страницы
                        text_blocks_for_page = [block for block in self.viewer.text_blocks if block.page_index == i]

                        for block in text_blocks_for_page:
                            if block and block.toPlainText().strip():  # Проверяем что блок не пустой
                                painter.save()

                                # Получаем позицию блока
                                pos = block.pos()
                                painter.translate(pos.x(), pos.y())

                                # Создаем временную опцию стиля для отрисовки
                                style_option = QStyleOptionGraphicsItem()
                                style_option.state = QStyle.State_None

                                # Рисуем блок
                                try:
                                    block.paint(painter, style_option, None)
                                except Exception as e:
                                    logger.warning(f"Ошибка при отрисовке текстового блока: {e}")

                                painter.restore()

                    finally:
                        painter.end()

                    # Сохраняем результат
                    if not result.save(export_path, "PNG", 100):
                        errors.append(f"Не удалось сохранить: {filename}")
                    else:
                        exported_count += 1

                except Exception as e:
                    logger.error(f"Ошибка при экспорте {filename}: {e}")
                    errors.append(f"{filename}: {str(e)}")

            progress.setValue(len(self.image_paths))
            progress.close()

            # Показываем результат
            if errors:
                error_msg = "\n".join(errors[:10])  # Показываем первые 10 ошибок
                if len(errors) > 10:
                    error_msg += f"\n... и еще {len(errors) - 10} ошибок"

                QMessageBox.warning(
                    self,
                    "Экспорт завершен с ошибками",
                    f"Экспортировано {exported_count} из {len(self.image_paths)} изображений.\n\n"
                    f"Ошибки:\n{error_msg}\n\n"
                    f"Файлы сохранены в:\n{export_dir}"
                )
            else:
                QMessageBox.information(
                    self,
                    "Экспорт завершен",
                    f"Успешно экспортировано {exported_count} изображений!\n\n"
                    f"Файлы сохранены в:\n{export_dir}"
                )

            # Открываем папку с результатами
            if exported_count > 0:
                if sys.platform == "win32":
                    os.startfile(export_dir)
                elif sys.platform == "darwin":
                    os.system(f"open '{export_dir}'")
                else:
                    os.system(f"xdg-open '{export_dir}'")

        except Exception as e:
            logger.error(f"Критическая ошибка при экспорте: {e}")
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Произошла критическая ошибка при экспорте:\n{str(e)}"
            )

        finally:
            self.unlockInterface()

    def startLoading(self):
        """Начало загрузки изображений с блокировкой интерфейса"""
        # Загружаем пути к очищенным изображениям
        self.cleaned_image_paths = self._getCleanedImages()

        # Проверяем существование очищенных изображений
        if self.cleaned_image_paths:
            logger.info(f"Найдено {len(self.cleaned_image_paths)} очищенных изображений")
        else:
            logger.info("Очищенные изображения не найдены")
            self.show_cleaned = False

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
            # Сохраняем основное изображение
            self.viewer.pixmaps[idx] = pixmap

            # Проверяем наличие очищенного изображения
            if self.cleaned_image_paths and idx < len(self.cleaned_image_paths):
                cleaned_path = self.cleaned_image_paths[idx]
                cleaned_pm = QPixmap(cleaned_path)
                if not cleaned_pm.isNull():
                    # Сохраняем очищенное изображение
                    self.viewer.cleaned_pixmaps[idx] = cleaned_pm
                    logger.info(f"Загружено очищенное изображение: {cleaned_path}")

            # Обновляем текущую страницу если это текущий индекс
            if self.viewer.current_page == idx:
                self.viewer.displayCurrentPage()

            # Обновляем миниатюру
            if self.preview_scroll_area and 0 <= idx < len(self.thumbnail_labels):
                tw = 150
                th = tw * 2
                scaled_pix = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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

        # Активируем переключение на очищенные изображения если они есть
        if self.cleaned_image_paths:
            self.cleaned_radio.setEnabled(True)
            # Показываем очищенные изображения по умолчанию
            if self.show_cleaned:
                self.cleaned_radio.setChecked(True)
                self.viewer.set_cleaned(True)
        else:
            self.cleaned_radio.setEnabled(False)
            self.original_radio.setChecked(True)
            self.viewer.set_cleaned(False)
            self.show_cleaned = False

        # Скрываем окно загрузки
        QTimer.singleShot(500, self._finishLoadingCleanup)

        # Загружаем заметки перевода
        self.loadTranslationNotes()

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

        # Группа типа изображения
        image_type_group = QGroupBox("Тип изображения")
        image_type_group.setStyleSheet("""
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
        image_type_layout = QHBoxLayout()

        self.image_type_group = QButtonGroup(self)
        self.original_radio = QRadioButton("Оригинал")
        self.cleaned_radio = QRadioButton("Очищенное")

        self.original_radio.setStyleSheet("color: white;")
        self.cleaned_radio.setStyleSheet("color: white;")

        # По умолчанию показываем очищенное, если есть
        self.cleaned_radio.setChecked(True)
        self.cleaned_radio.setEnabled(False)  # Активируем только если найдены очищенные
        self.image_type_group.addButton(self.original_radio)
        self.image_type_group.addButton(self.cleaned_radio)

        self.original_radio.toggled.connect(self.onImageTypeChanged)
        self.cleaned_radio.toggled.connect(self.onImageTypeChanged)

        image_type_layout.addWidget(self.original_radio)
        image_type_layout.addWidget(self.cleaned_radio)
        image_type_group.setLayout(image_type_layout)
        right_layout.addWidget(image_type_group)

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

        update_group.setLayout(update_layout)
        right_layout.addWidget(update_group)

        # Панель настроек текстовых блоков
        self.initTextBlockSettingsPanel(right_layout)

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

        # Кнопка экспорта
        self.export_btn = QPushButton("Экспортировать изображения")
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

    def initTextBlockSettingsPanel(self, parent_layout):
        """Инициализация панели настроек текстовых блоков"""
        text_group = QGroupBox("Настройки текста")
        text_group.setStyleSheet("""
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
        text_layout = QVBoxLayout()
        text_layout.setSpacing(8)

        # Выбор шрифта
        font_layout = QHBoxLayout()
        font_label = QLabel("Шрифт:")
        font_label.setStyleSheet("color: white;")
        font_layout.addWidget(font_label)

        self.font_combo = QComboBox()
        for font_family in AVAILABLE_FONTS:
            self.font_combo.addItem(font_family)
        self.font_combo.setCurrentText("Arial")
        self.font_combo.setStyleSheet("""
            QComboBox {
                background-color: #4E4E6F;
                color: white;
                border-radius: 4px;
                padding: 2px;
                min-height: 20px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #4E4E6F;
                color: white;
                selection-background-color: #7E1E9F;
                selection-color: white;
                border: 1px solid #7E1E9F;
            }
        """)
        self.font_combo.currentTextChanged.connect(self.updateTextSettings)
        font_layout.addWidget(self.font_combo)
        text_layout.addLayout(font_layout)

        # Размер шрифта
        size_layout = QHBoxLayout()
        size_label = QLabel("Размер:")
        size_label.setStyleSheet("color: white;")
        size_layout.addWidget(size_label)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 72)
        self.font_size_spin.setValue(16)
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
        self.font_size_spin.valueChanged.connect(self.updateTextSettings)
        size_layout.addWidget(self.font_size_spin)
        text_layout.addLayout(size_layout)

        # Стили шрифта
        style_layout = QHBoxLayout()
        self.bold_checkbox = QCheckBox("Жирный")
        self.bold_checkbox.setStyleSheet("color: white;")
        self.bold_checkbox.stateChanged.connect(self.updateTextSettings)

        self.italic_checkbox = QCheckBox("Курсив")
        self.italic_checkbox.setStyleSheet("color: white;")
        self.italic_checkbox.stateChanged.connect(self.updateTextSettings)

        style_layout.addWidget(self.bold_checkbox)
        style_layout.addWidget(self.italic_checkbox)
        text_layout.addLayout(style_layout)

        # Цвет текста
        text_color_layout = QHBoxLayout()
        text_color_label = QLabel("Цвет текста:")
        text_color_label.setStyleSheet("color: white;")
        text_color_layout.addWidget(text_color_label)

        self.text_color_button = QToolButton()
        self.text_color_button.setStyleSheet("""
            QToolButton {
                background-color: #FFFFFF;
                border: 1px solid #666;
                border-radius: 3px;
            }
        """)
        self.text_color_button.setFixedSize(30, 24)
        self.text_color_button.setCursor(Qt.PointingHandCursor)
        self.text_color_button.clicked.connect(lambda: self.pickColor("text_color"))
        text_color_layout.addWidget(self.text_color_button)
        text_color_layout.addStretch()
        text_layout.addLayout(text_color_layout)

        # Цвет обводки
        outline_layout = QHBoxLayout()
        outline_color_label = QLabel("Цвет обводки:")
        outline_color_label.setStyleSheet("color: white;")
        outline_layout.addWidget(outline_color_label)

        self.outline_color_button = QToolButton()
        self.outline_color_button.setStyleSheet("""
            QToolButton {
                background-color: #000000;
                border: 1px solid #666;
                border-radius: 3px;
            }
        """)
        self.outline_color_button.setFixedSize(30, 24)
        self.outline_color_button.setCursor(Qt.PointingHandCursor)
        self.outline_color_button.clicked.connect(lambda: self.pickColor("outline_color"))
        outline_layout.addWidget(self.outline_color_button)

        outline_width_label = QLabel("Ширина:")
        outline_width_label.setStyleSheet("color: white;")
        outline_layout.addWidget(outline_width_label)

        self.outline_width_spin = QSpinBox()
        self.outline_width_spin.setRange(0, 10)
        self.outline_width_spin.setValue(2)
        self.outline_width_spin.setSingleStep(1)
        self.outline_width_spin.setStyleSheet("""
            QSpinBox {
                color: white;
                background-color: #4E4E6F;
                border-radius: 4px;
                padding: 2px;
                max-width: 50px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px;
                border-radius: 3px;
                background-color: #7E1E9F;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #9E3EBF;
            }
        """)
        self.outline_width_spin.valueChanged.connect(self.updateTextSettings)
        outline_layout.addWidget(self.outline_width_spin)
        outline_layout.addStretch()
        text_layout.addLayout(outline_layout)

        # Выравнивание текста
        align_layout = QHBoxLayout()
        align_label = QLabel("Выравнивание:")
        align_label.setStyleSheet("color: white;")
        align_layout.addWidget(align_label)

        self.align_group = QButtonGroup(self)
        self.align_left = QRadioButton("Л")
        self.align_center = QRadioButton("Ц")
        self.align_right = QRadioButton("П")

        self.align_left.setStyleSheet("color: white;")
        self.align_center.setStyleSheet("color: white;")
        self.align_right.setStyleSheet("color: white;")

        self.align_center.setChecked(True)
        self.align_group.addButton(self.align_left, Qt.AlignLeft)
        self.align_group.addButton(self.align_center, Qt.AlignCenter)
        self.align_group.addButton(self.align_right, Qt.AlignRight)

        align_layout.addWidget(self.align_left)
        align_layout.addWidget(self.align_center)
        align_layout.addWidget(self.align_right)

        self.align_group.buttonClicked.connect(self.onAlignmentChanged)
        text_layout.addLayout(align_layout)

        # Кнопка добавления текстового блока
        self.add_text_block_btn = QPushButton("Добавить текстовый блок")
        self.add_text_block_btn.setStyleSheet("""
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
        self.add_text_block_btn.clicked.connect(self.addTextBlock)
        text_layout.addWidget(self.add_text_block_btn)

        text_group.setLayout(text_layout)
        parent_layout.addWidget(text_group)

    def toggleTransparentBackground(self, state):
        """Переключение прозрачности фона"""
        if state == Qt.Checked:
            # Прозрачный фон включен
            self.bg_color_button.setStyleSheet("""
                QToolButton {
                    background-color: rgba(255, 255, 255, 0);
                    border: 1px dashed #666;
                    border-radius: 3px;
                }
            """)
            self.viewer.text_settings["background_color"] = "transparent"
            self.bg_color_button.setEnabled(False)
        else:
            # Прозрачный фон выключен
            self.bg_color_button.setEnabled(True)

            # Получаем сохраненный цвет или устанавливаем белый по умолчанию
            current_color = self.viewer.text_settings.get("background_color", "#FFFFFF")
            if current_color == "transparent" or not current_color:
                current_color = "#FFFFFF"
                self.viewer.text_settings["background_color"] = current_color

            # ВАЖНО: Применяем цвет к кнопке
            self.bg_color_button.setStyleSheet(f"""
                QToolButton {{
                    background-color: {current_color};
                    border: 1px solid #666;
                    border-radius: 3px;
                }}
            """)

        self.updateTextSettings()

    def pickColor(self, color_type):
        """Выбор цвета через диалоговое окно"""
        # Определяем текущий цвет и кнопку
        if color_type == "text_color":
            current_color = self.viewer.text_settings.get("text_color", "#FFFFFF")
            button = self.text_color_button
        elif color_type == "outline_color":
            current_color = self.viewer.text_settings.get("outline_color", "#000000")
            button = self.outline_color_button
        else:
            return  # Игнорируем неизвестные типы

        # Преобразуем цвет для диалога
        initial_color = QColor(current_color)

        # Открываем диалог выбора цвета с поддержкой альфа-канала
        color_dialog = QColorDialog(self)
        color_dialog.setOption(QColorDialog.ShowAlphaChannel, True)
        color_dialog.setCurrentColor(initial_color)

        if color_dialog.exec() == QColorDialog.Accepted:
            col = color_dialog.selectedColor()

            # Формируем строку цвета
            if col.alpha() < 255:
                # Цвет с прозрачностью
                color_name = col.name(QColor.HexArgb)
            else:
                # Непрозрачный цвет
                color_name = col.name()

            # Обновляем стиль кнопки
            button.setStyleSheet(f"""
                QToolButton {{
                    background-color: {color_name};
                    border: 1px solid #666;
                    border-radius: 3px;
                }}
            """)

            # Обновляем настройки
            self.viewer.text_settings[color_type] = color_name

            self.updateTextSettings()

    def onAlignmentChanged(self, button):
        """Обработчик изменения выравнивания текста"""
        alignment = self.align_group.id(button)
        self.viewer.text_settings["alignment"] = alignment
        self.updateTextSettings()

    def updateTextSettings(self):
        """Обновляет настройки текста в просмотрщике"""
        settings = {
            "font_family": self.font_combo.currentText(),
            "font_size": self.font_size_spin.value(),
            "text_color": self.viewer.text_settings.get("text_color", "#FFFFFF"),
            "outline_color": self.viewer.text_settings.get("outline_color", "#000000"),
            "outline_width": self.outline_width_spin.value(),
            "background_color": "transparent",  # Всегда прозрачный фон
            "alignment": self.viewer.text_settings.get("alignment", Qt.AlignCenter),
            "bold": self.bold_checkbox.isChecked(),
            "italic": self.italic_checkbox.isChecked(),
        }

        self.viewer.text_settings.update(settings)

        # Обновляем выделенный текстовый блок
        self.viewer.updateSelectedTextBlockSettings()

        # Сохраняем настройки
        self.saveStatus()

    def onImageTypeChanged(self, checked):
        """Обработчик изменения типа изображения"""
        if not checked:
            return

        btn = self.image_type_group.checkedButton()
        if not btn:
            return

        if btn.text() == "Оригинал":
            logger.info("Переключение на оригинальные изображения")
            self.viewer.set_cleaned(False)
            self.show_cleaned = False
        else:
            logger.info("Переключение на очищенные изображения")
            self.viewer.set_cleaned(True)
            self.show_cleaned = True

        # Сохраняем настройки
        self.saveStatus()

    def addTextBlock(self):
        """Добавляет новый текстовый блок по центру экрана"""
        if not hasattr(self.viewer, "scene_"):
            return

        # Определяем центр видимой области
        center = self.viewer.mapToScene(self.viewer.viewport().rect().center())

        # Создаем текстовый блок
        self.viewer.createTextBlock(center, "Текст")

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
        c_folder = self.ch_paths["typesetting"]

        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        data = self.collectTypesettingData()
        data["status"] = status

        # Обновляем chapter.json
        ch_json_path = os.path.join(self.ch_folder, "chapter.json")
        if os.path.exists(ch_json_path):
            try:
                with open(ch_json_path, 'r', encoding='utf-8') as f:
                    ch_data = json.load(f)

                if "stages" in ch_data:
                    if status == "Не начат":
                        ch_data["stages"]["Тайпсеттинг"] = False
                    elif status == "В работе":
                        ch_data["stages"]["Тайпсеттинг"] = "partial"
                    elif status == "Завершен":
                        ch_data["stages"]["Тайпсеттинг"] = True

                with open(ch_json_path, 'w', encoding='utf-8') as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"Ошибка при обновлении chapter.json: {e}")

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def loadTranslationNotes(self):
        """Загружает и отображает заметки перевода на холсте"""
        if not self.translation_json or "notes" not in self.translation_json:
            return

        # Очищаем старые заметки, если есть
        self.clearTranslationNotes()

        # Создаем заметки из данных перевода
        for note_data in self.translation_json.get("notes", []):
            self.createTranslationNoteFromData(note_data)

        # Обновляем видимость заметок для текущей страницы
        self.viewer.updateTranslationNotesVisibility()

    def clearTranslationNotes(self):
        """Очищает заметки перевода с холста"""
        # Очищаем все заметки перевода
        for note in self.viewer.translation_notes:
            if note.scene():
                self.viewer.scene_.removeItem(note)

        # Очищаем линии соединения
        for line in self.viewer.translation_lines:
            if line.scene():
                self.viewer.scene_.removeItem(line)

        # Очищаем точки привязки
        for anchor in self.viewer.translation_anchors:
            if anchor.scene():
                self.viewer.scene_.removeItem(anchor)

        # Сбрасываем коллекции
        self.viewer.translation_notes = []
        self.viewer.translation_lines = []
        self.viewer.translation_anchors = []

    def createTranslationNoteFromData(self, note_data):
        """Создает заметку перевода из данных"""
        # Получаем основные параметры
        page_index = note_data.get("page_index", 0)
        mode = note_data.get("mode", "Стандартная")
        pos = QPointF(
            note_data.get("position", {}).get("x", 0),
            note_data.get("position", {}).get("y", 0)
        )

        extra = {}

        # Создаем точку привязки или прямоугольник если есть
        if "anchor" in note_data:
            anchor_x = note_data["anchor"].get("x", 0)
            anchor_y = note_data["anchor"].get("y", 0)

            if mode == "Прямоугольная":
                # Создаем прямоугольник для режима OCR
                rect_width = note_data.get("rect_size", {}).get("width", 100)
                rect_height = note_data.get("rect_size", {}).get("height", 100)
                rect = QRectF(anchor_x, anchor_y, rect_width, rect_height)
                rect_item = MovableRectItem(rect, {"note_dotted_color": "#808080"}, True)
                rect_item.page_index = page_index
                rect_item.setVisible(page_index == self.viewer.current_page)
                self.viewer.scene_.addItem(rect_item)
                extra["p1"] = rect_item
                self.viewer.translation_anchors.append(rect_item)
            else:
                # Создаем точку привязки
                point = AnchorPointItem(anchor_x, anchor_y, 5, {"note_point_color": "#FF0000"})
                point.page_index = page_index
                point.setVisible(page_index == self.viewer.current_page)
                self.viewer.scene_.addItem(point)
                extra["p1"] = point
                self.viewer.translation_anchors.append(point)

        # Создаем саму заметку
        note_settings = {
            "note_bg_color": "#FFFFE0",
            "note_border_color": "#000000",
            "note_dotted_color": "#808080",
            "note_point_color": "#FF0000",
            "note_text_color": "#000000",
            "font_size": 12
        }

        note = NoteItem(pos, page_index, mode, note_settings, extra)

        # Устанавливаем размеры
        width = note_data.get("size", {}).get("width", 150)
        height = note_data.get("size", {}).get("height", 120)
        note.setPreferredSize(width, height)
        note.resize(QSizeF(width, height))
        note._width, note._height = width, height
        note.resize_handle.setVisible(False)  # Скрываем ручку изменения размера

        # Устанавливаем текст и делаем его только для чтения
        if "text" in note_data:
            note.text_edit.setHtml(note_data["text"])
            note.text_edit.setReadOnly(True)

        # Добавляем линию привязки если есть
        if "p1" in extra and hasattr(note, "line") and note.line:
            line = note.line
            line.page_index = page_index
            line.setVisible(page_index == self.viewer.current_page)
            self.viewer.scene_.addItem(line)
            self.viewer.translation_lines.append(line)

            # Принудительно обновляем линию
            line.updateLine()

            # Для точки привязки или прямоугольника
            anchor_item = extra["p1"]

            # Создаем функцию обновления линии
            def update_line_on_move():
                if hasattr(note, 'line') and note.line:
                    note.line.updateLine()

            # Подключаем сигналы перемещения
            if hasattr(anchor_item, 'itemChange'):
                # Переопределяем itemChange для отслеживания перемещения
                original_itemChange = anchor_item.itemChange

                def new_itemChange(change, value):
                    result = original_itemChange(change, value)
                    if change == QGraphicsItem.ItemPositionHasChanged:
                        update_line_on_move()
                    return result

                anchor_item.itemChange = new_itemChange

        # Устанавливаем видимость в зависимости от текущей страницы
        note.setVisible(page_index == self.viewer.current_page)

        # Добавляем заметку на сцену
        self.viewer.scene_.addItem(note)
        self.viewer.translation_notes.append(note)

    def collectTypesettingData(self):
        """Собирает данные для сохранения в JSON"""
        # Проверяем существование viewer
        if not hasattr(self, 'viewer'):
            return {}

        data = {
            "text_settings": self.viewer.text_settings,
            "show_cleaned": self.show_cleaned,
            "text_blocks": self.viewer.getTextBlocksData()
        }
        return data

    def saveStatus(self):
        """Сохранение статуса обработки и разметки в реальном времени"""
        # Проверяем, что viewer существует
        if not hasattr(self, 'viewer'):
            return

        c_folder = self.ch_paths["typesetting"]
        if not os.path.isdir(c_folder):
            return

        json_path = os.path.join(c_folder, self.status_json_filename)
        data = self.collectTypesettingData()

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
        """Загрузка статуса обработки и восстановление текстовых блоков"""
        c_folder = self.ch_paths["typesetting"]
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

                if "text_settings" in data:
                    self.viewer.text_settings.update(data["text_settings"])

                    # Применяем настройки к UI
                    self.applyTextSettingsToUI(data["text_settings"])

                if "show_cleaned" in data:
                    self.show_cleaned = data["show_cleaned"]

                # Восстанавливаем текстовые блоки
                if "text_blocks" in data:
                    self.viewer.restoreTextBlocks(data["text_blocks"])

    def applyTextSettingsToUI(self, settings):
        """Применяет сохраненные настройки текста к элементам интерфейса"""
        # Шрифт
        if "font_family" in settings and settings["font_family"] in AVAILABLE_FONTS:
            self.font_combo.setCurrentText(settings["font_family"])

        # Размер шрифта
        if "font_size" in settings:
            self.font_size_spin.setValue(settings["font_size"])

        # Стили
        if "bold" in settings:
            self.bold_checkbox.setChecked(settings["bold"])
        if "italic" in settings:
            self.italic_checkbox.setChecked(settings["italic"])

        # Цвета
        if "text_color" in settings:
            self.text_color_button.setStyleSheet(f"background-color: {settings['text_color']}; border: 1px solid #666;")

        if "outline_color" in settings:
            self.outline_color_button.setStyleSheet(
                f"background-color: {settings['outline_color']}; border: 1px solid #666;")

        # Ширина обводки
        if "outline_width" in settings:
            self.outline_width_spin.setValue(settings["outline_width"])

        # Выравнивание
        if "alignment" in settings:
            alignment = settings["alignment"]
            if alignment == Qt.AlignLeft:
                self.align_left.setChecked(True)
            elif alignment == Qt.AlignCenter:
                self.align_center.setChecked(True)
            elif alignment == Qt.AlignRight:
                self.align_right.setChecked(True)

    def lockInterface(self, operation):
        """Блокировка интерфейса при длительных операциях"""
        self.is_proc = True
        self.curr_op = operation

        self.refresh_btn.setEnabled(False)
        self.add_text_block_btn.setEnabled(False)
        self.export_btn.setEnabled(False)

        QApplication.processEvents()

    def unlockInterface(self):
        """Разблокировка интерфейса после операций"""
        self.is_proc = False
        self.curr_op = None

        self.refresh_btn.setEnabled(True)
        self.add_text_block_btn.setEnabled(True)
        self.export_btn.setEnabled(True)

        QApplication.processEvents()

    def keyPressEvent(self, event):
        """Обработчик нажатий клавиш"""
        if event.key() == Qt.Key_Space:
            from PySide6.QtWidgets import QGraphicsView
            self.viewer.setDragMode(QGraphicsView.ScrollHandDrag)
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
            from PySide6.QtWidgets import QGraphicsView
            self.viewer.setDragMode(QGraphicsView.NoDrag)
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