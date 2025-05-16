# ui/windows/m2_0_create_project.py

import os
import re
import json
from datetime import datetime
from PySide6.QtWidgets import QDialog

from PySide6.QtCore import Qt, QPoint, QSize, Signal, QTimer, QStringListModel
from PySide6.QtGui import QPainter, QLinearGradient, QColor, QPixmap, QPainterPath, QFontMetrics, QCursor, \
    QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QRadioButton, QButtonGroup, QMessageBox,
    QStyleOption, QStyle, QPlainTextEdit, QComboBox, QApplication, QSizePolicy,
    QCompleter, QListWidget, QListWidgetItem, QMenu, QToolTip
)


# Функция естественной сортировки строк с числами
def natural_sort_key(s):
    """
    Ключ для естественной сортировки строк с числами.
    Разбивает строку на текстовые и числовые части,
    обрабатывая числа как целые числа при сравнении.
    """
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', s)]


# Глобальные константы
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROJECTS_PATH = os.path.join(BASE_DIR, "data", "projects")
FORBIDDEN_CHARS_PATTERN = r'[\\/:"*?<>|@]+'  # Добавлен @ в запрещенные символы
MAX_TAG_LENGTH = 20
MIN_TAG_LENGTH = 1


class GradientBackgroundWidget(QDialog):
    """Виджет с градиентным фоном и скруглёнными углами"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowSystemMenuHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event):
        """Отрисовка градиентного фона со скруглёнными углами"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect()
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)

        # Настройка градиента
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor(20, 0, 30))  # Тёмно-фиолетовый
        gradient.setColorAt(1, QColor(90, 0, 120))  # Более светлый фиолетовый

        painter.setBrush(gradient)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 10, 10)

        super().paintEvent(event)


class ImagePreviewLabel(QLabel):
    """Виджет превью обложки с поддержкой Drag & Drop"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.image_path = None
        self.setFixedSize(200, 300)
        self.setStyleSheet(
            "QLabel { "
            "  background-color: rgba(255, 255, 255, 30);"
            "  border: 2px dashed #FFFFFF;"
            "  border-radius: 5px;"
            "  color: white;"
            "  font-size: 14px;"
            "  padding: 0px;"
            "}"
        )
        self.setText("Обложка\n(перетащить или кликнуть)")

    def dragEnterEvent(self, event):
        """Разрешаем перетаскивание файлов"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def cropToSize(self, pixmap, target_width, target_height):
        """Обрезка изображения до нужных размеров с сохранением центра"""
        x_offset = (pixmap.width() - target_width) // 2
        y_offset = (pixmap.height() - target_height) // 2
        rect = pixmap.rect().adjusted(x_offset, y_offset, -x_offset, -y_offset)
        return pixmap.copy(rect)

    def getRoundedPixmap(self, pixmap, radius):
        """Создание изображения со скруглёнными углами"""
        rounded = QPixmap(pixmap.size())
        rounded.fill(Qt.transparent)

        painter = QPainter(rounded)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

        return rounded

    def dropEvent(self, event):
        """Обработка перетаскивания файла в виджет"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if os.path.isfile(file_path):
                    self.set_image(file_path)
                    break

    def mousePressEvent(self, event):
        """Открытие диалога выбора файла по клику"""
        super().mousePressEvent(event)
        file_dialog = QFileDialog()
        file_dialog.setNameFilters(["Изображения (*.png *.jpg *.jpeg *.bmp *.gif)", "Все файлы (*)"])
        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                self.set_image(selected_files[0])

    def set_image(self, image_path):
        """Загрузка и подготовка изображения для предпросмотра"""
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self.image_path = image_path

            # Масштабирование и обрезка
            scaled = pixmap.scaled(
                self.width(), self.height(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            cropped = self.cropToSize(scaled, self.width(), self.height())
            rounded = self.getRoundedPixmap(cropped, 5)

            self.setPixmap(rounded)
            self.setText("")
        else:
            self.setText("Ошибка\nзагрузки изображения")
            self.image_path = None

    def resizeEvent(self, event):
        """Обновление изображения при изменении размеров"""
        super().resizeEvent(event)
        if self.image_path:
            self.set_image(self.image_path)


# Подсветка для тегов
class TagHighlighter(QSyntaxHighlighter):
    """Подсветка синтаксиса для недопустимых тегов"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.invalid_tags = []

        # Формат для недопустимых тегов
        self.invalid_format = QTextCharFormat()
        self.invalid_format.setUnderlineStyle(QTextCharFormat.WaveUnderline)
        self.invalid_format.setUnderlineColor(QColor("#FF5555"))
        self.invalid_format.setToolTip("Недопустимый тег")

    def set_invalid_tags(self, tags_with_positions):
        """Установка списка недопустимых тегов с их позициями"""
        self.invalid_tags = tags_with_positions
        self.rehighlight()

    def highlightBlock(self, text):
        """Подсветка блока текста"""
        for start, length, tooltip in self.invalid_tags:
            if start <= self.currentBlock().position() < start + length:
                # Тег находится в текущем блоке
                block_pos = self.currentBlock().position()
                tag_start = max(0, start - block_pos)
                tag_end = min(len(text), start + length - block_pos)

                if tag_start < tag_end:
                    # Применяем формат
                    format = QTextCharFormat(self.invalid_format)
                    format.setToolTip(tooltip)
                    self.setFormat(tag_start, tag_end - tag_start, format)


class TagSuggestionList(QListWidget):
    """Улучшенный список тегов с расширенными возможностями редактирования"""
    tag_selected = Signal(str)
    new_tag_created = Signal(str)
    tag_edit_requested = Signal(str, int)  # Текст тега и позиция
    tag_delete_requested = Signal(int)  # Позиция тега

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

        # Специальные элементы
        self.create_new_tag_item = None
        self.mode = "suggest"  # Режимы: suggest, edit_tag
        self.current_tag_index = -1
        self.current_input = ""
        self.all_tags = []

    def update_suggestions(self, suggestions, all_tags, current_input="", existing_tags=None):
        """Обновление списка подсказок с жёстким ограничением на 8 элементов"""
        self.clear()
        self.mode = "suggest"
        self.current_input = current_input
        self.all_tags = all_tags

        # Исключаем уже использованные теги
        if existing_tags:
            existing_lower = [tag.lower() for tag in existing_tags]
            filtered_suggestions = [tag for tag in suggestions if tag.lower() not in existing_lower]
        else:
            filtered_suggestions = suggestions

        # Добавляем подходящие теги
        for tag in filtered_suggestions:
            self.addItem(tag)

        # Устанавливаем create_new_tag_item в None
        self.create_new_tag_item = None

        # Определяем высоту одного элемента
        item_height = self.sizeHintForRow(0) if self.count() > 0 else 20

        # Строго ограничиваем высоту для 8 элементов
        max_visible = min(8, self.count())
        fixed_height = item_height * max_visible

        # Принудительно устанавливаем фиксированный размер
        self.setFixedHeight(fixed_height)

        # Устанавливаем политику прокрутки
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if self.count() > 8 else Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Включаем режим прокрутки по пикселям
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        # Выбираем первый элемент
        if self.count() > 0:
            self.setCurrentRow(0)

    def update_edit_options(self, tag_index, tag_value, all_tags, existing_tags):
        """Обновление опций редактирования для тега"""
        self.clear()
        self.mode = "edit_tag"
        self.current_tag_index = tag_index
        self.all_tags = all_tags

        # Добавляем заголовок
        header = QListWidgetItem(f"✏️ Редактирование тега {tag_index + 1}: \"{tag_value}\"")
        header.setFlags(Qt.NoItemFlags)
        header.setForeground(QColor("#FFCC00"))
        self.addItem(header)

        # Опция удаления
        delete_item = QListWidgetItem("🗑️ Удалить этот тег")
        delete_item.setForeground(QColor("#FF6666"))
        delete_item.setData(Qt.UserRole, "delete")
        self.addItem(delete_item)

        # Разделитель
        separator = QListWidgetItem("───────")
        separator.setFlags(Qt.NoItemFlags)
        separator.setForeground(QColor("#777777"))
        self.addItem(separator)

        # Доступные теги для замены (исключая уже использованные)
        used_tags = [tag.lower() for i, tag in enumerate(existing_tags) if i != tag_index]
        available_tags = [tag for tag in all_tags if tag.lower() not in used_tags]

        for tag in available_tags:
            tag_item = QListWidgetItem(tag)
            self.addItem(tag_item)

        # Добавляем подсказки
        if self.count() > 0:
            help_item = QListWidgetItem("───────")
            help_item.setFlags(Qt.NoItemFlags)
            help_item.setForeground(QColor("#777777"))
            self.addItem(help_item)

            shortcuts = QListWidgetItem("↑/↓: Навигация • Enter: Выбор • Del: Удалить")
            shortcuts.setFlags(Qt.NoItemFlags)
            shortcuts.setForeground(QColor("#999999"))
            self.addItem(shortcuts)

        # Выбираем первый элемент (опцию удаления)
        if self.count() > 1:
            self.setCurrentRow(1)

        # Определяем высоту одного элемента и общую высоту для всех элементов
        total_height = 0
        for i in range(self.count()):
            total_height += self.sizeHintForRow(i)

        # Определяем высоту для 8 видимых элементов
        first_row_height = self.sizeHintForRow(0) if self.count() > 0 else 20
        max_height = first_row_height * 8 + 2

        # Ограничиваем высоту (не более 8 видимых элементов)
        height = min(total_height + 2, max_height)
        self.setFixedHeight(height)

        # Включаем прокрутку только если элементов больше 8
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if self.count() > 8 else Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

    def on_item_clicked(self, item):
        """Обработка клика по элементу с возвратом фокуса"""
        if not item:
            self.hide()
            self.parent().setFocus()  # Возврат фокуса в поле ввода
            return

        item_type = item.data(Qt.UserRole)

        if self.mode == "suggest":
            if item.flags() & Qt.ItemIsSelectable:  # Пропускаем неселектируемые элементы
                if item_type == "create_new":
                    # Извлекаем имя тега из "Создать: "тег""
                    tag_text = item.text()[11:-1]  # Убираем "➕ Создать: "" и закрывающую кавычку
                    self.new_tag_created.emit(tag_text)
                else:
                    self.tag_selected.emit(item.text())
        elif self.mode == "edit_tag":
            if item.flags() & Qt.ItemIsSelectable:
                if item_type == "delete":
                    self.tag_delete_requested.emit(self.current_tag_index)
                else:
                    self.tag_edit_requested.emit(item.text(), self.current_tag_index)

        self.hide()
        self.parent().setFocus()  # Возврат фокуса в поле ввода

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

        # Разные действия в зависимости от режима
        if self.mode == "suggest":
            select_action = menu.addAction("Выбрать")

            if item.data(Qt.UserRole) == "create_new":
                create_action = menu.addAction("Создать новый тег")
                action = menu.exec_(self.mapToGlobal(position))

                if action == create_action:
                    tag_text = item.text()[11:-1]  # Убираем "➕ Создать: "" и закрывающую кавычку
                    self.new_tag_created.emit(tag_text)
                    self.hide()
                    self.parent().setFocus()  # Возврат фокуса в поле ввода
                elif action == select_action:
                    self.on_item_clicked(item)
            else:
                action = menu.exec_(self.mapToGlobal(position))
                if action == select_action:
                    self.on_item_clicked(item)

        elif self.mode == "edit_tag":
            if item.data(Qt.UserRole) == "delete":
                delete_action = menu.addAction("Удалить тег")
                action = menu.exec_(self.mapToGlobal(position))

                if action == delete_action:
                    self.tag_delete_requested.emit(self.current_tag_index)
                    self.hide()
                    self.parent().setFocus()  # Возврат фокуса в поле ввода
            else:
                replace_action = menu.addAction("Заменить на этот тег")
                action = menu.exec_(self.mapToGlobal(position))

                if action == replace_action:
                    self.tag_edit_requested.emit(item.text(), self.current_tag_index)
                    self.hide()
                    self.parent().setFocus()  # Возврат фокуса в поле ввода

    def keyPressEvent(self, event):
        """Полностью переопределенная обработка нажатий клавиш"""
        key = event.key()

        # Получаем только выбираемые элементы
        selectable_items = [i for i in range(self.count()) if self.item(i).flags() & Qt.ItemIsSelectable]

        if not selectable_items:
            self.parent().setFocus()
            return

        # Прерываем стандартное поведение Qt - это критически важно!
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

        elif key == Qt.Key_Delete and self.mode == "edit_tag":
            self.tag_delete_requested.emit(self.current_tag_index)
            self.hide()
            self.parent().setFocus()

        elif key == Qt.Key_N and event.modifiers() & Qt.ControlModifier:
            if self.mode == "suggest" and self.current_input and self.current_input.strip():
                # Проверка валидности тега
                if (MIN_TAG_LENGTH <= len(self.current_input) <= MAX_TAG_LENGTH and
                        '@' not in self.current_input):
                    self.new_tag_created.emit(self.current_input)
                    self.hide()
                    self.parent().setFocus()
                else:
                    # Показываем предупреждение
                    reason = ""
                    if len(self.current_input) > MAX_TAG_LENGTH:
                        reason = f"Тег слишком длинный (макс. {MAX_TAG_LENGTH} символов)"
                    elif len(self.current_input) < MIN_TAG_LENGTH:
                        reason = f"Тег слишком короткий (мин. {MIN_TAG_LENGTH} символ)"
                    elif '@' in self.current_input:
                        reason = "Тег содержит запрещённый символ @"

                    QToolTip.showText(
                        self.parent().mapToGlobal(self.parent().rect().bottomLeft()),
                        f"Невозможно создать тег: {reason}",
                        self.parent()
                    )

        elif key == Qt.Key_Up:
            current_row = self.currentRow()

            # Определяем индекс текущего элемента среди выбираемых
            try:
                current_idx = selectable_items.index(current_row)
                # Переходим к предыдущему или к последнему при циклическом переходе
                next_idx = (current_idx - 1) % len(selectable_items)
            except ValueError:
                # Если элемент не найден в списке выбираемых
                next_idx = 0

            # Устанавливаем новую выбранную строку
            self.setCurrentRow(selectable_items[next_idx])

        elif key == Qt.Key_Down:
            current_row = self.currentRow()

            # Определяем индекс текущего элемента среди выбираемых
            try:
                current_idx = selectable_items.index(current_row)
                # Переходим к следующему или к первому при циклическом переходе
                next_idx = (current_idx + 1) % len(selectable_items)
            except ValueError:
                # Если элемент не найден в списке выбираемых
                next_idx = 0

            # Устанавливаем новую выбранную строку
            self.setCurrentRow(selectable_items[next_idx])

        else:
            # Передаем все остальные клавиши обратно в поле ввода
            self.parent().setFocus()
            self.parent().event(event)

    def wheelEvent(self, event):
        """Блокировка прокрутки колесом мыши для малых списков"""
        # Получаем только выбираемые элементы
        selectable_count = sum(1 for i in range(self.count())
                               if self.item(i).flags() & Qt.ItemIsSelectable)

        # Если элементов мало, полностью блокируем прокрутку
        if selectable_count <= 8:
            event.accept()  # Принимаем событие, но не делаем ничего
            return

        # Для больших списков разрешаем стандартную прокрутку
        super().wheelEvent(event)


class TagSearchLineEdit(QLineEdit):
    """Виджет для ввода тегов с улучшенным автодополнением"""

    def __init__(self, all_tags=None, tags_file=None, parent=None):
        super().__init__(parent)
        self.all_tags = all_tags[:] if all_tags else []
        # Используем естественную сортировку тегов
        self.all_tags.sort(key=natural_sort_key)
        self.tags_file = tags_file
        self.debug_prefix = "[TagSearch]"
        print(f"{self.debug_prefix} Инициализация с {len(self.all_tags)} тегами")

        # Инициализация флага для отслеживания нажатия клавиши Backspace
        self.is_backspace_pressed = False

        # Создаем виджет списка подсказок
        self.suggestion_list = TagSuggestionList(self)
        self.suggestion_list.tag_selected.connect(self.insert_tag)
        self.suggestion_list.new_tag_created.connect(self.create_and_insert_tag)
        self.suggestion_list.tag_edit_requested.connect(self.edit_tag)
        self.suggestion_list.tag_delete_requested.connect(self.delete_tag)

        # Настройка внешнего вида
        self.setStyleSheet(
            "QLineEdit { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; }"
            "QLineEdit:focus { border-color: #DDDDDD; }"
        )
        self.setPlaceholderText("Введите теги через запятую...")

        # Подключаем сигналы
        self.textChanged.connect(self.on_text_changed)

        # Контекстное меню
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_tag_context_menu)

        # Таймер для отложенного обновления подсказок
        self.suggestion_timer = QTimer()
        self.suggestion_timer.setSingleShot(True)
        self.suggestion_timer.timeout.connect(self.update_suggestions)

        # Флаг для отслеживания неверных тегов
        self.has_invalid_tags = False

        # Кэш позиций тегов для подсветки
        self.invalid_tag_positions = []

    def keyPressEvent(self, event):
        """Обработка клавиш с улучшенным управлением фокусом"""
        key = event.key()

        # Если список открыт, обрабатываем стрелку вниз особым образом
        if self.suggestion_list.isVisible() and key == Qt.Key_Down:
            # Передаем фокус списку подсказок и выбираем первый элемент
            self.suggestion_list.setFocus()

            # Выбираем первый селектируемый элемент
            selectable_items = [i for i in range(self.suggestion_list.count())
                                if self.suggestion_list.item(i).flags() & Qt.ItemIsSelectable]
            if selectable_items:
                self.suggestion_list.setCurrentRow(selectable_items[0])
            event.accept()
            return

        # Обработка запятой
        if key == Qt.Key_Comma:
            self.insert_comma()
            event.accept()
            return

        # Открытие списка редактирования тега по двойному клику на Tab
        if key == Qt.Key_Tab and event.modifiers() & Qt.ControlModifier:
            self.open_tag_editor()
            event.accept()
            return

        # Стандартная обработка для других клавиш
        super().keyPressEvent(event)

        # Отложенное обновление подсказок для снижения нагрузки
        self.suggestion_timer.start(100)

    def mousePressEvent(self, event):
        """Обработка клика по полю ввода"""
        super().mousePressEvent(event)
        # Если список подсказок открыт, но пользователь кликает по полю ввода
        # возвращаем фокус на поле ввода
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
        """Форматирование запятых и обновление подсказок с валидацией тегов"""
        # Сохраняем информацию о нажатой клавише
        app = QApplication.instance()
        is_backspace_pressed = app.keyboardModifiers() == Qt.NoModifier and app.queryKeyboardModifiers() == Qt.NoModifier and self.is_backspace_pressed

        cursor_pos = self.cursorPosition()
        original_len = len(text)

        # Ограничение длины текущего тега
        current_tag, tag_start, tag_end = self.get_current_tag_bounds(cursor_pos)
        if current_tag and len(current_tag) > MAX_TAG_LENGTH:
            # Обрезаем до максимальной длины
            truncated_tag = current_tag[:MAX_TAG_LENGTH]
            new_text = text[:tag_start] + truncated_tag + text[tag_end:]

            self.blockSignals(True)
            self.setText(new_text)
            self.setCursorPosition(tag_start + len(truncated_tag))
            self.blockSignals(False)
            return

        # Если есть запятые и не нажат backspace, форматируем теги
        if ',' in text and not is_backspace_pressed:
            parts = []
            for part in text.split(','):
                part = part.strip()
                if '@' in part:
                    part = part.replace('@', '')
                parts.append(part)

            formatted = ', '.join(parts)

            if text != formatted:
                delta = len(formatted) - original_len
                new_pos = cursor_pos + delta

                self.blockSignals(True)
                self.setText(formatted)
                self.setCursorPosition(min(new_pos, len(formatted)))
                self.blockSignals(False)

        self.check_tag_validity()
        self.suggestion_timer.start(100)

    def check_tag_validity(self):
        """Проверка валидности тегов и подсветка ошибок"""
        tags = self.get_all_entered_tags()
        text = self.text()

        invalid_positions = []
        has_invalid = False

        for tag in tags:
            # Находим позицию тега в тексте
            start = text.find(tag)
            if start >= 0:
                invalid_reason = None

                # Проверяем ограничения
                if len(tag) > MAX_TAG_LENGTH:
                    invalid_reason = "Превышена максимальная длина"
                    has_invalid = True
                elif len(tag) < MIN_TAG_LENGTH:
                    invalid_reason = "Слишком короткий тег"
                    has_invalid = True
                elif '@' in tag:
                    invalid_reason = "Содержит запрещённый символ @"
                    has_invalid = True

                if invalid_reason:
                    invalid_positions.append((start, len(tag), invalid_reason))

        # Сохраняем информацию о невалидных тегах
        self.invalid_tag_positions = invalid_positions
        self.has_invalid_tags = has_invalid

        # Обновляем стиль поля ввода - более ненавязчивое подчеркивание
        if has_invalid:
            self.setStyleSheet(
                "QLineEdit { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; }"
                "QLineEdit:focus { border-color: #FFAA55; }"
            )

            # Формируем краткую подсказку
            self.setToolTip("Некоторые теги содержат ошибки")
        else:
            self.setStyleSheet(
                "QLineEdit { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; }"
                "QLineEdit:focus { border-color: #DDDDDD; }"
            )
            self.setToolTip("")

    def update_suggestions(self):
        """Обновление списка подсказок без прерывания ввода"""
        cursor_pos = self.cursorPosition()
        current_tag, _, _ = self.get_current_tag_bounds(cursor_pos)

        # Если текущий тег пустой, скрываем подсказки
        if not current_tag:
            self.suggestion_list.hide()
            return

        # Получаем все введенные теги
        entered_tags = self.get_all_entered_tags()

        # Фильтруем доступные теги
        used_tags_lower = [tag.lower() for tag in entered_tags]
        available_tags = [tag for tag in self.all_tags
                          if tag.lower() not in used_tags_lower]

        # Показываем подходящие теги
        # Фильтруем по текущему вводу
        matches = [tag for tag in available_tags
                   if current_tag.lower() in tag.lower()]

        if matches or current_tag:
            # Обновляем без перехвата фокуса и прерывания ввода
            self.suggestion_list.update_suggestions(matches, self.all_tags,
                                                    current_tag, entered_tags)
            # Показываем подсказки
            self.show_suggestions_popup()
            self.setFocus()  # Возвращаем фокус полю ввода
        else:
            self.suggestion_list.hide()

    def show_suggestions_popup(self):
        """Показ подсказок с точной подгонкой размера и позиционированием"""
        if self.suggestion_list.count() == 0:
            self.suggestion_list.hide()
            return

        # Сохраняем текущую позицию курсора
        current_cursor_pos = self.cursorPosition()

        # Рассчитываем оптимальную ширину на основе содержимого
        font_metrics = QFontMetrics(self.suggestion_list.font())
        max_text_width = 0

        # Определяем минимальную ширину для 8 символов
        min_width = font_metrics.horizontalAdvance("W" * 8) + 30  # W как самый широкий символ + отступы

        for i in range(self.suggestion_list.count()):
            item = self.suggestion_list.item(i)
            # Измеряем реальную ширину текста для точной подгонки
            text_width = font_metrics.horizontalAdvance(
                item.text()) + 30  # Увеличиваем отступ для предотвращения обрезки
            max_text_width = max(max_text_width, text_width)

        # Устанавливаем ширину по самому длинному тексту, но не меньше минимальной
        width = max(max_text_width, min_width)

        # Определяем высоту одного элемента
        item_height = self.suggestion_list.sizeHintForRow(0) if self.suggestion_list.count() > 0 else 20

        # Ограничиваем количество видимых элементов до 8
        max_visible = min(8, self.suggestion_list.count())
        height = item_height * max_visible

        # Определяем позицию относительно курсора
        cursor_rect = self.cursorRect()
        global_pos = self.mapToGlobal(cursor_rect.bottomLeft())

        # Проверка доступного пространства по горизонтали
        screen_rect = QApplication.primaryScreen().availableGeometry()
        if global_pos.x() + width > screen_rect.right():
            global_pos.setX(screen_rect.right() - width)

        # Проверка доступного пространства по вертикали
        if global_pos.y() + height > screen_rect.bottom():
            # Показываем над курсором
            global_pos = self.mapToGlobal(cursor_rect.topLeft())
            global_pos.setY(global_pos.y() - height)

        # Устанавливаем размер списка и показываем его
        self.suggestion_list.setFixedSize(width, height)
        self.suggestion_list.move(global_pos)

        # Настраиваем полосы прокрутки
        self.suggestion_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.suggestion_list.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if self.suggestion_list.count() > 8 else Qt.ScrollBarAlwaysOff)

        # Включаем режим прокрутки по пикселям для плавной прокрутки
        self.suggestion_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        # Обновляем геометрию и показываем список
        self.suggestion_list.updateGeometry()
        self.suggestion_list.show()

        # Возвращаем фокус на поле ввода
        self.setFocus()
        self.setCursorPosition(current_cursor_pos)

    def insert_tag(self, tag):
        """Вставка выбранного тега с валидацией"""
        # Проверка длины тега
        if len(tag) > MAX_TAG_LENGTH:
            tag = tag[:MAX_TAG_LENGTH]  # Обрезаем если слишком длинный

        # Удаляем символ @ если он есть
        if '@' in tag:
            tag = tag.replace('@', '')

        # Проверка минимальной длины
        if len(tag) < MIN_TAG_LENGTH:
            return  # Не вставляем слишком короткие теги

        text = self.text()
        cursor_pos = self.cursorPosition()

        # Получаем границы текущего тега
        _, start, end = self.get_current_tag_bounds(cursor_pos)

        # Заменяем только текущий тег
        new_text = text[:start] + tag + text[end:]

        # Применяем изменения
        self.setText(new_text)
        new_pos = start + len(tag)
        self.setCursorPosition(new_pos)

        # Добавляем запятую если это конец строки
        if new_pos == len(new_text):
            self.insert_comma()

        # Проверяем валидность тегов
        self.check_tag_validity()

    def keyPressEvent(self, event):
        """Обработка нажатий клавиш с отслеживанием backspace"""
        if event.key() == Qt.Key_Backspace:
            self.is_backspace_pressed = True
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        """Обработка отпускания клавиш"""
        if event.key() == Qt.Key_Backspace:
            self.is_backspace_pressed = False
        super().keyReleaseEvent(event)

        self.is_backspace_pressed = False
    def create_and_insert_tag(self, tag):
        """Создание нового тега с проверкой ограничений и вставка"""
        # Валидация тега
        if not tag or len(tag) < MIN_TAG_LENGTH:
            return

        # Обрезаем до максимальной длины и удаляем @
        tag = tag[:MAX_TAG_LENGTH].replace('@', '')

        if not tag:  # Если после обработки тег пустой
            return

        # Добавляем в глобальный список, если тег непустой
        if tag.lower() not in [t.lower() for t in self.all_tags]:
            self.all_tags.append(tag)
            # Используем естественную сортировку тегов
            self.all_tags.sort(key=natural_sort_key)

        # Вставляем тег в текст
        self.insert_tag(tag)

    def edit_tag(self, new_tag, tag_index):
        """Редактирование существующего тега с валидацией"""
        # Валидация нового тега
        if not new_tag or len(new_tag) < MIN_TAG_LENGTH:
            return

        # Обрезаем до максимальной длины и удаляем @
        new_tag = new_tag[:MAX_TAG_LENGTH].replace('@', '')

        if not new_tag:  # Если после обработки тег пустой
            return

        tags = self.get_all_entered_tags()
        if 0 <= tag_index < len(tags):
            tags[tag_index] = new_tag
            self.set_tags(tags)
            self.check_tag_validity()

    def delete_tag(self, tag_index):
        """Удаление тега"""
        tags = self.get_all_entered_tags()
        if 0 <= tag_index < len(tags):
            del tags[tag_index]
            self.set_tags(tags)
            self.check_tag_validity()

    def set_tags(self, tags):
        """Установка полного списка тегов"""
        if tags:
            self.setText(", ".join(tags) + ", ")
        else:
            self.clear()
        self.setCursorPosition(len(self.text()))

    def get_current_tag_bounds(self, pos):
        """Определение границ текущего тега под курсором"""
        text = self.text()
        if not text:
            return "", 0, 0

        # Ищем запятую слева от курсора
        left_part = text[:pos]
        last_comma = left_part.rfind(',')

        # Определяем начало тега
        if last_comma >= 0:
            tag_start = last_comma + 1
            # Пропускаем пробелы
            while tag_start < pos and text[tag_start].isspace():
                tag_start += 1
        else:
            tag_start = 0

        # Ищем запятую справа от курсора
        right_part = text[pos:]
        next_comma = right_part.find(',')

        # Определяем конец тега
        if next_comma >= 0:
            tag_end = pos + next_comma
        else:
            tag_end = len(text)

        # Получаем тег без пробелов
        current_tag = text[tag_start:tag_end].strip()

        return current_tag, tag_start, tag_end

    def get_tag_at_position(self, pos):
        """Определение индекса тега в позиции курсора"""
        text = self.text()
        if not text:
            return -1

        # Получаем все теги
        tags = []
        start_positions = [0]  # Начальные позиции тегов
        end_positions = []  # Конечные позиции тегов

        for i, part in enumerate(text.split(',')):
            if i > 0:
                # Для всех тегов кроме первого добавляем +2 для учета запятой и пробела
                start_positions.append(end_positions[-1] + 2)
            end_positions.append(start_positions[-1] + len(part.strip()))
            tags.append(part.strip())

        # Находим тег, в котором находится курсор
        for i in range(len(tags)):
            if start_positions[i] <= pos <= end_positions[i] and tags[i]:
                return i

        return -1

    def insert_comma(self):
        """Вставка запятой и пробела"""
        text = self.text()
        pos = self.cursorPosition()

        if pos == len(text):
            # В конец строки
            self.setText(text + ", ")
            self.setCursorPosition(len(self.text()))
        else:
            # Внутри строки
            new_text = text[:pos] + ", " + text[pos:]
            self.setText(new_text)
            self.setCursorPosition(pos + 2)

        # Проверяем валидность после вставки запятой
        self.check_tag_validity()

    def finish_current_tag(self):
        """Завершение текущего тега запятой"""
        text = self.text().strip()
        if not text.endswith(','):
            self.setText(text + ", ")
            self.setCursorPosition(len(self.text()))

        # Проверяем валидность после завершения тега
        self.check_tag_validity()

    def open_tag_editor(self):
        """Открытие редактора тега под курсором"""
        cursor_pos = self.cursorPosition()
        tag_index = self.get_tag_at_position(cursor_pos)

        if tag_index >= 0:
            tags = self.get_all_entered_tags()
            if 0 <= tag_index < len(tags):
                # Открываем редактор для тега
                self.suggestion_list.update_edit_options(
                    tag_index, tags[tag_index], self.all_tags, tags
                )
                self.show_suggestions_popup()

    def show_tag_context_menu(self, position):
        """Показ контекстного меню для тегов"""
        cursor_pos = self.cursorPositionAt(position)
        tag_index = self.get_tag_at_position(cursor_pos)

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

        if tag_index >= 0:
            # Меню для существующего тега
            tags = self.get_all_entered_tags()
            edit_action = menu.addAction(f"Редактировать тег: {tags[tag_index]}")
            delete_action = menu.addAction("Удалить этот тег")

            menu.addSeparator()

        # Общие пункты меню
        show_suggestions_action = menu.addAction("Показать подсказки")
        menu.addSeparator()
        clear_action = menu.addAction("Очистить все теги")

        # Дополнительные опции для невалидных тегов
        if self.has_invalid_tags:
            menu.addSeparator()
            validate_action = menu.addAction("Исправить недопустимые теги")

        # Выполнение выбранного действия
        action = menu.exec_(self.mapToGlobal(position))

        if not action:
            return

        if tag_index >= 0:
            tags = self.get_all_entered_tags()
            if action == edit_action:
                self.open_tag_editor()
            elif action == delete_action:
                self.delete_tag(tag_index)

        if action == show_suggestions_action:
            self.setCursorPosition(cursor_pos)
            self.update_suggestions()
        elif action == clear_action:
            self.clear()
        elif self.has_invalid_tags and action == validate_action:
            self.fix_invalid_tags()

    def fix_invalid_tags(self):
        """Автоматическое исправление недопустимых тегов"""
        tags = self.get_all_entered_tags()
        fixed_tags = []

        for tag in tags:
            # Исправляем теги
            fixed_tag = tag.replace('@', '')
            if len(fixed_tag) > MAX_TAG_LENGTH:
                fixed_tag = fixed_tag[:MAX_TAG_LENGTH]

            if len(fixed_tag) >= MIN_TAG_LENGTH:
                fixed_tags.append(fixed_tag)

        # Устанавливаем исправленные теги
        self.set_tags(fixed_tags)
        self.check_tag_validity()

    def get_all_entered_tags(self):
        """Получение всех введенных тегов"""
        text = self.text().strip()
        if not text:
            return []

        return [tag.strip() for tag in text.split(',') if tag.strip()]

    def get_valid_tags(self):
        """Получение только валидных тегов"""
        all_tags = self.get_all_entered_tags()
        valid_tags = []

        for tag in all_tags:
            if (MIN_TAG_LENGTH <= len(tag) <= MAX_TAG_LENGTH and
                    '@' not in tag):
                valid_tags.append(tag)

        return valid_tags

    def get_new_tags(self):
        """Определение новых тегов с валидацией"""
        entered = self.get_valid_tags()
        existing = [tag.lower() for tag in self.all_tags]
        return [tag for tag in entered if tag.lower() not in existing]

    def add_new_tags_to_global(self):
        """Сохранение новых тегов в глобальный файл с валидацией"""
        if not self.tags_file:
            print("[create_project] ERROR: ❌ Не указан путь к файлу тегов")
            return

        # Получаем только валидные новые теги
        new_tags = self.get_new_tags()
        if not new_tags:
            return

        # Объединение и сортировка тегов
        all_tags_extended = self.all_tags + new_tags
        lower_map = {}
        for tag in all_tags_extended:
            # Пропускаем невалидные теги
            if not (MIN_TAG_LENGTH <= len(tag) <= MAX_TAG_LENGTH and '@' not in tag):
                continue
            lower_map[tag.lower()] = tag

        # Используем естественную сортировку тегов
        final_list = sorted(lower_map.values(), key=natural_sort_key)

        # Создание директории если нужно
        tags_dir = os.path.dirname(self.tags_file)
        if not os.path.exists(tags_dir):
            try:
                os.makedirs(tags_dir, exist_ok=True)
                print(f"[create_project] INFO: 📁 Создана директория для тегов: {tags_dir}")
            except Exception as e:
                print(f"[create_project] ERROR: ❌ Ошибка создания директории: {e}")
                return

        # Сохранение в файл
        try:
            with open(self.tags_file, 'w', encoding='utf-8') as f:
                for tag in final_list:
                    f.write(tag + "\n")
            print(f"[create_project] INFO: ✅ Теги сохранены в {self.tags_file}")
        except Exception as e:
            print(f"[create_project] ERROR: ❌ Ошибка сохранения тегов: {e}")

        # Обновление списка тегов
        self.all_tags = final_list


class NewProjectWindow(GradientBackgroundWidget):
    """Окно создания нового проекта"""
    project_created = Signal(dict)  # Сигнал с метаданными проекта
    finished = Signal(int)  # Сигнал о закрытии окна

    def __init__(self, projects_path=None, parent=None):
        super().__init__(parent)
        self._is_auto_positioning = False
        self.setWindowTitle("Создание нового проекта")
        self.projects_path = projects_path or PROJECTS_PATH

        # Определяем путь к файлу тегов относительно пути проектов
        self.tags_file = os.path.join(os.path.dirname(self.projects_path), "tags.txt")

        self.desired_size = QSize(800, 800)
        self.adaptSizeToScreen()
        self.setModal(True)
        self._current_screen = self.screen()

        # Загрузка тегов
        all_tags = self._load_tags_from_file()

        # Создание интерфейса
        self._createUI(all_tags)

        # Отслеживание изменений экрана
        QApplication.instance().screenAdded.connect(self.screenUpdated)
        QApplication.instance().screenRemoved.connect(self.screenUpdated)

    def _createUI(self, all_tags):
        """Создание пользовательского интерфейса"""
        # Верхняя панель с заголовком и кнопкой закрытия
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(0)

        left_spacer = QLabel("")
        left_spacer.setFixedWidth(50)
        top_bar_layout.addWidget(left_spacer, alignment=Qt.AlignLeft)

        self.title_label = QLabel("Создание нового произведения")
        self.title_label.setStyleSheet("QLabel { color: white; font-size: 18px; font-weight: bold; }")
        top_bar_layout.addWidget(self.title_label, alignment=Qt.AlignCenter)

        self.close_btn = QPushButton("✕")
        self.close_btn.setStyleSheet(
            "QPushButton { border: none; color: white; font-size: 18px; font-weight: bold; padding: 4px 8px; }"
            "QPushButton:hover { color: #FF8888; }"
        )
        self.close_btn.setFixedWidth(40)
        self.close_btn.clicked.connect(self.close_window)
        top_bar_layout.addWidget(self.close_btn, alignment=Qt.AlignRight)

        # Основная форма
        grid_layout = QGridLayout()
        grid_layout.setContentsMargins(10, 10, 10, 10)
        grid_layout.setHorizontalSpacing(20)
        grid_layout.setVerticalSpacing(10)

        # Название проекта
        name_label = QLabel("Название:")
        self._set_label_style(name_label)
        self.project_name_input = QLineEdit()
        self._set_lineedit_style(self.project_name_input)
        self.project_name_input.setPlaceholderText("Введите название произведения")
        self.project_name_input.textChanged.connect(self.validate_project_name)
        grid_layout.addWidget(name_label, 0, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.project_name_input, 0, 1)

        # Тип (вебтун/листовое)
        type_label = QLabel("Тип:")
        self._set_label_style(type_label)
        self.type_group = QButtonGroup(self)
        self.radio_webtoon = QRadioButton("Вебтун")
        self.radio_paper = QRadioButton("Листовое")
        self.radio_webtoon.setChecked(True)
        self._set_radiobutton_style(self.radio_webtoon)
        self._set_radiobutton_style(self.radio_paper)
        self.type_group.addButton(self.radio_webtoon, 1)
        self.type_group.addButton(self.radio_paper, 2)

        type_layout = QHBoxLayout()
        type_layout.addWidget(self.radio_webtoon)
        type_layout.addWidget(self.radio_paper)
        type_widget = QWidget()
        type_widget.setLayout(type_layout)

        grid_layout.addWidget(type_label, 1, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(type_widget, 1, 1)

        # Цветность (полноцвет/Ч/Б)
        color_label = QLabel("Цветность:")
        self._set_label_style(color_label)
        self.color_group = QButtonGroup(self)
        self.radio_color = QRadioButton("Полноцвет")
        self.radio_bw = QRadioButton("Ч/Б")
        self.radio_color.setChecked(True)
        self._set_radiobutton_style(self.radio_color)
        self._set_radiobutton_style(self.radio_bw)
        self.color_group.addButton(self.radio_color, 1)
        self.color_group.addButton(self.radio_bw, 2)

        color_layout = QHBoxLayout()
        color_layout.addWidget(self.radio_color)
        color_layout.addWidget(self.radio_bw)
        color_widget = QWidget()
        color_widget.setLayout(color_layout)

        grid_layout.addWidget(color_label, 2, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(color_widget, 2, 1)

        # Язык перевода
        lang_label = QLabel("Язык перевода:")
        self._set_label_style(lang_label)
        self.language_combo = QComboBox()
        self._set_combobox_style(self.language_combo)
        self.language_combo.addItems(["ru", "en", "es", "fr", "ko", "jp", "zh"])

        grid_layout.addWidget(lang_label, 3, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.language_combo, 3, 1)

        # Страна
        country_label = QLabel("Страна:")
        self._set_label_style(country_label)
        self.country_combo = QComboBox()
        self._set_combobox_style(self.country_combo)
        self.country_combo.addItems(["Япония", "Корея", "Китай", "Россия", "США", "Другое"])

        grid_layout.addWidget(country_label, 4, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.country_combo, 4, 1)

        # Год
        year_label = QLabel("Год:")
        self._set_label_style(year_label)
        self.year_input = QLineEdit()
        self._set_lineedit_style(self.year_input)
        self.year_input.setPlaceholderText("Например, 2023")
        grid_layout.addWidget(year_label, 5, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.year_input, 5, 1)

        # Теги с передачей пути к файлу
        tags_label = QLabel("Теги:")
        self._set_label_style(tags_label)
        self.tags_input = TagSearchLineEdit(all_tags, tags_file=self.tags_file)
        self.tags_input.setPlaceholderText("Введите или выберите теги (через запятую)")
        grid_layout.addWidget(tags_label, 6, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.tags_input, 6, 1)

        # Обложка (справа, на уровне строк 0..6)
        self.image_preview = ImagePreviewLabel()
        grid_layout.addWidget(self.image_preview, 0, 2, 7, 1)  # row=0..6, col=2

        # Описание (на всю ширину)
        desc_label = QLabel("Описание:")
        self._set_label_style(desc_label)
        self.description_input = QPlainTextEdit()
        self._set_plaintext_style(self.description_input)
        self.description_input.setPlaceholderText("Краткое описание...")

        grid_layout.addWidget(desc_label, 7, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.description_input, 7, 1, 1, 2)

        # Ссылки (на всю ширину)
        links_label = QLabel("Ссылки:")
        self._set_label_style(links_label)
        self.links_input = QPlainTextEdit()
        self._set_plaintext_style(self.links_input)
        self.links_input.setPlaceholderText("Список ссылок (каждая с новой строки или через запятую)")
        self.links_input.setFixedHeight(100)

        grid_layout.addWidget(links_label, 8, 0, alignment=Qt.AlignRight)
        grid_layout.addWidget(self.links_input, 8, 1, 1, 2)

        # Кнопка "Создать проект"
        self.create_btn = QPushButton("Создать проект")
        self.create_btn.setStyleSheet(
            "QPushButton { "
            "  background-color: rgba(255, 255, 255, 50);"
            "  border: 2px solid #FFFFFF;"
            "  border-radius: 5px;"
            "  color: white;"
            "  font-size: 16px;"
            "  font-weight: bold;"
            "  padding: 8px 16px;"
            "}"
            "QPushButton:hover { background-color: rgba(255,255,255,80); }"
            "QPushButton:pressed { background-color: rgba(255,255,255,120); }"
        )
        self.create_btn.clicked.connect(self.create_project)
        grid_layout.addWidget(self.create_btn, 9, 0, 1, 3, alignment=Qt.AlignCenter)

        # Сборка основного интерфейса
        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top_bar_layout)
        main_layout.addLayout(grid_layout)

    def validate_project_name(self, text):
        """Валидация имени проекта (удаление символа @)"""
        if '@' in text:
            # Запоминаем позицию курсора
            cursor_pos = self.project_name_input.cursorPosition()

            # Считаем, сколько @ было до позиции курсора
            at_count = text[:cursor_pos].count('@')

            # Заменяем @ на пустую строку
            cleaned_text = text.replace('@', '')

            # Устанавливаем новый текст и позицию курсора
            self.project_name_input.blockSignals(True)
            self.project_name_input.setText(cleaned_text)
            # Корректируем позицию курсора с учетом удаленных символов
            self.project_name_input.setCursorPosition(cursor_pos - at_count)
            self.project_name_input.blockSignals(False)

            # Показываем всплывающую подсказку
            QToolTip.showText(
                self.project_name_input.mapToGlobal(self.project_name_input.rect().bottomLeft()),
                "Символ @ запрещён в названии проекта",
                self.project_name_input
            )

    def _set_label_style(self, label):
        """Стиль для меток"""
        label.setStyleSheet("QLabel { color: white; font-size: 14px; }")

    def _set_lineedit_style(self, lineedit):
        """Стиль для полей ввода"""
        lineedit.setStyleSheet(
            "QLineEdit { "
            "  border: 2px solid #FFFFFF;"
            "  border-radius: 5px;"
            "  color: white;"
            "  background: transparent;"
            "  padding: 4px 6px;"
            "}"
            "QLineEdit:focus { border-color: #DDDDDD; }"
        )

    def _set_plaintext_style(self, textedit):
        """Стиль для многострочных полей"""
        textedit.setStyleSheet(
            "QPlainTextEdit { "
            "  border: 2px solid #FFFFFF;"
            "  border-radius: 5px;"
            "  color: white;"
            "  background: transparent;"
            "  padding: 4px 6px;"
            "}"
            "QPlainTextEdit:focus { border-color: #DDDDDD; }"
        )

    def _set_radiobutton_style(self, radio):
        """Стиль для переключателей"""
        radio.setStyleSheet(
            "QRadioButton { color: white; }"
            "QRadioButton::indicator { width: 18px; height: 18px; }"
            "QRadioButton::indicator:unchecked { border: 2px solid white; border-radius: 9px; }"
            "QRadioButton::indicator:checked { background-color: #FFFFFF; border-radius: 9px; }"
        )

    def _set_combobox_style(self, combo):
        """Стиль для выпадающих списков"""
        combo.setStyleSheet(
            "QComboBox { "
            "  border: 2px solid #FFFFFF;"
            "  border-radius: 5px;"
            "  color: white;"
            "  background: transparent;"
            "  padding: 4px 6px;"
            "  combobox-popup: 0;"
            "}"
            "QComboBox:drop-down { border: none; }"
            "QComboBox:focus { border-color: #DDDDDD; }"
            "QComboBox QAbstractItemView { "
            "  background-color: #333333;"
            "  color: white;"
            "  selection-background-color: #555555;"
            "}"
        )

    def screenUpdated(self, screen):
        """Обработка изменений в составе экранов"""
        # Проверка смены текущего экрана
        current_screen = self.screen()
        if self._current_screen != current_screen:
            self._current_screen = current_screen
            self.adaptSizeToScreen()
            self.centerOnParent()

    def adaptSizeToScreen(self):
        """Адаптация размеров окна под текущий экран"""
        # Используем экран родителя, если доступен
        if self.parent():
            screen = self.parent().screen()
        else:
            screen = self.screen()

        screen_size = screen.availableSize()

        # Запас 10% от края экрана
        margin = 0.1
        max_width = screen_size.width() * (1 - margin)
        max_height = screen_size.height() * (1 - margin)

        if self.desired_size.width() > max_width or self.desired_size.height() > max_height:
            scale_factor = min(
                max_width / self.desired_size.width(),
                max_height / self.desired_size.height()
            )
            adapted_size = QSize(
                int(self.desired_size.width() * scale_factor),
                int(self.desired_size.height() * scale_factor)
            )
            self.resize(adapted_size)
        else:
            self.resize(self.desired_size)

        # Перецентрирование при видимости
        if self.isVisible():
            QTimer.singleShot(0, self.centerOnParent)

    def moveEvent(self, event):
        """Упрощенная обработка перемещения окна"""
        if not self._is_auto_positioning:
            # Проверка смены экрана
            new_screen = self.screen()
            if self._current_screen != new_screen:
                self._current_screen = new_screen
                self.adaptSizeToScreen()

        super().moveEvent(event)

    def showEvent(self, event):
        """Центрирование окна при показе"""
        super().showEvent(event)
        self.centerOnParent()

    def centerOnParent(self):
        """Центрирование окна относительно родителя"""
        if not self.parent():
            return

        # Предотвращение рекурсии при позиционировании
        self._is_auto_positioning = True

        # Получение родительского виджета
        parent = self.parent()
        central_widget = parent.centralWidget() if hasattr(parent, 'centralWidget') else parent

        # Расчет позиции центрирования
        global_pos = central_widget.mapToGlobal(QPoint(0, 0))
        center_x = global_pos.x() + (central_widget.width() - self.width()) // 2
        center_y = global_pos.y() + (central_widget.height() - self.height()) // 2

        # Перемещение окна
        self.move(center_x, center_y)
        self.raise_()
        self.activateWindow()

        self._is_auto_positioning = False

    def _load_tags_from_file(self):
        """Загрузка тегов из файла"""
        if not os.path.exists(self.tags_file):
            print(f"[create_project] INFO: 📄 Файл с тегами отсутствует: {self.tags_file}")
            return []

        try:
            with open(self.tags_file, "r", encoding="utf-8") as f:
                tags = [line.strip() for line in f if line.strip()]

            # Фильтруем теги по ограничениям
            filtered_tags = []
            for tag in tags:
                if '@' in tag:
                    tag = tag.replace('@', '')

                if MIN_TAG_LENGTH <= len(tag) <= MAX_TAG_LENGTH:
                    filtered_tags.append(tag)

            print(f"[create_project] INFO: 🏷️ Загружено {len(filtered_tags)} тегов из {self.tags_file}")
            # Используем естественную сортировку тегов
            return sorted(filtered_tags, key=natural_sort_key)
        except Exception as e:
            print(f"[create_project] ERROR: ❌ Ошибка загрузки тегов: {e}")
            return []

    def create_project(self):
        """Создание проекта и сохранение в файловой системе"""
        # Проверка заполнения обязательных полей
        original_project_name = self.project_name_input.text().strip()
        if not original_project_name:
            QMessageBox.warning(self, "Ошибка", "Введите название произведения!")
            return

        # Конвертация недопустимых символов в названии папки
        folder_name = re.sub(FORBIDDEN_CHARS_PATTERN, "_", original_project_name)
        if not folder_name:
            QMessageBox.warning(self, "Ошибка", "Недопустимое название. Слишком много запрещённых символов!")
            return

        # Проверка существования проекта
        project_path = os.path.join(self.projects_path, folder_name)
        if os.path.exists(project_path):
            QMessageBox.information(self, "Информация", f"Проект '{original_project_name}' уже существует!")
            return

        # Проверка невалидных тегов
        if self.tags_input.has_invalid_tags:
            response = QMessageBox.question(
                self,
                "Внимание",
                "В списке тегов есть недопустимые теги. Исправить автоматически?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )

            if response == QMessageBox.Cancel:
                return
            elif response == QMessageBox.Yes:
                self.tags_input.fix_invalid_tags()

        # Создание структуры папок
        os.makedirs(project_path, exist_ok=True)
        os.makedirs(os.path.join(project_path, "chapters"), exist_ok=True)
        os.makedirs(os.path.join(project_path, "history"), exist_ok=True)

        # Сохранение обложки
        cover_image_filename = None
        if self.image_preview.image_path:
            ext = os.path.splitext(self.image_preview.image_path)[1]
            cover_image_filename = "cover" + ext
            cover_destination = os.path.join(project_path, cover_image_filename)
            try:
                with open(self.image_preview.image_path, "rb") as src, open(cover_destination, "wb") as dst:
                    dst.write(src.read())
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить обложку: {e}")

        # Сбор метаданных проекта - используем только валидные теги
        metadata = {
            "original_title": original_project_name,
            "folder_name": folder_name,
            "type": "Вебтун" if self.radio_webtoon.isChecked() else "Листовое",
            "color": "Полноцвет" if self.radio_color.isChecked() else "Ч/Б",
            "language": self.language_combo.currentText(),
            "country": self.country_combo.currentText(),
            "year": self.year_input.text().strip(),
            "tags": self.tags_input.get_valid_tags(),
            "description": self.description_input.toPlainText().strip(),
            "links": self._parse_links(self.links_input.toPlainText()),
            "cover_image": cover_image_filename if cover_image_filename else "",
            "chapters": [],
            "status": "new",
            "created_at": datetime.now().isoformat()
        }

        # Сохранение метаданных
        metadata_path = os.path.join(project_path, "metadata.json")
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить metadata.json: {e}")
            return

        # Добавление новых тегов в общий список
        self.tags_input.add_new_tags_to_global()

        # Информирование пользователя
        QMessageBox.information(self, "Готово", f"Проект '{original_project_name}' успешно создан!")

        # Отправка сигнала для обновления списка проектов
        self.project_created.emit(metadata)
        self.accept()

    def _parse_links(self, text):
        """Разбор ссылок из текста"""
        lines = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            lines.extend(parts)
        if not lines:
            # Если нет переводов строк, проверяем запятые
            lines = [p.strip() for p in text.split(",") if p.strip()]
        return lines

    def close_window(self):
        """Закрытие окна с отрицательным результатом"""
        self.finished.emit(QDialog.Rejected)
        self.close()

    def closeEvent(self, event):
        """Обработка события закрытия окна"""
        self.finished.emit(QDialog.Rejected)
        super().closeEvent(event)

def open_new_project_window(parent=None, projects_path=None):
    """Создание экземпляра окна нового проекта"""
    return NewProjectWindow(projects_path=projects_path, parent=parent)

# Локальный тест
if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    window = NewProjectWindow()
    window.show()
    sys.exit(app.exec())