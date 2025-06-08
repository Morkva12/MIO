# ui/windows/m3_0_edit_project.py

import os
import re
import json
import shutil
from datetime import datetime
from PySide6.QtWidgets import QDialog, QMessageBox

from PySide6.QtCore import Qt, QPoint, QSize, Signal, QTimer, QStringListModel
from PySide6.QtGui import QPainter, QLinearGradient, QColor, QPixmap, QPainterPath, QFontMetrics, QCursor, \
    QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QRadioButton, QButtonGroup, QMessageBox,
    QStyleOption, QStyle, QPlainTextEdit, QComboBox, QApplication, QSizePolicy,
    QCompleter, QListWidget, QListWidgetItem, QMenu, QToolTip
)

# Импортируем все необходимые классы из m2_0_create_project
from .m2_0_create_project import (
    GradientBackgroundWidget, ImagePreviewLabel, TagHighlighter,
    TagSuggestionList, TagSearchLineEdit, natural_sort_key,
    FORBIDDEN_CHARS_PATTERN, MAX_TAG_LENGTH, MIN_TAG_LENGTH
)


class EditProjectWindow(GradientBackgroundWidget):
    """Окно редактирования проекта"""
    project_updated = Signal(dict)  # Сигнал с обновленными метаданными
    finished = Signal(int)  # Сигнал о закрытии окна

    def __init__(self, project_path, projects_path=None, parent=None):
        super().__init__(parent)
        self._is_auto_positioning = False
        self.setWindowTitle("Редактирование проекта")
        self.project_path = project_path
        self.projects_path = projects_path or os.path.dirname(project_path)

        # Определяем путь к файлу тегов относительно пути проектов
        self.tags_file = os.path.join(os.path.dirname(self.projects_path), "tags.txt")

        self.desired_size = QSize(800, 800)
        self.adaptSizeToScreen()
        self.setModal(True)
        self._current_screen = self.screen()

        # Загрузка метаданных проекта
        self.metadata = self._load_project_metadata()
        self.original_folder_name = os.path.basename(self.project_path)

        # Загрузка тегов
        all_tags = self._load_tags_from_file()

        # Создание интерфейса
        self._createUI(all_tags)

        # Заполнение полей данными проекта
        self._fill_fields_from_metadata()

        # Отслеживание изменений экрана
        QApplication.instance().screenAdded.connect(self.screenUpdated)
        QApplication.instance().screenRemoved.connect(self.screenUpdated)

    def _load_project_metadata(self):
        """Загрузка метаданных проекта"""
        metadata_path = os.path.join(self.project_path, "metadata.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[edit_project] ERROR: ❌ Ошибка загрузки metadata.json: {e}")
        return {}

    def _createUI(self, all_tags):
        """Создание пользовательского интерфейса (копия из create_project с изменениями)"""
        # Верхняя панель с заголовком и кнопкой закрытия
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(0)

        left_spacer = QLabel("")
        left_spacer.setFixedWidth(50)
        top_bar_layout.addWidget(left_spacer, alignment=Qt.AlignLeft)

        self.title_label = QLabel("Редактирование произведения")
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

        # Кнопка "Сохранить изменения"
        self.save_btn = QPushButton("Сохранить изменения")
        self.save_btn.setStyleSheet(
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
        self.save_btn.clicked.connect(self.save_project)
        grid_layout.addWidget(self.save_btn, 9, 0, 1, 3, alignment=Qt.AlignCenter)

        # Сборка основного интерфейса
        main_layout = QVBoxLayout(self)
        main_layout.addLayout(top_bar_layout)
        main_layout.addLayout(grid_layout)

    def _on_image_click(self):
        """Обработчик клика по области предпросмотра изображения"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите обложку",
            "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif)"
        )
        if file_path:
            self.image_preview.set_image(file_path)
    def _fill_fields_from_metadata(self):
        """Заполнение полей данными из метаданных"""
        # Название
        self.project_name_input.setText(self.metadata.get("original_title", ""))

        # Тип
        if self.metadata.get("type") == "Вебтун":
            self.radio_webtoon.setChecked(True)
        else:
            self.radio_paper.setChecked(True)

        # Цветность
        if self.metadata.get("color") == "Полноцвет":
            self.radio_color.setChecked(True)
        else:
            self.radio_bw.setChecked(True)

        # Язык
        lang_index = self.language_combo.findText(self.metadata.get("language", "ru"))
        if lang_index >= 0:
            self.language_combo.setCurrentIndex(lang_index)

        # Страна
        country_index = self.country_combo.findText(self.metadata.get("country", ""))
        if country_index >= 0:
            self.country_combo.setCurrentIndex(country_index)

        # Год
        self.year_input.setText(self.metadata.get("year", ""))

        # Теги
        tags = self.metadata.get("tags", [])
        if tags:
            self.tags_input.setText(", ".join(tags) + ", ")

        # Описание
        self.description_input.setPlainText(self.metadata.get("description", ""))

        # Ссылки
        links = self.metadata.get("links", [])
        self.links_input.setPlainText("\n".join(links))

        # Обложка
        cover_file = self.metadata.get("cover_image", "")
        if cover_file:
            cover_path = os.path.join(self.project_path, cover_file)
            if os.path.exists(cover_path):
                self.image_preview.set_image(cover_path)

    def validate_project_name(self, text):
        """Валидация имени проекта (удаление символа @)"""
        if '@' in text:
            cursor_pos = self.project_name_input.cursorPosition()
            at_count = text[:cursor_pos].count('@')
            cleaned_text = text.replace('@', '')
            self.project_name_input.blockSignals(True)
            self.project_name_input.setText(cleaned_text)
            self.project_name_input.setCursorPosition(cursor_pos - at_count)
            self.project_name_input.blockSignals(False)
            QToolTip.showText(
                self.project_name_input.mapToGlobal(self.project_name_input.rect().bottomLeft()),
                "Символ @ запрещён в названии проекта",
                self.project_name_input
            )

    def save_project(self):
        """Сохранение изменений в проекте"""
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

        # СНАЧАЛА обрабатываем обложку (до переименования папки!)
        cover_image_filename = self.metadata.get("cover_image", "")

        # Если есть новое изображение
        if self.image_preview.image_path:
            # Определяем путь к текущей обложке
            current_cover_path = os.path.join(self.project_path, cover_image_filename) if cover_image_filename else None

            # Проверяем, изменилась ли обложка (сравниваем нормализованные пути)
            if (not current_cover_path or
                    not os.path.exists(current_cover_path) or
                    os.path.normpath(os.path.abspath(self.image_preview.image_path)) != os.path.normpath(
                        os.path.abspath(current_cover_path))):

                # Обложка изменилась
                ext = os.path.splitext(self.image_preview.image_path)[1]
                new_cover_filename = "cover" + ext
                new_cover_path = os.path.join(self.project_path, new_cover_filename)

                try:
                    # Удаляем все старые обложки
                    for f in os.listdir(self.project_path):
                        if f.startswith("cover.") and os.path.isfile(os.path.join(self.project_path, f)):
                            try:
                                os.remove(os.path.join(self.project_path, f))
                            except:
                                pass

                    # Копируем новую обложку
                    shutil.copy2(self.image_preview.image_path, new_cover_path)
                    cover_image_filename = new_cover_filename

                except Exception as e:
                    QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить обложку: {e}")
                    # Продолжаем сохранение остальных данных

        # ЗАТЕМ проверяем переименование папки
        new_project_path = os.path.join(self.projects_path, folder_name)

        if self.project_path != new_project_path:
            # Проверка, не существует ли уже папка с таким именем
            if os.path.exists(new_project_path):
                QMessageBox.warning(self, "Ошибка", f"Проект с именем '{folder_name}' уже существует!")
                return

            # Переименование папки проекта
            try:
                os.rename(self.project_path, new_project_path)
                self.project_path = new_project_path
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось переименовать папку проекта: {e}")
                return

        # Обновление метаданных
        self.metadata["original_title"] = original_project_name
        self.metadata["folder_name"] = folder_name
        self.metadata["type"] = "Вебтун" if self.radio_webtoon.isChecked() else "Листовое"
        self.metadata["color"] = "Полноцвет" if self.radio_color.isChecked() else "Ч/Б"
        self.metadata["language"] = self.language_combo.currentText()
        self.metadata["country"] = self.country_combo.currentText()
        self.metadata["year"] = self.year_input.text().strip()
        self.metadata["tags"] = self.tags_input.get_valid_tags()
        self.metadata["description"] = self.description_input.toPlainText().strip()
        self.metadata["links"] = self._parse_links(self.links_input.toPlainText())

        # Обновляем имя файла обложки в метаданных
        self.metadata["cover_image"] = cover_image_filename

        # Сохранение метаданных
        metadata_path = os.path.join(self.project_path, "metadata.json")
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить metadata.json: {e}")
            return

        # Добавление новых тегов в общий список
        self.tags_input.add_new_tags_to_global()

        # Информирование пользователя
        QMessageBox.information(self, "Готово", f"Изменения в проекте '{original_project_name}' сохранены!")

        # Отправка сигнала для обновления
        self.metadata["_new_folder_name"] = folder_name  # Передаем новое имя папки
        self.project_updated.emit(self.metadata)
        self.accept()

    def _parse_links(self, text):
        """Разбор ссылок из текста"""
        lines = []
        for line in text.splitlines():
            parts = [p.strip() for p in line.split(",") if p.strip()]
            lines.extend(parts)
        if not lines:
            lines = [p.strip() for p in text.split(",") if p.strip()]
        return lines

    # Стили (копируем из create_project)
    def _set_label_style(self, label):
        label.setStyleSheet("QLabel { color: white; font-size: 14px; }")

    def _set_lineedit_style(self, lineedit):
        lineedit.setStyleSheet(
            "QLineEdit { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; }"
            "QLineEdit:focus { border-color: #DDDDDD; }"
        )

    def _set_plaintext_style(self, textedit):
        textedit.setStyleSheet(
            "QPlainTextEdit { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; }"
            "QPlainTextEdit:focus { border-color: #DDDDDD; }"
        )

    def _set_radiobutton_style(self, radio):
        radio.setStyleSheet(
            "QRadioButton { color: white; }"
            "QRadioButton::indicator { width: 18px; height: 18px; }"
            "QRadioButton::indicator:unchecked { border: 2px solid white; border-radius: 9px; }"
            "QRadioButton::indicator:checked { background-color: #FFFFFF; border-radius: 9px; }"
        )

    def _set_combobox_style(self, combo):
        combo.setStyleSheet(
            "QComboBox { border: 2px solid #FFFFFF; border-radius: 5px; color: white; background: transparent; padding: 4px 6px; combobox-popup: 0; }"
            "QComboBox:drop-down { border: none; }"
            "QComboBox:focus { border-color: #DDDDDD; }"
            "QComboBox QAbstractItemView { background-color: #333333; color: white; selection-background-color: #555555; }"
        )

    def _load_tags_from_file(self):
        """Загрузка тегов из файла"""
        if not os.path.exists(self.tags_file):
            return []
        try:
            with open(self.tags_file, "r", encoding="utf-8") as f:
                tags = [line.strip() for line in f if line.strip()]
            filtered_tags = []
            for tag in tags:
                if '@' in tag:
                    tag = tag.replace('@', '')
                if MIN_TAG_LENGTH <= len(tag) <= MAX_TAG_LENGTH:
                    filtered_tags.append(tag)
            return sorted(filtered_tags, key=natural_sort_key)
        except Exception as e:
            print(f"[edit_project] ERROR: ❌ Ошибка загрузки тегов: {e}")
            return []

    # Методы для позиционирования окна
    def screenUpdated(self, screen):
        current_screen = self.screen()
        if self._current_screen != current_screen:
            self._current_screen = current_screen
            self.adaptSizeToScreen()
            self.centerOnParent()

    def adaptSizeToScreen(self):
        if self.parent():
            screen = self.parent().screen()
        else:
            screen = self.screen()
        screen_size = screen.availableSize()
        margin = 0.1
        max_width = screen_size.width() * (1 - margin)
        max_height = screen_size.height() * (1 - margin)
        if self.desired_size.width() > max_width or self.desired_size.height() > max_height:
            scale_factor = min(max_width / self.desired_size.width(), max_height / self.desired_size.height())
            adapted_size = QSize(int(self.desired_size.width() * scale_factor),
                                 int(self.desired_size.height() * scale_factor))
            self.resize(adapted_size)
        else:
            self.resize(self.desired_size)
        if self.isVisible():
            QTimer.singleShot(0, self.centerOnParent)

    def moveEvent(self, event):
        if not self._is_auto_positioning:
            new_screen = self.screen()
            if self._current_screen != new_screen:
                self._current_screen = new_screen
                self.adaptSizeToScreen()
        super().moveEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.centerOnParent()

    def centerOnParent(self):
        if not self.parent():
            return
        self._is_auto_positioning = True
        parent = self.parent()
        central_widget = parent.centralWidget() if hasattr(parent, 'centralWidget') else parent
        global_pos = central_widget.mapToGlobal(QPoint(0, 0))
        center_x = global_pos.x() + (central_widget.width() - self.width()) // 2
        center_y = global_pos.y() + (central_widget.height() - self.height()) // 2
        self.move(center_x, center_y)
        self.raise_()
        self.activateWindow()
        self._is_auto_positioning = False

    def close_window(self):
        self.finished.emit(QDialog.Rejected)
        self.close()

    def closeEvent(self, event):
        """Обработка закрытия окна"""
        # Очистка временных файлов если они есть
        temp_files = [f for f in os.listdir(self.project_path) if f.endswith('.tmp')]
        for temp_file in temp_files:
            try:
                os.remove(os.path.join(self.project_path, temp_file))
            except:
                pass

        self.finished.emit(QDialog.Rejected)
        super().closeEvent(event)


def open_edit_project_window(project_path, parent=None, projects_path=None):
    """Создание экземпляра окна редактирования проекта"""
    return EditProjectWindow(project_path=project_path, projects_path=projects_path, parent=parent)