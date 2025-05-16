# -*- coding: utf-8 -*-
# ui/windows/m8_1_graphics_items.py
from PySide6.QtCore import Qt, QRectF, QPointF, QEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsPolygonItem, QGraphicsPathItem, \
    QGraphicsEllipseItem
from PySide6.QtGui import QPen, QBrush, QColor, QPolygonF, QPainterPath


class SelectionEvent(QEvent):
    Type = QEvent.Type(QEvent.User + 1)

    def __init__(self, rect):
        super().__init__(SelectionEvent.Type)
        self.rect = rect


class EditableMask(QGraphicsRectItem):
    def __init__(self, x, y, width, height, mask_type, class_name, confidence, color, parent=None):
        super().__init__(x, y, width, height, parent)
        self.mask_type = mask_type
        self.class_name = class_name
        self.confidence = confidence
        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.deleted = False
        self.page_index = None
        self.last_expansion = 0

        pen = QPen(QColor(*color))
        pen.setWidth(2)
        self.setPen(pen)
        brush = QBrush(QColor(color[0], color[1], color[2], 60))
        self.setBrush(brush)

        self.resizing = False
        self.resize_corner = None

    def hoverMoveEvent(self, event):
        rect = self.rect()
        pos = event.pos()
        threshold = 10

        # Угловые зоны для изменения размера
        if ((pos.x() < rect.left() + threshold and pos.y() < rect.top() + threshold) or
                (pos.x() > rect.right() - threshold and pos.y() > rect.bottom() - threshold)):
            self.setCursor(Qt.SizeFDiagCursor)
        elif ((pos.x() > rect.right() - threshold and pos.y() < rect.top() + threshold) or
              (pos.x() < rect.left() + threshold and pos.y() > rect.bottom() - threshold)):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            rect = self.rect()
            pos = event.pos()
            threshold = 10

            # Определяем, за какой угол захватили маску
            if pos.x() < rect.left() + threshold and pos.y() < rect.top() + threshold:
                self.resizing = True
                self.resize_corner = "top-left"
            elif pos.x() > rect.right() - threshold and pos.y() < rect.top() + threshold:
                self.resizing = True
                self.resize_corner = "top-right"
            elif pos.x() < rect.left() + threshold and pos.y() > rect.bottom() - threshold:
                self.resizing = True
                self.resize_corner = "bottom-left"
            elif pos.x() > rect.right() - threshold and pos.y() > rect.bottom() - threshold:
                self.resizing = True
                self.resize_corner = "bottom-right"
            else:
                self.resizing = False
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Улучшенная обработка движения мыши"""
        if self.resizing and self.resize_corner:
            rect = self.rect()
            pos = event.pos()

            # Старые размеры
            old_width = rect.width()
            old_height = rect.height()
            old_x = rect.x()
            old_y = rect.y()

            # Новые координаты и размеры
            new_x, new_y, new_width, new_height = old_x, old_y, old_width, old_height

            # Изменение размера с учетом угла захвата
            if self.resize_corner == "top-left":
                # Защита от "выворачивания" маски
                if pos.x() < old_x + old_width - 5 and pos.y() < old_y + old_height - 5:
                    new_x = pos.x()
                    new_y = pos.y()
                    new_width = old_width - (pos.x() - old_x)
                    new_height = old_height - (pos.y() - old_y)
            elif self.resize_corner == "top-right":
                # Защита от "выворачивания" маски
                if pos.x() > old_x + 5 and pos.y() < old_y + old_height - 5:
                    new_y = pos.y()
                    new_width = pos.x() - old_x
                    new_height = old_height - (pos.y() - old_y)
            elif self.resize_corner == "bottom-left":
                # Защита от "выворачивания" маски
                if pos.x() < old_x + old_width - 5 and pos.y() > old_y + 5:
                    new_x = pos.x()
                    new_width = old_width - (pos.x() - old_x)
                    new_height = pos.y() - old_y
            elif self.resize_corner == "bottom-right":
                # Защита от "выворачивания" маски
                if pos.x() > old_x + 5 and pos.y() > old_y + 5:
                    new_width = pos.x() - old_x
                    new_height = pos.y() - old_y

            # Проверка минимального размера (5x5 пикселей)
            if new_width >= 5 and new_height >= 5:
                self.setRect(new_x, new_y, new_width, new_height)

            # Принудительное обновление всей сцены
            if self.scene():
                self.scene().update()
        else:
            super().mouseMoveEvent(event)

        # Обновление сцены после любого движения
        if self.scene():
            self.scene().update()



    def mouseReleaseEvent(self, event):
        """Улучшенная обработка отпускания кнопки мыши"""
        self.resizing = False

        # Принудительное полное обновление сцены
        if self.scene():
            scene_rect = self.scene().sceneRect()
            self.scene().update(scene_rect)

        super().mouseReleaseEvent(event)

    def set_page_index(self, index):
        """Привязка маски к конкретной странице"""
        self.page_index = index

    def mouseDoubleClickEvent(self, event):
        """Обработка двойного клика - удаление маски"""
        self.deleted = True
        self.setVisible(False)
        super().mouseDoubleClickEvent(event)


class EditablePolygonMask(QGraphicsPolygonItem):
    def __init__(self, points, mask_type, class_name, confidence, color, parent=None, editing=False):
        polygon = QPolygonF()
        for point in points:
            polygon.append(QPointF(point[0], point[1]))

        super().__init__(polygon, parent)
        self.mask_type = mask_type
        self.class_name = class_name
        self.confidence = confidence
        self.deleted = False
        self.editing = editing
        self.points = points
        self.page_index = None
        self.last_expansion = 0

        self.setFlag(QGraphicsItem.ItemIsMovable)
        self.setFlag(QGraphicsItem.ItemIsSelectable)
        self.setAcceptHoverEvents(True)

        pen = QPen(QColor(*color))
        pen.setWidth(2)
        self.setPen(pen)
        brush = QBrush(QColor(color[0], color[1], color[2], 60))
        self.setBrush(brush)

        # Добавляем точки для редактирования полигона
        if editing:
            self.control_points = []
            for i, point in enumerate(points):
                point_item = QGraphicsEllipseItem(point[0] - 5, point[1] - 5, 10, 10, self)
                point_item.setFlag(QGraphicsItem.ItemIsMovable)
                point_item.setFlag(QGraphicsItem.ItemSendsGeometryChanges)
                point_item.point_index = i
                point_item.setZValue(200)
                point_item.setBrush(QBrush(QColor(255, 255, 255)))
                point_item.setPen(QPen(QColor(*color)))
                self.control_points.append(point_item)

    def updatePolygon(self):
        if not self.editing or not self.control_points:
            return

        polygon = QPolygonF()
        self.points = []

        for point_item in self.control_points:
            pos = point_item.pos()
            x = pos.x() + 5  # Центр круга
            y = pos.y() + 5
            polygon.append(QPointF(x, y))
            self.points.append([x, y])

        self.setPolygon(polygon)

    def set_page_index(self, index):
        """Привязка маски к конкретной странице"""
        self.page_index = index

    def mouseDoubleClickEvent(self, event):
        """Обработка двойного клика - удаление маски"""
        self.deleted = True
        self.setVisible(False)
        super().mouseDoubleClickEvent(event)


class BrushStroke(QGraphicsPathItem):
    def __init__(self, color, size, parent=None):
        super().__init__(parent)
        self.path = QPainterPath()
        self.stroke_color = color
        self.stroke_size = size
        self.deleted = False
        self.page_index = None
        self.mask_type = 'brush'

        # Создаем перо
        pen = QPen(QColor(*color))
        pen.setWidth(size)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self.setPen(pen)
        self.setZValue(100)

    def set_page_index(self, index):
        """Привязка маски к конкретной странице"""
        self.page_index = index

    def clip_to_page_bounds(self, width, height):
        """Обрезка штриха по границам страницы"""
        clipped_path = QPainterPath()
        first_point = True

        for i in range(self.path.elementCount()):
            elem = self.path.elementAt(i)
            x = min(max(0, elem.x), width)
            y = min(max(0, elem.y), height)

            if first_point:
                clipped_path.moveTo(x, y)
                first_point = False
            else:
                clipped_path.lineTo(x, y)

        self.setPath(clipped_path)
        self.path = clipped_path

    def mouseDoubleClickEvent(self, event):
        """Обработка двойного клика - удаление штриха"""
        self.deleted = True
        self.setVisible(False)
        super().mouseDoubleClickEvent(event)


class SelectionRect(QGraphicsRectItem):
    def __init__(self, x, y, width, height, parent=None):
        super().__init__(x, y, width, height, parent)
        pen = QPen(QColor(0, 255, 255))
        pen.setWidth(2)
        pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        brush = QBrush(QColor(0, 255, 255, 30))
        self.setBrush(brush)