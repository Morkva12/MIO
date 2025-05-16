# -*- coding: utf-8 -*-
# ui/windows/m8_2_image_viewer.py
import cv2
import numpy as np
import logging
from PySide6.QtCore import Qt, QObject, Signal, QRectF, QPointF, QEvent, QTimer
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QApplication, QMenu, QMessageBox)
from PySide6.QtGui import (QPainter, QPixmap, QPen, QBrush, QColor, QCursor,
                           QAction, QTransform, QAction)

from ui.windows.m8_1_graphics_items import EditableMask, EditablePolygonMask, BrushStroke
from ui.windows.m8_1_graphics_items import SelectionRect, SelectionEvent

# Константы для настройки инструментов
MIN_BRUSH_SIZE = 1
MAX_BRUSH_SIZE = 50
DEFAULT_BRUSH_SIZE = 5
DEFAULT_BRUSH_COLOR = (255, 0, 0)
CURSOR_OUTLINE_COLOR = QColor(255, 255, 255)
CURSOR_FILL_COLOR = QColor(255, 0, 0, 128)

logger = logging.getLogger(__name__)


class DrawingMode:
    """Режимы рисования"""
    NONE = 0  # Навигация
    BRUSH = 1  # Кисть
    ERASER = 2  # Ластик


class PageChangeSignal(QObject):
    """Сигнал смены страницы"""
    page_changed = Signal(int)


class CustomImageViewer(QGraphicsView):
    """Просмотрщик изображений с функциями рисования и редактирования"""
    mask_updated = Signal(int)
    operation_started = Signal(str)
    operation_finished = Signal()

    def __init__(self, image_paths, parent=None):
        super().__init__(parent)
        # Основные параметры
        self.pages = image_paths
        self.cur_page = 0
        self.scale_factor = 1.0
        self.masks = {}  # Хранение масок
        self.draw_layers = {}  # page_idx -> QPixmap
        self.draw_items = {}  # page_idx -> QGraphicsPixmapItem

        # Настройка представления
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        # Включаем режим перемещения левой кнопкой мыши
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setCursor(Qt.OpenHandCursor)  # Курсор "открытая рука"

        # Настройка сцены
        self.fit_to_view = True
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)

        # Графические элементы
        self.single_page_item = QGraphicsPixmapItem()
        self.scene_.addItem(self.single_page_item)

        # Рисование
        self.draw_mode = DrawingMode.NONE
        self.draw_color = DEFAULT_BRUSH_COLOR
        self.draw_size = DEFAULT_BRUSH_SIZE
        self.drawing = False
        self.last_pt = None

        # Инициализация pixmaps
        self.pixmaps = []
        self.orig_pixmaps = {}

        # Загрузка изображений
        self._load_images()

        # Отображаем текущую страницу
        self.display_current_page()

    def _load_images(self):
        """Загрузка изображений"""
        self.pixmaps = []
        for path in self.pages:
            try:
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    self.pixmaps.append(pixmap)
                    page_idx = len(self.pixmaps) - 1
                    self.orig_pixmaps[page_idx] = pixmap.copy()
                    # Сразу создаем слой для рисования
                    self._create_drawing_layer(page_idx)
                else:
                    logger.error(f"Не удалось загрузить изображение: {path}")
                    self.pixmaps.append(QPixmap())
            except Exception as e:
                logger.error(f"Ошибка загрузки изображения {path}: {str(e)}")
                self.pixmaps.append(QPixmap())

    def _create_drawing_layer(self, page_idx):
        """Создает слой для рисования"""
        if page_idx in self.draw_layers and not self.draw_layers[page_idx].isNull():
            return self.draw_layers[page_idx]

        if page_idx < 0 or page_idx >= len(self.pixmaps):
            logger.error(f"Некорректный индекс страницы: {page_idx}")
            return None

        pixmap = self.pixmaps[page_idx]
        if pixmap.isNull():
            logger.error(f"Пустой pixmap для страницы {page_idx}")
            return None

        # Создаем слой рисования точно такого же размера как изображение
        w, h = pixmap.width(), pixmap.height()
        if w <= 0 or h <= 0:
            logger.error(f"Некорректные размеры pixmap: {w}x{h}")
            return None

        layer = QPixmap(w, h)
        layer.fill(Qt.transparent)
        self.draw_layers[page_idx] = layer

        # Создаем элемент отображения и добавляем на сцену
        item = QGraphicsPixmapItem(layer)
        item.setPos(0, 0)  # Позиция соответствует position image
        item.setZValue(50)  # Поверх основного изображения
        self.scene_.addItem(item)
        self.draw_items[page_idx] = item

        # Видимость зависит от текущей страницы
        item.setVisible(page_idx == self.cur_page)

        logger.debug(f"Создан слой для страницы {page_idx}: {w}x{h}")
        return layer

    def set_draw_mode(self, mode):
        """Устанавливает режим рисования"""
        self.draw_mode = mode
        if mode == DrawingMode.NONE:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self._update_cursor()

    def _update_cursor(self):
        """Создает кастомный курсор в виде круга"""
        if self.draw_mode == DrawingMode.NONE:
            return

        cursor_size = max(16, self.draw_size * 2)
        pixmap = QPixmap(cursor_size, cursor_size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.draw_mode == DrawingMode.BRUSH:
            painter.setPen(QPen(CURSOR_OUTLINE_COLOR, 1, Qt.SolidLine))
            painter.setBrush(QColor(self.draw_color[0], self.draw_color[1], self.draw_color[2], 128))
        else:  # ERASER
            painter.setPen(QPen(QColor(255, 0, 0), 1, Qt.DashLine))
            painter.setBrush(QColor(255, 255, 255, 128))

        painter.drawEllipse(
            (cursor_size - self.draw_size) // 2,
            (cursor_size - self.draw_size) // 2,
            self.draw_size,
            self.draw_size
        )

        painter.end()
        self.viewport().setCursor(QCursor(pixmap))

    def set_draw_color(self, color):
        """Устанавливает цвет кисти"""
        self.draw_color = color
        if self.draw_mode == DrawingMode.BRUSH:
            self._update_cursor()

    def set_draw_size(self, size):
        """Устанавливает размер кисти"""
        self.draw_size = max(MIN_BRUSH_SIZE, min(MAX_BRUSH_SIZE, size))
        if self.draw_mode != DrawingMode.NONE:
            self._update_cursor()

    def _draw_stroke(self, page_idx, start_pos, end_pos=None, is_eraser=False):
        """Рисует линию на слое рисования"""
        try:
            if page_idx not in self.draw_layers:
                layer = self._create_drawing_layer(page_idx)
                if layer is None:
                    print(f"ОШИБКА: Не удалось создать слой для страницы {page_idx}")
                    return False

            layer = self.draw_layers[page_idx]
            painter = QPainter(layer)
            painter.setRenderHint(QPainter.Antialiasing)

            if is_eraser:
                painter.setCompositionMode(QPainter.CompositionMode_Clear)
                if end_pos:
                    pen = QPen()
                    pen.setWidth(self.draw_size)
                    pen.setCapStyle(Qt.RoundCap)
                    pen.setJoinStyle(Qt.RoundJoin)
                    painter.setPen(pen)
                    painter.drawLine(start_pos, end_pos)
                else:
                    painter.setBrush(Qt.black)
                    painter.setPen(Qt.NoPen)
                    painter.drawEllipse(start_pos, self.draw_size / 2, self.draw_size / 2)
            else:
                pen = QPen(QColor(*self.draw_color))
                pen.setWidth(self.draw_size)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                painter.setPen(pen)
                if end_pos:
                    painter.drawLine(start_pos, end_pos)
                else:
                    painter.drawPoint(start_pos)

            painter.end()
            self.draw_items[page_idx].setPixmap(layer)
            self.viewport().update()
            window = self.window()
            if hasattr(window, 'image_status'):
                window.image_status[page_idx] = 'modified'
            self.mask_updated.emit(page_idx)
            QApplication.processEvents()
            return True
        except Exception as e:
            print(f"ОШИБКА: Рисования штриха: {str(e)}")

    def display_current_page(self):
        """Отображение текущей страницы точно так же, как в m6"""
        if not self.pixmaps or self.cur_page < 0 or self.cur_page >= len(self.pixmaps):
            logger.error(f"Некорректный индекс страницы: {self.cur_page}")
            return

        pm = self.pixmaps[self.cur_page]
        if pm.isNull():
            logger.error(f"Пустой pixmap для страницы {self.cur_page}")
            return

        old_transform = self.transform()
        old_scale_factor = self.scale_factor

        for item in self.scene_.items():
            if item != self.single_page_item and item not in self.draw_items.values():
                item.setVisible(False)

        self.single_page_item.setPixmap(pm)
        self.single_page_item.setPos(0, 0)

        for idx, item in self.draw_items.items():
            item.setVisible(idx == self.cur_page)
            if idx == self.cur_page:
                item.setPos(0, 0)

        if self.cur_page not in self.draw_layers:
            self._create_drawing_layer(self.cur_page)

        w, h = pm.width(), pm.height()
        self.setSceneRect(-30, -30, w + 60, h + 60)

        for masks in self.masks.values():
            for mask in masks:
                mask.setVisible(False)

        count = 0
        if self.cur_page in self.masks:
            for mask in self.masks[self.cur_page]:
                if not (hasattr(mask, 'deleted') and mask.deleted):
                    mask.setVisible(True)
                    if mask.scene() is None:
                        self.scene_.addItem(mask)
                    if mask.page_index != self.cur_page:
                        mask.set_page_index(self.cur_page)
                    count += 1
            logger.debug(f"Отображаем {count} масок для страницы {self.cur_page}")

        if self.fit_to_view:
            self.resetTransform()
            self.scale_factor = 1.0
            self.fitInView(self.single_page_item, Qt.KeepAspectRatio)
        else:
            QTimer.singleShot(50, lambda: self.setTransform(old_transform))
            self.scale_factor = old_scale_factor

        self.scene_.update()
        self.viewport().update()

    def _update_masks_for_current_page(self):
        """Обновление масок для текущей страницы"""
        # Скрываем все маски
        for page_idx, masks_list in self.masks.items():
            for mask in masks_list:
                mask.setVisible(False)

        if self.cur_page in self.masks:
            for mask in self.masks[self.cur_page]:
                if not (hasattr(mask, 'deleted') and mask.deleted):
                    mask.setVisible(True)
                    # Устанавливаем правильную страницу, если нужно
                    if mask.page_index is None or mask.page_index != self.cur_page:
                        mask.set_page_index(self.cur_page)

    def nextPage(self):
        """Переход на следующую страницу"""
        if self.cur_page < len(self.pages) - 1:
            self.cur_page += 1
            logger.debug(f"Переход на страницу {self.cur_page}")
            self.display_current_page()
            return True
        return False

    def previousPage(self):
        """Переход на предыдущую страницу"""
        if self.cur_page > 0:
            self.cur_page -= 1
            logger.debug(f"Переход на страницу {self.cur_page}")
            self.display_current_page()
            return True
        return False

    def contextMenuEvent(self, event):
        """Контекстное меню при клике правой кнопкой мыши"""
        menu = QMenu(self)

        # Определяем, находится ли курсор над маскойА
        items = self.scene_.items(self.mapToScene(event.pos()))
        is_over_mask = False

        for item in items:
            if isinstance(item, (EditableMask, EditablePolygonMask, BrushStroke)):
                is_over_mask = True
                break

        if is_over_mask:
            # Действие "Удалить маску" если курсор над маской
            delete_action = QAction("Удалить маску", self)
            delete_action.triggered.connect(lambda: self.delete_mask_at_position(self.mapToScene(event.pos())))
            menu.addAction(delete_action)
        else:
            # Действие "Удалить разметку" если курсор НЕ над маской
            clear_action = QAction("Удалить разметку", self)
            clear_action.triggered.connect(self.clear_all_masks)
            menu.addAction(clear_action)

        # Сбрасываем состояние рисования перед показом меню
        self.drawing = False
        self.last_pt = None

        # Показываем меню на позиции события
        menu.exec_(event.globalPos())

        # После закрытия меню снова сбрасываем состояние рисования
        self.drawing = False
        self.last_pt = None

    def clear_all_masks(self):
        """Удаляет все маски на текущей странице"""
        if self.cur_page in self.masks:
            for mask in self.masks[self.cur_page]:
                self.scene_.removeItem(mask)
            self.masks[self.cur_page] = []

            # Очищаем слой рисования
            if self.cur_page in self.draw_layers:
                self.draw_layers[self.cur_page].fill(Qt.transparent)
                if self.cur_page in self.draw_items:
                    self.draw_items[self.cur_page].setPixmap(self.draw_layers[self.cur_page])

            # Обновляем отображение
            self.viewport().update()

            # Отправляем сигнал обновления
            self.mask_updated.emit(self.cur_page)

            # Уведомляем родительское окно
            window = self.window()
            if hasattr(window, 'update_combined_mask_from_visual'):
                window.update_combined_mask_from_visual(self.cur_page)

            # Сбрасываем состояние рисования
            self.drawing = False
            self.last_pt = None

    def _cleanup_graphics_artifacts(self):
        """Очистка артефактов графики после операций с масками"""
        # Принудительное обновление всей сцены
        self.scene_.update()

        # Обновляем видимую область
        self.viewport().update()

        # Опционально: запланировать дополнительное обновление через небольшую задержку
        QTimer.singleShot(50, self.viewport().update)

    def delete_mask_at_position(self, scene_pos):
        """Удаляет маску на указанной позиции"""
        items = self.scene_.items(scene_pos)
        mask_deleted = False
        page_idx = self.cur_page

        for item in items:
            if isinstance(item, (EditableMask, EditablePolygonMask, BrushStroke)):
                if not hasattr(item, 'deleted') or not item.deleted:
                    item.deleted = True
                    item.setVisible(False)
                    mask_deleted = True

                    # Сохраняем индекс страницы для обновления
                    if hasattr(item, 'page_index'):
                        page_idx = item.page_index

        if mask_deleted:
            # Обновляем комбинированную маску
            window = self.window()
            if hasattr(window, 'update_combined_mask_from_visual'):
                window.update_combined_mask_from_visual(page_idx)

            # Проверяем, остались ли еще маски
            remaining_masks = False
            if page_idx in self.masks:
                for mask in self.masks[page_idx]:
                    if not (hasattr(mask, 'deleted') and mask.deleted):
                        remaining_masks = True
                        break

            # Если маски не остались, меняем статус на saved
            if not remaining_masks:
                if hasattr(window, 'image_status'):
                    window.image_status[page_idx] = 'saved'

            # Принудительно обновляем миниатюру
            if hasattr(window, 'force_update_thumbnail'):
                window.force_update_thumbnail(page_idx)

            # Принудительно обновляем интерфейс
            QApplication.processEvents()

            # Отправляем сигнал обновления
            self.mask_updated.emit(page_idx)

            # Обновляем сцену
            self.scene_.update()
            self.viewport().update()

    def mousePressEvent(self, event):
        """Нажатие мыши - начало рисования или взаимодействия"""
        # Для режима просмотра используем стандартное поведение
        if self.draw_mode == DrawingMode.NONE:
            super().mousePressEvent(event)
            return

        # Правая кнопка для временного переключения на ластик
        if event.button() == Qt.RightButton and self.draw_mode == DrawingMode.BRUSH:
            self.saved_draw_mode = self.draw_mode
            self.set_draw_mode(DrawingMode.ERASER)

            # Создаем новое событие с левой кнопкой
            new_position = event.position()
            new_buttons = event.buttons() & ~Qt.RightButton | Qt.LeftButton
            new_event = type(event)(
                QEvent.MouseButtonPress,
                new_position,
                Qt.LeftButton,
                new_buttons,
                event.modifiers()
            )
            self.mousePressEvent(new_event)
            return

        # Для режима рисования
        if event.button() == Qt.LeftButton:
            # Получаем координаты в системе сцены
            scene_pos = self.mapToScene(event.position().toPoint())
            page_idx = self.cur_page

            # Проверяем и создаем слой для рисования
            if page_idx not in self.draw_layers:
                if not self._create_drawing_layer(page_idx):
                    logger.error(f"Не удалось создать слой для страницы {page_idx}")
                    return

            # Начинаем рисование
            self.drawing = True
            self.last_pt = scene_pos

            # Рисуем первую точку
            is_eraser = (self.draw_mode == DrawingMode.ERASER)
            success = self._draw_stroke(page_idx, scene_pos, None, is_eraser)
            if not success:
                self.drawing = False
                self.last_pt = None

    # Добавить в файл m8_2_image_viewer.py

    def mouseMoveEvent(self, event):
        """Движение мыши - продолжение рисования или взаимодействия"""
        # Для режима просмотра или если не рисуем
        if self.draw_mode == DrawingMode.NONE or not self.drawing:
            super().mouseMoveEvent(event)
            return

        # Проверка начальной точки
        if self.last_pt is None:
            return

        # Получаем координаты в системе сцены
        scene_pos = self.mapToScene(event.position().toPoint())

        # Проверяем, что точка не совпадает с предыдущей (избегаем лишних обновлений)
        if (abs(scene_pos.x() - self.last_pt.x()) < 1 and
                abs(scene_pos.y() - self.last_pt.y()) < 1):
            return

        # Определяем режим (кисть/ластик)
        is_eraser = (self.draw_mode == DrawingMode.ERASER)

        # Рисуем линию от предыдущей точки к текущей
        success = self._draw_stroke(self.cur_page, self.last_pt, scene_pos, is_eraser)

        # Запоминаем новую точку ТОЛЬКО если успешно нарисовали
        if success:
            self.last_pt = scene_pos

            # Обновляем окно и миниатюру
            window = self.window()
            if hasattr(window, 'force_update_thumbnail'):
                # Делаем обновление миниатюры не каждый раз, а периодически
                if not hasattr(self, 'thumbnail_update_timer'):
                    self.thumbnail_update_timer = QTimer()
                    self.thumbnail_update_timer.setSingleShot(True)
                    self.thumbnail_update_timer.timeout.connect(
                        lambda: window.force_update_thumbnail(self.cur_page))

                # Если таймер не активен, запускаем его
                if not self.thumbnail_update_timer.isActive():
                    self.thumbnail_update_timer.start(200)  # Обновляем не чаще чем раз в 200 мс

                # Устанавливаем статус "modified"
                if hasattr(window, 'image_status'):
                    window.image_status[self.cur_page] = 'modified'

        # Обновляем представление
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        """Отпускание кнопки мыши - завершение рисования или взаимодействия"""
        # Возвращаемся к кисти, если использовали ластик с правой кнопкой
        if event.button() == Qt.RightButton and hasattr(self, 'saved_draw_mode'):
            self.set_draw_mode(self.saved_draw_mode)
            delattr(self, 'saved_draw_mode')
            return

        # Для режима просмотра или если не рисуем
        if self.draw_mode == DrawingMode.NONE or not self.drawing:
            super().mouseReleaseEvent(event)
            return

        # ВАЖНО: НЕ рисуем заключительную линию до точки отпускания мыши
        # Просто заканчиваем рисование на последней обработанной точке

        # Финальное обновление миниатюры для завершения рисования
        window = self.window()
        if hasattr(window, 'force_update_thumbnail'):
            # Устанавливаем статус перед обновлением
            if hasattr(window, 'image_status'):
                window.image_status[self.cur_page] = 'modified'
            window.force_update_thumbnail(self.cur_page)

        # Отправка сигнала об изменении
        self.mask_updated.emit(self.cur_page)

        # Сбрасываем состояние рисования
        self.drawing = False
        self.last_pt = None

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        """Обработка колесика мыши для масштабирования"""
        if event.modifiers() == Qt.ControlModifier:
            # Масштабирование с помощью колесика мыши и Ctrl
            delta = event.angleDelta().y()
            z = 1.15 if delta > 0 else 1 / 1.15
            new_scale = self.scale_factor * z
            if 0.05 <= new_scale <= 20.0:
                self.scale(z, z)
                self.scale_factor = new_scale
                self.fit_to_view = False
            event.accept()
        elif event.modifiers() == Qt.AltModifier and self.draw_mode != DrawingMode.NONE:
            # Изменение размера кисти с помощью Alt+колесико
            delta = event.angleDelta().y()
            size_change = 1 if delta > 0 else -1
            new_size = max(MIN_BRUSH_SIZE, min(MAX_BRUSH_SIZE, self.draw_size + size_change))

            # Обновляем размер кисти
            window = self.window()
            if hasattr(window, 'size_slider') and hasattr(window, 'size_value'):
                window.size_slider.setValue(new_size)
            else:
                self.set_draw_size(new_size)

            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        """Обработка нажатия клавиш"""
        if event.key() == Qt.Key_Escape:
            # Отмена режима рисования по Escape
            self.draw_mode = DrawingMode.NONE
            self.drawing = False
            self.last_pt = None
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().setCursor(Qt.OpenHandCursor)

            # Уведомляем родительское окно
            window = self.window()
            if hasattr(window, 'set_drawing_tool'):
                window.set_drawing_tool(DrawingMode.NONE)

        elif event.key() == Qt.Key_Delete:
            # Удаление выбранных масок по Delete
            selected_items = [item for item in self.scene_.selectedItems()
                              if isinstance(item, (EditableMask, EditablePolygonMask, BrushStroke))]
            if selected_items:
                for item in selected_items:
                    item.deleted = True
                    item.setVisible(False)

                    # Обновляем окно
                    window = self.window()
                    if hasattr(window, 'update_combined_mask_from_visual') and hasattr(item, 'page_index'):
                        window.update_combined_mask_from_visual(item.page_index)
                        self.mask_updated.emit(item.page_index)

        elif event.key() == Qt.Key_Left:
            # Предыдущая страница
            self.previousPage()

        elif event.key() == Qt.Key_Right:
            # Следующая страница
            self.nextPage()

        super().keyPressEvent(event)

    def resizeEvent(self, event):
        """Обработка изменения размера окна"""
        super().resizeEvent(event)

        # Если включено автомасштабирование и есть изображение, вписываем
        if self.fit_to_view and not self.single_page_item.pixmap().isNull():
            self.fitInView(self.single_page_item, Qt.KeepAspectRatio)

    def getDrawingMasks(self):
        """Возвращает маски рисования для всех страниц"""
        result = {}

        for page_idx, layer in self.draw_layers.items():
            # Проверка содержимого
            if layer.isNull():
                continue

            img = layer.toImage()
            w, h = img.width(), img.height()

            # Создаем маску
            mask = np.zeros((h, w), dtype=np.uint8)

            # Проверка непрозрачных пикселей
            has_content = False
            for y in range(0, h, 10):
                for x in range(0, w, 10):
                    if QColor(img.pixel(x, y)).alpha() > 0:
                        has_content = True
                        break
                if has_content:
                    break

            # Если есть непрозрачные пиксели
            if has_content:
                for y in range(h):
                    for x in range(w):
                        if QColor(img.pixel(x, y)).alpha() > 0:
                            mask[y, x] = 255

                result[page_idx] = mask

        return result

    def event(self, event):
        """Обработка специальных событий"""
        if event.type() == SelectionEvent.Type:
            window = self.window()
            if hasattr(window, 'run_area_detection'):
                window.run_area_detection(self.cur_page, event.rect)
            return True
        return super().event(event)

    def showOperationProgress(self, visible, message=None):
        """Показ/скрытие индикатора прогресса операции"""
        if visible:
            self.operation_started.emit(message or "Операция выполняется...")
        else:
            self.operation_finished.emit()