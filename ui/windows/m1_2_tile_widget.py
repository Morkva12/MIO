# ui/windows/m1_2_tile_widget.py

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QPixmap, QPainter, QPainterPath, Qt, QFontMetrics
from PySide6.QtCore import Signal, QPoint


class TileWidget(QWidget):
    """Виджет плитки проекта, отображающий обложку и название."""

    clicked = Signal(str)
    rightClicked = Signal(str, QPoint)

    def __init__(
            self,
            image: QPixmap,
            title: str,
            folder_name: str,
            date_added=None,
            base_width=149,
            base_height=213,
            parent=None
    ):
        super().__init__(parent)

        self.base_width = base_width
        self.base_height = base_height
        self.current_scale = 1.0
        self.original_image = image
        self.current_title = title
        self.folder_name = folder_name
        self.date_added = date_added

        # Основной вертикальный макет
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(2)
        self.layout.setContentsMargins(0, 0, 0, 0)

        # Виджет изображения
        self.image_label = QLabel()
        self.image_label.setScaledContents(True)
        self.layout.addWidget(self.image_label, alignment=Qt.AlignCenter)

        # Виджет заголовка (строго две строки)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("color: white; font-size: 12px; padding: 0 3px;")
        self.title_label.setAlignment(Qt.AlignCenter)
        # Мы вручную управляем переносом в setTitleText
        self.title_label.setWordWrap(False)
        self.layout.addWidget(self.title_label)

        # Инициализация
        self.updateTileSize(1.0)
        self.setTitleText(title)

    def setTitleText(self, text: str):
        """Форматирование текста заголовка с корректными переносами и сохранением букв."""
        if not text:
            self.title_label.setText("")
            return

        max_width = self.title_label.width() - 6
        if max_width <= 0:
            self.title_label.setText(text)
            return

        fm = QFontMetrics(self.title_label.font())
        ellipsis = "..."
        ellipsis_width = fm.horizontalAdvance(ellipsis)

        # Если текст помещается в одну строку
        if fm.horizontalAdvance(text) <= max_width:
            self.title_label.setText(text)
            return

        # Формирование первой строки
        first_line = ""
        words = text.split()
        word_index = 0

        # Добавляем слова в первую строку
        while word_index < len(words):
            word = words[word_index]
            test = first_line + (" " if first_line else "") + word

            # Если слово целиком помещается
            if fm.horizontalAdvance(test) <= max_width:
                first_line = test
                word_index += 1
            else:
                # Если слово не помещается целиком, пробуем перенести часть по дефису
                if not first_line:  # Если это первое слово
                    for i in range(1, len(word)):
                        partial = word[:i] + "-"
                        if fm.horizontalAdvance(partial) <= max_width:
                            first_line = partial
                        else:
                            if i > 1:
                                first_line = word[:i - 1] + "-"
                            break

                    if not first_line and len(word) > 0:  # Если даже одна буква не помещается
                        first_line = word[0]

                    # Оставшаяся часть слова для второй строки
                    if len(first_line) > 0 and first_line[-1] == '-':
                        words[word_index] = word[len(first_line) - 1:]
                    else:
                        words[word_index] = word[len(first_line):]
                else:
                    # Для не первого слова проверяем возможность переноса
                    space = " "
                    for i in range(1, len(word)):
                        test = first_line + space + word[:i] + "-"
                        if fm.horizontalAdvance(test) <= max_width:
                            continue
                        else:
                            if i > 1:
                                first_line += space + word[:i - 1] + "-"
                                words[word_index] = word[i - 1:]
                            break

                    # Если не удалось добавить часть слова с дефисом
                    if first_line and first_line[-1] != '-':
                        break

                break

        # Формирование второй строки
        remaining_text = " ".join(words[word_index:])
        second_line = ""

        # Если есть место только для многоточия, используем его
        if max_width <= ellipsis_width:
            second_line = ellipsis[:int(max_width / ellipsis_width * 3)]
        else:
            # Рассчитываем максимальную ширину для текста без многоточия
            max_text_width = max_width - ellipsis_width

            # Добавляем символы пока они помещаются с учетом многоточия
            for i, char in enumerate(remaining_text):
                if fm.horizontalAdvance(second_line + char) <= max_text_width:
                    second_line += char
                else:
                    break

            # Если второй строки нет, но есть оставшийся текст
            if not second_line and remaining_text:
                second_line = remaining_text[0]  # Хотя бы один символ

            # Добавляем многоточие только если есть непоместившийся текст
            if i < len(remaining_text) - 1:
                second_line += ellipsis

        # Объединяем строки
        display_text = first_line + "\n" + second_line
        self.title_label.setText(display_text)

    def updateTileSize(self, scale_factor: float):
        """Обновление размеров плитки и масштабирование содержимого."""
        self.current_scale = scale_factor
        w = int(self.base_width * scale_factor)
        h = int(self.base_height * scale_factor)

        # Масштабирование изображения
        scaled = self.original_image.scaled(
            w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        cropped = self.cropToSize(scaled, w, h)
        rounded = self.getRoundedPixmap(cropped, 10)
        self.image_label.setPixmap(rounded)

        # Масштабирование шрифта
        font = self.title_label.font()
        scaled_font_size = max(8, int(12 * scale_factor))
        font.setPointSize(scaled_font_size)
        self.title_label.setFont(font)

        # Установка высоты для двух строк текста
        fm = QFontMetrics(self.title_label.font())
        line_height = fm.lineSpacing()
        title_height = line_height * 2 + 4  # Две строки + небольшой отступ

        # Установка фиксированных размеров для заголовка
        self.title_label.setFixedHeight(title_height)
        self.title_label.setFixedWidth(w - 6)  # Учитываем отступы

        # Установка общего размера плитки
        self.setFixedSize(w, h + title_height + 2)

        # Обновление текста с учетом новых размеров
        self.setTitleText(self.current_title)

    def cropToSize(self, pixmap: QPixmap, target_width: int, target_height: int) -> QPixmap:
        """Обрезка изображения до целевого размера по центру."""
        if pixmap.isNull() or target_width <= 0 or target_height <= 0:
            return QPixmap(target_width, target_height)

        # Проверка на случай, если изображение меньше целевого размера
        if pixmap.width() < target_width or pixmap.height() < target_height:
            result = QPixmap(target_width, target_height)
            result.fill(Qt.transparent)

            painter = QPainter(result)
            x = max(0, (target_width - pixmap.width()) // 2)
            y = max(0, (target_height - pixmap.height()) // 2)
            painter.drawPixmap(x, y, pixmap)
            painter.end()
            return result

        # Стандартная обрезка по центру
        x_offset = (pixmap.width() - target_width) // 2
        y_offset = (pixmap.height() - target_height) // 2
        rect = pixmap.rect().adjusted(x_offset, y_offset, -x_offset, -y_offset)
        return pixmap.copy(rect)

    def getRoundedPixmap(self, pixmap: QPixmap, radius: int) -> QPixmap:
        """Создание изображения со скругленными углами."""
        if pixmap.isNull():
            return QPixmap()

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

    def mousePressEvent(self, event):
        """Обработка кликов мыши по плитке."""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.folder_name)
        elif event.button() == Qt.RightButton:
            global_pos = self.mapToGlobal(event.pos())
            self.rightClicked.emit(self.folder_name, global_pos)
        super().mousePressEvent(event)