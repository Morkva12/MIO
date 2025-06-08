# -*- coding: utf-8 -*-
# ui/windows/m10_1_image_viewer.py

import os
import logging
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem)
from PySide6.QtGui import (QPainter, QPixmap, QFont, QColor, QPen, QBrush,
                           QPainterPath, QTextDocument, QTextOption)
from PySide6.QtCore import Qt, QRectF
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsTextItem, QStyleOptionGraphicsItem, QStyle)
logger = logging.getLogger(__name__)
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsTextItem, QStyleOptionGraphicsItem, QStyle)
from PySide6.QtGui import (QPainter, QPixmap, QFont, QColor, QPen, QBrush,
                           QPainterPath, QTextDocument, QTextOption)
from PySide6.QtCore import Qt, QRectF, QPointF

class ImageViewer(QGraphicsView):
    """Просмотрщик изображений для контроля качества с поддержкой текстовых блоков из тайпсеттинга"""

    def __init__(self, pixmap_paths, parent=None):
        super().__init__(parent)
        self.parent_window = parent

        # Настройки отображения
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)

        # Инициализация переменных
        self.pages = pixmap_paths
        self.current_page = 0
        self.scale_factor = 1.0
        self.typesetting_data = None

        # Создаём сцену
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)

        # Создаем элементы для отображения
        self.pixmaps = [QPixmap() for _ in range(len(pixmap_paths))]
        self.page_pixmap_item = QGraphicsPixmapItem()
        self.scene_.addItem(self.page_pixmap_item)

        # Инициализация размера сцены с отступами
        self._setSceneRectWithMargin(QRectF(0, 0, 800, 600), margin=80)

    def setTypesettingData(self, data):
        """Устанавливает данные тайпсеттинга для отображения текстовых блоков"""
        self.typesetting_data = data
        self.update()

        # Перерисовываем текущую страницу
        if 0 <= self.current_page < len(self.pages):
            self.displayCurrentPage()

    def _setSceneRectWithMargin(self, rect, margin=80):
        """Устанавливает размер сцены с отступами"""
        self.setSceneRect(rect.x() - margin, rect.y() - margin,
                          rect.width() + 2 * margin, rect.height() + 2 * margin)

    def displayCurrentPage(self):
        """Отображает текущую страницу с текстовыми блоками"""
        if not 0 <= self.current_page < len(self.pages) or not self.pixmaps:
            return

        # Отображаем текущую страницу
        pm = self.pixmaps[self.current_page]
        if pm.isNull():
            logger.warning(f"Пустое изображение для страницы {self.current_page}")
            return

        # Создаем изображение с наложенным текстом
        result_pixmap = self._createPixmapWithText(pm, self.current_page)

        # Устанавливаем изображение
        self.page_pixmap_item.setPixmap(result_pixmap)
        self.page_pixmap_item.setPos(0, 0)

        # Устанавливаем размер сцены с учетом размера изображения
        self._setSceneRectWithMargin(QRectF(0, 0, result_pixmap.width(), result_pixmap.height()), 80)

        # Обновляем информацию о странице в родительском окне
        if hasattr(self.parent_window, 'updatePageInfo'):
            self.parent_window.updatePageInfo()

    def _createPixmapWithText(self, base_pixmap, page_index):
        """Создает изображение с наложенными текстовыми блоками"""
        if not self.typesetting_data or "text_blocks" not in self.typesetting_data:
            return base_pixmap

        # Создаем результирующее изображение
        result = QPixmap(base_pixmap.size())
        result.fill(Qt.white)

        painter = QPainter()
        if not painter.begin(result):
            return base_pixmap

        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

            # Рисуем базовое изображение
            painter.drawPixmap(0, 0, base_pixmap)

            # Рисуем текстовые блоки для текущей страницы
            text_blocks = self.typesetting_data.get("text_blocks", [])

            for block_data in text_blocks:
                if block_data.get("page_index") == page_index:
                    self._drawTextBlock(painter, block_data)

        finally:
            painter.end()

        return result

    def _drawTextBlock(self, painter, block_data):
        """Отрисовывает текстовый блок точно как при экспорте в m9"""
        # Получаем параметры блока
        text = block_data.get("text", "")
        if not text:
            return

        pos = block_data.get("position", {})
        x = pos.get("x", 0)
        y = pos.get("y", 0)
        width = block_data.get("width", 200)
        height = block_data.get("height", 60)

        settings = block_data.get("settings", {})

        # Сохраняем состояние и перемещаемся в позицию блока (как в exportAsImages)
        painter.save()
        painter.translate(x, y)

        # Включаем антиалиасинг (как в paint() из m9)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Создаем прямоугольник (boundingRect в локальных координатах)
        rect = QRectF(0, 0, width, height)

        # Фон с закруглёнными углами (из paint() в m9)
        bg_color = settings.get("background_color", "transparent")
        if bg_color and bg_color != "transparent":
            painter.setPen(Qt.NoPen)
            bg_qcolor = QColor(bg_color)
            if bg_qcolor.isValid():
                painter.setBrush(QBrush(bg_qcolor))
                painter.drawRoundedRect(rect, 5, 5)

        # Получаем текст
        if not text:
            painter.restore()
            return

        # Настройка шрифта
        font = QFont(
            settings.get("font_family", "Arial"),
            settings.get("font_size", 16)
        )
        font.setBold(settings.get("bold", False))
        font.setItalic(settings.get("italic", False))

        # Параметры для отрисовки
        outline_width = settings.get("outline_width", 2)
        outline_color = QColor(settings.get("outline_color", "#000000"))
        text_color = QColor(settings.get("text_color", "#FFFFFF"))

        # Получаем выравнивание и преобразуем в Qt.AlignmentFlag (как в m9)
        alignment = settings.get("alignment", Qt.AlignCenter)
        if isinstance(alignment, int):
            alignment = Qt.AlignmentFlag(alignment)

        # Обводка текста (код из paint() в m9)
        if outline_width > 0:
            painter.save()

            # Настройка пера для обводки
            painter.setPen(QPen(outline_color, outline_width * 2,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)

            # Используем QTextDocument для правильного рендеринга с выравниванием
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setPlainText(text)
            doc.setTextWidth(rect.width())

            # Устанавливаем выравнивание для документа
            text_option = QTextOption()
            text_option.setAlignment(alignment)
            text_option.setWrapMode(QTextOption.WordWrap)
            doc.setDefaultTextOption(text_option)

            # Получаем layout документа
            layout = doc.documentLayout()

            # Проходим по всем блокам текста
            block = doc.begin()
            while block.isValid():
                block_layout = block.layout()
                if block_layout:
                    # Получаем позицию блока
                    block_rect = layout.blockBoundingRect(block)

                    # Для каждой строки в блоке
                    for i in range(block_layout.lineCount()):
                        line = block_layout.lineAt(i)

                        # Получаем естественную ширину строки
                        line_width = line.naturalTextWidth()

                        # Вычисляем x-позицию в зависимости от выравнивания
                        x_offset = 0
                        if alignment == Qt.AlignCenter or alignment == Qt.AlignHCenter:
                            x_offset = (rect.width() - line_width) / 2
                        elif alignment == Qt.AlignRight:
                            x_offset = rect.width() - line_width

                        # Получаем текст строки
                        start = block.position() + line.textStart()
                        length = line.textLength()
                        line_text = text[start:start + length].rstrip('\n')

                        if line_text:
                            # Создаем путь для строки с правильным выравниванием
                            path = QPainterPath()
                            y_pos = block_rect.top() + line.position().y() + line.ascent()
                            path.addText(x_offset, y_pos, font, line_text)
                            painter.drawPath(path)

                block = block.next()

            painter.restore()

        # Основной текст (эмулируем super().paint() из QGraphicsTextItem)
        # Создаем временный QGraphicsTextItem точно как в m9
        from PySide6.QtWidgets import QGraphicsTextItem
        temp_item = QGraphicsTextItem()
        temp_item.setPlainText(text)
        temp_item.setFont(font)
        temp_item.setDefaultTextColor(text_color)
        temp_item.setTextWidth(width)

        # Устанавливаем выравнивание текста
        option = temp_item.document().defaultTextOption()
        option.setAlignment(alignment)
        option.setWrapMode(QTextOption.WordWrap)
        temp_item.document().setDefaultTextOption(option)

        # Отрисовываем через paint() как в m9
        style_option = QStyleOptionGraphicsItem()
        temp_item.paint(painter, style_option, None)

        painter.restore()

    def exportPageWithText(self, page_index, file_path):
        """Экспортирует страницу с текстовыми блоками"""
        if not 0 <= page_index < len(self.pixmaps):
            logger.error(f"Неверный индекс страницы: {page_index}")
            return False

        pm = self.pixmaps[page_index]
        if pm.isNull():
            logger.error(f"Пустое изображение для страницы {page_index}")
            return False

        try:
            # Создаем изображение с текстом
            result = self._createPixmapWithText(pm, page_index)

            # Сохраняем с максимальным качеством
            success = result.save(file_path, "PNG", 100)

            if success:
                logger.info(f"Успешно экспортирована страница {page_index} в {file_path}")
            else:
                logger.error(f"Не удалось сохранить страницу {page_index} в {file_path}")

            return success

        except Exception as e:
            logger.error(f"Ошибка при экспорте страницы {page_index}: {str(e)}")
            return False

    def nextPage(self):
        """Переход на следующую страницу"""
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.displayCurrentPage()
            return True

        return False

    def previousPage(self):
        """Переход на предыдущую страницу"""
        if self.current_page > 0:
            self.current_page -= 1
            self.displayCurrentPage()
            return True

        return False

    def wheelEvent(self, event):
        """Обработка прокрутки колесом мыши для масштабирования"""
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            zoom_factor = 1.15 if delta > 0 else 1 / 1.15
            new_scale = self.scale_factor * zoom_factor

            if 0.05 <= new_scale <= 20.0:
                self.scale(zoom_factor, zoom_factor)
                self.scale_factor = new_scale
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        """Обработка нажатия клавиш"""
        if event.key() == Qt.Key_Left:
            self.previousPage()
            if hasattr(self.parent_window, 'updateActiveThumbnail'):
                self.parent_window.updateActiveThumbnail(self.current_page)
            event.accept()
            return
        elif event.key() == Qt.Key_Right:
            self.nextPage()
            if hasattr(self.parent_window, 'updateActiveThumbnail'):
                self.parent_window.updateActiveThumbnail(self.current_page)
            event.accept()
            return

        super().keyPressEvent(event)