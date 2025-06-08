# -*- coding: utf-8 -*-
# ui/windows/m7_3_ui_components.py

import os
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, Signal, QObject, QEvent, QPointF
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QProgressBar, QGraphicsProxyWidget)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


# События загрузки изображений
class ImageLoadedEvent(QEvent):
    """Событие загрузки одного изображения"""
    EventType = QEvent.Type(QEvent.User + 1)

    def __init__(self, index=None):
        super().__init__(ImageLoadedEvent.EventType)
        self.index = index


class AllImagesLoadedEvent(QEvent):
    """Событие загрузки всех изображений"""
    EventType = QEvent.Type(QEvent.User + 2)

    def __init__(self):
        super().__init__(AllImagesLoadedEvent.EventType)


class ImageLoader(QObject):
    """Загрузчик изображений с поддержкой оптимизации и многопоточности"""
    image_loaded = Signal(int, QPixmap, str)  # индекс, изображение, имя файла
    loading_progress = Signal(int, int, str)  # загружено, всего, имя файла
    loading_complete = Signal()
    loading_cancelled = Signal()

    def __init__(self, image_paths, thread_pool):
        super().__init__()
        self.image_paths = image_paths
        self.thread_pool = thread_pool
        self.cancel_loading = False
        self.loaded_count = 0
        self.total_count = len(image_paths) if image_paths else 0

    def start_loading(self, priority_index=0):
        """Запуск загрузки изображений"""
        # Определяем порядок загрузки: сначала текущая, потом соседние, потом остальные
        load_order = self._get_load_order(priority_index)

        self.loaded_count = 0
        self.total_count = len(self.image_paths)
        self.loading_progress.emit(0, self.total_count, "")

        # Запускаем загрузку в отдельном потоке
        thread = threading.Thread(target=self._load_thread, args=(load_order,), daemon=True)
        thread.start()

    def _get_load_order(self, priority_index):
        """Определяет оптимальный порядок загрузки изображений"""
        load_order = [priority_index]

        # Добавляем соседние страницы
        for offset in range(1, 4):
            if priority_index + offset < len(self.image_paths):
                load_order.append(priority_index + offset)
            if priority_index - offset >= 0:
                load_order.append(priority_index - offset)

        # Добавляем оставшиеся страницы
        for i in range(len(self.image_paths)):
            if i not in load_order:
                load_order.append(i)

        return load_order

    def _load_image(self, idx):
        """Загрузка одного изображения"""
        if self.cancel_loading:
            return None, idx, ""

        try:
            path = self.image_paths[idx]
            current_file = os.path.basename(path)
            pixmap = QPixmap(path)

            if not pixmap.isNull():
                return pixmap, idx, current_file
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения {idx}: {str(e)}")

        return None, idx, ""

    def _load_thread(self, load_order):
        """Поток загрузки изображений"""
        try:
            futures = []

            # Создаем задачи для загрузки всех изображений
            for idx in load_order:
                if self.cancel_loading:
                    break

                future = self.thread_pool.submit(self._load_image, idx)
                futures.append(future)

                # Небольшая задержка чтобы не блокировать GUI
                time.sleep(0.05)

            # Обрабатываем результаты по мере их завершения
            for future in as_completed(futures):
                if self.cancel_loading:
                    break

                result = future.result()
                if result and result[0] is not None:
                    pixmap, idx, current_file = result
                    self.image_loaded.emit(idx, pixmap, current_file)
                    self.loaded_count += 1
                    self.loading_progress.emit(self.loaded_count, self.total_count, current_file)

            # Сигнализируем о завершении загрузки, если не было отмены
            if not self.cancel_loading:
                self.loading_complete.emit()
                QApplication.postEvent(QApplication.instance().activeWindow(), AllImagesLoadedEvent())
            else:
                self.loading_cancelled.emit()

        except Exception as e:
            logger.error(f"Ошибка в процессе загрузки: {str(e)}")
            self.loading_cancelled.emit()

    def cancel(self):
        """Отмена загрузки изображений"""
        self.cancel_loading = True
        logger.debug("Загрузка изображений отменена")


class LoadingOverlay(QWidget):
    """Центральное окно загрузки с информацией о прогрессе"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loading_overlay")

        self.setStyleSheet("""
            QWidget#loading_overlay {
                background-color: #1E1E2A;
                border-radius: 35px;
                border: 1px solid #7E1E9F;
            }
        """)

        # Размер и положение
        self.setFixedSize(550, 220)
        self._setupUI()

        # Данные о прогрессе
        self.total_images = 0
        self.loaded_images = 0
        self.loading_cancelled = False

        # Устанавливаем флаги для отображения поверх всех окон
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

    def _setupUI(self):
        """Настройка интерфейса окна загрузки"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # Заголовок
        title_label = QLabel("Загрузка изображений")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background: transparent;")
        title_label.setAlignment(Qt.AlignCenter)

        # Информационный текст
        self.info_label = QLabel("Подготовка...")
        self.info_label.setStyleSheet("color: white; background: transparent;")
        self.info_label.setAlignment(Qt.AlignCenter)

        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v из %m")
        self.progress_bar.setMinimumHeight(25)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 8px;
                background: #2D2D3A;
                padding: 2px;
                text-align: center;
                color: white;
                font-weight: bold;
                text-shadow: 1px 1px 1px black;
            }
            QProgressBar::chunk {
                background-color: #7E1E9F;
                border-radius: 6px;
            }
        """)

        # Кнопка отмены
        self.cancel_button = QPushButton("Отменить загрузку")
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EBF;
            }
        """)
        self.cancel_button.setFixedWidth(180)

        # Добавление виджетов в layout
        layout.addWidget(title_label)
        layout.addWidget(self.info_label)
        layout.addWidget(self.progress_bar)
        layout.addStretch(1)
        layout.addWidget(self.cancel_button, 0, Qt.AlignCenter)

    def updateProgress(self, loaded, total, current_file=""):
        """Обновление информации о прогрессе"""
        self.loaded_images = loaded
        self.total_images = total

        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(loaded)
            self.progress_bar.setFormat(f"{loaded} из {total}")

            if current_file:
                self.info_label.setText(f"Загрузка: {current_file}")
        else:
            # Для неопределенного прогресса
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("Загрузка...")

    def centerInParent(self):
        """Центрирование окна в родительском виджете"""
        if self.parentWidget():
            parent_rect = self.parentWidget().rect()
            self.move(
                parent_rect.width() // 2 - self.width() // 2,
                parent_rect.height() // 2 - self.height() // 2
            )