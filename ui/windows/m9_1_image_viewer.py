# -*- coding: utf-8 -*-
# ui/windows/m9_1_image_viewer.py

import os
import logging
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsTextItem, QGraphicsRectItem, QMenu,
                               QGraphicsItem, QApplication)
from PySide6.QtGui import (QPainter, QPixmap, QFont, QColor, QPen, QBrush,
                           QTextDocument, QPainterPath,QTextOption,QTextCharFormat,QTextCursor)
from PySide6.QtCore import Qt, QRectF, QPointF, QSizeF, QObject, Signal

logger = logging.getLogger(__name__)


class TextBlockModifiedSignal(QObject):
    """Сигнал изменения текстовых блоков"""
    text_blocks_modified = Signal()


class ResizeHandle(QGraphicsRectItem):
    """Маркер изменения размера для текстового блока"""

    def __init__(self, parent_item, position):
        super().__init__(-4, -4, 8, 8)
        self.parent_item = parent_item
        self.position = position  # Позиция: "bottom-right", "bottom-left", "top-right", "top-left"

        # Настройка внешнего вида
        self.setBrush(QBrush(QColor(150, 150, 255)))
        self.setPen(QPen(QColor(0, 0, 0), 1))
        self.setFlags(QGraphicsItem.ItemIsMovable)

        # Устанавливаем правильный курсор в зависимости от позиции
        if position == "bottom-right" or position == "top-left":
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.setCursor(Qt.SizeBDiagCursor)

        self.setParentItem(parent_item)
        self.setZValue(10)
        self.setVisible(False)

        # Сохраняем начальную позицию при начале перетаскивания
        self.start_pos = None
        self.start_rect = None

    def mousePressEvent(self, event):
        """Сохранение начального состояния при нажатии"""
        if event.button() == Qt.LeftButton:
            self.start_pos = event.pos()
            self.start_rect = self.parent_item.boundingRect()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Изменение размера при перетаскивании"""
        if event.buttons() & Qt.LeftButton and self.start_rect:
            # Получаем текущую позицию в координатах родителя
            new_pos = self.mapToParent(event.pos())

            # Минимальные размеры
            min_width = 50
            min_height = 30

            # Текущие размеры
            current_rect = self.parent_item.boundingRect()
            new_width = current_rect.width()
            new_height = current_rect.height()

            # Изменяем размеры в зависимости от позиции маркера
            if "right" in self.position:
                new_width = max(new_pos.x(), min_width)
            elif "left" in self.position:
                # Для левых маркеров нужно изменить и позицию блока
                delta_x = new_pos.x()
                new_width = max(current_rect.width() - delta_x, min_width)
                if new_width > min_width:
                    self.parent_item.setPos(self.parent_item.pos().x() + delta_x, self.parent_item.pos().y())

            if "bottom" in self.position:
                new_height = max(new_pos.y(), min_height)
            elif "top" in self.position:
                # Для верхних маркеров нужно изменить и позицию блока
                delta_y = new_pos.y()
                new_height = max(current_rect.height() - delta_y, min_height)
                if new_height > min_height:
                    self.parent_item.setPos(self.parent_item.pos().x(), self.parent_item.pos().y() + delta_y)

            # Применяем новые размеры
            self.parent_item.setTextWidth(new_width)
            self.parent_item._height = new_height

            # Обновляем позиции маркеров
            self.parent_item.updateHandlePositions()

            # Сигнализируем об изменении
            self.parent_item.text_modified_signal.text_blocks_modified.emit()

    def mouseReleaseEvent(self, event):
        """Окончание перетаскивания"""
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
        super().mouseReleaseEvent(event)


class TextBlockItem(QGraphicsTextItem):
    """Текстовый блок с настройками стиля для тайпсеттинга"""

    def __init__(self, text="", page_index=0, settings=None):
        super().__init__(text)
        self.settings = settings or {}
        self.page_index = page_index
        self._is_dragging = False
        self._is_resizing = False

        # Настройка флагов взаимодействия
        self.setFlags(QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemIsFocusable)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)

        # Параметры стиля - ВАЖНО: инициализируем из settings
        self._outline_width = self.settings.get("outline_width", 2)
        self._outline_color = QColor(self.settings.get("outline_color", "#000000"))

        # Инициализация цвета фона
        bg_color = self.settings.get("background_color", "transparent")
        if bg_color and bg_color != "transparent":
            self._background_color = bg_color
        else:
            self._background_color = "transparent"

        # Размеры блока
        self._width = 200
        self._height = 60

        # Сигнал изменений
        self.text_modified_signal = TextBlockModifiedSignal()
        self.document().contentsChanged.connect(self.onTextChanged)

        # Создание маркеров изменения размера
        self.resize_handles = []
        for pos in ["bottom-right", "bottom-left", "top-right", "top-left"]:
            handle = ResizeHandle(self, pos)
            self.resize_handles.append(handle)

        # Начальная ширина с переносом слов
        self.setTextWidth(self._width)

        # Применение стилей
        self.applyTextSettings()

        # Обновление позиций маркеров
        self.updateHandlePositions()

        # Устанавливаем z-значение для правильного отображения
        self.setZValue(5)

    def boundingRect(self):
        """Переопределяем для учёта высоты блока"""
        # Получаем оригинальный прямоугольник
        original_rect = super().boundingRect()

        # Используем заданную высоту если она больше высоты текста
        height = max(original_rect.height(), self._height)

        return QRectF(0, 0, original_rect.width(), height)

    def updateHandlePositions(self):
        """Обновление позиций маркеров"""
        rect = self.boundingRect()

        for handle in self.resize_handles:
            if handle.position == "bottom-right":
                handle.setPos(rect.width() - 4, rect.height() - 4)
            elif handle.position == "bottom-left":
                handle.setPos(-4, rect.height() - 4)
            elif handle.position == "top-right":
                handle.setPos(rect.width() - 4, -4)
            elif handle.position == "top-left":
                handle.setPos(-4, -4)

    def itemChange(self, change, value):
        """Обработка изменений элемента"""
        if change == QGraphicsItem.ItemSelectedChange:
            # Показ/скрытие маркеров при выделении
            for handle in self.resize_handles:
                handle.setVisible(value)

            # Обновляем z-значение для выделенного элемента
            if value:
                self.setZValue(10)
            else:
                self.setZValue(5)

        elif change == QGraphicsItem.ItemPositionHasChanged:
            # Сигнализируем об изменении при перемещении
            self.text_modified_signal.text_blocks_modified.emit()

        return super().itemChange(change, value)

    def onTextChanged(self):
        """Обработка изменения текста"""
        self.updateHandlePositions()
        self.text_modified_signal.text_blocks_modified.emit()

    def applyTextSettings(self):
        """Применение настроек текста"""
        if not self.settings:
            return

        # Настройка шрифта
        font = QFont(
            self.settings.get("font_family", "Arial"),
            self.settings.get("font_size", 16)
        )
        font.setBold(self.settings.get("bold", False))
        font.setItalic(self.settings.get("italic", False))
        self.setFont(font)

        # Цвет текста
        self.setDefaultTextColor(QColor(self.settings.get("text_color", "#FFFFFF")))

        # Выравнивание текста
        option = self.document().defaultTextOption()
        option.setAlignment(Qt.AlignmentFlag(self.settings.get("alignment", Qt.AlignCenter)))
        option.setWrapMode(QTextOption.WordWrap)
        self.document().setDefaultTextOption(option)

        # Параметры обводки и фона - ВАЖНО: обновляем внутренние переменные
        self._outline_width = self.settings.get("outline_width", 2)
        self._outline_color = QColor(self.settings.get("outline_color", "#000000"))

        # Обработка цвета фона
        bg_color = self.settings.get("background_color", "transparent")
        if bg_color and bg_color != "transparent":
            self._background_color = bg_color
        else:
            self._background_color = "transparent"

        self.update()
        self.updateHandlePositions()

    def paint(self, painter, option, widget):
        """Отрисовка с улучшенной визуальной обратной связью"""
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        # Получаем внутренний rect (без margins)
        inner_rect = super().boundingRect()
        rect = QRectF(0, 0, inner_rect.width(), max(inner_rect.height(), self._height))

        # Визуальная обратная связь
        if self._is_dragging:
            painter.setOpacity(0.8)
        elif self._is_resizing:
            painter.setOpacity(0.9)

        # Фон с закруглёнными углами
        if self._background_color and self._background_color != "transparent":
            painter.setPen(Qt.NoPen)
            bg_color = QColor(self._background_color)
            if bg_color.isValid():
                painter.setBrush(QBrush(bg_color))
                painter.drawRoundedRect(rect, 5, 5)

        # Рамка выделения
        if self.isSelected():
            if self._is_resizing:
                # Более яркая рамка при изменении размера
                painter.setPen(QPen(QColor(255, 100, 100, 200), 2, Qt.SolidLine))
            elif self._is_dragging:
                # Синяя рамка при перетаскивании
                painter.setPen(QPen(QColor(100, 100, 255, 200), 2, Qt.SolidLine))
            else:
                # Обычная рамка выделения
                painter.setPen(QPen(QColor(100, 100, 255, 150), 2, Qt.DashLine))

            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)

        # Восстанавливаем прозрачность для текста
        painter.setOpacity(1.0)

        # Получаем текст
        text = self.toPlainText()
        if not text:
            if self.hasFocus():
                painter.setPen(QPen(QColor(150, 150, 150)))
                painter.drawText(rect, Qt.AlignCenter, "Введите текст...")
            return

        # Обводка текста
        if self._outline_width > 0:
            doc = self.document().clone()
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.Document)

            char_format = QTextCharFormat()
            char_format.setTextOutline(QPen(self._outline_color, self._outline_width * 2,
                                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            char_format.setForeground(QBrush(Qt.transparent))

            cursor.mergeCharFormat(char_format)

            painter.save()
            doc.drawContents(painter, rect)
            painter.restore()

        # Рисуем основной текст
        super().paint(painter, option, widget)




    def export(self):
        """Экспорт данных блока для сохранения"""
        return {
            "page_index": self.page_index,
            "text": self.toPlainText(),
            "position": {"x": self.pos().x(), "y": self.pos().y()},
            "width": self._width,
            "height": self._height,
            "settings": {k: v for k, v in self.settings.items() if k != "scene_"}
        }

    def mousePressEvent(self, event):
        """Выделение при клике и начало перетаскивания"""
        if event.button() == Qt.LeftButton:
            self._is_dragging = True
        self.setSelected(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Окончание перетаскивания"""
        if event.button() == Qt.LeftButton:
            self._is_dragging = False
        super().mouseReleaseEvent(event)
    def setTextWidth(self, width):
        """Установка ширины с обновлением маркеров"""
        self._width = width
        super().setTextWidth(width)
        self.update()
        self.updateHandlePositions()

    def focusOutEvent(self, event):
        """Обработка потери фокуса"""
        super().focusOutEvent(event)
        # Убираем пустые блоки при потере фокуса
        if not self.toPlainText().strip():
            self.setPlainText("Текст")


class ImageViewer(QGraphicsView):
    """Просмотрщик изображений с поддержкой текстовых блоков"""

    def __init__(self, pixmap_paths, parent=None):
        super().__init__(parent)
        self.parent_window = parent

        # Настройки отображения
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)

        # Инициализация данных
        self.pages = pixmap_paths
        self.current_page = 0
        self.text_settings = {}
        self.text_blocks = []
        self.scale_factor = 1.0
        self.show_cleaned = True

        # Создание сцены
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)

        # Массивы изображений
        self.pixmaps = [QPixmap() for _ in range(len(pixmap_paths))]
        self.cleaned_pixmaps = [QPixmap() for _ in range(len(pixmap_paths))]

        # Элемент отображения страницы
        self.page_pixmap_item = QGraphicsPixmapItem()
        self.scene_.addItem(self.page_pixmap_item)

        # Сигналы
        self.text_blocks_modified_signal = TextBlockModifiedSignal()
        self.text_blocks_modified_signal.text_blocks_modified.connect(self.onTextBlocksModified)

        # Начальный размер сцены
        self._setSceneRectWithMargin(QRectF(0, 0, 800, 1200))

        # Коллекции для заметок перевода
        self.translation_notes = []
        self.translation_lines = []
        self.translation_anchors = []

    def onTextBlocksModified(self):
        """Сохранение при изменении блоков"""
        if hasattr(self.parent_window, 'saveStatus'):
            self.parent_window.saveStatus()

    def _setSceneRectWithMargin(self, rect, margin=None):
        """Установка размера сцены с отступами"""
        if margin is None:
            margin = rect.width() / 2

        self.setSceneRect(
            rect.x() - margin,
            rect.y() - margin,
            rect.width() + 2 * margin,
            rect.height() + 2 * margin
        )

    def displayCurrentPage(self):
        """Отображение текущей страницы"""
        if not 0 <= self.current_page < len(self.pages) or not self.pixmaps:
            return

        # Выбор изображения
        pm = self.pixmaps[self.current_page]
        if pm.isNull():
            logger.warning(f"Пустое изображение для страницы {self.current_page}")
            return

        # Проверка очищенного изображения
        if (self.show_cleaned and hasattr(self, 'cleaned_pixmaps') and
                self.cleaned_pixmaps and self.current_page < len(self.cleaned_pixmaps) and
                not self.cleaned_pixmaps[self.current_page].isNull()):
            pm = self.cleaned_pixmaps[self.current_page]
            logger.info(f"Отображение очищенного изображения для страницы {self.current_page}")

        # Установка изображения
        self.page_pixmap_item.setPixmap(pm)
        self.page_pixmap_item.setPos(0, 0)

        # Обновление размера сцены
        self._setSceneRectWithMargin(QRectF(0, 0, pm.width(), pm.height()))

        # Обновление видимости элементов
        self.updateTextBlocksVisibility()
        self.updateTranslationNotesVisibility()

    def updateTextBlocksVisibility(self):
        """Обновление видимости текстовых блоков"""
        for block in self.text_blocks:
            block.setVisible(block.page_index == self.current_page)

    def updateTranslationNotesVisibility(self):
        """Обновление видимости заметок перевода"""
        current = self.current_page

        for note in self.translation_notes:
            note.setVisible(note.page_index == current)

        for line in self.translation_lines:
            line.setVisible(getattr(line, "page_index", -1) == current)

        for anchor in self.translation_anchors:
            anchor.setVisible(getattr(anchor, "page_index", -1) == current)

    def set_cleaned(self, show_cleaned):
        """Переключение между оригинальными и очищенными изображениями"""
        if self.show_cleaned != show_cleaned:
            self.show_cleaned = show_cleaned
            self.displayCurrentPage()
            logger.info(f"Переключение на {'очищенные' if show_cleaned else 'оригинальные'} изображения")

    def createTextBlock(self, pos, text="Текст"):
        """Создание нового текстового блока"""
        # Копируем текущие настройки
        block_settings = self.text_settings.copy()

        # Отладка - выводим настройки
        print(f"Creating text block with settings: {block_settings}")

        text_block = TextBlockItem(text, self.current_page, block_settings)
        text_block.text_modified_signal.text_blocks_modified.connect(self.onTextBlocksModified)
        text_block.setPos(pos)

        self.scene_.addItem(text_block)
        self.text_blocks.append(text_block)

        # Активация редактирования
        text_block.setSelected(True)
        text_block.setFocus()

        self.updateTextBlocksVisibility()
        self.text_blocks_modified_signal.text_blocks_modified.emit()

        return text_block

    def updateSelectedTextBlockSettings(self):
        """Обновление настроек выделенных блоков"""
        for block in self.text_blocks:
            if block.isSelected():
                # Обновляем настройки блока
                block.settings = self.text_settings.copy()
                block.applyTextSettings()
                # Принудительно обновляем отрисовку
                block.update()

        self.text_blocks_modified_signal.text_blocks_modified.emit()

    def getTextBlocksData(self):
        """Получение данных всех блоков"""
        return [block.export() for block in self.text_blocks]

    def restoreTextBlocks(self, blocks_data):
        """Восстановление блоков из сохраненных данных"""
        if not blocks_data:
            return

        # Удаление существующих блоков
        for block in self.text_blocks:
            if block.scene():
                self.scene_.removeItem(block)
        self.text_blocks = []

        # Создание блоков из данных
        for data in blocks_data:
            block = TextBlockItem(
                data.get("text", ""),
                data.get("page_index", 0),
                data.get("settings", {})
            )
            block.text_modified_signal.text_blocks_modified.connect(self.onTextBlocksModified)

            # Восстановление позиции и размера
            pos = data.get("position", {"x": 0, "y": 0})
            block.setPos(pos["x"], pos["y"])

            # Восстанавливаем ширину и высоту
            block._width = data.get("width", 200)
            block._height = data.get("height", 60)
            block.setTextWidth(block._width)

            self.scene_.addItem(block)
            self.text_blocks.append(block)

        self.updateTextBlocksVisibility()

    def exportPage(self, page_index, file_path):
        """Экспорт страницы с текстовыми блоками"""
        if not 0 <= page_index < len(self.pixmaps):
            return False

        # Сохранение текущей страницы
        current_page_save = self.current_page
        self.current_page = page_index
        self.displayCurrentPage()

        # Выбор изображения для экспорта
        pm = self.pixmaps[page_index]
        if (self.show_cleaned and page_index < len(self.cleaned_pixmaps) and
                not self.cleaned_pixmaps[page_index].isNull()):
            pm = self.cleaned_pixmaps[page_index]

        if pm.isNull():
            return False

        # Создание результирующего изображения
        result = QPixmap(pm.width(), pm.height())
        result.fill(Qt.transparent)

        # Отрисовка
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Основное изображение
        painter.drawPixmap(0, 0, pm)

        # Текстовые блоки
        for block in self.text_blocks:
            if block.page_index == page_index:
                painter.save()
                painter.translate(block.pos())
                block.paint(painter, None, None)
                painter.restore()

        painter.end()

        # Сохранение
        result.save(file_path)

        # Восстановление страницы
        self.current_page = current_page_save
        self.displayCurrentPage()

        return True

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
        """Масштабирование колесом мыши"""
        if event.modifiers() == Qt.ControlModifier:
            delta = event.angleDelta().y()
            zf = 1.15 if delta > 0 else 1 / 1.15
            new_scale = self.scale_factor * zf

            if 0.05 <= new_scale <= 20.0:
                self.scale(zf, zf)
                self.scale_factor = new_scale
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        """Обработка клавиш"""
        if event.key() == Qt.Key_Delete:
            # Удаление выделенных блоков
            selected = self.scene_.selectedItems()
            for item in selected:
                if isinstance(item, TextBlockItem) and item in self.text_blocks:
                    self.text_blocks.remove(item)
                    self.scene_.removeItem(item)
                    self.text_blocks_modified_signal.text_blocks_modified.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        """Контекстное меню"""
        menu = QMenu(self)
        pos = self.mapToScene(event.pos())
        item = self.scene_.itemAt(pos, self.transform())

        if item in self.translation_notes:
            # Меню для заметок перевода
            copy_action = menu.addAction("Копировать текст заметки")
            copy_action.triggered.connect(lambda: self.copyTranslationNoteText(item))

        elif isinstance(item, TextBlockItem):
            # Меню для текстового блока
            delete_action = menu.addAction("Удалить текстовый блок")
            delete_action.triggered.connect(lambda: self.deleteTextBlock(item))

            duplicate_action = menu.addAction("Дублировать")
            duplicate_action.triggered.connect(lambda: self.duplicateTextBlock(item))

            menu.addSeparator()

            # Подменю выравнивания
            align_menu = menu.addMenu("Выравнивание")

            align_left = align_menu.addAction("По левому краю")
            align_left.triggered.connect(lambda: self.setTextBlockAlignment(item, Qt.AlignLeft))

            align_center = align_menu.addAction("По центру")
            align_center.triggered.connect(lambda: self.setTextBlockAlignment(item, Qt.AlignCenter))

            align_right = align_menu.addAction("По правому краю")
            align_right.triggered.connect(lambda: self.setTextBlockAlignment(item, Qt.AlignRight))

        else:
            # Общее меню сцены
            add_text_action = menu.addAction("Добавить текстовый блок")
            add_text_action.triggered.connect(lambda: self.createTextBlock(pos))

            if self.text_blocks:
                delete_all_action = menu.addAction("Удалить все текстовые блоки")
                delete_all_action.triggered.connect(self.deleteAllTextBlocks)

                delete_current_action = menu.addAction("Удалить блоки на текущей странице")
                delete_current_action.triggered.connect(self.deleteCurrentPageTextBlocks)

        menu.exec_(event.globalPos())

    def copyTranslationNoteText(self, note):
        """Копирование текста заметки в буфер обмена"""
        if hasattr(note, "text_edit"):
            clipboard = QApplication.clipboard()
            clipboard.setText(note.text_edit.toPlainText())

            if hasattr(self.parent_window, "show_message"):
                from ui.windows.m9_2_utils import show_message
                show_message(self.parent_window, "Информация", "Текст скопирован в буфер обмена")

    def deleteTextBlock(self, block):
        """Удаление текстового блока"""
        if block in self.text_blocks:
            self.text_blocks.remove(block)
            if block.scene():
                self.scene_.removeItem(block)
            self.text_blocks_modified_signal.text_blocks_modified.emit()

    def duplicateTextBlock(self, block):
        """Дублирование текстового блока"""
        if block not in self.text_blocks:
            return

        # Создание копии
        new_block = TextBlockItem(
            block.toPlainText(),
            block.page_index,
            block.settings.copy()
        )
        new_block.text_modified_signal.text_blocks_modified.connect(self.onTextBlocksModified)
        new_block.setPos(block.pos() + QPointF(20, 20))
        new_block.setTextWidth(block.textWidth())

        self.scene_.addItem(new_block)
        self.text_blocks.append(new_block)

        # Выделение нового блока
        block.setSelected(False)
        new_block.setSelected(True)
        new_block.setFocus()

        self.text_blocks_modified_signal.text_blocks_modified.emit()

    def setTextBlockAlignment(self, block, alignment):
        """Изменение выравнивания текста"""
        if block in self.text_blocks:
            block.settings["alignment"] = alignment
            block.applyTextSettings()
            self.text_blocks_modified_signal.text_blocks_modified.emit()

    def deleteAllTextBlocks(self):
        """Удаление всех текстовых блоков"""
        for block in self.text_blocks[:]:
            if block.scene():
                self.scene_.removeItem(block)
        self.text_blocks = []
        self.text_blocks_modified_signal.text_blocks_modified.emit()

    def deleteCurrentPageTextBlocks(self):
        """Удаление блоков на текущей странице"""
        blocks_to_delete = [b for b in self.text_blocks if b.page_index == self.current_page]

        for block in blocks_to_delete:
            self.text_blocks.remove(block)
            if block.scene():
                self.scene_.removeItem(block)

        self.text_blocks_modified_signal.text_blocks_modified.emit()