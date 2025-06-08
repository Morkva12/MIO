# -*- coding: utf-8 -*-
# ui/windows/m6_0_preprocess_images.py

import os
import sys
import json
import logging
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import (Qt, Signal, QThreadPool, QTimer)
from PySide6.QtGui import (QPixmap, QFont, QColor, QBrush)
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSpacerItem, QSizePolicy, QRadioButton,
                               QButtonGroup, QSlider, QLineEdit, QMessageBox, QComboBox,
                               QCheckBox, QProgressBar, QWidget, QGroupBox, QApplication, QGraphicsBlurEffect)

# Импортируем наши модули
from ui.components.gradient_widget import GradientBackgroundWidget
from ui.windows.m6_1_image_viewer import ImageViewer
from ui.windows.m6_2_enhancement import EnhancementWorker
from ui.windows.m6_3_utils import (get_images_from_folder, prepare_images_and_folders,
                                   populate_gpu_options, handle_sync_slider, show_message,
                                   PageChangeSignal, check_enhanced_availability, delete_enhanced_image,
                                   delete_all_enhanced, get_file_hash, detect_folder_changes,
                                   get_folder_state, sync_enhanced_images, copy_images)
from ui.windows.m6_4_ui_components import (ImageLoader, LoadingOverlay,
                                           ImageLoadedEvent, AllImagesLoadedEvent)

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class PreprocessingWindow(QDialog):
    """
    Окно "Предобработка".
    Основная функциональность:
    - Загрузка и отображение изображений
    - Улучшение изображений с помощью Real-ESRGAN
    - Сохранение улучшенных изображений
    """
    back_requested = Signal()

    def __init__(self, chapter_folder, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("preprocessing_window")

        # Сохраняем пути
        self.ch_folder = chapter_folder
        self.paths = paths or {}

        # Определяем базовые папки
        self.ch_paths = {
            "preproc": os.path.join(chapter_folder, "Предобработка"),
            "upload": os.path.join(chapter_folder, "Загрузка"),
            "orig": os.path.join(chapter_folder, "Загрузка", "originals"),
        }

        # Создаем папки
        for folder in self.ch_paths.values():
            os.makedirs(folder, exist_ok=True)

        # Готовим папки для изображений
        self.originals_folder, self.enhanced_folder = prepare_images_and_folders(
            base_input_folder=self.ch_paths["upload"],
            base_preprocessing_folder=self.ch_paths["preproc"]
        )

        # Собираем пути к изображениям
        self.image_paths = get_images_from_folder(self.originals_folder)

        # Флаги состояния
        self.is_proc = False  # Идет ли процесс
        self.curr_op = None  # Текущая операция
        self.show_enhanced = False  # Показывать улучшенные
        self.is_loading_complete = False  # Завершена ли загрузка изображений

        # Добавляем счетчики для корректного отображения прогресса
        self.total_originals = 0
        self.total_enhanced = 0

        # Инициализация окна
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle("Предобработка")
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

        # Создаем интерфейс ПЕРЕД загрузкой изображений
        self.initTopBar(main_layout)
        self.initContent(main_layout)

        # Два пула потоков для разных типов задач
        max_workers = min(4, multiprocessing.cpu_count())
        self.q_thread_pool = QThreadPool.globalInstance()  # Для QRunnable объектов (EnhancementWorker)
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)  # Для ImageLoader

        # Загрузчик изображений
        self.image_loader = ImageLoader(self.image_paths, self.thread_pool)
        self.image_loader.image_loaded.connect(self.onImageLoaded)
        self.image_loader.loading_progress.connect(self.updateLoadingProgress)
        self.image_loader.loading_complete.connect(self.onLoadingComplete)
        self.image_loader.loading_cancelled.connect(self.onLoadingCancelled)

        # Подключаем сигналы для обновления интерфейса
        self.page_change_signal.page_changed.connect(self.updateActiveThumbnail)
        self.page_change_signal.page_changed.connect(self.update_page_info)

        # Основной лейаут диалога
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.bg_widget)
        self._closing = False
        # Активный воркер
        self.enhancement_worker = None
        self.active_timers = []
        self.scale_warning_shown = False

        # Состояние папки загрузки для отслеживания изменений
        self.upload_folder_state = {}
        self.last_sync_check = None

        # Таймер для периодической проверки улучшенных изображений
        self.check_enhanced_timer = QTimer(self)
        self.check_enhanced_timer.timeout.connect(self.check_enhanced_availability)
        self.check_enhanced_timer.start(5000)  # Проверяем каждые 5 секунд

        # Связываем обработчики сигналов
        self.outscale_slider.valueChanged.connect(lambda: self.update_project_info())
        self.model_select.currentIndexChanged.connect(self.update_project_info)
        self.face_enhance_checkbox.stateChanged.connect(self.update_project_info)
        self.tile_select.currentIndexChanged.connect(self.update_project_info)

        # Обновляем информацию о проекте
        self.update_project_info()

        # Загружаем статус проекта
        self.loadStatus()
        self.active_timers = []

        # Показываем загрузочный экран и начинаем загрузку изображений
        QTimer.singleShot(100, self.startLoading)
        QTimer.singleShot(1000, self.check_initial_sync)

    def startLoading(self):
        """Начало загрузки изображений с блокировкой интерфейса"""
        # Применяем размытие к главному окну
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(10)
        self.bg_widget.setGraphicsEffect(self.blur_effect)

        # Создаем блокирующую подложку
        self.overlay_widget = QWidget(self)
        self.overlay_widget.setObjectName("loading_block_overlay")
        self.overlay_widget.setStyleSheet("QWidget#loading_block_overlay { background-color: rgba(0, 0, 0, 120); }")
        self.overlay_widget.setGeometry(self.rect())
        self.overlay_widget.show()

        # Подсчитываем общее количество изображений (оригинальные + улучшенные)
        total_images = len(self.image_paths)
        enhanced_count = 0
        for path in self.image_paths:
            base = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1]
            enhanced_path = os.path.join(self.enhanced_folder, f"{base}_enhanced{ext}")
            if os.path.exists(enhanced_path):
                enhanced_count += 1

        # Сохраняем общее количество для корректного подсчета прогресса
        self.total_originals = total_images
        self.total_enhanced = enhanced_count
        total_to_load = total_images

        # Показываем окно загрузки с непрозрачным фоном
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.loading_overlay.setStyleSheet("""
            QWidget#loading_overlay {
                background-color: #1E1E2A;
                border-radius: 35px;
                border: 2px solid #7E1E9F;
            }
        """)

        # Обновляем информацию о загрузке с учетом улучшенных изображений
        self.loading_overlay.updateProgress(0, total_to_load)
        if enhanced_count > 0:
            self.loading_overlay.info_label.setText(
                f"Загрузка оригинальных изображений ({total_images} шт.)")

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

    def check_initial_sync(self):
        """Проверяет необходимость синхронизации при открытии окна"""
        logger.info("=== НАЧАЛО check_initial_sync ===")

        if hasattr(self, '_closing') and self._closing:
            logger.info("Окно закрывается, выход из check_initial_sync")
            return

        logger.info("Окно активно, продолжаем проверку")

        try:
            # Получаем полные пути для анализа
            logger.info(f"Папка загрузки: {self.ch_paths['upload']}")
            logger.info(f"Папка оригиналов: {self.originals_folder}")

            upload_files = []
            originals_files = []

            logger.info("Сканирование папки загрузки...")
            for f in sorted(os.listdir(self.ch_paths["upload"])):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')) and not f.startswith('temp_'):
                    full_path = os.path.join(self.ch_paths["upload"], f)
                    upload_files.append(full_path)
                    logger.debug(f"  Найден файл в загрузке: {f}")

            logger.info(f"Всего файлов в загрузке: {len(upload_files)}")

            logger.info("Сканирование папки оригиналов...")
            for f in sorted(os.listdir(self.originals_folder)):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    full_path = os.path.join(self.originals_folder, f)
                    originals_files.append(full_path)
                    logger.debug(f"  Найден файл в оригиналах: {f}")

            logger.info(f"Всего файлов в оригиналах: {len(originals_files)}")

            # Используем detect_folder_changes для анализа
            logger.info("Вызов detect_folder_changes...")
            changes = detect_folder_changes(upload_files, originals_files)
            logger.info(f"Результат detect_folder_changes: {changes}")

            # Проверяем есть ли изменения
            has_changes = any([changes['added'], changes['removed'], changes['modified'], changes['renamed']])
            logger.info(f"Есть изменения: {has_changes}")

            if has_changes:
                logger.info("!!! ОБНАРУЖЕНЫ ИЗМЕНЕНИЯ !!!")
                logger.info(f"  Добавлено: {len(changes['added'])} файлов")
                logger.info(f"  Удалено: {len(changes['removed'])} файлов")
                logger.info(f"  Изменено: {len(changes['modified'])} файлов")
                logger.info(f"  Переименовано: {len(changes['renamed'])} пар")

                logger.info("Импорт SyncDialog...")
                from ui.windows.m6_4_ui_components import SyncDialog
                logger.info("SyncDialog импортирован успешно")

                logger.info("Создание экземпляра SyncDialog...")
                dialog = SyncDialog(changes, self)
                logger.info("SyncDialog создан")

                logger.info("Показ диалога (вызов exec)...")
                sync_mode = dialog.exec()
                logger.info(f"!!! Диалог закрыт, возвращено значение: '{sync_mode}'")
                logger.info(f"    Тип возвращенного значения: {type(sync_mode)}")
                logger.info(f"    repr значения: {repr(sync_mode)}")

                logger.info(f"Проверка условия: sync_mode not in ['cancel', 'none']")
                logger.info(f"    sync_mode == 'cancel': {sync_mode == 'cancel'}")
                logger.info(f"    sync_mode == 'none': {sync_mode == 'none'}")
                logger.info(f"    Условие выполнено: {sync_mode not in ['cancel', 'none']}")

                if sync_mode not in ['cancel', 'none']:
                    logger.info(f"!!! ЗАПУСК СИНХРОНИЗАЦИИ с режимом: {sync_mode}")
                    logger.info("Вызов self.perform_sync...")
                    self.perform_sync(sync_mode)
                    logger.info("perform_sync завершен")
                else:
                    logger.info(f"!!! СИНХРОНИЗАЦИЯ ОТМЕНЕНА, режим: {sync_mode}")
            else:
                logger.info("Изменений не обнаружено, синхронизация не требуется")

        except Exception as e:
            logger.error(f"!!! ОШИБКА в check_initial_sync: {e}", exc_info=True)
            logger.error(f"Тип исключения: {type(e)}")
            logger.error(f"Детали: {str(e)}")

        logger.info("=== КОНЕЦ check_initial_sync ===")

    def _finalize_sync(self, results):
        """Завершение синхронизации"""
        # Перезагружаем список изображений
        self.image_paths = get_images_from_folder(self.originals_folder)

        if hasattr(self, 'viewer') and self.viewer:
            self.viewer.pages = self.image_paths
            if hasattr(self.viewer, 'page_pixmap_item') and self.viewer.page_pixmap_item:
                self.viewer.reloadOriginalPixmaps()

        if not (hasattr(self, '_closing') and self._closing):
            self._reloadThumbnails()

        # Показываем результаты
        if results['errors']:
            error_text = "\n".join(results['errors'][:5])
            show_message(self, "Синхронизация завершена с ошибками",
                         f"Синхронизация выполнена, но возникли ошибки:\n{error_text}",
                         QMessageBox.Warning)
        else:
            info_text = f"Синхронизировано: {results['synced']} файлов\n"
            if results['deleted'] > 0:
                info_text += f"Удалено: {results['deleted']} улучшенных\n"
            if results['renamed'] > 0:
                info_text += f"Переименовано: {results['renamed']} улучшенных"
            show_message(self, "Синхронизация завершена", info_text)

        self.update_project_info()
        self.check_enhanced_availability()
    def showEnhancementDialog(self):
        """Показывает диалог выбора режима улучшения"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор режима улучшения")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Заголовок
        label = QLabel("Выберите режим улучшения изображений:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        # Информация о текущей странице
        current_page_info = QLabel(f"Текущая страница: {self.viewer.current_page + 1} из {len(self.image_paths)}")
        current_page_info.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(current_page_info)

        # Кнопки выбора
        buttons_layout = QVBoxLayout()
        buttons_layout.setSpacing(10)

        # Кнопка "Улучшить текущее"
        current_btn = QPushButton(f"Улучшить только текущее изображение (стр. {self.viewer.current_page + 1})")
        current_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        current_btn.clicked.connect(lambda: dialog.done(1))  # Вернуть 1 для текущего
        buttons_layout.addWidget(current_btn)

        # Кнопка "Улучшить все"
        all_btn = QPushButton(f"Улучшить все изображения ({len(self.image_paths)} шт.)")
        all_btn.setStyleSheet("""
            QPushButton {
                background-color: #5E7E9F;
                color: white;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #6E8EAF;
            }
        """)
        all_btn.clicked.connect(lambda: dialog.done(2))  # Вернуть 2 для всех
        buttons_layout.addWidget(all_btn)

        layout.addLayout(buttons_layout)
        layout.addStretch()

        # Кнопка отмены
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
        cancel_btn.clicked.connect(lambda: dialog.done(0))  # Вернуть 0 для отмены

        cancel_layout = QHBoxLayout()
        cancel_layout.addStretch()
        cancel_layout.addWidget(cancel_btn)
        layout.addLayout(cancel_layout)

        return dialog.exec()

    def load_enhanced_images_cache(self):
        """Загрузка улучшенных изображений в кэш для ускорения переключения"""
        if not hasattr(self, 'image_paths') or not self.image_paths:
            return

        # Обновляем информацию о загрузке только если есть улучшенные изображения
        has_enhanced = check_enhanced_availability(self.image_paths, self.enhanced_folder)

        if hasattr(self, 'enhanced_radio'):
            self.enhanced_radio.setEnabled(has_enhanced)

        # Сигнализируем о завершении загрузки
        if self.is_loading_complete:
            # Отложенная очистка интерфейса загрузки
            QTimer.singleShot(500, self._finishLoadingCleanup)

    def cancelLoading(self):
        """Отмена загрузки изображений"""
        self.image_loader.cancel()

        # Обновляем интерфейс
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.info_label.setText("Отмена загрузки...")
            self.loading_overlay.cancel_button.setEnabled(False)

        # Используем наш новый метод для отложенного действия
        self._createDelayedAction(500, self.back_requested.emit)

    def onImageLoaded(self, idx, pixmap, current_file):
        """Обработка загрузки изображения"""
        # Обновляем отображение
        if self.viewer:
            self.viewer.original_pixmaps[idx] = pixmap

            # Отображаем текущую страницу, если она совпадает с загруженной
            if self.viewer.current_page == idx:
                self.viewer.displayCurrentPage()

            # Обновляем миниатюру
            if self.preview_scroll_area and 0 <= idx < len(self.thumbnail_labels):
                tw = 150
                th = tw * 2
                scaled_pix = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumbnail_labels[idx].setPixmap(scaled_pix)

        # Отправляем событие загрузки изображения
        QApplication.postEvent(self, ImageLoadedEvent(idx))

    def event(self, event):
        """Обработка событий"""
        if event.type() == ImageLoadedEvent.EventType:
            # Обработка события загрузки изображения
            return True
        elif event.type() == AllImagesLoadedEvent.EventType:
            # Завершение загрузки - можно инициализировать интерфейс, если еще не сделано
            return True
        return super().event(event)

    def updateLoadingProgress(self, loaded, total, current_file):
        """Обновление прогресса загрузки"""
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.updateProgress(loaded, total, current_file)

    def onLoadingComplete(self):
        """Обработка завершения загрузки"""
        self.is_loading_complete = True

        # Обновляем интерфейс
        self.viewer.displayCurrentPage()

        # Запускаем загрузку улучшенных изображений только после загрузки оригинальных
        QTimer.singleShot(100, self.load_enhanced_images_cache)

    def _finishLoadingCleanup(self):
        """Завершающая очистка после загрузки"""
        # Убираем блокировку интерфейса
        self.bg_widget.setGraphicsEffect(None)
        self.blur_effect = None

        # Удаляем подложку
        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            self.overlay_widget.deleteLater()
            self.overlay_widget = None

        # Скрываем окно загрузки
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.hide()
            self.loading_overlay.deleteLater()
            self.loading_overlay = None

    def onLoadingCancelled(self):
        """Обработка отмены загрузки"""
        # Убираем блокировку интерфейса
        self.bg_widget.setGraphicsEffect(None)
        self.blur_effect = None

        if hasattr(self, 'overlay_widget') and self.overlay_widget:
            self.overlay_widget.deleteLater()
            self.overlay_widget = None

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

    def _cleanup_gpu_resources(self):
        """Очистка GPU ресурсов при закрытии"""
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
        except:
            pass
    def resizeEvent(self, event):
        """Обработчик изменения размера окна"""
        super().resizeEvent(event)

        # Центрируем загрузочный экран, если он есть
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            self.loading_overlay.move(
                self.width() // 2 - self.loading_overlay.width() // 2,
                self.height() // 2 - self.loading_overlay.height() // 2
            )

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
        self.viewer = ImageViewer(
            self.image_paths,
            output_folder=self.enhanced_folder,
            parent=self
        )

        # Настраиваем отступы для информационных блоков в просмотрщике
        self.viewer.info_width = 240

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

        # Контейнер для миниатюр
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(5, 5, 5, 0)
        container_layout.setSpacing(5)

        self.thumbnail_labels = []
        self.index_labels = []

        thumbnail_width = 150
        thumbnail_height = thumbnail_width * 2

        # Создаем миниатюры для каждого изображения
        for i, path in enumerate(self.image_paths):
            thumb_container = QWidget()
            thumb_layout = QVBoxLayout(thumb_container)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setSpacing(0)

            # Миниатюра изображения
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
                    border: 2px solid transparent;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                }
            """)

            # Номер страницы
            index_label = QLabel(str(i + 1))
            index_label.setAlignment(Qt.AlignCenter)
            index_label.setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #222222;
                    font-size: 14px;
                    font-weight: bold;
                    border: 2px solid transparent;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                }
            """)

            thumb_layout.addWidget(thumb_label)
            thumb_layout.addWidget(index_label)

            # Обработчик клика
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

                # Обновляем информацию о странице
                self.update_page_info(index)
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
                                border: 2px solid transparent;
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
                                border: 2px solid transparent;
                                border-bottom-left-radius: 8px;
                                border-bottom-right-radius: 8px;
                            }
                        """)

    def update_project_info(self):
        """Обновляет упрощенную информацию о проекте в правой панели"""
        # Подсчет размеров оригинальных файлов
        orig_kb, orig_mb = 0, 0
        for path in self.image_paths:
            kb, mb = self._calculate_file_size(path)
            orig_kb += kb
            orig_mb += mb

        # Подсчет размеров конечного проекта (улучшенные + оригинальные где нет улучшенных)
        final_kb, final_mb = 0, 0
        enh_count = 0

        for path in self.image_paths:
            base = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1]
            enh_path = os.path.join(self.enhanced_folder, f"{base}_enhanced{ext}")

            if os.path.exists(enh_path):
                # Если есть улучшенное, считаем его размер
                enh_count += 1
                kb, mb = self._calculate_file_size(enh_path)
                final_kb += kb
                final_mb += mb
            else:
                # Если нет улучшенного, считаем размер оригинала
                kb, mb = self._calculate_file_size(path)
                final_kb += kb
                final_mb += mb

        # Формируем текст с улучшенным форматированием
        info_text = ""

        # Информация об оригинальных изображениях
        info_text += f"<b style='color:#A9DFFF;'>Оригинальный проект:</b><br>"
        info_text += f"Количество файлов: {len(self.image_paths)}<br>"
        info_text += f"Общий размер: {orig_kb:.1f} КБ ({orig_mb:.2f} МБ)<br><br>"

        # Информация о конечном проекте
        info_text += f"<b style='color:#AAFFAA;'>Конечный проект:</b><br>"
        info_text += f"Всего файлов: {len(self.image_paths)}<br>"
        if enh_count > 0:
            info_text += f"Из них улучшенных: {enh_count}<br>"
            info_text += f"Оригинальных: {len(self.image_paths) - enh_count}<br>"
        else:
            info_text += f"Все оригинальные<br>"
        info_text += f"Общий размер: {final_kb:.1f} КБ ({final_mb:.2f} МБ)<br>"

        # Показываем разницу в размере, если есть улучшенные изображения
        if enh_count > 0:
            size_diff = final_mb - orig_mb
            percent_change = ((final_mb - orig_mb) / orig_mb * 100) if orig_mb > 0 else 0

            if size_diff > 0:
                info_text += f"<span style='color:#FF9999;'>Увеличение: +{size_diff:.2f} МБ ({percent_change:.1f}%)</span>"
            else:
                info_text += f"<span style='color:#99FF99;'>Уменьшение: {size_diff:.2f} МБ ({percent_change:.1f}%)</span>"

        # Обновляем текст в панели
        self.project_info_text.setText(info_text)

    def update_page_info(self, page_index):
        """Обновляет информацию для конкретной страницы"""
        if page_index < 0 or page_index >= len(self.image_paths):
            return

        # Обновляем информацию о проекте
        self.update_project_info()

    def check_enhanced_availability(self):
        """Проверяет наличие улучшенных изображений и обновляет UI"""
        has_any_enhanced = check_enhanced_availability(self.image_paths, self.enhanced_folder)

        # Включаем опцию Улучшенное если есть хоть одно улучшенное изображение
        if has_any_enhanced:
            self.enhanced_radio.setEnabled(True)

        # Обновляем информацию о проекте
        self.update_project_info()

    def check_upload_folder_changes(self):
        """Проверяет изменения в папке загрузки"""
        # Не проверяем если идет процесс или окно закрывается
        if self.is_proc or (hasattr(self, '_closing') and self._closing):
            return

        # Получаем текущее состояние папки загрузки
        upload_folder = self.ch_paths["upload"]
        current_state = get_folder_state(upload_folder)

        # Проверяем изменения
        changes = detect_folder_changes(current_state, self.upload_folder_state)

        # Если есть изменения
        if changes['added'] or changes['removed'] or changes['modified'] or changes['reordered']:
            logger.info(f"Обнаружены изменения в папке загрузки: {changes}")

            # Останавливаем таймер на время диалога
            self.check_changes_timer.stop()

            # Показываем диалог синхронизации
            from ui.windows.m6_4_ui_components import SyncDialog
            dialog = SyncDialog(changes, self)
            sync_mode = dialog.exec()

            if sync_mode != 'cancel':
                # Обновляем состояние папки
                self.upload_folder_state = current_state

                if sync_mode != 'none':
                    # Выполняем синхронизацию
                    self.perform_sync(sync_mode)

            # Возобновляем таймер
            self.check_changes_timer.start(3000)
        else:
            # Обновляем состояние даже если нет изменений
            self.upload_folder_state = current_state

    def _reload_after_sync(self, results):
        """Перезагрузка интерфейса после синхронизации"""
        # Перезагружаем список изображений
        self.image_paths = get_images_from_folder(self.originals_folder)

        # Обновляем viewer
        if hasattr(self, 'viewer') and self.viewer:
            self.viewer.pages = self.image_paths
            self.viewer.original_pixmaps = []
            for path in self.image_paths:
                pixmap = QPixmap(path)
                self.viewer.original_pixmaps.append(pixmap)

            # Сбрасываем на первую страницу
            self.viewer.current_page = 0
            self.viewer.displayCurrentPage()

        # Перезагружаем миниатюры
        self._reloadThumbnails()

        # Показываем результаты
        if results['errors']:
            error_text = "\n".join(results['errors'][:5])
            show_message(self, "Синхронизация завершена с ошибками",
                         f"Синхронизация выполнена, но возникли ошибки:\n{error_text}",
                         QMessageBox.Warning)
        else:
            info_text = f"Синхронизировано: {results['synced']} файлов\n"
            if results['deleted'] > 0:
                info_text += f"Удалено: {results['deleted']} улучшенных\n"
            if results['renamed'] > 0:
                info_text += f"Переименовано: {results['renamed']} улучшенных"
            show_message(self, "Синхронизация завершена", info_text)

        self.update_project_info()
        self.check_enhanced_availability()

    def perform_sync(self, sync_mode):
        """Выполняет синхронизацию изображений"""
        if hasattr(self, '_closing') and self._closing:
            return

        logger.info(f"Выполнение синхронизации в режиме: {sync_mode}")

        try:
            # Определяем изменения ДО копирования
            upload_files = []
            originals_files = []

            for f in sorted(os.listdir(self.ch_paths["upload"])):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')) and not f.startswith('temp_'):
                    upload_files.append(os.path.join(self.ch_paths["upload"], f))

            for f in sorted(os.listdir(self.originals_folder)):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    originals_files.append(os.path.join(self.originals_folder, f))

            changes = detect_folder_changes(upload_files, originals_files)
            rename_map = {old: new for old, new in changes['renamed']}

            # ВАЖНО: Копируем файлы ВСЕГДА при preserve
            logger.info(f"Копирование файлов из загрузки в originals...")
            copied = copy_images(self.ch_paths["upload"], self.originals_folder)
            logger.info(f"Скопировано {copied} файлов")

            # Синхронизируем улучшенные
            if sync_mode == 'preserve':
                results = sync_enhanced_images(
                    self.originals_folder,
                    self.enhanced_folder,
                    sync_mode,
                    rename_map=rename_map
                )

                # Для modified файлов тоже считаем как синхронизированные
                if changes['modified']:
                    results['synced'] += len(changes['modified'])
            else:
                results = {'synced': 0, 'deleted': 0, 'renamed': 0, 'errors': []}

            self._finalize_sync(results)

        except Exception as e:
            logger.error(f"Ошибка при синхронизации: {e}", exc_info=True)
            show_message(self, "Ошибка", f"Произошла ошибка при синхронизации:\n{str(e)}", QMessageBox.Critical)

    def reloadOriginalPixmaps(self):
        """Перезагружает оригинальные изображения"""
        # Проверяем что viewer еще существует
        try:
            if not hasattr(self, 'pages') or not self.pages:
                return

            self.original_pixmaps = []
            for p in self.pages:
                if os.path.isfile(p):
                    pixmap = QPixmap(p)
                    self.original_pixmaps.append(pixmap)

            # Отображаем текущую страницу только если есть все необходимые элементы
            if (hasattr(self, 'page_pixmap_item') and
                    self.page_pixmap_item and
                    hasattr(self, 'scene_') and
                    self.scene_):
                self.displayCurrentPage()
        except RuntimeError as e:
            logger.error(f"Ошибка при перезагрузке изображений: {e}")
    def _reloadThumbnails(self):
        """Перезагружает миниатюры после синхронизации"""
        # Удаляем старые миниатюры
        for container in self.preview_scroll_area.widget().findChildren(QWidget):
            container.deleteLater()

        self.thumbnail_labels = []
        self.index_labels = []

        # Создаем новые миниатюры
        container_layout = self.preview_scroll_area.widget().layout()

        thumbnail_width = 150
        thumbnail_height = thumbnail_width * 2

        for i, path in enumerate(self.image_paths):
            thumb_container = QWidget()
            thumb_layout = QVBoxLayout(thumb_container)
            thumb_layout.setContentsMargins(0, 0, 0, 0)
            thumb_layout.setSpacing(0)

            # Миниатюра изображения
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
                    border: 2px solid transparent;
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 0px;
                }
            """)

            # Номер страницы
            index_label = QLabel(str(i + 1))
            index_label.setAlignment(Qt.AlignCenter)
            index_label.setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #222222;
                    font-size: 14px;
                    font-weight: bold;
                    border: 2px solid transparent;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                }
            """)

            thumb_layout.addWidget(thumb_label)
            thumb_layout.addWidget(index_label)

            # Обработчик клика
            thumb_label.mousePressEvent = self.makePreviewClickHandler(i)

            self.thumbnail_labels.append(thumb_label)
            self.index_labels.append(index_label)

            container_layout.insertWidget(i, thumb_container)

        # Обновляем активную миниатюру
        if self.viewer.current_page < len(self.image_paths):
            self.updateActiveThumbnail(self.viewer.current_page)

    def _createDelayedAction(self, delay_ms, action):
        """Создает отложенное действие с возможностью отмены"""
        # Проверяем, что окно еще не закрывается
        if hasattr(self, '_closing') and self._closing:
            return None

        timer = QTimer(self)
        timer.setSingleShot(True)

        # Обертка для безопасного вызова
        def safe_action():
            try:
                # Проверяем, что объект еще существует
                if self and not (hasattr(self, '_closing') and self._closing):
                    action()
            except RuntimeError:
                pass  # Объект уже удален
            finally:
                if timer in self.active_timers:
                    self.active_timers.remove(timer)

        timer.timeout.connect(safe_action)
        self.active_timers.append(timer)
        timer.start(delay_ms)
        return timer

    def _calculate_file_size(self, file_path):
        """Возвращает размер файла в КБ и МБ"""
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
            size_kb = size_bytes / 1024
            size_mb = size_kb / 1024
            return size_kb, size_mb
        return 0, 0

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
        self.enhanced_radio = QRadioButton("Улучшенное")

        self.original_radio.setStyleSheet("color: white;")
        self.enhanced_radio.setStyleSheet("color: white;")

        self.original_radio.setChecked(True)
        self.image_type_group.addButton(self.original_radio)
        self.image_type_group.addButton(self.enhanced_radio)

        has_enhanced = check_enhanced_availability(self.image_paths, self.enhanced_folder)
        self.enhanced_radio.setEnabled(has_enhanced)

        self.original_radio.toggled.connect(self.onImageTypeChanged)
        self.enhanced_radio.toggled.connect(self.onImageTypeChanged)

        image_type_layout.addWidget(self.original_radio)
        image_type_layout.addWidget(self.enhanced_radio)
        image_type_group.setLayout(image_type_layout)
        right_layout.addWidget(image_type_group)

        # Группа улучшения изображений
        enhancement_group = QGroupBox("Улучшение изображения")
        enhancement_group.setStyleSheet("""
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
        enhancement_layout = QVBoxLayout()

        # Выбор модели
        model_layout = QHBoxLayout()
        model_label = QLabel("Модель:")
        model_label.setStyleSheet("color: white;")

        self.model_select = QComboBox()
        self.model_select.addItems([
            "RealESRGAN_x4plus_anime_6B",
            "RealESRGAN_x4plus",
            "RealESRNet_x4plus",
            "RealESRGAN_x2plus",
            "realesr-animevideov3",
            "realesr-general-x4v3"
        ])
        self.model_select.setCurrentText("RealESRGAN_x4plus_anime_6B")
        self.model_select.setStyleSheet("background-color: #FFFFFF; color: #000000;")
        model_layout.addWidget(model_label)
        model_layout.addWidget(self.model_select)

        enhancement_layout.addLayout(model_layout)

        # Выбор GPU
        gpu_layout = QHBoxLayout()
        gpu_label = QLabel("GPU:")
        gpu_label.setStyleSheet("color: white;")

        self.gpu_select = QComboBox()
        populate_gpu_options(self.gpu_select, self)
        self.gpu_select.setStyleSheet("background-color: #FFFFFF; color: #000000;")
        gpu_layout.addWidget(gpu_label)
        gpu_layout.addWidget(self.gpu_select)

        enhancement_layout.addLayout(gpu_layout)

        # Масштаб
        scale_layout = QHBoxLayout()
        scale_label = QLabel("Масштаб:")
        scale_label.setStyleSheet("color: white;")

        self.outscale_slider = QSlider(Qt.Horizontal)
        self.outscale_slider.setRange(25, 400)
        self.outscale_slider.setSingleStep(25)
        self.outscale_slider.setValue(200)
        self.outscale_slider.setStyleSheet("""
                    QSlider::groove:horizontal {
                        height: 6px;
                        background: #CCCCCC;
                        border-radius: 3px;
                    }
                    QSlider::handle:horizontal {
                        background: #7E1E9F;
                        border: 1px solid #5E0E7F;
                        width: 14px;
                        margin: -4px 0;
                        border-radius: 7px;
                    }
                """)

        self.outscale_input = QLineEdit("2.0")
        self.outscale_input.setFixedWidth(50)
        self.outscale_input.setStyleSheet("background-color: #FFFFFF; color: #000000;")

        self.outscale_slider.valueChanged.connect(self.onScaleChanged)

        self.outscale_input.editingFinished.connect(
            handle_sync_slider(self.outscale_slider, self.outscale_input, 0.25, 4.0, 100, 0.25)
        )

        scale_layout.addWidget(scale_label)
        scale_layout.addWidget(self.outscale_slider)
        scale_layout.addWidget(self.outscale_input)

        enhancement_layout.addLayout(scale_layout)

        # Размер плитки
        tile_layout = QHBoxLayout()
        tile_label = QLabel("Размер плитки:")
        tile_label.setStyleSheet("color: white;")

        self.tile_select = QComboBox()
        self.tile_select.addItem("Выключено", 0)
        self.tile_select.addItem("256", 256)
        self.tile_select.addItem("512", 512)
        self.tile_select.setCurrentIndex(1)  # 256 по умолчанию
        self.tile_select.setStyleSheet("background-color: #FFFFFF; color: #000000;")

        tile_layout.addWidget(tile_label)
        tile_layout.addWidget(self.tile_select)

        enhancement_layout.addLayout(tile_layout)

        # Улучшение лиц
        self.face_enhance_checkbox = QCheckBox("Улучшить лица")
        self.face_enhance_checkbox.setChecked(True)
        self.face_enhance_checkbox.setStyleSheet("color: white;")
        enhancement_layout.addWidget(self.face_enhance_checkbox)

        # Прогресс улучшения
        self.enh_prog = QProgressBar()
        self.enh_prog.setRange(0, 100)
        self.enh_prog.setValue(0)
        self.enh_prog.setVisible(False)
        self.enh_prog.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #999;
                        border-radius: 4px;
                        text-align: center;
                        background-color: #444;
                        color: white;
                    }
                    QProgressBar::chunk {
                        background-color: #2EA44F;
                        border-radius: 3px;
                    }
                """)
        enhancement_layout.addWidget(self.enh_prog)

        # Кнопка улучшения
        self.enh_btn = QPushButton("Улучшить изображения")
        self.enh_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #7E1E9F;
                        color: white;
                        border-radius: 8px;
                        padding: 10px 20px;
                        font-size: 16px;
                    }
                    QPushButton:hover {
                        background-color: #9E3EAF;
                    }
                """)
        self.enh_btn.clicked.connect(self.runEnhancement)
        enhancement_layout.addWidget(self.enh_btn)

        enhancement_group.setLayout(enhancement_layout)
        right_layout.addWidget(enhancement_group)

        # Группа информации о проекте
        project_info_group = QGroupBox("Информация о проекте")
        project_info_group.setStyleSheet("""
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
        project_info_layout = QVBoxLayout()

        # Текст с информацией
        self.project_info_text = QLabel("Загрузка информации...")
        self.project_info_text.setTextFormat(Qt.RichText)
        self.project_info_text.setStyleSheet("color: white;")
        self.project_info_text.setWordWrap(True)

        project_info_layout.addWidget(self.project_info_text)

        project_info_group.setLayout(project_info_layout)
        right_layout.addWidget(project_info_group)

        # Группа статуса
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

        # Кнопки удаления улучшенных изображений
        self.delete_enhanced_btn = QPushButton("Удалить улучшенные")
        self.delete_enhanced_btn.setStyleSheet("""
            QPushButton {
                background-color: #995500;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #AA6600;
            }
        """)
        self.delete_enhanced_btn.clicked.connect(self.onDeleteEnhanced)
        right_layout.addWidget(self.delete_enhanced_btn)

        # Кнопка сохранения
        self.save_btn = QPushButton("Сохранить результат")
        self.save_btn.setStyleSheet("""
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
        self.save_btn.clicked.connect(self.saveResult)
        right_layout.addWidget(self.save_btn)

        # Навигация
        nav_buttons_layout = QHBoxLayout()
        nav_buttons_layout.setSpacing(10)

        self.prev_page_btn = QPushButton("Предыдущая")
        self.prev_page_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #7E1E9F;
                        color: white;
                        border-radius: 0px;
                        border-bottom-left-radius: 8px;
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
                        border-radius: 0px;
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

        # Активируем первую страницу и настраиваем кнопки навигации
        count = len(self.image_paths)
        if count > 0:
            self.prev_page_btn.setEnabled(False)
            self.next_page_btn.setEnabled(count > 1)

        right_layout.addStretch(1)

        return right_widget

    def onScaleChanged(self, value):
        """Обработчик изменения масштаба"""
        # Обновляем поле ввода
        scale_value = value / 100
        self.outscale_input.setText(f"{scale_value:.1f}")

        # Проверяем, не превышает ли масштаб 2x
        if scale_value > 2.0 and not self.scale_warning_shown:
            # Показываем предупреждение только один раз
            self.scale_warning_shown = True

            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Предупреждение о масштабе")
            msg.setText("Внимание! Масштабирование более чем в 2 раза не рекомендуется.")
            msg.setInformativeText(
                "При увеличении изображений более чем в 2 раза, функция клининга (очистки) "
                "станет недоступна из-за слишком высокого разрешения изображений.\n\n"
                "Рекомендуется использовать масштаб не более 2.0x для сохранения "
                "возможности последующей обработки."
            )
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec()
        elif scale_value <= 2.0:
            # Сбрасываем флаг, если масштаб вернулся в нормальный диапазон
            self.scale_warning_shown = False

        # Обновляем информацию о проекте
        self.update_project_info()
    def onImageTypeChanged(self, checked):
        """Обработчик изменения типа изображения"""
        if not checked:
            return

        btn = self.image_type_group.checkedButton()
        if not btn:
            return

        if btn.text() == "Оригинал":
            self.viewer.set_enhanced(False)
            self.show_enhanced = False
            logger.debug("Переключение на ОРИГИНАЛ")
        else:
            self.viewer.set_enhanced(True)
            self.show_enhanced = True
            logger.debug("Переключение на УЛУЧШЕННОЕ")

        # Обновляем информацию о проекте
        self.update_project_info()

    def onStatusChanged(self):
        """Обработчик изменения статуса обработки"""
        btn = self.status_group.checkedButton()
        if not btn:
            return

        st = btn.text()
        preproc_folder = self.ch_paths["preproc"]
        json_path = os.path.join(preproc_folder, "preprocessing.json")

        # Обновляем chapter.json
        ch_json_path = os.path.join(self.ch_folder, "chapter.json")
        if os.path.exists(ch_json_path):
            try:
                with open(ch_json_path, 'r', encoding='utf-8') as f:
                    ch_data = json.load(f)

                if "stages" in ch_data:
                    if st == "Не начат":
                        ch_data["stages"]["Предобработка"] = False
                    elif st == "В работе":
                        ch_data["stages"]["Предобработка"] = "partial"
                    elif st == "Завершен":
                        ch_data["stages"]["Предобработка"] = True

                with open(ch_json_path, 'w', encoding='utf-8') as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"Ошибка при обновлении chapter.json: {e}")

        # Сохраняем статус в preprocessing.json
        data = {"status": st}
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def loadStatus(self):
        """Загрузка статуса обработки"""
        preproc_folder = self.ch_paths["preproc"]
        json_path = os.path.join(preproc_folder, "preprocessing.json")

        # Проверяем chapter.json
        ch_json_path = os.path.join(self.ch_folder, "chapter.json")
        if os.path.exists(ch_json_path):
            try:
                with open(ch_json_path, 'r', encoding='utf-8') as f:
                    ch_data = json.load(f)

                preproc_status = ch_data.get("stages", {}).get("Предобработка", False)

                if preproc_status is True:
                    self.status_done.setChecked(True)
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump({"status": "Завершен"}, f, ensure_ascii=False, indent=4)
                    return
                elif preproc_status == "partial":
                    self.status_in_prog.setChecked(True)
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump({"status": "В работе"}, f, ensure_ascii=False, indent=4)
                    return
            except Exception as e:
                logger.error(f"Ошибка при чтении chapter.json: {e}")

        # Если не удалось получить из chapter.json, проверяем preprocessing.json
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    st = data.get("status", "Не начат")
                    if st == "Не начат":
                        self.status_not.setChecked(True)
                    elif st == "В работе":
                        self.status_in_prog.setChecked(True)
                    elif st == "Завершен":
                        self.status_done.setChecked(True)
            except Exception as e:
                logger.error(f"Ошибка при чтении preprocessing.json: {e}")

    def onPreviousPage(self):
        """Обработчик кнопки 'Предыдущая'"""
        logger.debug(f"Запрос предыдущей страницы, текущая: {self.viewer.current_page}")

        if self.viewer.previousPage():
            logger.debug(f"Переход выполнен, новая страница: {self.viewer.current_page}")

            # Обновляем состояние кнопок навигации
            if self.viewer.current_page == 0:
                self.prev_page_btn.setEnabled(False)
            self.next_page_btn.setEnabled(True)
        else:
            logger.debug("Переход на предыдущую страницу не выполнен")

    def onNextPage(self):
        """Обработчик кнопки 'Следующая'"""
        logger.debug(f"Запрос следующей страницы, текущая: {self.viewer.current_page}")

        if self.viewer.nextPage():
            logger.debug(f"Переход выполнен, новая страница: {self.viewer.current_page}")

            # Обновляем состояние кнопок навигации
            if self.viewer.current_page == len(self.viewer.pages) - 1:
                self.next_page_btn.setEnabled(False)
            self.prev_page_btn.setEnabled(True)
        else:
            logger.debug("Переход на следующую страницу не выполнен")

    def showDeleteEnhancedDialog(self):
        """Показывает диалог выбора режима удаления улучшенных изображений"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Удаление улучшенных изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(450)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Заголовок
        label = QLabel("Выберите режим удаления улучшенных изображений:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        # Проверяем наличие улучшенного изображения для текущей страницы
        current_enhanced_exists = False
        if 0 <= self.viewer.current_page < len(self.image_paths):
            current_path = self.image_paths[self.viewer.current_page]
            base = os.path.splitext(os.path.basename(current_path))[0]
            ext = os.path.splitext(current_path)[1]
            enh_path = os.path.join(self.enhanced_folder, f"{base}_enhanced{ext}")
            current_enhanced_exists = os.path.exists(enh_path)

        # Информация о текущей странице
        current_page_info = QLabel(f"Текущая страница: {self.viewer.current_page + 1} из {len(self.image_paths)}")
        current_page_info.setStyleSheet("font-size: 12px; color: #888;")
        layout.addWidget(current_page_info)

        # Кнопки выбора
        buttons_layout = QVBoxLayout()
        buttons_layout.setSpacing(10)

        # Кнопка "Удалить текущее"
        current_btn = QPushButton(
            f"Удалить только текущее улучшенное изображение (стр. {self.viewer.current_page + 1})")
        current_btn.setStyleSheet("""
            QPushButton {
                background-color: #995500;
                color: white;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #AA6600;
            }
            QPushButton:disabled {
                background-color: #666;
                color: #999;
            }
        """)
        current_btn.clicked.connect(lambda: dialog.done(1))  # Вернуть 1 для текущего
        current_btn.setEnabled(current_enhanced_exists)

        if not current_enhanced_exists:
            current_btn.setText(
                f"Удалить текущее (стр. {self.viewer.current_page + 1}) - улучшенное изображение отсутствует")

        buttons_layout.addWidget(current_btn)

        # Подсчитываем количество улучшенных изображений
        enhanced_count = 0
        for path in self.image_paths:
            base = os.path.splitext(os.path.basename(path))[0]
            ext = os.path.splitext(path)[1]
            enh_path = os.path.join(self.enhanced_folder, f"{base}_enhanced{ext}")
            if os.path.exists(enh_path):
                enhanced_count += 1

        # Кнопка "Удалить все"
        all_btn = QPushButton(f"Удалить все улучшенные изображения ({enhanced_count} шт.)")
        all_btn.setStyleSheet("""
            QPushButton {
                background-color: #CC3333;
                color: white;
                border-radius: 8px;
                padding: 12px 20px;
                font-size: 14px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #DD4444;
            }
            QPushButton:disabled {
                background-color: #666;
                color: #999;
            }
        """)
        all_btn.clicked.connect(lambda: dialog.done(2))  # Вернуть 2 для всех
        all_btn.setEnabled(enhanced_count > 0)

        if enhanced_count == 0:
            all_btn.setText("Удалить все - нет улучшенных изображений")

        buttons_layout.addWidget(all_btn)

        layout.addLayout(buttons_layout)
        layout.addStretch()

        # Кнопка отмены
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
        cancel_btn.clicked.connect(lambda: dialog.done(0))  # Вернуть 0 для отмены

        cancel_layout = QHBoxLayout()
        cancel_layout.addStretch()
        cancel_layout.addWidget(cancel_btn)
        layout.addLayout(cancel_layout)

        return dialog.exec()

    def onDeleteEnhanced(self):
        """Удаляет улучшенные изображения"""
        # Проверяем наличие улучшенных изображений
        has_enhanced = check_enhanced_availability(self.image_paths, self.enhanced_folder)

        if not has_enhanced:
            show_message(self, "Информация", "Нет улучшенных изображений для удаления.", QMessageBox.Information)
            return

        # Показываем диалог выбора
        choice = self.showDeleteEnhancedDialog()

        if choice == 0:  # Отмена
            return
        elif choice == 1:  # Удалить текущее
            self._deleteCurrentEnhanced()
        elif choice == 2:  # Удалить все
            self._deleteAllEnhanced()

    def _deleteCurrentEnhanced(self):
        """Удаляет улучшенное изображение текущей страницы"""
        if self.viewer.current_page < 0 or self.viewer.current_page >= len(self.image_paths):
            return

        current_path = self.image_paths[self.viewer.current_page]

        # Запрашиваем подтверждение
        reply = QMessageBox.question(
            self, "Удаление улучшенного изображения",
            f"Вы уверены, что хотите удалить улучшенное изображение для страницы {self.viewer.current_page + 1}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # Выполняем удаление
            if delete_enhanced_image(current_path, self.enhanced_folder):
                # Проверяем, остались ли еще улучшенные изображения
                has_more_enhanced = check_enhanced_availability(self.image_paths, self.enhanced_folder)

                if not has_more_enhanced:
                    # Если это было последнее улучшенное изображение
                    self.enhanced_radio.setEnabled(False)
                    self.original_radio.setChecked(True)
                    self.viewer.set_enhanced(False)
                    self.show_enhanced = False
                else:
                    # Если текущий режим - показ улучшенных, переключаемся на оригинал для этой страницы
                    if self.show_enhanced:
                        self.viewer.displayCurrentPage()

                # Обновляем информацию
                self.update_project_info()

                show_message(self, "Удаление выполнено",
                             f"Улучшенное изображение для страницы {self.viewer.current_page + 1} удалено.")
            else:
                show_message(self, "Ошибка",
                             "Не удалось удалить улучшенное изображение.", QMessageBox.Warning)

    def _deleteAllEnhanced(self):
        """Удаляет все улучшенные изображения"""
        # Запрашиваем подтверждение
        reply = QMessageBox.question(
            self, "Удаление всех улучшенных изображений",
            "Вы уверены, что хотите удалить ВСЕ улучшенные изображения?\n\nЭто действие необратимо!",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            # Выполняем удаление
            count = delete_all_enhanced(self.enhanced_folder)

            # Обновляем интерфейс
            self.enhanced_radio.setEnabled(False)
            self.original_radio.setChecked(True)
            self.viewer.set_enhanced(False)
            self.show_enhanced = False

            # Обновляем информацию
            self.update_project_info()

            show_message(self, "Удаление выполнено", f"Удалено {count} улучшенных изображений.")

    def runEnhancement(self):
        """Запуск процесса улучшения изображений"""
        if self.is_proc:
            # Если процесс идет - останавливаем его
            self.stopEnhancement()
            return

        # Проверяем наличие изображений
        current_index = self.viewer.current_page
        if current_index < 0 or current_index >= len(self.image_paths):
            show_message(self, "Предупреждение",
                         "Нет выбранного изображения для улучшения.", QMessageBox.Warning)
            return

        # Показываем диалог выбора
        choice = self.showEnhancementDialog()

        if choice == 0:  # Отмена
            return
        elif choice == 1:  # Текущее изображение
            current_image = self.image_paths[current_index]
            self._enhanceSingleImage(current_image, current_index)
        elif choice == 2:  # Все изображения
            self._enhanceAllImages()

    def stopEnhancement(self):
        """Остановка процесса улучшения"""
        if self.enhancement_worker:
            reply = QMessageBox.question(
                self, "Остановка улучшения",
                "Вы уверены, что хотите остановить процесс улучшения изображений?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                logger.info("Остановка процесса улучшения по запросу пользователя...")
                self.enhancement_worker.stop()

                # Обновляем интерфейс
                self.enh_prog.setValue(0)
                self._createDelayedAction(500, lambda: self.enh_prog.setVisible(False))

                # Разблокируем интерфейс
                self.unlockInterface()
                self.enhancement_worker = None

                show_message(self, "Остановлено", "Процесс улучшения изображений был остановлен.")
    def _enhanceSingleImage(self, image_path, image_index):
        """Улучшение одного изображения"""
        # Блокируем интерфейс
        self.lockInterface("Улучшение изображения")

        # Показываем прогресс-бар
        self.enh_prog.setVisible(True)
        self.enh_prog.setValue(0)

        # Создаем временную папку для исходного изображения
        temp_folder = os.path.join(self.enhanced_folder, "temp_input")
        os.makedirs(temp_folder, exist_ok=True)

        # Копируем исходное изображение во временную папку
        base_name = os.path.basename(image_path)
        temp_path = os.path.join(temp_folder, base_name)
        try:
            import shutil
            shutil.copy2(image_path, temp_path)
        except Exception as e:
            logger.error(f"Ошибка при копировании изображения: {e}")
            show_message(self, "Ошибка", f"Не удалось скопировать изображение: {e}", QMessageBox.Critical)
            self.unlockInterface()
            return

        # Собираем настройки
        outscale = float(self.outscale_input.text())
        tile_size = self.tile_select.currentData()

        settings = {
            "model_name": self.model_select.currentText(),
            "denoise_strength": 0.5,
            "outscale": outscale,
            "tile": tile_size,
            "tile_pad": 10,
            "pre_pad": 0,
            "face_enhance": self.face_enhance_checkbox.isChecked(),
            "fp32": False,
            "alpha_upsampler": "realesrgan",
            "suffix": "enhanced",
            "gpu_id": self.gpu_select.currentData(),
            "num_processes": 1,
        }

        # Создаем воркер для улучшения
        worker = EnhancementWorker(
            input_path=temp_folder,
            output_path=self.enhanced_folder,
            settings=settings
        )

        # Подключаем сигналы
        worker.signals.progress.connect(self._updateEnhProgress)
        worker.signals.finished.connect(lambda: self._onSingleEnhFinished(image_index))
        worker.signals.error.connect(self._onEnhError)

        # Запускаем в отдельном потоке
        self.q_thread_pool.start(worker)
        self.enhancement_worker = worker

    def _onSingleEnhFinished(self, image_index):
        """Обработка завершения улучшения одного изображения"""
        # Удаляем временную папку
        temp_folder = os.path.join(self.enhanced_folder, "temp_input")
        if os.path.exists(temp_folder):
            try:
                import shutil
                shutil.rmtree(temp_folder)
            except Exception as e:
                logger.error(f"Ошибка при удалении временной папки: {e}")

        # Обновляем изображение в просмотрщике
        self.viewer.updateImages()

        # Включаем опцию "Улучшенное" и переключаемся на нее
        self.enhanced_radio.setEnabled(True)
        self.enhanced_radio.setChecked(True)
        self.viewer.set_enhanced(True)
        self.show_enhanced = True

        # Статус "В работе"
        self.status_in_prog.setChecked(True)
        self.onStatusChanged()

        # Показываем сообщение
        show_message(self, "Готово",
                     f"Улучшение изображения (страница {image_index + 1}) завершено успешно!")

        # Обновляем информацию
        self.update_project_info()

        # Скрываем прогресс-бар через 3 секунды
        self._createDelayedAction(3000, lambda: self.enh_prog.setVisible(False) if hasattr(self,
                                                                                           'enh_prog') and self.enh_prog else None)

        # Разблокируем интерфейс
        self.unlockInterface()
        self.enhancement_worker = None

    def _onEnhFinished(self):
        """Обработка завершения улучшения"""
        self.enh_prog.setValue(100)

        # Обновляем изображения в просмотрщике
        self.viewer.updateImages()

        # Включаем опцию "Улучшенное" и переключаемся на нее
        self.enhanced_radio.setEnabled(True)
        self.enhanced_radio.setChecked(True)
        self.viewer.set_enhanced(True)
        self.show_enhanced = True

        # Статус "В работе"
        self.status_in_prog.setChecked(True)
        self.onStatusChanged()

        # Показываем сообщение
        show_message(self, "Готово", "Улучшение изображений завершено успешно!")

        # Обновляем информацию
        self.update_project_info()

        # Скрываем прогресс-бар через 3 секунды
        self._createDelayedAction(3000, lambda: self.enh_prog.setVisible(False) if hasattr(self,
                                                                                           'enh_prog') and self.enh_prog else None)

        # Разблокируем интерфейс
        self.unlockInterface()
        self.enhancement_worker = None

    def _enhanceAllImages(self):
        """Улучшение всех изображений"""
        # Блокируем интерфейс
        self.lockInterface("Улучшение изображений")

        # Показываем прогресс-бар
        self.enh_prog.setVisible(True)
        self.enh_prog.setValue(0)

        # Собираем настройки
        outscale = float(self.outscale_input.text())
        tile_size = self.tile_select.currentData()

        settings = {
            "model_name": self.model_select.currentText(),
            "denoise_strength": 0.5,
            "outscale": outscale,
            "tile": tile_size,
            "tile_pad": 10,
            "pre_pad": 0,
            "face_enhance": self.face_enhance_checkbox.isChecked(),
            "fp32": False,
            "alpha_upsampler": "realesrgan",
            "suffix": "enhanced",
            "gpu_id": self.gpu_select.currentData(),
            "num_processes": 1,
        }

        # Создаем воркер для улучшения
        worker = EnhancementWorker(
            input_path=self.originals_folder,
            output_path=self.enhanced_folder,
            settings=settings
        )

        # Подключаем сигналы
        worker.signals.progress.connect(self._updateEnhProgress)
        worker.signals.finished.connect(self._onEnhFinished)
        worker.signals.error.connect(self._onEnhError)

        # Запускаем в отдельном потоке
        self.q_thread_pool.start(worker)
        self.enhancement_worker = worker

    def _updateEnhProgress(self, value):
        """Обновление прогресса улучшения"""
        self.enh_prog.setValue(value)
        QApplication.processEvents()

    def _onEnhFinished(self):
        """Обработка завершения улучшения"""
        self.enh_prog.setValue(100)

        # Обновляем изображения в просмотрщике
        self.viewer.updateImages()

        # Включаем опцию "Улучшенное" и переключаемся на нее
        self.enhanced_radio.setEnabled(True)
        self.enhanced_radio.setChecked(True)
        self.viewer.set_enhanced(True)
        self.show_enhanced = True

        # Статус "В работе"
        self.status_in_prog.setChecked(True)
        self.onStatusChanged()

        # Показываем сообщение
        show_message(self, "Готово", "Улучшение изображений завершено успешно!")

        # Обновляем информацию
        self.update_project_info()

        # Скрываем прогресс-бар через 3 секунды с проверкой
        def hide_progress():
            if hasattr(self, 'enh_prog') and self.enh_prog:
                try:
                    self.enh_prog.setVisible(False)
                except RuntimeError:
                    pass  # Объект уже удален

        QTimer.singleShot(3000, hide_progress)

        # Разблокируем интерфейс
        self.unlockInterface()
        self.enhancement_worker = None

    def _onEnhError(self, error_msg):
        """Обработка ошибки улучшения"""
        self.enh_prog.setValue(0)

        # Показываем сообщение об ошибке
        show_message(self, "Ошибка", f"Произошла ошибка при улучшении изображений:\n{error_msg}", QMessageBox.Critical)

        # Скрываем прогресс-бар через 3 секунды
        self._createDelayedAction(3000, lambda: self.enh_prog.setVisible(False) if hasattr(self,
                                                                                           'enh_prog') and self.enh_prog else None)

        # Разблокируем интерфейс и возвращаем кнопку в исходное состояние
        self.unlockInterface()
        self.enhancement_worker = None

    def lockInterface(self, operation):
        """Блокировка интерфейса при длительных операциях"""
        self.is_proc = True
        self.curr_op = operation

        # Меняем кнопку улучшения на кнопку остановки
        self.enh_btn.setText("Остановить улучшение")
        self.enh_btn.setStyleSheet("""
            QPushButton {
                background-color: #CC3333;
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #DD4444;
            }
        """)
        # Кнопка остается активной для возможности остановки
        self.enh_btn.setEnabled(True)

        # Блокируем остальные кнопки
        self.delete_enhanced_btn.setEnabled(False)
        self.save_btn.setEnabled(False)

        QApplication.processEvents()

    def unlockInterface(self):
        """Разблокировка интерфейса после операций"""
        self.is_proc = False
        self.curr_op = None

        # Возвращаем кнопку улучшения в исходное состояние
        self.enh_btn.setText("Улучшить изображения")
        self.enh_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)

        # Разблокируем все кнопки
        self.enh_btn.setEnabled(True)
        self.delete_enhanced_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

        QApplication.processEvents()

    def saveResult(self):
        """Сохранение результата обработки в папку Save"""
        # Блокируем интерфейс во время операции
        self.lockInterface("Сохранение результата")

        try:
            # Создаем папку Save
            save_folder = os.path.join(self.ch_paths["preproc"], "Save")
            os.makedirs(save_folder, exist_ok=True)

            # Очищаем папку Save
            for file in os.listdir(save_folder):
                file_path = os.path.join(save_folder, file)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(f"Ошибка при удалении файла {file_path}: {e}")

            # Проверяем наличие файлов
            files = [f for f in os.listdir(self.originals_folder) if
                     f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')) and
                     os.path.isfile(os.path.join(self.originals_folder, f))]

            if not files:
                show_message(self, "Предупреждение", "Нет файлов для сохранения!")
                return

            # Подсчитываем улучшенные изображения
            enhanced_count = 0
            missing_enhanced = []

            for f in files:
                base = os.path.splitext(f)[0]
                ext = os.path.splitext(f)[1]
                enh_file = f"{base}_enhanced{ext}"
                enh_path = os.path.join(self.enhanced_folder, enh_file)

                if os.path.exists(enh_path):
                    enhanced_count += 1
                else:
                    missing_enhanced.append(f)

            # Если не все изображения улучшены - показываем предупреждение
            if missing_enhanced:
                message = f"Внимание! Не все изображения были улучшены.\n\n"
                message += f"Улучшено: {enhanced_count} из {len(files)}\n"
                message += f"Будут сохранены оригиналы для {len(missing_enhanced)} изображений."

                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Warning)
                msg.setWindowTitle("Не все изображения улучшены")
                msg.setText(message)
                msg.setStandardButtons(QMessageBox.Ok)
                msg.exec()

            # Копируем файлы согласно алгоритму
            copied_count = 0
            errors = []

            for f in files:
                base = os.path.splitext(f)[0]
                ext = os.path.splitext(f)[1]

                # Проверяем наличие улучшенного изображения
                enh_file = f"{base}_enhanced{ext}"
                enh_path = os.path.join(self.enhanced_folder, enh_file)

                # Путь назначения в папке Save (без суффикса _enhanced)
                dst_path = os.path.join(save_folder, f)

                try:
                    if os.path.exists(enh_path):
                        # Если есть улучшенное - копируем его
                        import shutil
                        shutil.copy2(enh_path, dst_path)
                        logger.debug(f"Скопировано улучшенное: {enh_file} -> {f}")
                    else:
                        # Если нет улучшенного - копируем оригинал
                        src_path = os.path.join(self.originals_folder, f)
                        import shutil
                        shutil.copy2(src_path, dst_path)
                        logger.debug(f"Скопирован оригинал: {f}")

                    copied_count += 1

                except Exception as e:
                    error_msg = f"Ошибка при копировании {f}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

            # Статус "Завершен"
            self.status_done.setChecked(True)
            self.onStatusChanged()

            # Показываем результат
            if errors:
                error_text = "\n".join(errors[:5])  # Показываем первые 5 ошибок
                show_message(
                    self, "Сохранение завершено с ошибками",
                    f"Сохранено {copied_count} из {len(files)} файлов.\n\nОшибки:\n{error_text}",
                    QMessageBox.Warning
                )
            else:
                show_message(
                    self, "Готово",
                    f"Результат успешно сохранен в папку Save!\n"
                    f"Сохранено файлов: {copied_count}\n"
                    f"Из них улучшенных: {enhanced_count}"
                )

        except Exception as e:
            show_message(self, "Ошибка", f"Произошла ошибка при сохранении результата: {str(e)}", QMessageBox.Critical)
        finally:
            # Разблокируем интерфейс
            self.unlockInterface()

    def keyPressEvent(self, event):
        """Обработчик нажатий клавиш"""
        if event.key() == Qt.Key_Space:
            # Переключение между оригиналом и улучшенным
            self.show_enhanced = not self.show_enhanced
            if self.show_enhanced:
                self.enhanced_radio.setChecked(True)
            else:
                self.original_radio.setChecked(True)

            self.viewer.set_enhanced(self.show_enhanced)
            state = "Улучшенное" if self.show_enhanced else "Оригинал"
            logger.debug(f"Переключено на {state}.")

            # Обновляем информацию о проекте
            self.update_project_info()
        elif event.key() == Qt.Key_Left:
            self.onPreviousPage()
            event.accept()
            return
        elif event.key() == Qt.Key_Right:
            self.onNextPage()
            event.accept()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        """Обработчик события закрытия окна"""
        self._closing = True

        # Отключаем все сигналы чтобы избежать вызовов после закрытия
        try:
            self.page_change_signal.page_changed.disconnect()
            if hasattr(self, 'image_loader'):
                self.image_loader.image_loaded.disconnect()
                self.image_loader.loading_progress.disconnect()
                self.image_loader.loading_complete.disconnect()
        except:
            pass

        # Проверяем, идет ли процесс улучшения
        if self.is_proc and self.enhancement_worker:
            reply = QMessageBox.question(
                self, "Подтверждение закрытия",
                "В данный момент выполняется улучшение изображений.\n"
                "Прервать процесс и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.No:
                event.ignore()
                self._closing = False
                return

            # Останавливаем процесс
            logger.info("Остановка процесса улучшения...")
            self.enhancement_worker.stop()

            # Даем больше времени для корректного завершения
            QApplication.processEvents()

            # Ждем завершения воркера
            if hasattr(self, 'q_thread_pool'):
                self.q_thread_pool.waitForDone(2000)  # Ждем до 2 секунд

        # Выполняем закрытие
        self._performClose()
        event.accept()

    def _performClose(self):
        """Выполнение закрытия окна"""
        self._closing = True

        # Останавливаем все активные таймеры
        for timer in self.active_timers[:]:
            try:
                if timer and hasattr(timer, 'stop'):
                    timer.stop()
                    timer.deleteLater()
            except RuntimeError:
                pass
        self.active_timers.clear()


        # Останавливаем check_enhanced_timer
        if hasattr(self, 'check_enhanced_timer') and self.check_enhanced_timer:
            try:
                self.check_enhanced_timer.stop()
                self.check_enhanced_timer.deleteLater()
            except RuntimeError:
                pass

        # Отменяем загрузку изображений
        if hasattr(self, 'image_loader') and self.image_loader:
            self.image_loader.cancel()
            QApplication.processEvents()

        # Закрываем пул потоков с ожиданием
        if hasattr(self, 'thread_pool') and self.thread_pool:
            try:
                import sys
                if sys.version_info >= (3, 9):
                    self.thread_pool.shutdown(wait=True, cancel_futures=True)
                else:
                    self.thread_pool.shutdown(wait=True)
            except Exception as e:
                logger.error(f"Ошибка при закрытии thread_pool: {e}")
                self.thread_pool.shutdown(wait=True)

        # Очищаем QThreadPool
        if hasattr(self, 'q_thread_pool'):
            self.q_thread_pool.clear()
            self.q_thread_pool.waitForDone(1000)

        # Очищаем GPU ресурсы
        self._cleanup_gpu_resources()

        # Очищаем ссылки
        self.enhancement_worker = None

        # Удаляем временные виджеты
        for attr in ['loading_overlay', 'overlay_widget']:
            if hasattr(self, attr):
                widget = getattr(self, attr)
                if widget:
                    try:
                        widget.close()
                        widget.deleteLater()
                    except:
                        pass
                    setattr(self, attr, None)

        # Очищаем эффекты
        if hasattr(self, 'bg_widget') and self.bg_widget:
            self.bg_widget.setGraphicsEffect(None)

        if hasattr(self, 'blur_effect'):
            self.blur_effect = None
