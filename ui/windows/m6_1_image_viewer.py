# -*- coding: utf-8 -*-
# ui/windows/m6_1_image_viewer.py

import os
import logging
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsTextItem, QGraphicsRectItem)
from PySide6.QtGui import QPainter, QPixmap, QFont, QColor, QPen, QBrush, QTransform, QCursor
from PySide6.QtCore import Qt, QRectF, QEvent, QTimer, QPointF

logger = logging.getLogger(__name__)


class ImageViewer(QGraphicsView):
    """
    Просмотрщик изображений для режима постраничного просмотра.
    Поддерживает отображение оригинальных и улучшенных изображений.
    """

    def __init__(self, pixmap_paths, output_folder, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.output_folder = output_folder

        # Настройки отображения
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        # Включаем режим перемещения левой кнопкой мыши
        self.setDragMode(QGraphicsView.NoDrag)  # Сначала отключаем стандартный режим
        self.panning = False
        self.last_pan_point = QPointF()
        self.setCursor(Qt.OpenHandCursor)  # Устанавливаем курсор "открытая рука"

        # Создаём сцену
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)

        # Инициализация переменных
        self.pages = pixmap_paths
        self.current_page = 0
        self.show_enhanced = False
        self.scale_factor = 1.0

        # Смещение для информационных блоков слева
        self.info_width = 240

        # Важно: создаем элементы интерфейса ПЕРЕД загрузкой изображений
        # Элементы интерфейса
        self.page_pixmap_item = QGraphicsPixmapItem()
        self.page_pixmap_item.setPos(self.info_width, 0)
        self.scene_.addItem(self.page_pixmap_item)

        # Информационные элементы
        self.info_blocks = []

        # Теперь загружаем изображения
        self.original_pixmaps = []
        self.reloadOriginalPixmaps()

        # Создаем информационные блоки
        self.createInfoBlocks()

        # Устанавливаем размер сцены
        self._setSceneRectWithMargin(QRectF(0, 0, 800, 600), margin=80)

    def reloadOriginalPixmaps(self):
        """Перезагружает оригинальные изображения"""
        self.original_pixmaps = [QPixmap(p) for p in self.pages if os.path.isfile(p)]
        # Отображаем текущую страницу только если есть все необходимые элементы интерфейса
        if hasattr(self, 'page_pixmap_item') and self.page_pixmap_item:
            self.displayCurrentPage()

    def displayCurrentPage(self):
        """Отображает текущую страницу"""
        if not hasattr(self, 'page_pixmap_item') or not self.page_pixmap_item:
            logger.error("page_pixmap_item не инициализирован")
            return

        if self.current_page < 0 or self.current_page >= len(self.pages):
            return

        orig_path = self.pages[self.current_page]
        if self.current_page >= len(self.original_pixmaps):
            return

        orig_pm = self.original_pixmaps[self.current_page]
        if orig_pm.isNull():
            return

        if self.show_enhanced:
            # Пробуем загрузить улучшенное изображение
            base = os.path.splitext(os.path.basename(orig_path))[0]
            ext = os.path.splitext(orig_path)[1]
            enh_name = f"{base}_enhanced{ext}"
            enh_path = os.path.join(self.output_folder, enh_name)

            if os.path.isfile(enh_path):
                enh_pm = QPixmap(enh_path)
                if not enh_pm.isNull():
                    self.page_pixmap_item.setPixmap(enh_pm)
                    # Адаптируем размер улучшенного изображения к размеру оригинала
                    self._fitEnhancedImageToOriginalSize(orig_pm, enh_pm)
                    # Обновляем информацию
                    self.createInfoBlocks()
                    return

        # Если нет улучшенного или show_enhanced=False, показываем оригинал
        self.page_pixmap_item.setPixmap(orig_pm)
        self.page_pixmap_item.setTransform(QTransform())  # Используем QTransform вместо Qt.transform()
        self.page_pixmap_item.setPos(self.info_width, 0)

        # Устанавливаем размер сцены
        ow, oh = orig_pm.width(), orig_pm.height()
        self._setSceneRectWithMargin(QRectF(0, 0, self.info_width + ow, oh), margin=80)

        # Обновляем информационные блоки
        self.createInfoBlocks()

    def _fitEnhancedImageToOriginalSize(self, orig_pm, enh_pm):
        """Подгоняет улучшенное изображение под размер оригинала"""
        orig_w, orig_h = orig_pm.width(), orig_pm.height()
        ew, eh = enh_pm.width(), enh_pm.height()

        if ew > 0 and eh > 0:
            sx = orig_w / ew
            sy = orig_h / eh

            transform = QTransform()  # Используем QTransform вместо Qt.transform()
            transform.scale(sx, sy)
            self.page_pixmap_item.setTransform(transform)

            # Настраиваем позицию
            self.page_pixmap_item.setPos(self.info_width, 0)

            # Устанавливаем sceneRect
            self._setSceneRectWithMargin(QRectF(0, 0, self.info_width + orig_w, orig_h), margin=80)

    def createInfoBlocks(self):
        """Создает информационные блоки с данными о текущем изображении"""
        # Удаляем старые блоки
        for item in self.info_blocks:
            self.scene_.removeItem(item)
        self.info_blocks = []

        # Проверяем наличие страниц
        if not hasattr(self, 'pages') or not self.pages or not self.original_pixmaps:
            return

        # В режиме постраничного просмотра создаем блок для текущей страницы
        if 0 <= self.current_page < len(self.pages):
            path = self.pages[self.current_page]
            if self.current_page < len(self.original_pixmaps):
                pixmap = self.original_pixmaps[self.current_page]

                if os.path.isfile(path) and not pixmap.isNull():
                    self._createInfoBlock(path, pixmap.height())

    def _createInfoBlock(self, image_path, height):
        """Создает один информационный блок"""
        # Ширина блока
        info_width = self.info_width - 20

        # Создаем фоновую подложку
        bg_color = QColor(40, 40, 50, 220)
        bg_rect = QGraphicsRectItem(0, 0, info_width, height)
        bg_rect.setBrush(QBrush(bg_color))
        bg_rect.setPen(QPen(Qt.NoPen))
        bg_rect.setPos(10, 0)
        self.scene_.addItem(bg_rect)
        self.info_blocks.append(bg_rect)

        # Добавляем тонкую линию снизу
        bottom_line = QGraphicsRectItem(0, height - 1, info_width, 1, bg_rect)
        bottom_line.setPen(QPen(Qt.NoPen))
        bottom_line.setBrush(QBrush(QColor(80, 80, 100)))
        self.info_blocks.append(bottom_line)

        # Получаем данные об оригинальном изображении
        filename = os.path.basename(image_path)
        orig_width, orig_height = self._get_image_dimensions(image_path)
        orig_kb, orig_mb = self._calculate_file_size(image_path)

        # Проверяем наличие улучшенного изображения
        base = os.path.splitext(os.path.basename(image_path))[0]
        ext = os.path.splitext(image_path)[1]
        enh_name = f"{base}_enhanced{ext}"
        enh_path = os.path.join(self.output_folder, enh_name)

        enhanced_exists = os.path.exists(enh_path)
        enh_width, enh_height, enh_kb, enh_mb = 0, 0, 0, 0

        if enhanced_exists:
            enh_width, enh_height = self._get_image_dimensions(enh_path)
            enh_kb, enh_mb = self._calculate_file_size(enh_path)

        # Определяем, какую информацию показывать
        if not self.show_enhanced or not enhanced_exists:
            # Формируем текст для оригинала
            info_text = f"<span style='color:#A490FF; font-weight:bold;'>{filename}</span><br>"
            info_text += f"<span style='color:#87CEFA;'>Оригинал:</span> {orig_width}×{orig_height} пикс.<br>"
            info_text += f"<span style='color:#87CEFA;'>Размер:</span> {orig_kb:.1f} КБ ({orig_mb:.2f} МБ)"
        else:
            # Формируем текст для улучшенного
            actual_scale = round(enh_width / orig_width, 1) if orig_width > 0 else 0
            info_text = f"<span style='color:#A490FF; font-weight:bold;'>{filename}</span><br>"
            info_text += f"<span style='color:#AAFFAA;'>Улучшенное:</span> {enh_width}×{enh_height} пикс.<br>"
            info_text += f"<span style='color:#AAFFAA;'>Размер:</span> {enh_kb:.1f} КБ ({enh_mb:.2f} МБ)<br>"
            info_text += f"<span style='color:#AAFFAA;'>Масштаб:</span> {actual_scale}×"

        # Создаем текстовый элемент
        text_item = QGraphicsTextItem()
        text_item.setHtml(info_text)
        text_item.setTextWidth(info_width - 20)
        text_item.setPos(20, 10)

        # Настраиваем шрифт
        text_font = QFont("Arial", 9)
        text_item.setFont(text_font)
        text_item.setDefaultTextColor(QColor(220, 220, 240))

        self.scene_.addItem(text_item)
        self.info_blocks.append(text_item)

        # Устанавливаем Z-index
        bg_rect.setZValue(10)
        text_item.setZValue(11)

    def _calculate_file_size(self, file_path):
        """Возвращает размер файла в КБ и МБ"""
        if os.path.exists(file_path):
            size_bytes = os.path.getsize(file_path)
            size_kb = size_bytes / 1024
            size_mb = size_kb / 1024
            return size_kb, size_mb
        return 0, 0

    def _get_image_dimensions(self, file_path):
        """Возвращает размеры изображения (ширина x высота)"""
        try:
            from PIL import Image
            with Image.open(file_path) as img:
                return img.width, img.height
        except Exception as e:
            logger.error(f"Ошибка при получении размера изображения {file_path}: {e}")
            return 0, 0

    def set_enhanced(self, show_enhanced):
        """Переключение между оригинальными и улучшенными изображениями"""
        if self.show_enhanced == show_enhanced:
            return

        self.show_enhanced = show_enhanced
        self.displayCurrentPage()

    def updateImages(self):
        """Обновляет отображение после создания новых улучшенных изображений"""
        logger.debug("Обновление изображений")
        self.displayCurrentPage()
        self.createInfoBlocks()

    def wheelEvent(self, event):
        """Обработка прокрутки колесом мыши (масштабирование)"""
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            zf = 1.15 if delta > 0 else 1 / 1.15
            new_scale = self.scale_factor * zf
            if 0.05 <= new_scale <= 20.0:
                self.scale(zf, zf)
                self.scale_factor = new_scale
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        """Обработка нажатия кнопки мыши"""
        # Если нажата левая кнопка мыши, активируем режим перемещения
        if event.button() == Qt.LeftButton:
            self.panning = True
            self.last_pan_point = event.position()
            self.setCursor(Qt.ClosedHandCursor)  # Меняем курсор на "закрытую руку"
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Обработка перемещения мыши"""
        if self.panning:
            # Если режим перемещения активен, перемещаем видимую область холста
            delta = event.position() - self.last_pan_point
            self.last_pan_point = event.position()

            # Перемещаем сцену относительно вьюпорта
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Обработка отпускания кнопки мыши"""
        if event.button() == Qt.LeftButton and self.panning:
            self.panning = False
            self.setCursor(Qt.OpenHandCursor)  # Возвращаем курсор "открытая рука"
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def nextPage(self):
        """Переход на следующую страницу"""
        logger.debug(f"Запрос следующей страницы, текущая: {self.current_page}")

        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            logger.debug(f"Переход выполнен, новая страница: {self.current_page}")
            self.displayCurrentPage()

            # Если в родительском окне есть метод для уведомления о смене страницы
            if hasattr(self.parent_window, 'updateActiveThumbnail'):
                self.parent_window.updateActiveThumbnail(self.current_page)

            # Если в родительском окне есть метод для обновления информации о странице
            if hasattr(self.parent_window, 'update_page_info'):
                self.parent_window.update_page_info(self.current_page)

            return True

        logger.debug("Достигнут конец списка страниц")
        return False

    def previousPage(self):
        """Переход на предыдущую страницу"""
        logger.debug(f"Запрос предыдущей страницы, текущая: {self.current_page}")

        if self.current_page > 0:
            self.current_page -= 1
            logger.debug(f"Переход выполнен, новая страница: {self.current_page}")
            self.displayCurrentPage()

            # Если в родительском окне есть метод для уведомления о смене страницы
            if hasattr(self.parent_window, 'updateActiveThumbnail'):
                self.parent_window.updateActiveThumbnail(self.current_page)

            # Если в родительском окне есть метод для обновления информации о странице
            if hasattr(self.parent_window, 'update_page_info'):
                self.parent_window.update_page_info(self.current_page)

            return True

        logger.debug("Достигнуто начало списка страниц")
        return False

    def _setSceneRectWithMargin(self, bounding_rect, margin=80):
        """Устанавливает размер сцены с отступами"""
        x, y, w, h = bounding_rect.x(), bounding_rect.y(), bounding_rect.width(), bounding_rect.height()
        expanded = QRectF(x - margin, y - margin, w + margin * 2, h + margin * 2)
        self.setSceneRect(expanded)