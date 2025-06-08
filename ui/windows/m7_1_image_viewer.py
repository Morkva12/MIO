# -*- coding: utf-8 -*-
# ui/windows/m7_1_image_viewer.py

import os
import logging
import threading
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsTextItem, QGraphicsRectItem, QGraphicsWidget,
                               QGraphicsLinearLayout, QGraphicsProxyWidget, QTextEdit,
                               QMenu, QToolButton, QGraphicsLineItem, QGraphicsEllipseItem,
                               QGraphicsItem, QMessageBox, QHBoxLayout, QVBoxLayout,
                               QWidget, QPushButton, QApplication, QLabel)
from PySide6.QtGui import QPainter, QPixmap, QFont, QColor, QPen, QBrush, QTransform, QCursor
from PySide6.QtCore import Qt, QRectF, QEvent, QPointF, QSizeF, QObject, Signal, QMetaObject, Q_ARG

logger = logging.getLogger(__name__)


class PageChangeSignal(QObject):
    """Сигнал для уведомления об изменении страницы"""
    page_changed = Signal(int)


class NotesModifiedSignal(QObject):
    """Сигнал для уведомления об изменении заметок"""
    notes_modified = Signal()


class MyTextEdit(QTextEdit):
    """Класс текстового редактора для заметок"""
    text_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.textChanged.connect(self.onTextChanged)

    def onTextChanged(self):
        """Обработчик изменения текста"""
        self.text_changed.emit()


class LinkLineItem(QGraphicsLineItem):
    """Соединительная линия между точкой привязки и заметкой"""

    def __init__(self):
        super().__init__()
        self._anchorObj = None
        self._noteObj = None
        self.page_index = -1

    def updateLine(self):
        """Обновление линии соединения"""
        if not (self._anchorObj and self._noteObj and self.scene()):
            return

        noteCenter = self._noteObj.mapToScene(self._noteObj.boundingRect().center())

        if isinstance(self._anchorObj, AnchorPointItem):
            start = self._anchorObj.mapToScene(self._anchorObj.boundingRect().center())
        elif hasattr(self._anchorObj, "mapRectToScene"):
            brA = self._anchorObj.mapRectToScene(self._anchorObj.boundingRect())
            start = self._closestPointOnRect(brA, noteCenter)
        else:
            start = self._anchorObj if isinstance(self._anchorObj, QPointF) else None

        if not start:
            return

        if hasattr(self._noteObj, "mapRectToScene"):
            brN = self._noteObj.mapRectToScene(self._noteObj.boundingRect())
            end = self._closestPointOnRect(brN, start)
        else:
            end = noteCenter

        self.setLine(start.x(), start.y(), end.x(), end.y())

    def _closestPointOnRect(self, rectF, pt):
        """Находит ближайшую точку на прямоугольнике к заданной точке"""
        rx, ry, rw, rh = rectF.x(), rectF.y(), rectF.width(), rectF.height()
        return QPointF(min(max(pt.x(), rx), rx + rw), min(max(pt.y(), ry), ry + rh))


class AnchorPointItem(QGraphicsEllipseItem):
    """Точка привязки для заметок"""

    def __init__(self, x, y, r, settings):
        super().__init__(x - r, y - r, r * 2, r * 2)
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        c = settings.get("note_point_color", "#FF0000")
        self.setPen(QPen(QColor(c), 2))
        self.setBrush(QBrush(QColor(c)))
        self.setZValue(10)
        self.page_index = -1
        self.is_visible = True

        # Для обработки наведения мыши
        self.setAcceptHoverEvents(True)

        # Сигнал изменения заметок
        self.notes_modified_signal = NotesModifiedSignal()

    def itemChange(self, change, value):
        """Обработчик изменения положения точки"""
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() and hasattr(self.scene(), "_lines"):
            for ln in self.scene()._lines:
                ln.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        """Подсветка при наведении"""
        self.setPen(QPen(self.pen().color(), 3))
        self.setCursor(Qt.OpenHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Возврат к стандартному отображению"""
        self.setPen(QPen(self.pen().color(), 2))
        self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        """Обработка нажатия кнопки мыши"""
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Обработка отпускания кнопки мыши"""
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class MovableRectItem(QGraphicsRectItem):
    """Перемещаемый прямоугольник с возможностью изменения размера"""

    def __init__(self, rect, settings, is_ocr=False):
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.setPos(rect.x(), rect.y())
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.note_settings = settings
        self.is_ocr = is_ocr
        dc = settings.get("note_dotted_color", "#808080")
        self.setPen(QPen(QColor(dc), 2, Qt.DashLine))
        self.setBrush(QBrush(Qt.transparent))
        self.page_index = -1
        self.is_visible = True

        # Для обработки наведения мыши
        self.setAcceptHoverEvents(True)

        # Сигнал изменения заметок
        self.notes_modified_signal = NotesModifiedSignal()

        # Создаем ручку изменения размера
        self.resize_handle = QGraphicsRectItem(0, 0, 10, 10, self)
        self.resize_handle.setBrush(QBrush(Qt.gray))
        self.resize_handle.setCursor(Qt.SizeFDiagCursor)

        # Чтобы ручка не масштабировалась
        self.resize_handle.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self._updateResizeHandlePos()

        self._resizing = False
        self._resize_start = QPointF()
        self._start_size = QSizeF(self.rect().width(), self.rect().height())

    def _updateResizeHandlePos(self):
        """Обновление позиции ручки изменения размера"""
        r = self.rect()
        self.resize_handle.setPos(r.width() - self.resize_handle.rect().width(),
                                  r.height() - self.resize_handle.rect().height())

    def itemChange(self, change, value):
        """Обработчик изменения положения прямоугольника"""
        if change == QGraphicsItem.ItemPositionHasChanged and self.scene() and hasattr(self.scene(), "_lines"):
            for ln in self.scene()._lines:
                ln.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        """Подсветка при наведении"""
        self.setPen(QPen(self.pen().color(), 3, Qt.DashLine))
        self.setCursor(Qt.OpenHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Возврат к стандартному отображению"""
        self.setPen(QPen(self.pen().color(), 2, Qt.DashLine))
        self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        """Обработчик нажатия кнопки мыши"""
        if QRectF(self.resize_handle.pos(), self.resize_handle.rect().size()).contains(event.pos()):
            self._resizing = True
            self._resize_start = event.pos()
            self._start_size = QSizeF(self.rect().width(), self.rect().height())
            event.accept()
            return

        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.ClosedHandCursor)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Обработчик перемещения мыши"""
        if self._resizing:
            dx = event.pos().x() - self._resize_start.x()
            dy = event.pos().y() - self._resize_start.y()
            new_width = max(20, self._start_size.width() + dx)
            new_height = max(20, self._start_size.height() + dy)
            self.setRect(0, 0, new_width, new_height)
            self._updateResizeHandlePos()

            if self.scene() and hasattr(self.scene(), "_lines"):
                for ln in self.scene()._lines:
                    ln.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Обработчик отпускания кнопки мыши"""
        if self._resizing:
            self._resizing = False

            if self.scene() and hasattr(self.scene(), "_lines"):
                for ln in self.scene()._lines:
                    if ln._anchorObj == self:
                        ln.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

            event.accept()
            return

        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)

        super().mouseReleaseEvent(event)


class NoteItem(QGraphicsWidget):
    """Виджет заметки с возможностью редактирования текста и OCR-распознавания"""

    def __init__(self, pos, page_index, mode, settings, extra=None):
        super().__init__()
        self.line = None
        self.setFlags(
            QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemSendsGeometryChanges)
        self.page_index = page_index
        self.mode = mode
        self.settings = settings
        self.extra = extra if extra is not None else {}
        self.ocr_result = None
        self.is_visible = True

        # Сигнал изменения заметок
        self.notes_modified_signal = NotesModifiedSignal()

        # Увеличенный начальный размер заметки
        self._width, self._height = 220, 150
        self.setPreferredSize(self._width, self._height)
        self.setPos(pos)

        self.border_color = QColor(settings.get("note_border_color", "#000000"))
        self.bg_color = QColor(settings.get("note_bg_color", "#FFFFE0"))

        # Создаем вертикальный лейаут с правильными отступами
        lyt = QGraphicsLinearLayout(Qt.Vertical)
        lyt.setContentsMargins(8, 8, 8, 8)
        lyt.setSpacing(5)
        self.setLayout(lyt)

        # Создаем текстовый редактор
        self.text_edit = MyTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setFrameStyle(0)
        self.text_edit.text_changed.connect(self.onTextChanged)

        tc, fs = settings.get("note_text_color", "#000000"), settings.get("font_size", 12)
        self.text_edit.setStyleSheet(
            f"background: {self.bg_color.name()}; color: {tc}; "
            f"border: 1px solid {self.border_color.name()}; "
            f"border-radius: 8px; padding: 5px;"
        )
        self.text_edit.setFontPointSize(fs)
        self.text_edit.setFocusPolicy(Qt.StrongFocus)
        self.text_edit.setMinimumHeight(80)

        # Добавляем текстовый редактор в лейаут
        self.proxy_text = QGraphicsProxyWidget()
        self.proxy_text.setWidget(self.text_edit)
        lyt.addItem(self.proxy_text)

        # Контейнер для результатов OCR
        self.ocr_container = QGraphicsWidget()
        ocr_layout = QGraphicsLinearLayout(Qt.Vertical)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        ocr_layout.setSpacing(5)
        self.ocr_container.setLayout(ocr_layout)

        # Заголовок панели OCR с кнопкой скрытия
        ocr_header = QWidget()
        ocr_header_layout = QHBoxLayout(ocr_header)
        ocr_header_layout.setContentsMargins(0, 0, 0, 0)
        ocr_header_layout.setSpacing(5)

        ocr_title = QLabel("Результаты OCR")
        ocr_title.setStyleSheet("color: white; font-weight: bold;")

        self.hide_ocr_btn = QPushButton("×")
        self.hide_ocr_btn.setFixedSize(22, 22)
        self.hide_ocr_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #9E3EBF;
            }
        """)
        self.hide_ocr_btn.clicked.connect(self.hideOcrPanel)

        ocr_header_layout.addWidget(ocr_title, 1)
        ocr_header_layout.addWidget(self.hide_ocr_btn, 0)

        ocr_header_proxy = QGraphicsProxyWidget()
        ocr_header_proxy.setWidget(ocr_header)
        ocr_layout.addItem(ocr_header_proxy)

        # Текстовое поле для отображения результатов OCR
        self.ocr_text = QTextEdit()
        self.ocr_text.setReadOnly(True)
        self.ocr_text.setFrameStyle(0)
        self.ocr_text.setStyleSheet("""
            QTextEdit {
                background: #363650;
                color: #FFFFFF;
                border: 1px solid #7E1E9F;
                border-radius: 8px;
                padding: 5px;
            }
        """)
        self.ocr_text.setFixedHeight(60)
        self.ocr_text.setPlaceholderText("Результаты OCR")

        self.ocr_text_proxy = QGraphicsProxyWidget()
        self.ocr_text_proxy.setWidget(self.ocr_text)
        ocr_layout.addItem(self.ocr_text_proxy)

        # Кнопки действий для OCR
        ocr_buttons_widget = QWidget()
        ocr_buttons_layout = QHBoxLayout(ocr_buttons_widget)
        ocr_buttons_layout.setContentsMargins(0, 0, 0, 0)
        ocr_buttons_layout.setSpacing(5)

        self.copy_ocr_btn = QPushButton("Копировать")
        self.copy_ocr_btn.setFixedHeight(22)
        self.copy_ocr_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 4px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #9E3EBF;
            }
        """)
        self.copy_ocr_btn.clicked.connect(self.copyOcrResult)

        self.insert_ocr_btn = QPushButton("В заметку")
        self.insert_ocr_btn.setFixedHeight(22)
        self.insert_ocr_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 4px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #9E3EBF;
            }
        """)
        self.insert_ocr_btn.clicked.connect(self.insertOcrToNote)

        ocr_buttons_layout.addWidget(self.copy_ocr_btn)
        ocr_buttons_layout.addWidget(self.insert_ocr_btn)

        self.ocr_buttons_proxy = QGraphicsProxyWidget()
        self.ocr_buttons_proxy.setWidget(ocr_buttons_widget)
        ocr_layout.addItem(self.ocr_buttons_proxy)

        # По умолчанию контейнер OCR скрыт
        self.ocr_container.setVisible(False)
        lyt.addItem(self.ocr_container)

        # Создаем контейнер для кнопок
        self.button_container = QGraphicsWidget()
        hlyt = QGraphicsLinearLayout(Qt.Horizontal)
        hlyt.setContentsMargins(0, 0, 0, 0)
        hlyt.setSpacing(5)
        self.button_container.setLayout(hlyt)

        if mode == "Прямоугольная":
            # Компактные кнопки без подложки
            button_width = 85
            button_height = 22

            # OCR кнопка только для прямоугольного режима
            self.btn_ocr = QPushButton("OCR")
            self.btn_ocr.setToolTip("Распознать текст")
            self.btn_ocr.setFixedSize(button_width, button_height)
            self.btn_ocr.setStyleSheet("""
                QPushButton {
                    background-color: #7E1E9F;
                    color: white;
                    border-radius: 4px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #9E3EBF;
                }
            """)
            self.btn_ocr.clicked.connect(self.runOcr)

            # Кнопка копирования изображения
            self.btn_copy_image = QPushButton("Копировать")
            self.btn_copy_image.setToolTip("Копировать изображение")
            self.btn_copy_image.setFixedSize(button_width, button_height)
            self.btn_copy_image.setStyleSheet("""
                QPushButton {
                    background-color: #7E1E9F;
                    color: white;
                    border-radius: 4px;
                    font-size: 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #9E3EBF;
                }
            """)
            self.btn_copy_image.clicked.connect(self.copyImageToClipboard)

            # Просто добавляем кнопки напрямую в layout, без промежуточного виджета
            btn_ocr_proxy = QGraphicsProxyWidget()
            btn_ocr_proxy.setWidget(self.btn_ocr)
            hlyt.addItem(btn_ocr_proxy)

            btn_copy_proxy = QGraphicsProxyWidget()
            btn_copy_proxy.setWidget(self.btn_copy_image)
            hlyt.addItem(btn_copy_proxy)

        lyt.addItem(self.button_container)

        # Создаем ручку изменения размера
        self.resize_handle = QGraphicsRectItem(self)
        self.resize_handle.setRect(self._width - 10, self._height - 10, 10, 10)
        self.resize_handle.setBrush(QBrush(Qt.gray))
        self.resize_handle.setCursor(Qt.SizeFDiagCursor)

        self._resizing = False
        self._resize_start = QPointF()
        self._start_geometry = QSizeF(self._width, self._height)

        # Создаем линию соединения если есть точка привязки
        if "p1" in self.extra:
            self.line = LinkLineItem()
            dc = settings.get("note_dotted_color", "#808080")
            self.line.setPen(QPen(QColor(dc), 2, Qt.DashLine))
            self.line._anchorObj = self.extra["p1"]
            self.line._noteObj = self
            self.line.page_index = page_index

    def hideOcrPanel(self):
        """Скрывает панель OCR"""
        self.ocr_container.setVisible(False)
        self._adjustNoteSize()

    def onTextChanged(self):
        """Обработчик изменения текста заметки"""
        self.notes_modified_signal.notes_modified.emit()

    def runOcr(self):
        """Запускает OCR-распознавание для выделенной области"""
        # Проверяем, что есть связанный прямоугольник и изображение
        if not ("p1" in self.extra and self.scene() and hasattr(self.scene(), "views") and self.scene().views()):
            return

        view = self.scene().views()[0]
        if not hasattr(view, "pixmaps") or not view.pixmaps:
            return

        # Получаем текущее изображение
        if not hasattr(view, "current_page"):
            return

        current_page = view.current_page
        if current_page < 0 or current_page >= len(view.pixmaps):
            return

        pixmap = view.pixmaps[current_page]
        if pixmap.isNull():
            return

        # Получаем прямоугольник области
        rect_item = self.extra["p1"]
        if not isinstance(rect_item, MovableRectItem):
            return

        # Преобразуем координаты прямоугольника в координаты изображения
        scene_rect = rect_item.mapRectToScene(rect_item.rect())
        x, y, w, h = scene_rect.x(), scene_rect.y(), scene_rect.width(), scene_rect.height()

        # Создаем QImage из QPixmap и вырезаем нужную область
        image = pixmap.toImage()
        crop_img = image.copy(int(x), int(y), int(w), int(h))

        # Получаем язык OCR из настроек
        ocr_lang = self.settings.get("ocr_language", "Русский")

        # Запускаем OCR в отдельном потоке
        self.startOcrWorker(crop_img, ocr_lang)

        # Показываем панель с результатами OCR
        self.ocr_container.setVisible(True)
        self.ocr_text.setPlainText(f"Распознавание на языке: {ocr_lang}...")

        # Обновляем размер заметки
        self._adjustNoteSize()

    def startOcrWorker(self, image, lang="Русский"):
        """Запускает OCR в отдельном потоке"""
        # Сохраняем изображение во временный файл
        temp_dir = os.path.join(os.path.expanduser("~"), ".manga_localizer_temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"ocr_temp_{id(self)}.png")
        image.save(temp_path)

        # Запускаем OCR в отдельном потоке
        threading.Thread(
            target=self._performOcr,
            args=(temp_path, lang),
            daemon=True
        ).start()

    def _performOcr(self, image_path, lang="Русский"):
        """Выполняет OCR-распознавание"""
        try:
            import easyocr

            # Получаем язык распознавания
            lang_map = {
                "Русский": "ru",
                "Английский": "en",
                "Японский": "ja",
                "Китайский": "ch_sim",
                "Корейский": "ko"
            }
            ocr_lang = lang_map.get(lang, "en")

            # Инициализируем ридер EasyOCR
            reader = easyocr.Reader([ocr_lang])

            # Распознаем текст
            result = reader.readtext(image_path)

            # Соединяем все распознанные фрагменты
            text = "\n".join([item[1] for item in result]) if result else "Текст не распознан"

            # Обновляем UI в основном потоке
            QMetaObject.invokeMethod(
                self.ocr_text,
                "setPlainText",
                Qt.QueuedConnection,
                Q_ARG(str, text)
            )

            # Сохраняем результат
            self.ocr_result = text

            # Удаляем временный файл
            try:
                os.remove(image_path)
            except:
                pass

        except Exception as e:
            # В случае ошибки обновляем UI
            error_msg = f"Ошибка OCR: {str(e)}"
            QMetaObject.invokeMethod(
                self.ocr_text,
                "setPlainText",
                Qt.QueuedConnection,
                Q_ARG(str, error_msg)
            )

    def copyOcrResult(self):
        """Копирует результат OCR в буфер обмена"""
        if self.ocr_result:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.ocr_result)

    def insertOcrToNote(self):
        """Вставляет результат OCR в заметку"""
        if self.ocr_result:
            current_text = self.text_edit.toPlainText()
            if current_text:
                self.text_edit.setPlainText(current_text + "\n" + self.ocr_result)
            else:
                self.text_edit.setPlainText(self.ocr_result)

    def copyImageToClipboard(self):
        """Копирует выделенную область изображения в буфер обмена"""
        if not ("p1" in self.extra and self.scene() and hasattr(self.scene(), "views") and self.scene().views()):
            return

        view = self.scene().views()[0]
        if not hasattr(view, "pixmaps") or not view.pixmaps:
            return

        # Получаем текущее изображение
        if not hasattr(view, "current_page"):
            return

        current_page = view.current_page
        if current_page < 0 or current_page >= len(view.pixmaps):
            return

        pixmap = view.pixmaps[current_page]
        if pixmap.isNull():
            return

        # Получаем прямоугольник области
        rect_item = self.extra["p1"]
        if not isinstance(rect_item, MovableRectItem):
            return

        # Преобразуем координаты прямоугольника в координаты изображения
        scene_rect = rect_item.mapRectToScene(rect_item.rect())
        x, y, w, h = scene_rect.x(), scene_rect.y(), scene_rect.width(), scene_rect.height()

        # Создаем новый QPixmap с вырезанной областью
        crop_pixmap = pixmap.copy(int(x), int(y), int(w), int(h))

        # Копируем в буфер обмена
        clipboard = QApplication.clipboard()
        clipboard.setPixmap(crop_pixmap)

    def _adjustNoteSize(self):
        """Настраивает размер заметки после изменения содержимого"""
        # Если OCR-панель видима, делаем заметку больше
        if self.ocr_container.isVisible():
            min_height = 220  # Минимальная высота с OCR-панелью
        else:
            min_height = 150  # Базовая минимальная высота

        if self._height < min_height:
            new_width = max(self._width, 220)
            self.setPreferredSize(new_width, min_height)
            self.resize(QSizeF(new_width, min_height))
            self._width, self._height = new_width, min_height
            self.resize_handle.setRect(self._width - 10, self._height - 10, 10, 10)

    def setVisibility(self, visible):
        """Установка видимости заметки"""
        self.is_visible = visible

        # Обновляем видимость связанных объектов
        self.setVisible(visible)
        if self.line:
            self.line.setVisible(visible)

        if "p1" in self.extra:
            anchor = self.extra["p1"]
            if hasattr(anchor, "setVisible"):
                anchor.is_visible = visible
                anchor.setVisible(visible)

        # Сигнал о модификации заметок
        self.notes_modified_signal.notes_modified.emit()

    def itemChange(self, change, value):
        """Обработчик изменения положения заметки"""
        if change == QGraphicsItem.ItemPositionHasChanged:
            if self.line:
                self.line.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

        return super().itemChange(change, value)

    def boundingRect(self):
        """Определение границ элемента"""
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter, option, widget):
        """Отрисовка заметки"""
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(self.border_color, 2))
        painter.setBrush(QBrush(self.bg_color))
        painter.drawRoundedRect(self.boundingRect(), 10, 10)

    def resizeEvent(self, event):
        """Обработчик изменения размера"""
        new_size = event.newSize()
        self._width, self._height = new_size.width(), new_size.height()
        self.resize_handle.setRect(self._width - 10, self._height - 10, 10, 10)

        # Сигнал о модификации заметок
        self.notes_modified_signal.notes_modified.emit()

        super().resizeEvent(event)

    def mousePressEvent(self, event):
        """Обработчик нажатия кнопки мыши"""
        if event.button() == Qt.LeftButton and self.resize_handle.isUnderMouse():
            self._resizing = True
            self._resize_start = event.pos()
            self._start_geometry = QSizeF(self._width, self._height)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Обработчик перемещения мыши"""
        if self._resizing:
            delta = event.pos() - self._resize_start
            new_width = max(150, self._start_geometry.width() + delta.x())
            new_height = max(120, self._start_geometry.height() + delta.y())
            self.setPreferredSize(new_width, new_height)
            self.resize(QSizeF(new_width, new_height))

            if self.line:
                self.line.updateLine()

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Обработчик отпускания кнопки мыши"""
        if self._resizing:
            self._resizing = False

            # Сигнал о модификации заметок
            self.notes_modified_signal.notes_modified.emit()

            event.accept()
            return

        super().mouseReleaseEvent(event)

    def updateStyles(self, settings):
        """Обновление стилей заметки"""
        self.settings.update(settings)
        self.border_color = QColor(settings.get("note_border_color", "#000000"))
        self.bg_color = QColor(settings.get("note_bg_color", "#FFFFE0"))

        tc, fs = settings.get("note_text_color", "#000000"), settings.get("font_size", 12)
        self.text_edit.setStyleSheet(
            f"background:{self.bg_color.name()}; color:{tc}; "
            f"border: 1px solid {self.border_color.name()}; "
            f"border-radius: 8px; padding: 5px;"
        )
        self.text_edit.setFontPointSize(fs)
        self.update()


class ImageViewer(QGraphicsView):
    """Просмотрщик изображений с поддержкой заметок и постраничного просмотра"""

    def __init__(self, pixmap_paths, parent=None):
        super().__init__(parent)
        self.parent_window = parent

        # Настройки отображения
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFocusPolicy(Qt.StrongFocus)

        # Инициализация переменных
        self.pages = pixmap_paths
        self.current_page = 0
        self.note_mode = "Стандартная"
        self.note_settings = {}
        self.notes = []
        self.scale_factor = 1.0
        self._panning = False
        self._last_pan_point = QPointF()
        self._temp_data = {}

        # Создаём сцену
        self.scene_ = QGraphicsScene(self)
        self.scene_._lines = []
        self.setScene(self.scene_)

        # Создаем элементы для отображения
        self.pixmaps = [QPixmap(p) for p in self.pages]
        self.page_pixmap_item = QGraphicsPixmapItem()
        self.scene_.addItem(self.page_pixmap_item)

        # Сигнал изменения заметок
        self.notes_modified_signal = NotesModifiedSignal()
        self.notes_modified_signal.notes_modified.connect(self.onNotesModified)

        # Инициализация размера сцены с отступами
        self._setSceneRectWithMargin(QRectF(0, 0, 800, 600), margin=80)

    def onNotesModified(self):
        """Обработчик изменения заметок"""
        if hasattr(self.parent_window, 'saveStatus'):
            self.parent_window.saveStatus()

    def _setSceneRectWithMargin(self, rect, margin=80):
        """Устанавливает размер сцены с отступами"""
        self.setSceneRect(rect.x() - margin, rect.y() - margin,
                          rect.width() + 2 * margin, rect.height() + 2 * margin)

    def _closestPointOnRect(self, rectF, pt):
        """Находит ближайшую точку на прямоугольнике к заданной точке"""
        rx, ry, rw, rh = rectF.x(), rectF.y(), rectF.width(), rectF.height()
        return QPointF(min(max(pt.x(), rx), rx + rw), min(max(pt.y(), ry), ry + rh))

    def displayCurrentPage(self):
        """Отображает текущую страницу"""
        if not 0 <= self.current_page < len(self.pages) or not self.pixmaps:
            return

        # Отображаем текущую страницу
        pm = self.pixmaps[self.current_page]
        if pm.isNull():
            return

        self.page_pixmap_item.setPixmap(pm)
        self.page_pixmap_item.setTransform(QTransform())
        self.page_pixmap_item.setPos(0, 0)

        # Устанавливаем размер сцены с учетом размера изображения
        self._setSceneRectWithMargin(QRectF(0, 0, pm.width(), pm.height()), 80)

        # Обновляем видимость всех объектов
        self.updateNotesVisibility()

    def updateNotesVisibility(self):
        """Обновляет видимость заметок и связанных объектов для текущей страницы"""
        # Обрабатываем заметки
        for note in self.notes:
            is_current_page = note.page_index == self.current_page
            # Видимость заметки определяется и номером страницы, и флагом видимости
            note.setVisible(is_current_page and note.is_visible)

            # Управляем видимостью линии напрямую
            if note.line:
                note.line.setVisible(is_current_page and note.is_visible)

        # Обрабатываем точки привязки и прямоугольники
        for item in self.scene_.items():
            if isinstance(item, AnchorPointItem):
                is_current_page = getattr(item, "page_index", None) == self.current_page
                is_visible = getattr(item, "is_visible", True)
                item.setVisible(is_current_page and is_visible)
            elif isinstance(item, MovableRectItem):
                is_current_page = getattr(item, "page_index", None) == self.current_page
                is_visible = getattr(item, "is_visible", True)
                item.setVisible(is_current_page and is_visible)

        # Проверяем, что все линии имеют правильную видимость
        for line in self.scene_._lines:
            if not hasattr(line, "_noteObj") or not line._noteObj:
                continue

            note = line._noteObj
            is_current_page = getattr(line, "page_index", None) == self.current_page
            line.setVisible(is_current_page and note.is_visible)

    def nextPage(self):
        """Переход на следующую страницу"""
        if self.current_page < len(self.pages) - 1:
            # Сохраняем текущую страницу перед переходом
            if hasattr(self.parent_window, 'saveStatus'):
                self.parent_window.saveStatus()

            self.current_page += 1
            self.displayCurrentPage()

            # Если в родительском окне есть сигнал смены страницы, вызываем его
            if hasattr(self.parent_window, 'page_change_signal'):
                self.parent_window.page_change_signal.page_changed.emit(self.current_page)

            return True

        return False

    def previousPage(self):
        """Переход на предыдущую страницу"""
        if self.current_page > 0:
            # Сохраняем текущую страницу перед переходом
            if hasattr(self.parent_window, 'saveStatus'):
                self.parent_window.saveStatus()

            self.current_page -= 1
            self.displayCurrentPage()

            # Если в родительском окне есть сигнал смены страницы, вызываем его
            if hasattr(self.parent_window, 'page_change_signal'):
                self.parent_window.page_change_signal.page_changed.emit(self.current_page)

            return True

        return False

    def wheelEvent(self, event):
        """Обработка прокрутки колесом мыши"""
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
        """Обработка нажатия клавиш"""
        focusItem = self.scene().focusItem()

        if event.key() == Qt.Key_Space:
            # Проверяем, не в текстовом ли поле мы находимся
            if focusItem and hasattr(focusItem, 'widget') and isinstance(focusItem.widget(), QTextEdit):
                super().keyPressEvent(event)
                return

            # Активируем режим перемещения
            self._panning = True
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if event.key() == Qt.Key_Left:
            self.previousPage()
            event.accept()
            return
        elif event.key() == Qt.Key_Right:
            self.nextPage()
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """Обработка отпускания клавиш"""
        if event.key() == Qt.Key_Space:
            self._panning = False
            self.viewport().setCursor(Qt.ArrowCursor)
            event.accept()
            return

        super().keyReleaseEvent(event)

    def mousePressEvent(self, event):
        """Создание заметок при клике на изображении"""
        # Если есть фокус на текстовом поле или режим заметок выключен - передаем событие дальше
        if self.scene().focusItem() or self.note_mode == "Выключено" or event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        # Получаем позицию в координатах сцены
        pos = self.mapToScene(event.pos())

        # Проверяем, не кликнули ли по существующему элементу
        item = self.scene().itemAt(pos, self.transform())
        if item and not isinstance(item, QGraphicsPixmapItem):
            super().mousePressEvent(event)
            return

        # Создаем заметку в зависимости от выбранного режима
        if self.note_mode == "Стандартная":
            # Создаем точку привязки
            r = self.note_settings.get("point_radius", 5)
            point = AnchorPointItem(pos.x(), pos.y(), r, self.note_settings)
            point.page_index = self.current_page
            point.notes_modified_signal.notes_modified.connect(self.onNotesModified)
            self.scene_.addItem(point)

            # Создаем заметку со смещением от точки
            note_pos = QPointF(pos.x() + 30, pos.y() - 60)
            note = NoteItem(note_pos, self.current_page, self.note_mode, self.note_settings, {"p1": point})
            note.notes_modified_signal.notes_modified.connect(self.onNotesModified)

            # Добавляем линию связи
            if note.line:
                self.scene_._lines.append(note.line)
                self.scene_.addItem(note.line)
                note.line.updateLine()

        elif self.note_mode == "Прямоугольная":
            # Создаем прямоугольник для OCR
            rect = QRectF(pos.x(), pos.y(), 100, 100)
            rect_item = MovableRectItem(rect, self.note_settings, True)
            rect_item.page_index = self.current_page
            rect_item.notes_modified_signal.notes_modified.connect(self.onNotesModified)
            self.scene_.addItem(rect_item)

            # Создаем заметку справа от прямоугольника
            note_pos = QPointF(pos.x() + 120, pos.y())
            note = NoteItem(note_pos, self.current_page, "Прямоугольная", self.note_settings, {"p1": rect_item})
            note.notes_modified_signal.notes_modified.connect(self.onNotesModified)

            # Добавляем линию связи
            if note.line:
                self.scene_._lines.append(note.line)
                self.scene_.addItem(note.line)
                note.line.updateLine()

        elif self.note_mode == "Простая":
            # Создаем простую заметку без привязки
            note = NoteItem(pos, self.current_page, "Простая", self.note_settings)
            note.notes_modified_signal.notes_modified.connect(self.onNotesModified)

        # Добавляем созданную заметку на сцену
        self.scene_.addItem(note)
        self.notes.append(note)

        # Сохраняем изменения
        self.notes_modified_signal.notes_modified.emit()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Обработка перемещения мыши"""
        if self._panning:
            pos = event.pos()
            delta = pos - self._last_pan_point
            self._last_pan_point = pos

            if not delta.isNull():
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())

            event.accept()
            return

        super().mouseMoveEvent(event)

    def contextMenuEvent(self, event):
        """Обработка контекстного меню по правой кнопке мыши"""
        # Создаем меню
        menu = QMenu(self)

        # Получаем элемент под курсором
        pos = self.mapToScene(event.pos())
        item = self.scene().itemAt(pos, self.transform())

        if isinstance(item, NoteItem) or isinstance(item, AnchorPointItem) or isinstance(item, MovableRectItem):
            # Меню для элемента
            if isinstance(item, NoteItem):
                # Действия для заметки
                delete_action = menu.addAction("Удалить заметку")
                delete_action.triggered.connect(lambda: self.deleteNote(item))

                if item.is_visible:
                    hide_action = menu.addAction("Скрыть заметку")
                    hide_action.triggered.connect(lambda: self.toggleNoteVisibility(item, False))
                else:
                    show_action = menu.addAction("Показать заметку")
                    show_action.triggered.connect(lambda: self.toggleNoteVisibility(item, True))
            elif isinstance(item, AnchorPointItem) or isinstance(item, MovableRectItem):
                # Для привязки находим соответствующую заметку
                related_note = None
                for note in self.notes:
                    if "p1" in note.extra and note.extra["p1"] == item:
                        related_note = note
                        break

                if related_note:
                    delete_action = menu.addAction("Удалить заметку")
                    delete_action.triggered.connect(lambda: self.deleteNote(related_note))

                    if related_note.is_visible:
                        hide_action = menu.addAction("Скрыть заметку")
                        hide_action.triggered.connect(lambda: self.toggleNoteVisibility(related_note, False))
                    else:
                        show_action = menu.addAction("Показать заметку")
                        show_action.triggered.connect(lambda: self.toggleNoteVisibility(related_note, True))
        else:
            # Общее меню для сцены
            delete_all_action = menu.addAction("Удалить все заметки")
            delete_all_action.triggered.connect(self.deleteAllNotes)

            hide_all_action = menu.addAction("Скрыть все заметки")
            hide_all_action.triggered.connect(lambda: self.toggleAllNotesVisibility(False))

            show_all_action = menu.addAction("Показать все заметки")
            show_all_action.triggered.connect(lambda: self.toggleAllNotesVisibility(True))

            delete_current_action = menu.addAction("Удалить заметки на текущей странице")
            delete_current_action.triggered.connect(self.deleteCurrentPageNotes)

        # Показываем меню
        menu.exec_(event.globalPos())

    def deleteNote(self, note):
        """Удаляет заметку и все связанные с ней объекты"""
        if note in self.notes:
            # Удаляем линию связи
            if note.line:
                if note.line in self.scene_._lines:
                    self.scene_._lines.remove(note.line)
                if note.line.scene():
                    self.scene_.removeItem(note.line)

            # Удаляем точку привязки или прямоугольник
            if "p1" in note.extra:
                anchor = note.extra["p1"]
                if anchor and anchor.scene():
                    self.scene_.removeItem(anchor)

            # Удаляем саму заметку
            if note.scene():
                self.scene_.removeItem(note)

            # Удаляем из списка заметок
            self.notes.remove(note)

            # Обновляем состояние
            self.notes_modified_signal.notes_modified.emit()

    def deleteAllNotes(self):
        """Удаляет все заметки"""
        # Копируем список заметок, т.к. будем его изменять в цикле
        notes_to_delete = self.notes.copy()

        for note in notes_to_delete:
            self.deleteNote(note)

    def deleteCurrentPageNotes(self):
        """Удаляет заметки на текущей странице"""
        # Находим заметки текущей страницы
        notes_to_delete = [note for note in self.notes if note.page_index == self.current_page]

        for note in notes_to_delete:
            self.deleteNote(note)

    def toggleNoteVisibility(self, note, visible):
        """Переключает видимость заметки"""
        note.setVisibility(visible)
        self.updateNotesVisibility()

    def toggleAllNotesVisibility(self, visible):
        """Переключает видимость всех заметок"""
        for note in self.notes:
            note.setVisibility(visible)

        # Дополнительно обрабатываем линии, чтобы гарантировать их корректную видимость
        for line in self.scene_._lines:
            # Линия должна быть видна только если привязана к видимой заметке и на текущей странице
            related_note = None
            for note in self.notes:
                if note.line == line:
                    related_note = note
                    break

            if related_note:
                is_current_page = line.page_index == self.current_page
                line.setVisible(is_current_page and related_note.is_visible)

        # Обновляем общую видимость элементов
        self.updateNotesVisibility()