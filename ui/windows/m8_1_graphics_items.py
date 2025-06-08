# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QRectF, QPointF, QEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QGraphicsPolygonItem, QGraphicsPathItem, QGraphicsEllipseItem
from PySide6.QtGui import QPen, QBrush, QColor, QPolygonF, QPainterPath

class SelectionEvent(QEvent):
    Type = QEvent.Type(QEvent.User + 1)
    def __init__(self, rect):
        super().__init__(SelectionEvent.Type)
        self.rect = rect

class EditableMask(QGraphicsRectItem):
    def __init__(self, x, y, w, h, mask_type, class_name, conf, color, parent=None):
        super().__init__(x, y, w, h, parent)
        self.mask_type = mask_type
        self.class_name = class_name
        self.confidence = conf
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
        thresh = 10

        if ((pos.x() < rect.left() + thresh and pos.y() < rect.top() + thresh) or
                (pos.x() > rect.right() - thresh and pos.y() > rect.bottom() - thresh)):
            self.setCursor(Qt.SizeFDiagCursor)
        elif ((pos.x() > rect.right() - thresh and pos.y() < rect.top() + thresh) or
              (pos.x() < rect.left() + thresh and pos.y() > rect.bottom() - thresh)):
            self.setCursor(Qt.SizeBDiagCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            rect = self.rect()
            pos = event.pos()
            thresh = 10

            if pos.x() < rect.left() + thresh and pos.y() < rect.top() + thresh:
                self.resizing = True
                self.resize_corner = "top-left"
            elif pos.x() > rect.right() - thresh and pos.y() < rect.top() + thresh:
                self.resizing = True
                self.resize_corner = "top-right"
            elif pos.x() < rect.left() + thresh and pos.y() > rect.bottom() - thresh:
                self.resizing = True
                self.resize_corner = "bottom-left"
            elif pos.x() > rect.right() - thresh and pos.y() > rect.bottom() - thresh:
                self.resizing = True
                self.resize_corner = "bottom-right"
            else:
                self.resizing = False
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.resizing and self.resize_corner:
            rect = self.rect()
            pos = event.pos()

            old_w = rect.width()
            old_h = rect.height()
            old_x = rect.x()
            old_y = rect.y()

            new_x, new_y, new_w, new_h = old_x, old_y, old_w, old_h

            if self.resize_corner == "top-left":
                if pos.x() < old_x + old_w - 5 and pos.y() < old_y + old_h - 5:
                    new_x = pos.x()
                    new_y = pos.y()
                    new_w = old_w - (pos.x() - old_x)
                    new_h = old_h - (pos.y() - old_y)
            elif self.resize_corner == "top-right":
                if pos.x() > old_x + 5 and pos.y() < old_y + old_h - 5:
                    new_y = pos.y()
                    new_w = pos.x() - old_x
                    new_h = old_h - (pos.y() - old_y)
            elif self.resize_corner == "bottom-left":
                if pos.x() < old_x + old_w - 5 and pos.y() > old_y + 5:
                    new_x = pos.x()
                    new_w = old_w - (pos.x() - old_x)
                    new_h = pos.y() - old_y
            elif self.resize_corner == "bottom-right":
                if pos.x() > old_x + 5 and pos.y() > old_y + 5:
                    new_w = pos.x() - old_x
                    new_h = pos.y() - old_y

            if new_w >= 5 and new_h >= 5:
                self.setRect(new_x, new_y, new_w, new_h)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.resizing = False
        self.resize_corner = None
        super().mouseReleaseEvent(event)

    def set_page_index(self, index):
        self.page_index = index

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            new_pos = value
            rect = self.scene().sceneRect()

            if not rect.contains(self.mapToScene(self.rect()).boundingRect()):
                if new_pos.x() < rect.left():
                    new_pos.setX(rect.left())
                if new_pos.y() < rect.top():
                    new_pos.setY(rect.top())
                if new_pos.x() + self.rect().width() > rect.right():
                    new_pos.setX(rect.right() - self.rect().width())
                if new_pos.y() + self.rect().height() > rect.bottom():
                    new_pos.setY(rect.bottom() - self.rect().height())
                return new_pos

        return super().itemChange(change, value)
    def mouseDoubleClickEvent(self, event):
        self.deleted = True
        self.setVisible(False)
        super().mouseDoubleClickEvent(event)

class EditablePolygonMask(QGraphicsPolygonItem):
    def __init__(self, points, mask_type, class_name, conf, color, parent=None, editing=False):
        polygon = QPolygonF()
        for point in points:
            polygon.append(QPointF(point[0], point[1]))

        super().__init__(polygon, parent)
        self.mask_type = mask_type
        self.class_name = class_name
        self.confidence = conf
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
        self.page_index = index

    def mouseDoubleClickEvent(self, event):
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

        pen = QPen(QColor(*color))
        pen.setWidth(size)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        self.setPen(pen)
        self.setZValue(100)

    def set_page_index(self, index):
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
        self.deleted = True
        self.setVisible(False)
        super().mouseDoubleClickEvent(event)

class SelectionRect(QGraphicsRectItem):
    def __init__(self, x, y, w, h, parent=None):
        super().__init__(x, y, w, h, parent)
        pen = QPen(QColor(0, 255, 255))
        pen.setWidth(2)
        pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        brush = QBrush(QColor(0, 255, 255, 30))
        self.setBrush(brush)