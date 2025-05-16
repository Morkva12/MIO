# ui/windows/m1_0_main_window.py
import os
import json
import math
import re
from datetime import datetime

from PySide6.QtCore import Qt, Slot, QTimer, QPoint, Signal,QSize,QRect
from PySide6.QtGui import QPixmap, QColor, QWheelEvent, QFontMetrics, QCursor
from PySide6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QScrollArea, QFrame, QWidget, QGridLayout,
    QPushButton, QSpinBox, QMenu, QComboBox, QSizePolicy,
    QSpacerItem, QGraphicsBlurEffect, QDialog, QApplication,
    QListWidget, QListWidgetItem
)

from ui.components.gradient_widget import GradientBackgroundWidget
from .m1_2_tile_widget import TileWidget


# Функция естественной сортировки строк с числами
def natural_sort_key(s):
    """
    Ключ для естественной сортировки строк с числами.
    Разбивает строку на текстовые и числовые части,
    обрабатывая числа как целые числа при сравнении.
    """
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]


class TagSuggestionList(QListWidget):
    """Улучшенный список тегов с поддержкой выбора и исключения"""
    tag_selected = Signal(str)
    tag_exclude_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)

        # Настройка внешнего вида
        self.setStyleSheet("""
            QListWidget {
                border: 2px solid #7E1E9F;
                background-color: #3E3E5F;
                color: white;
                font-size: 14px;
                selection-background-color: #8E2EBF;
                selection-color: white;
                padding: 2px;
            }
            QScrollBar:vertical {
                background: #3E3E5F;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #7E1E9F;
                min-height: 20px;
                border-radius: 5px;
            }
            QListWidget::item {
                padding: 4px;
            }
        """)

        # Подключение сигналов
        self.itemClicked.connect(self.on_item_clicked)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        self.current_input = ""
        self.exclude_mode = False

    def update_suggestions(self, suggestions, current_input="", exclude_mode=False):
        """Обновление списка подсказок с минимальными отступами"""
        self.clear()
        self.current_input = current_input
        self.exclude_mode = exclude_mode

        # Добавляем теги
        for tag in suggestions:
            item = QListWidgetItem(tag)
            if exclude_mode:
                item.setForeground(QColor("#FF8888"))
            self.addItem(item)

        # Определяем точную ширину для тегов с минимальными отступами
        if self.count() > 0:
            fm = self.fontMetrics()
            max_width = 0
            for i in range(self.count()):
                if self.item(i).flags() & Qt.ItemIsSelectable:
                    tag_width = fm.horizontalAdvance(self.item(i).text())
                    max_width = max(max_width, tag_width)

            # Устанавливаем минимальную ширину для очень коротких тегов
            min_width = fm.horizontalAdvance("W" * 8) + 20
            width = max(max_width + 20, min_width)

            self.setMinimumWidth(width)
            self.setMaximumWidth(width)

            # Устанавливаем уменьшенные внутренние отступы для элементов списка
            self.setStyleSheet("""
                QListWidget {
                    border: 2px solid #7E1E9F;
                    background-color: #3E3E5F;
                    color: white;
                    font-size: 14px;
                    selection-background-color: #8E2EBF;
                    selection-color: white;
                    padding: 1px;
                }
                QScrollBar:vertical {
                    background: #3E3E5F;
                    width: 10px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical {
                    background: #7E1E9F;
                    min-height: 20px;
                    border-radius: 5px;
                }
                QListWidget::item {
                    padding: 2px 4px;
                }
            """)
        else:
            self.setMinimumWidth(150)
            self.setMaximumWidth(150)

        # Выбираем первый элемент
        if self.count() > 0 and self.item(0).flags() & Qt.ItemIsSelectable:
            self.setCurrentRow(0)

        # Настраиваем горизонтальную прокрутку (всегда отключена)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Ограничиваем высоту для ровно 8 видимых элементов
        item_height = self.sizeHintForRow(0) if self.count() > 0 else 20
        max_visible = min(8, self.count())
        height = item_height * max_visible + 2  # +2 для рамки

        self.setFixedHeight(height)

        # Включаем вертикальную прокрутку только если элементов больше 8
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if self.count() > 8 else Qt.ScrollBarAlwaysOff)

        # Устанавливаем режим прокрутки по пикселям для плавности
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

    def on_item_clicked(self, item):
        """Обработка клика по элементу с возвратом фокуса"""
        if not item or not (item.flags() & Qt.ItemIsSelectable):
            self.hide()
            self.parent().setFocus()
            return

        if self.exclude_mode:
            self.tag_exclude_selected.emit(item.text())
        else:
            self.tag_selected.emit(item.text())

        self.hide()
        self.parent().setFocus()

    def show_context_menu(self, position):
        """Показ контекстного меню"""
        item = self.itemAt(position)
        if not item or not (item.flags() & Qt.ItemIsSelectable):
            return

        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #3E3E5F;
                color: white;
                border: 1px solid #7E1E9F;
            }
            QMenu::item:selected {
                background-color: #7E1E9F;
            }
        """)

        select_action = menu.addAction("Выбрать")

        if self.exclude_mode:
            exclude_action = menu.addAction("Исключить этот тег")
            action = menu.exec_(self.mapToGlobal(position))

            if action == exclude_action:
                self.tag_exclude_selected.emit(item.text())
                self.hide()
                self.parent().setFocus()
            elif action == select_action:
                self.on_item_clicked(item)
        else:
            action = menu.exec_(self.mapToGlobal(position))
            if action == select_action:
                self.on_item_clicked(item)

    def keyPressEvent(self, event):
        """Полностью переопределенная обработка нажатий клавиш"""
        key = event.key()

        # Получаем только выбираемые элементы
        selectable_items = [i for i in range(self.count()) if self.item(i).flags() & Qt.ItemIsSelectable]

        if not selectable_items:
            self.parent().setFocus()
            return

        # Прерываем стандартное поведение Qt
        event.accept()

        if key in (Qt.Key_Enter, Qt.Key_Return):
            current_item = self.currentItem()
            if current_item and (current_item.flags() & Qt.ItemIsSelectable):
                self.on_item_clicked(current_item)
            self.hide()
            self.parent().setFocus()

        elif key == Qt.Key_Escape:
            self.hide()
            self.parent().setFocus()

        elif key == Qt.Key_Up:
            current_row = self.currentRow()
            try:
                current_idx = selectable_items.index(current_row)
                next_idx = (current_idx - 1) % len(selectable_items)
            except ValueError:
                next_idx = 0
            self.setCurrentRow(selectable_items[next_idx])

        elif key == Qt.Key_Down:
            current_row = self.currentRow()
            try:
                current_idx = selectable_items.index(current_row)
                next_idx = (current_idx + 1) % len(selectable_items)
            except ValueError:
                next_idx = 0
            self.setCurrentRow(selectable_items[next_idx])

        else:
            # Передаем все остальные клавиши обратно в поле ввода
            self.parent().setFocus()
            self.parent().event(event)

    def wheelEvent(self, event):
        """Обработка прокрутки колесиком мыши"""
        selectable_count = sum(1 for i in range(self.count())
                               if self.item(i).flags() & Qt.ItemIsSelectable)

        # Если элементов 8 или меньше, блокируем прокрутку
        if selectable_count <= 8:
            event.accept()
            return

        # Для больших списков разрешаем стандартную прокрутку
        super().wheelEvent(event)


class TagSearchLineEdit(QLineEdit):
    """Поле ввода с поддержкой тегов и спецсимволов"""

    def __init__(self, parent=None, tag_symbol='@', all_tags=None):
        super().__init__(parent)
        self.tag_symbol = tag_symbol
        self.all_tags = all_tags or []
        self.all_tags.sort(key=natural_sort_key)

        # Создаем виджет списка подсказок
        self.suggestion_list = TagSuggestionList(self)
        self.suggestion_list.tag_selected.connect(self.insert_tag)
        self.suggestion_list.tag_exclude_selected.connect(self.insert_exclude_tag)

        # Подключаем сигналы
        self.textChanged.connect(self.on_text_changed)

    def keyPressEvent(self, event):
        """Обработка клавиш"""
        key = event.key()

        # Если список открыт, обрабатываем стрелку вниз особым образом
        if self.suggestion_list.isVisible() and key == Qt.Key_Down:
            self.suggestion_list.setFocus()
            if self.suggestion_list.count() > 0:
                self.suggestion_list.setCurrentRow(0)
            event.accept()
            return

        # Стандартная обработка для других клавиш
        super().keyPressEvent(event)

        # Обновляем подсказки
        QTimer.singleShot(10, self.update_suggestions)

    def mousePressEvent(self, event):
        """Обработка клика по полю ввода"""
        super().mousePressEvent(event)
        # Если список подсказок открыт, но пользователь кликает по полю ввода
        if self.suggestion_list.isVisible():
            self.setFocus()

    def focusOutEvent(self, event):
        """Закрытие списка при потере фокуса"""
        # Проверяем, не внутри ли списка подсказок был клик
        cursor_pos = QCursor.pos()
        if not self.suggestion_list.geometry().contains(self.suggestion_list.mapFromGlobal(cursor_pos)):
            # Только если клик был не внутри списка подсказок, скрываем его с задержкой
            QTimer.singleShot(300, self._check_and_hide_popup)
        super().focusOutEvent(event)

    def _check_and_hide_popup(self):
        """Проверяет, можно ли скрыть всплывающий список"""
        # Если фокус не на списке подсказок и не на этом виджете, скрываем
        focus_widget = QApplication.focusWidget()
        if focus_widget is not self and focus_widget is not self.suggestion_list:
            self.suggestion_list.hide()

    def on_text_changed(self, text):
        """Обработка изменения текста"""
        self.update_suggestions()

    def update_suggestions(self):
        """Обновление списка подсказок с поддержкой коротких тегов"""
        text = self.text()
        cursor_pos = self.cursorPosition()

        # Разбираем текст для поиска тегов
        tag_info = self.get_tag_at_cursor(cursor_pos)
        if not tag_info:
            self.suggestion_list.hide()
            return

        tag_text, start_pos, end_pos, is_quoted, is_exclude = tag_info

        # Даже для пустых тегов после @ показываем все доступные теги
        # Получаем все уже введенные теги
        entered_tags = [tag.lower() for tag in self.get_all_entered_tags()]

        # Исключаем текущий редактируемый тег
        current_tag_text = tag_text.lower() if tag_text else ""
        if current_tag_text and current_tag_text in entered_tags:
            entered_tags.remove(current_tag_text)

        # Исключаем уже введенные теги
        available_tags = [tag for tag in self.all_tags if tag.lower() not in entered_tags]

        # Если тег пустой или очень короткий, показываем все доступные теги
        if not tag_text or len(tag_text) <= 1:
            matches = available_tags
        else:
            # Иначе фильтруем по началу слов
            if is_quoted:
                # Для тегов в кавычках ищем вхождение подстроки
                matches = [tag for tag in available_tags if tag_text.lower() in tag.lower()]
            else:
                # Для обычных тегов ищем по началу слов
                matches = []
                for tag in available_tags:
                    words = tag.lower().split()
                    if any(word.startswith(tag_text.lower()) for word in words):
                        matches.append(tag)

        if matches:
            # Сортируем результаты
            matches.sort(key=natural_sort_key)

            # Обновляем и показываем список подсказок
            self.suggestion_list.update_suggestions(matches, tag_text, is_exclude)
            self.show_suggestions_popup(start_pos)
        else:
            self.suggestion_list.hide()

    def show_suggestions_popup(self, cursor_offset=None):
        """Показ подсказок с привязкой к полю ввода"""
        if self.suggestion_list.count() == 0:
            self.suggestion_list.hide()
            return

        # Сохраняем текущую позицию курсора
        current_cursor_pos = self.cursorPosition()

        # Получаем геометрию поля ввода
        lineedit_rect = self.geometry()
        lineedit_global_pos = self.mapToGlobal(QPoint(0, 0))

        # Границы окна
        main_window = self.window()
        window_rect = QRect(main_window.mapToGlobal(QPoint(0, 0)),
                            QSize(main_window.width(), main_window.height()))

        # Размеры панели подсказок
        panel_width = self.suggestion_list.width()
        panel_height = self.suggestion_list.height()

        # Расчет позиции курсора для размещения панели
        if cursor_offset is not None:
            saved_pos = self.cursorPosition()
            self.setCursorPosition(cursor_offset)
            rect = self.cursorRect()
            cursor_x = rect.x()
            cursor_y = rect.bottom()
            self.setCursorPosition(saved_pos)
        else:
            rect = self.cursorRect()
            cursor_x = rect.x()
            cursor_y = rect.bottom()

        # Преобразуем координаты курсора в глобальные
        cursor_global_x = lineedit_global_pos.x() + cursor_x
        cursor_global_y = lineedit_global_pos.y() + cursor_y

        # Позиционирование панели под курсором
        panel_x = cursor_global_x - panel_width // 2
        panel_y = cursor_global_y + 5

        # Проверка, не выходит ли панель за пределы поля ввода
        if panel_x < lineedit_global_pos.x():
            # Если выходит за левую границу поля ввода, выравниваем по ней
            panel_x = lineedit_global_pos.x()

        # Проверка правой границы поля ввода
        right_edge = lineedit_global_pos.x() + lineedit_rect.width()
        if panel_x + panel_width > right_edge:
            # Если выходит за правую границу, выравниваем по ней
            panel_x = right_edge - panel_width

        # Проверка границ окна
        if panel_x < window_rect.left():
            panel_x = window_rect.left() + 5

        if panel_x + panel_width > window_rect.right():
            panel_x = window_rect.right() - panel_width - 5

        # Проверка нижней границы
        if panel_y + panel_height > window_rect.bottom():
            # Размещаем над курсором
            panel_y = cursor_global_y - panel_height - 5

        # Финальная проверка
        if panel_y < window_rect.top():
            panel_y = window_rect.top() + 5

        # Установка позиции
        self.suggestion_list.move(panel_x, panel_y)

        # Показываем панель и восстанавливаем позицию курсора
        self.suggestion_list.show()
        QTimer.singleShot(0, lambda: self.setFocus())
        QTimer.singleShot(0, lambda: self.setCursorPosition(current_cursor_pos))

    def get_all_entered_tags(self):
        """Получение всех введенных тегов из поля более точным способом"""
        text = self.text()

        # Находим все теги в тексте с помощью регулярного выражения
        tag_pattern = r'@([^\s",]+)|@"([^"]+)"|tag:([^\s",]+)|tag:"([^"]+)"|-@([^\s",]+)|-@"([^"]+)"|-tag:([^\s",]+)|-tag:"([^"]+)"'
        matches = re.finditer(tag_pattern, text)

        entered_tags = []
        for match in matches:
            # Получаем первую непустую группу
            tag = next((g for g in match.groups() if g is not None), "")
            if tag:
                entered_tags.append(tag)

        return entered_tags

    def get_tag_at_cursor(self, cursor_pos):
        """Определение тега в текущей позиции курсора с поддержкой коротких тегов"""
        text = self.text()
        if not text or cursor_pos <= 0:
            return None

        # Поиск начала тега перед курсором
        is_exclude = False
        is_quoted = False
        tag_start = -1

        # Ищем ближайший символ тега или формат тега
        i = cursor_pos - 1
        while i >= 0:
            # Проверяем наличие тега с исключением: -@
            if i >= 1 and text[i] == self.tag_symbol and text[i - 1] == '-':
                tag_start = i - 1
                is_exclude = True
                break

            # Проверяем наличие обычного тега: @
            elif text[i] == self.tag_symbol:
                tag_start = i
                break

            # Проверяем наличие формата tag:
            elif i >= 3 and text[i - 3:i + 1] == "tag:":
                tag_start = i - 3
                break

            # Проверяем наличие формата -tag:
            elif i >= 4 and text[i - 4:i + 1] == "-tag:":
                tag_start = i - 4
                is_exclude = True
                break

            # Если нашли пробел или другой специальный символ, значит не в теге
            elif text[i] == ' ':
                break

            i -= 1

        # Если не нашли начало тега, возвращаем None
        if tag_start == -1:
            return None

        # Определяем формат тега
        if text[tag_start] == '-' and tag_start + 1 < len(text) and text[tag_start + 1] == self.tag_symbol:
            # Исключающий формат: -@
            tag_format = "-@"
            prefix_len = 2
        elif text[tag_start] == self.tag_symbol:
            # Обычный формат: @
            tag_format = "@"
            prefix_len = 1
        elif tag_start + 3 < len(text) and text[tag_start:tag_start + 4] == "tag:":
            # Формат tag:
            tag_format = "tag:"
            prefix_len = 4
        elif tag_start + 4 < len(text) and text[tag_start:tag_start + 5] == "-tag:":
            # Формат -tag:
            tag_format = "-tag:"
            prefix_len = 5
            is_exclude = True
        else:
            # Неизвестный формат
            return None

        # Проверяем наличие кавычек
        if tag_start + prefix_len < len(text) and text[tag_start + prefix_len] == '"':
            is_quoted = True
            prefix_len += 1  # Учитываем кавычку

        # Определяем конец тега
        if is_quoted:
            # Ищем закрывающую кавычку
            tag_end = cursor_pos
            for j in range(cursor_pos, len(text)):
                if text[j] == '"':
                    tag_end = j + 1  # Включаем кавычку
                    break
        else:
            # Ищем конец слова или другой тег
            tag_end = len(text)
            for j in range(cursor_pos, len(text)):
                if text[j] in ' @':
                    tag_end = j
                    break

        # Извлекаем текст тега
        if is_quoted:
            # Без закрывающей кавычки, если курсор внутри
            if tag_end > cursor_pos and text[tag_end - 1] == '"':
                tag_text = text[tag_start + prefix_len:tag_end - 1]
            else:
                tag_text = text[tag_start + prefix_len:tag_end]
        else:
            tag_text = text[tag_start + prefix_len:tag_end]

        return tag_text, tag_start, tag_end, is_quoted, is_exclude

    def insert_tag(self, tag):
        """Вставка выбранного тега с сохранением формата"""
        text = self.text()
        cursor_pos = self.cursorPosition()

        # Получаем текущий тег под курсором
        tag_info = self.get_tag_at_cursor(cursor_pos)
        if not tag_info:
            return

        tag_text, start_pos, end_pos, is_quoted, is_exclude = tag_info

        # Определяем формат тега
        tag_format = ""

        # Сохраняем формат тега (tag: или @)
        if start_pos + 3 < len(text) and text[start_pos:start_pos + 4] == "tag:":
            tag_format = "tag:"
        elif start_pos + 4 < len(text) and text[start_pos:start_pos + 5] == "-tag:":
            tag_format = "-tag:"
        elif text[start_pos] == '-' and start_pos + 1 < len(text) and text[start_pos + 1] == self.tag_symbol:
            tag_format = f"-{self.tag_symbol}"
        elif text[start_pos] == self.tag_symbol:
            tag_format = self.tag_symbol
        else:
            # Дефолтный формат, если не определился
            tag_format = self.tag_symbol if not is_exclude else f"-{self.tag_symbol}"

        # Формируем новый текст с заменой тега
        if is_quoted:
            # Для тегов в кавычках заменяем только текст тега
            if start_pos + 1 < len(text) and text[start_pos + 1] == '"':
                replace_text = f'{text[:start_pos + 2]}{tag}{text[end_pos - 1:]}'
            else:
                replace_text = f'{text[:start_pos]}{tag_format}"{tag}"{text[end_pos:]}'
        else:
            # Проверяем, содержит ли новый тег пробелы
            if ' ' in tag:
                # Если в теге есть пробелы, используем кавычки
                replace_text = f'{text[:start_pos]}{tag_format}"{tag}"{text[end_pos:]}'
            else:
                # Без пробелов, простая замена
                replace_text = f'{text[:start_pos]}{tag_format}{tag}{text[end_pos:]}'

        # Применяем изменения
        self.setText(replace_text)

        # Позиционируем курсор после тега
        if ' ' in tag and ('"' not in text[start_pos:end_pos]):
            self.setCursorPosition(start_pos + len(tag_format) + len(tag) + 2)
        else:
            self.setCursorPosition(start_pos + len(tag_format) + len(tag))

    def insert_exclude_tag(self, tag):
        """Вставка исключающего тега"""
        text = self.text()
        cursor_pos = self.cursorPosition()

        # Получаем текущий тег под курсором
        tag_info = self.get_tag_at_cursor(cursor_pos)
        if not tag_info:
            return

        tag_text, start_pos, end_pos, is_quoted, is_exclude = tag_info

        # Определяем формат тега
        if not is_exclude:
            # Добавляем минус, сохраняя формат
            if start_pos + 3 < len(text) and text[start_pos:start_pos + 4] == "tag:":
                tag_format = "-tag:"
            elif text[start_pos] == self.tag_symbol:
                tag_format = f"-{self.tag_symbol}"
            else:
                tag_format = f"-{self.tag_symbol}"
        else:
            # Тег уже исключающий, просто сохраняем формат
            if start_pos + 4 < len(text) and text[start_pos:start_pos + 5] == "-tag:":
                tag_format = "-tag:"
            elif start_pos + 1 < len(text) and text[start_pos] == '-' and text[start_pos + 1] == self.tag_symbol:
                tag_format = f"-{self.tag_symbol}"
            else:
                tag_format = f"-{self.tag_symbol}"

        # Формируем новый текст
        if is_quoted:
            # Для тегов в кавычках
            if start_pos + 1 < len(text) and text[start_pos + 1] == '"':
                replace_text = f'{text[:start_pos + 2]}{tag}{text[end_pos - 1:]}'
            else:
                replace_text = f'{text[:start_pos]}{tag_format}"{tag}"{text[end_pos:]}'
        else:
            if ' ' in tag:
                replace_text = f'{text[:start_pos]}{tag_format}"{tag}"{text[end_pos:]}'
            else:
                replace_text = f'{text[:start_pos]}{tag_format}{tag}{text[end_pos:]}'

        # Применяем изменения
        self.setText(replace_text)

        # Позиционируем курсор
        if ' ' in tag and ('"' not in text[start_pos:end_pos]):
            self.setCursorPosition(start_pos + len(tag_format) + len(tag) + 2)
        else:
            self.setCursorPosition(start_pos + len(tag_format) + len(tag))

class MainWindow(QMainWindow):
    def __init__(self, paths, project_manager=None, window_manager=None):
        super().__init__()
        self.paths = paths
        self.project_manager = project_manager
        self.window_manager = window_manager

        self.setWindowTitle("MangaLocalizer")
        self.scale_factor = 1.0
        self.current_page = 1
        self.per_page = 1

        self.all_tiles_data = []
        self.filtered_tiles_data = []
        self.overlay = None
        self.new_project_window = None

        # Загрузка тегов для подсказок
        self.all_tags = self._load_tags_from_file()

        self.initUI()
        self.loadProjects()
        self.filtered_tiles_data = self.all_tiles_data[:]
        QTimer.singleShot(0, self.updateTiles)

        # Регистрация в менеджере окон
        if self.window_manager:
            self.window_manager.register_window(self, "main_window")

    # ==================== ИНИЦИАЛИЗАЦИЯ ИНТЕРФЕЙСА ====================

    def initUI(self):
        """Инициализация основного интерфейса"""
        # Градиентный фон
        self.gradient_widget = GradientBackgroundWidget()
        self.setCentralWidget(self.gradient_widget)

        # Основной макет
        self.main_layout = QVBoxLayout(self.gradient_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.createTopBar()
        self.createCenterContent()
        self.createBottomBar()

    def createTopBar(self):
        """Верхняя панель с заголовком, поиском и сортировкой"""
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 20, 20, 0)
        top_bar.setSpacing(10)

        # Заголовок
        title_label = QLabel("MangaLocalizer")
        title_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        title_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        # Растягивающийся элемент
        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)

        # Макет для поиска и сортировки
        search_sort_layout = QVBoxLayout()
        search_sort_layout.setSpacing(10)

        # Поле поиска (обновлено с поддержкой тегов)
        self.search_field = TagSearchLineEdit(self, tag_symbol='@', all_tags=self.all_tags)
        self.search_field.setPlaceholderText("Поиск... (используйте @ для тегов)")
        self.search_field.setMaxLength(300)
        font_metrics = self.search_field.fontMetrics()
        char_width = font_metrics.horizontalAdvance("W")
        visible_width = char_width * 25 + 20
        self.search_field.setFixedWidth(visible_width)
        self.search_field.setStyleSheet("""
                QLineEdit {
                    background: #3E3E5F; 
                    border-radius: 15px; 
                    padding-left: 10px;
                    padding-right: 10px;
                    color: white;
                    font-size: 14px;
                }
                QLineEdit:hover {
                    background: #4E4E6F;
                }
            """)
        self.search_field.setFixedHeight(30)
        self.search_field.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.search_field.textChanged.connect(self.applySortAndFilter)

        # Выпадающий список сортировки
        self.sort_combo = QComboBox()
        self.sort_combo.setStyleSheet("""
                QComboBox {
                    background: rgba(255,255,255,0.15);
                    border-radius: 8px;
                    color: white;
                    padding: 4px 8px;
                    font-size: 14px;
                }
                QComboBox:hover {
                    background: #8E2EBF;
                }
            """)
        self.sort_combo.addItem("Название (A-Z)", "title_asc")
        self.sort_combo.addItem("Название (Z-A)", "title_desc")
        self.sort_combo.addItem("Дата добавления (Новые)", "date_desc")
        self.sort_combo.addItem("Дата добавления (Старые)", "date_asc")
        self.sort_combo.setCurrentIndex(0)
        self.sort_combo.setFixedHeight(30)
        self.sort_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.sort_combo.currentIndexChanged.connect(self.applySortAndFilter)

        # Сборка верхней панели
        search_sort_layout.addWidget(self.search_field, alignment=Qt.AlignRight)
        search_sort_layout.addWidget(self.sort_combo, alignment=Qt.AlignRight)
        top_bar.addWidget(title_label, 0, Qt.AlignTop)
        top_bar.addItem(spacer)
        top_bar.addLayout(search_sort_layout)
        self.main_layout.addLayout(top_bar)

    def createCenterContent(self):
        """Создание центральной части с плитками проектов"""
        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(20, 0, 20, 0)
        center_layout.setSpacing(20)

        # Область прокрутки с плитками
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.content_widget = QWidget()
        self.grid_layout = QGridLayout(self.content_widget)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setContentsMargins(0, 20, 0, 20)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        self.scroll_area.setWidget(self.content_widget)
        center_layout.addWidget(self.scroll_area, 1)
        self.main_layout.addLayout(center_layout, 1)

    def createBottomBar(self):
        """Нижняя панель с кнопкой добавления и пагинацией"""
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(20, 20, 20, 20)
        bottom_bar.setSpacing(10)

        # Левый растягивающийся элемент
        left_stretch = QWidget()
        left_stretch.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        bottom_bar.addWidget(left_stretch, 1)

        # Кнопка добавления проекта
        add_btn = QPushButton("Добавить произведение")
        add_btn.setStyleSheet("""
                QPushButton {
                    background: #7E1E9F;
                    color: white;
                    font-size:14px;
                    border-radius: 15px;
                    padding: 8px 15px;
                }
                QPushButton:hover {
                    background: #8E2EBF;
                }
            """)
        add_btn.clicked.connect(self.openNewProjectWindow)
        bottom_bar.addWidget(add_btn, 0, Qt.AlignCenter)

        # Правый растягивающийся элемент
        right_stretch = QWidget()
        right_stretch.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        bottom_bar.addWidget(right_stretch, 1)

        # Элементы пагинации
        prev_btn = QPushButton("←")
        prev_btn.setStyleSheet("""
                QPushButton {
                    background: #3E3E5F;
                    color: white;
                    font-size: 14px;
                    border-radius: 10px;
                    padding: 5px;
                    min-width: 30px;
                }
                QPushButton:hover {
                    background: #4E4E6F;
                }
            """)
        prev_btn.clicked.connect(self.prevPage)
        bottom_bar.addWidget(prev_btn, 0, Qt.AlignRight)

        self.page_input = QSpinBox()
        self.page_input.setMinimum(1)
        self.page_input.setStyleSheet("""
                QSpinBox {
                    background: #3E3E5F;
                    color: white;
                    border-radius: 10px;
                    padding: 3px;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    width: 0;
                    height: 0;
                    border: none;
                }
            """)
        self.page_input.valueChanged.connect(self.goToPage)
        bottom_bar.addWidget(self.page_input, 0, Qt.AlignCenter)

        self.total_label = QLabel("/ 1")
        self.total_label.setStyleSheet("color: white; font-size: 14px;")
        bottom_bar.addWidget(self.total_label, 0, Qt.AlignCenter)

        next_btn = QPushButton("→")
        next_btn.setStyleSheet("""
                QPushButton {
                    background: #3E3E5F;
                    color: white;
                    font-size: 14px;
                    border-radius: 10px;
                    padding: 5px;
                    min-width: 30px;
                }
                QPushButton:hover {
                    background: #4E4E6F;
                }
            """)
        next_btn.clicked.connect(self.nextPage)
        bottom_bar.addWidget(next_btn, 0, Qt.AlignLeft)

        self.main_layout.addLayout(bottom_bar)

    # ==================== ЗАГРУЗКА И УПРАВЛЕНИЕ ПРОЕКТАМИ ====================

    def _load_tags_from_file(self):
        """Загрузка тегов из файла"""
        tags_file = os.path.join(os.path.dirname(self.paths.get('projects', '')), "tags.txt")

        if not os.path.exists(tags_file):
            print(f"[main_window] INFO: 📄 Файл с тегами отсутствует: {tags_file}")
            return []

        try:
            with open(tags_file, "r", encoding="utf-8") as f:
                tags = [line.strip() for line in f if line.strip()]

            print(f"[main_window] INFO: 🏷️ Загружено {len(tags)} тегов из {tags_file}")
            # Используем естественную сортировку тегов
            return sorted(tags, key=natural_sort_key)
        except Exception as e:
            print(f"[main_window] ERROR: ❌ Ошибка загрузки тегов: {e}")
            return []

    def loadProjects(self):
        """Загрузка проектов из директории"""
        projects_path = self.paths['projects']
        if not os.path.isdir(projects_path):
            print(f"[main_window] WARN: 📁 Папка проектов не найдена: {projects_path}")
            return

        self.all_tiles_data.clear()
        no_cover_path = os.path.join(self.paths['icons'], "no_cover.png")
        supported_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif']

        for folder_name in os.listdir(projects_path):
            folder_path = os.path.join(projects_path, folder_name)
            if not os.path.isdir(folder_path):
                continue

            metadata_path = os.path.join(folder_path, "metadata.json")
            if not os.path.isfile(metadata_path):
                continue

            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"[main_window] ERROR: ❌ Ошибка чтения JSON: {metadata_path}, {e}")
                continue

            title = data.get("name", folder_name)
            folder_name = data.get("folder_name", folder_name)
            tags = data.get("tags", [])

            # Загрузка даты добавления
            date_added_str = data.get("created_at")
            if date_added_str:
                try:
                    date_added = datetime.fromisoformat(date_added_str)
                except ValueError:
                    date_added = datetime.now()
            else:
                try:
                    timestamp = os.path.getctime(folder_path)
                    date_added = datetime.fromtimestamp(timestamp)
                except Exception:
                    date_added = datetime.now()

            # Поиск файла обложки
            cover_path = None
            for ext in supported_extensions:
                potential_path = os.path.join(folder_path, f"cover{ext}")
                if os.path.isfile(potential_path):
                    cover_path = potential_path
                    break

            if cover_path:
                pixmap = QPixmap(cover_path)
                if pixmap.isNull():
                    cover_path = None

            if not cover_path:
                if os.path.isfile(no_cover_path):
                    pixmap = QPixmap(no_cover_path)
                    if pixmap.isNull():
                        pixmap = QPixmap(149, 213)
                        pixmap.fill(QColor(60, 60, 90))
                else:
                    pixmap = QPixmap(149, 213)
                    pixmap.fill(QColor(60, 60, 90))

            # Сохраняем также теги проекта для фильтрации
            self.all_tiles_data.append((pixmap, title, folder_name, date_added, tags))

    # ==================== ФИЛЬТРАЦИЯ И СОРТИРОВКА ====================

    def parse_search_query(self, query):
        """Разбор поискового запроса с поддержкой тегов"""
        # Ищем теги в запросе
        include_tags = []
        exclude_tags = []
        text_terms = []

        # Регулярное выражение для поиска разных форматов тегов
        tag_pattern = r'(?:-)?@([^\s"]+)|(?:-)?@"([^"]+)"|(?:-)?tag:([^\s"]+)|(?:-)?tag:"([^"]+)"'
        matches = re.finditer(tag_pattern, query)

        for match in matches:
            # Определяем, что именно совпало
            full_match = match.group(0)

            # Игнорируем неполные теги (только символы без значения)
            if full_match in ('@', '-@', 'tag:', '-tag:'):
                continue

            if full_match.startswith('-'):
                # Это исключающий тег
                tag = next((g for g in match.groups() if g is not None), "")
                exclude_tags.append(tag.lower())
            else:
                # Это включающий тег
                tag = next((g for g in match.groups() if g is not None), "")
                include_tags.append(tag.lower())

        # Удаляем теги из запроса для получения обычного текста
        clean_query = re.sub(tag_pattern, '', query).strip()

        # Разбиваем текст на отдельные слова для поиска
        if clean_query:
            text_terms = [term.lower() for term in clean_query.split() if term.strip()]

        return {
            'include_tags': include_tags,
            'exclude_tags': exclude_tags,
            'text_terms': text_terms
        }

    def applySortAndFilter(self, reset_page=True):
        """Применение фильтрации и сортировки к проектам"""
        # Получаем и разбираем поисковый запрос
        query = self.search_field.text().strip()
        search_params = self.parse_search_query(query)

        # Фильтрация по параметрам поиска
        self.filtered_tiles_data = self.all_tiles_data.copy()

        # Применяем фильтры только если есть условия поиска
        if search_params['include_tags'] or search_params['exclude_tags'] or search_params['text_terms']:
            filtered = []

            for (pix, title, folder_name, date_added, tags) in self.filtered_tiles_data:
                # Преобразуем теги к нижнему регистру для регистронезависимого сравнения
                project_tags = [tag.lower() for tag in tags]

                # Проверка на соответствие включающим тегам
                tags_match = True
                for tag in search_params['include_tags']:
                    if tag not in project_tags:
                        tags_match = False
                        break

                # Проверка на отсутствие исключающих тегов
                for tag in search_params['exclude_tags']:
                    if tag in project_tags:
                        tags_match = False
                        break

                # Проверка на соответствие текстовому поиску
                text_match = True
                for term in search_params['text_terms']:
                    if term not in title.lower():
                        text_match = False
                        break

                # Добавляем проект, если он соответствует всем условиям
                if (not search_params['include_tags'] and not search_params['text_terms']) or (
                        tags_match and text_match):
                    filtered.append((pix, title, folder_name, date_added, tags))

            self.filtered_tiles_data = filtered

        # Сортировка по выбранному критерию
        sort_option = self.sort_combo.currentData()
        if sort_option == "title_asc":
            self.filtered_tiles_data.sort(key=lambda x: natural_sort_key(x[1]))
        elif sort_option == "title_desc":
            self.filtered_tiles_data.sort(key=lambda x: natural_sort_key(x[1]), reverse=True)
        elif sort_option == "date_desc":
            self.filtered_tiles_data.sort(key=lambda x: x[3], reverse=True)
        elif sort_option == "date_asc":
            self.filtered_tiles_data.sort(key=lambda x: x[3])

        # Сбрасываем страницу на первую только если требуется
        if reset_page:
            self.current_page = 1

        # Обновляем отображение
        self.updateTiles()

    def focusSearch(self):
        """Фокусировка на поле поиска"""
        self.search_field.setFocus()

    # ==================== ОТОБРАЖЕНИЕ И ВЗАИМОДЕЙСТВИЕ С ПЛИТКАМИ ====================

    def updateTiles(self):
        """Обновление плиток проектов с учетом фильтров и пагинации"""
        layout = self.grid_layout
        layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        # Очистка существующих плиток
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    layout.removeWidget(w)
                    w.setParent(None)

        total_tiles = len(self.filtered_tiles_data)

        # Расчеты размеров плиток
        base_tile_width = 149
        base_tile_height = 213
        text_height = 45

        sample_w = int(base_tile_width * self.scale_factor)
        sample_h = int(base_tile_height * self.scale_factor + text_height)

        spacing = layout.spacing()
        margins_h = layout.contentsMargins().left() + layout.contentsMargins().right()

        # Более надежный способ определения доступной ширины
        available_width = self.width() - 40  # Используем ширину окна как основу
        if self.scroll_area.isVisible() and self.scroll_area.width() > 100:
            available_width = self.scroll_area.width() - 40

        # Расчет количества элементов в строке
        count_in_row = max(1, (available_width - margins_h + spacing) // (sample_w + spacing))

        # Определение количества строк
        view_height = self.scroll_area.height()
        margins_v = layout.contentsMargins().top() + layout.contentsMargins().bottom()
        count_in_col = max(1, (view_height - margins_v + spacing) // (sample_h + spacing))

        # Устанавливаем минимальное количество плиток на странице
        self.per_page = max(count_in_row * count_in_col, 12)

        total_pages = math.ceil(total_tiles / self.per_page) if self.per_page else 1
        total_pages = max(total_pages, 1)
        self.current_page = min(self.current_page, total_pages)

        # Обработка пустых проектов или отсутствия результатов поиска
        if not self.all_tiles_data or total_tiles == 0:
            self.total_label.setText("/ 1")
            self.page_input.setMaximum(1)
            self.page_input.setValue(1)

            message = "Нет произведений" if not self.all_tiles_data else "Ничего не найдено"
            no_results_label = QLabel(message)
            no_results_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
            no_results_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(no_results_label, 0, 0, 1, count_in_row)
            return

        # Расчет индексов для текущей страницы
        start_index = (self.current_page - 1) * self.per_page
        end_index = min(start_index + self.per_page, total_tiles)
        current_data = self.filtered_tiles_data[start_index:end_index]

        # Добавление плиток в сетку
        row, col = 0, 0
        for (pix, title, folder_name, date_added, tags) in current_data:
            tile = TileWidget(pix, title, folder_name, date_added)
            tile.updateTileSize(self.scale_factor)
            tile.clicked.connect(self.onTileClicked)
            tile.rightClicked.connect(self.onTileRightClicked)
            layout.addWidget(tile, row, col)
            col += 1
            if col >= count_in_row:
                col = 0
                row += 1

        # Обновление меток пагинации
        self.total_label.setText(f"/ {total_pages}")
        self.page_input.setMaximum(total_pages)
        self.page_input.setValue(self.current_page)
# ==================== ОБРАБОТКА СОБЫТИЙ И НАВИГАЦИЯ ====================

    def keyPressEvent(self, event):
        """Обработка горячих клавиш"""
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_F:
            self.focusSearch()
            event.accept()
        else:
            super().keyPressEvent(event)

    def moveEvent(self, event):
        """Обработка перемещения главного окна"""
        super().moveEvent(event)
        if hasattr(self, 'new_project_window') and self.new_project_window and self.new_project_window.isVisible():
            QTimer.singleShot(0, lambda: self._centerChildWindow())

    def resizeEvent(self, event):
        """Обработка изменения размера окна"""
        super().resizeEvent(event)

        # Проверяем, находимся ли мы в режиме просмотра проекта
        if hasattr(self, 'project_window') and self.project_window:
            # В режиме просмотра проекта обновляем только оверлей
            if self.overlay:
                self.overlay.setGeometry(self.rect())

            if self.new_project_window and self.new_project_window.isVisible():
                self._centerChildWindow()
            return

        # Если мы здесь, значит мы в режиме основного окна
        self.scale_factor = self.calculateScaleFactor()
        self.updateTiles()

        # Обновляем размеры оверлея и позицию дочернего окна
        if self.overlay:
            self.overlay.setGeometry(self.rect())

        if self.new_project_window and self.new_project_window.isVisible():
            self._centerChildWindow()

    def wheelEvent(self, event: QWheelEvent):
        """Масштабирование плиток колесиком мыши с Ctrl"""
        if event.modifiers() & Qt.ControlModifier:
            num_degrees = event.angleDelta().y() / 8
            num_steps = num_degrees / 15
            new_scale = self.scale_factor + (0.1 * num_steps)
            new_scale = max(0.5, min(2.0, new_scale))
            self.scale_factor = new_scale
            self.updateTiles()
            event.accept()
        else:
            super().wheelEvent(event)

    def calculateScaleFactor(self, base_size=(1280, 720)):
        """Расчет коэффициента масштабирования"""
        base_w, base_h = base_size
        sw = self.width() / base_w
        sh = self.height() / base_h
        return min(sw, sh)

    # ==================== ПАГИНАЦИЯ ====================

    def prevPage(self):
        """Переход на предыдущую страницу"""
        if self.current_page > 1:
            self.current_page -= 1
            self.updateTiles()

    def nextPage(self):
        """Переход на следующую страницу"""
        total_tiles = len(self.filtered_tiles_data)
        total_pages = math.ceil(total_tiles / self.per_page) if self.per_page else 1
        if self.current_page < total_pages:
            self.current_page += 1
            self.updateTiles()

    def goToPage(self, page_num):
        """Переход на указанную страницу"""
        total_tiles = len(self.filtered_tiles_data)
        total_pages = math.ceil(total_tiles / self.per_page) if self.per_page else 1
        page_num = max(1, min(page_num, total_pages))
        self.current_page = page_num
        self.updateTiles()

    # ==================== ВЗАИМОДЕЙСТВИЕ С ПЛИТКАМИ ====================

    def openProject(self, folder_name):
        """Открывает проект в текущем окне"""
        project_path = os.path.join(self.paths['projects'], folder_name)
        if not os.path.isdir(project_path):
            print(f"[main_window] ERROR: ❌ Проект не найден: {project_path}")
            return

        # Сохраняем состояние UI и данных
        self._saved_search_text = self.search_field.text()
        self._saved_sort_index = self.sort_combo.currentIndex()
        self._saved_all_tiles_data = self.all_tiles_data.copy()
        self._saved_filtered_tiles_data = self.filtered_tiles_data.copy()
        self._saved_current_page = self.current_page
        self._saved_scale_factor = self.scale_factor
        self._current_project_folder = folder_name

        # Скрываем весь основной контейнер
        self.gradient_widget.hide()

        # Создаем новый градиентный фон для проекта
        self.project_gradient = GradientBackgroundWidget(self)
        self.setCentralWidget(self.project_gradient)

        # Создаем окно проекта на новом фоне
        from ui.windows.m4_0_project_view import ProjectDetailWindow
        self.project_window = ProjectDetailWindow(
            project_path=project_path,
            paths=self.paths,
            parent=self.project_gradient
        )

        # Размещаем окно проекта на весь контейнер
        layout = QVBoxLayout(self.project_gradient)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.project_window)

        # Подключаем сигнал возврата
        self.project_window.back_requested.connect(self.closeProject)

        # Обновляем заголовок
        self.setWindowTitle(f"MangaLocalizer - {folder_name}")

    def openProject(self, folder_name):
        """Открывает проект в текущем окне"""
        project_path = os.path.join(self.paths['projects'], folder_name)
        if not os.path.isdir(project_path):
            print(f"[main_window] ERROR: ❌ Проект не найден: {project_path}")
            return

        # Сохраняем состояние UI и данных
        self._saved_search_text = self.search_field.text()
        self._saved_sort_index = self.sort_combo.currentIndex()
        self._saved_all_tiles_data = self.all_tiles_data.copy()
        self._saved_filtered_tiles_data = self.filtered_tiles_data.copy()
        self._saved_current_page = self.current_page
        self._saved_scale_factor = self.scale_factor
        self._current_project_folder = folder_name

        # Скрываем весь основной контейнер
        self.gradient_widget.hide()

        # Создаем новый градиентный фон для проекта
        self.project_gradient = GradientBackgroundWidget(self)
        self.setCentralWidget(self.project_gradient)

        # Создаем окно проекта на новом фоне
        from ui.windows.m4_0_project_view import ProjectDetailWindow
        self.project_window = ProjectDetailWindow(
            project_path=project_path,
            paths=self.paths,
            parent=self.project_gradient
        )

        # Размещаем окно проекта на весь контейнер
        layout = QVBoxLayout(self.project_gradient)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.project_window)

        # Подключаем сигнал возврата
        self.project_window.back_requested.connect(self.closeProject)

        # Обновляем заголовок
        self.setWindowTitle(f"MangaLocalizer - {folder_name}")

    def closeProject(self):
        """Закрывает проект и возвращает к основному виду"""
        # Удаляем окно проекта и его контейнер
        if hasattr(self, 'project_window'):
            self.project_window.deleteLater()

        if hasattr(self, 'project_gradient'):
            self.project_gradient.setParent(None)
            self.project_gradient.deleteLater()

        # Создаем новый градиентный виджет
        self.gradient_widget = GradientBackgroundWidget()
        self.setCentralWidget(self.gradient_widget)

        # Воссоздаем интерфейс
        self.main_layout = QVBoxLayout(self.gradient_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.createTopBar()
        self.createCenterContent()
        self.createBottomBar()

        # Восстанавливаем состояние
        if hasattr(self, '_saved_search_text'):
            self.search_field.setText(self._saved_search_text)

        if hasattr(self, '_saved_sort_index'):
            self.sort_combo.blockSignals(True)  # Блокируем сигналы при восстановлении
            self.sort_combo.setCurrentIndex(self._saved_sort_index)
            self.sort_combo.blockSignals(False)

        if hasattr(self, '_saved_all_tiles_data'):
            self.all_tiles_data = self._saved_all_tiles_data

        if hasattr(self, '_saved_filtered_tiles_data'):
            self.filtered_tiles_data = self._saved_filtered_tiles_data

        if hasattr(self, '_saved_current_page'):
            self.current_page = self._saved_current_page

        if hasattr(self, '_saved_scale_factor'):
            self.scale_factor = self._saved_scale_factor

        # Проверяем, нужно ли обновить проект в списке (если данные изменились)
        if hasattr(self, '_current_project_folder'):
            folder_name = self._current_project_folder
            project_path = os.path.join(self.paths['projects'], folder_name)

            # Обновляем данные проекта, если они изменились
            metadata_path = os.path.join(project_path, "metadata.json")
            if os.path.isfile(metadata_path):
                try:
                    with open(metadata_path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    # Ищем проект в списке
                    for i, (pixmap, title, name, date, tags) in enumerate(self.all_tiles_data):
                        if name == folder_name:
                            # Обновляем данные
                            new_title = data.get("name", folder_name)
                            new_tags = data.get("tags", [])
                            self.all_tiles_data[i] = (pixmap, new_title, folder_name, date, new_tags)
                            break
                except Exception as e:
                    print(f"[main_window] ERROR: ❌ Ошибка обновления проекта {folder_name}: {e}")

        # Обновляем заголовок
        self.setWindowTitle("MangaLocalizer")

        # Вызываем applySortAndFilter без сброса страницы
        QTimer.singleShot(100, lambda: self.applySortAndFilter(reset_page=False))

    # Update onTileClicked method
    @Slot(str)
    def onTileClicked(self, folder_name):
        """Обработка клика по плитке проекта"""
        print(f"[main_window] INFO: 🖱️ Выбран проект: {folder_name}")
        self.openProject(folder_name)

    @Slot(str, QPoint)
    def onTileRightClicked(self, folder_name, global_pos):
        """Обработка правого клика по плитке проекта"""
        self.showContextMenu(folder_name, global_pos)

    def showContextMenu(self, folder_name, global_pos):
        """Показ контекстного меню при правом клике"""
        menu = QMenu(self)
        delete_action = menu.addAction("Удалить проект")
        edit_action = menu.addAction("Изменить проект")

        delete_action.triggered.connect(lambda: self.deleteProject(folder_name))
        edit_action.triggered.connect(lambda: self.editProject(folder_name))

        menu.exec_(global_pos)

    def deleteProject(self, folder_name):
        """
        Удаление проекта с обновлением списка тегов.
        Удаляет неиспользуемые теги из списка тегов.
        """
        # Атрибут для хранения состояния "не показывать подтверждение"
        if not hasattr(self, '_skip_delete_confirmation'):
            self._skip_delete_confirmation = False

        # Подтверждение удаления, если оно не отключено
        if not self._skip_delete_confirmation:
            from PySide6.QtWidgets import QMessageBox, QCheckBox

            # Создаем диалог
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Подтверждение удаления")
            msg_box.setText(f"Вы уверены, что хотите удалить проект '{folder_name}'?")
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.No)

            # Добавляем чекбокс "не показывать больше"
            checkbox = QCheckBox("Не показывать в этом сеансе")
            checkbox.setStyleSheet("color: white;")
            msg_box.setCheckBox(checkbox)

            # Показываем диалог
            reply = msg_box.exec_()

            # Сохраняем состояние чекбокса
            if checkbox.isChecked():
                self._skip_delete_confirmation = True

            if reply != QMessageBox.Yes:
                return

        # Получаем полный путь к папке проекта
        project_path = os.path.join(self.paths['projects'], folder_name)

        # Сохраняем копию текущих данных для анализа тегов
        previous_all_tiles_data = self.all_tiles_data.copy()

        # Удаляем папку проекта
        try:
            import shutil
            shutil.rmtree(project_path)
            print(f"[main_window] INFO: 🗑️ Удален проект: {folder_name}")
        except Exception as e:
            print(f"[main_window] ERROR: ❌ Ошибка при удалении проекта {folder_name}: {e}")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                "Ошибка удаления",
                f"Не удалось удалить проект '{folder_name}'.\n\nОшибка: {str(e)}",
                QMessageBox.Ok
            )
            return

        # Перезагружаем список проектов
        self.loadProjects()

        # Собираем все теги до и после удаления
        old_tags = set()
        for _, _, _, _, tags in previous_all_tiles_data:
            for tag in tags:
                old_tags.add(tag)

        new_tags = set()
        for _, _, _, _, tags in self.all_tiles_data:
            for tag in tags:
                new_tags.add(tag)

        # Находим теги, которые перестали использоваться
        unused_tags = old_tags - new_tags

        # Обновляем файл тегов, если есть неиспользуемые теги
        if unused_tags:
            tags_file = os.path.join(os.path.dirname(self.paths.get('projects', '')), "tags.txt")

            if os.path.exists(tags_file):
                try:
                    # Читаем существующие теги
                    with open(tags_file, "r", encoding="utf-8") as f:
                        all_tags = [line.strip() for line in f if line.strip()]

                    # Удаляем неиспользуемые теги
                    updated_tags = [tag for tag in all_tags if tag not in unused_tags]

                    # Сохраняем обновленный список тегов
                    with open(tags_file, "w", encoding="utf-8") as f:
                        for tag in updated_tags:
                            f.write(f"{tag}\n")

                    print(f"[main_window] INFO: 🏷️ Удалены неиспользуемые теги: {', '.join(unused_tags)}")

                    # Обновляем список тегов в приложении
                    self.all_tags = self._load_tags_from_file()
                    self.search_field.all_tags = self.all_tags
                except Exception as e:
                    print(f"[main_window] ERROR: ❌ Ошибка при обновлении файла тегов: {e}")

        # Обновляем интерфейс
        self.applySortAndFilter()

    def editProject(self, folder_name):
        """Редактирование проекта"""
        pass  # Будет реализовано для редактирования в главном окне

    # ==================== УПРАВЛЕНИЕ ДИАЛОГОМ СОЗДАНИЯ ПРОЕКТА ====================

    def openNewProjectWindow(self):
        """Открывает окно создания проекта с эффектом размытия фона"""
        # Создаём эффект размытия
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(15)
        self.gradient_widget.setGraphicsEffect(self.blur_effect)

        self.gradient_widget.update()
        QApplication.processEvents()

        # Создаём оверлей
        self.overlay = QWidget(self)
        self.overlay.setGeometry(self.rect())
        self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 30);")
        self.overlay.setCursor(Qt.ForbiddenCursor)
        self.overlay.setObjectName("modal_overlay")
        self.overlay.show()

        # Создаём дочернее окно
        from ui.windows.m2_0_create_project import open_new_project_window
        projects_path = self.paths.get('projects')
        self.new_project_window = open_new_project_window(self, projects_path)
        self.new_project_window.setParent(self)
        self.new_project_window.setWindowFlags(Qt.Widget | Qt.FramelessWindowHint)

        # Центрирование окна
        self._centerChildWindow()
        self.new_project_window.show()
        self.new_project_window.finished.connect(self._onDialogFinished)

    def _centerChildWindow(self):
        """Центрирование дочернего окна относительно родителя"""
        if not self.new_project_window:
            return

        parent_width = self.width()
        parent_height = self.height()
        dialog_width = self.new_project_window.width()
        dialog_height = self.new_project_window.height()

        x = (parent_width - dialog_width) // 2
        y = (parent_height - dialog_height) // 2

        self.new_project_window.move(x, y)
        self.new_project_window.raise_()

    def _onDialogFinished(self, result):
        """Обработчик закрытия диалога с исправлением бага overlay.deleteLater()"""
        # Удаляем оверлей (с проверкой на None)
        if self.overlay:
            self.overlay.deleteLater()
            self.overlay = None

        # Удаляем эффект размытия
        self.gradient_widget.setGraphicsEffect(None)

        # Обновляем список проектов при успешном создании
        if result == QDialog.Accepted:
            self.loadProjects()
            self.applySortAndFilter()

        # Очищаем ссылку на окно
        self.new_project_window = None