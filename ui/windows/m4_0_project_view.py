# ui/windows/m4_0_project_view.py
import sys
import os
import json
import datetime
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QPoint, QRect, QEvent, Signal, QSize
from PySide6.QtGui import (QPainter, QPixmap, QColor, QKeySequence, QShortcut,
                           QPainterPath, QCursor)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QScrollArea, QFrame, QComboBox, QLineEdit, QMenu,
    QProgressBar, QStyleOption, QStyle, QDialog, QDialogButtonBox, QGridLayout,
    QTabBar, QStackedWidget, QSizePolicy, QSpacerItem, QMessageBox,
    QGraphicsBlurEffect
)
from ui.components.gradient_widget import GradientBackgroundWidget
# from .edit_project.edit_project_window import open_edit_project_window

##############################################################################
# Диалог добавления главы (стильное окошко без рамки, с крестиком)
##############################################################################
# Импорт валидатора:
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression


class AddChapterDialog(QDialog):
    def __init__(self, callback, existing_chapters, parent=None):
        """
        Инициализация диалога добавления главы.

        :param callback: Функция обратного вызова для передачи данных новой главы.
        :param existing_chapters: Список существующих номеров глав для проверки дубликатов.
        :param parent: Родительский виджет.
        """
        super().__init__(parent)
        self.callback = callback
        self.existing_chapters = existing_chapters
        self.drag_pos = None

        # Настройка свойств диалога
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(400, 180)  # Фиксированный размер диалога

        self._initUI()

    def _initUI(self):
        # Основной контейнер с закругленными углами
        main_widget = QWidget(self)
        main_widget.setObjectName("main_widget")
        main_widget.setStyleSheet("""
            #main_widget {
                background-color: #3E3E5F;  /* Цвет фона основного окна программы */
                border-radius: 15px;
            }
        """)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Заголовок с названием и кнопкой закрытия
        header = QWidget(main_widget)

        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)

        title = QLabel("Добавить главу", header)
        title.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignCenter)  # Центрирование заголовка
        header_layout.addWidget(title)

        # Пробел для выталкивания кнопки закрытия вправо
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        close_btn = QPushButton("✕", header)
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton {
                color: white;
                background: transparent;
                border: none;
                font-size: 16px;
            }
            QPushButton:hover {
                color: #FF5C5C;
            }
        """)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)

        main_layout.addWidget(header)

        # Область контента с полем ввода
        content = QWidget(main_widget)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 15, 20, 15)
        content_layout.setSpacing(10)

        self.input_edit = QLineEdit(content)
        self.input_edit.setPlaceholderText("Номер главы")
        self.input_edit.setObjectName("input_edit")
        self.input_edit.setStyleSheet("""
            QLineEdit#input_edit {
                background-color: #2E2E4F;  /* Цвет фона поля ввода */
                color: white;
                border: 2px solid #4E4E6F;  /* Цвет границы поля ввода */
                border-radius: 8px;
                padding: 10px 10px;  /* Сокращены горизонтальные отступы для полного отображения подсказки */
                font-size: 14px;
            }
            QLineEdit#input_edit:focus {
                border: 2px solid #7289DA;  /* Цвет границы при фокусе */
                background-color: #3E3E5F;
            }
        """)
        # Установка валидатора для разрешения только цифр и точек
        validator = QRegularExpressionValidator(QRegularExpression("^[0-9\\.]+$"), self.input_edit)
        self.input_edit.setValidator(validator)
        content_layout.addWidget(self.input_edit)

        main_layout.addWidget(content)

        # Нижний колонтитул с кнопками Отмена и Создать
        footer = QWidget(main_widget)
        footer.setObjectName("footer")

        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 10, 20, 10)

        cancel_btn = QPushButton("Отмена", footer)
        cancel_btn.setFixedHeight(35)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;  /* Прозрачный фон для кнопки Отмена */
                color: white;
                border: 2px solid #727D8C;  /* Цвет границы кнопки */
                border-radius: 8px;
                padding: 5px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #5A6A82;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        footer_layout.addWidget(cancel_btn)

        self.create_btn = QPushButton("Создать", footer)
        self.create_btn.setFixedHeight(35)
        self.create_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;  /* Цвет фона кнопки Создать */
                color: white;
                border: none;
                border-radius: 8px;
                padding: 5px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #8E2EBF;
            }
        """)
        self.create_btn.clicked.connect(self.onCreateClicked)
        footer_layout.addWidget(self.create_btn)

        main_layout.addWidget(footer)

    def showEvent(self, event):
        """
        Переопределение метода showEvent для позиционирования диалога по центру родительского окна
        и установки фокуса на поле ввода.
        """
        super().showEvent(event)
        if self.parent():
            parent_geometry = self.parent().frameGeometry()
            parent_center = parent_geometry.center()
            self_geometry = self.frameGeometry()
            self_geometry.moveCenter(parent_center)
            self.move(self_geometry.topLeft())
        self.input_edit.setFocus()  # Установка фокуса на поле ввода при открытии

    def mousePressEvent(self, event):
        """
        Обработка события нажатия мыши для реализации перетаскивания диалога.
        """
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """
        Обработка события перемещения мыши для перетаскивания диалога.
        """
        if event.buttons() & Qt.LeftButton and self.drag_pos:
            new_pos = event.globalPosition().toPoint() - self.drag_pos
            # Получение геометрии доступного экрана
            screen_geometry = self.screen().availableGeometry()
            window_geometry = self.frameGeometry()

            # Вычисление новой позиции с учётом границ экрана
            new_x = max(screen_geometry.left(), min(new_pos.x(), screen_geometry.right() - window_geometry.width()))
            new_y = max(screen_geometry.top(), min(new_pos.y(), screen_geometry.bottom() - window_geometry.height()))
            self.move(new_x, new_y)
            event.accept()
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        """
        Переопределение метода keyPressEvent для обработки нажатий клавиш Enter и Escape.
        """
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Если нажата клавиша Enter, вызвать метод создания главы
            self.onCreateClicked()
            event.accept()
        elif event.key() == Qt.Key_Escape:
            # Если нажата клавиша Escape, закрыть диалог как отмену
            self.reject()
            event.accept()
        else:
            # Для остальных клавиш вызвать стандартную обработку
            super().keyPressEvent(event)

    def onCreateClicked(self):
        """
        Обработчик нажатия на кнопку "Создать". Проверяет ввод и передаёт данные через callback.
        Также проверяет наличие главы с таким номером.
        """
        text = self.input_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "Ошибка", "Номер главы не может быть пустым.", QMessageBox.Ok)
            return

        # Проверка на существование главы с таким же номером
        if text in self.existing_chapters:
            QMessageBox.warning(self, "Ошибка", f"Глава с номером '{text}' уже существует.", QMessageBox.Ok)
            return

        data = {
            "chapter_number": text,
            "created_at": datetime.datetime.now().isoformat(),
            "stages": {
                "Загрузка": False,
                "Предобработка": False,
                "Клининг": False,
                "Перевод": False,
                "Редактирование": False,
                "Тайпсеттинг": False,
                "QC": False
            }
        }
        if self.callback:
            self.callback(data)
        self.accept()


##############################################################################
# Вспомогательные функции для обрезки/скругления изображения
##############################################################################
def cropToSize(pixmap, target_width, target_height):
    """ Обрезаем QPixmap до (target_width×target_height), по центру. """
    x_offset = max(0, (pixmap.width() - target_width) // 2)
    y_offset = max(0, (pixmap.height() - target_height) // 2)
    rect = pixmap.rect().adjusted(x_offset, y_offset, -x_offset, -y_offset)
    return pixmap.copy(rect)


def getRoundedPixmap(pixmap, radius):
    """ Скругляем углы QPixmap на radius. """
    if pixmap.isNull():
        return pixmap
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


##############################################################################
# Основное окно
##############################################################################
class ProjectDetailWindow(QWidget):  # Изменено с QMainWindow на QWidget для встраивания
    back_requested = Signal()  # Сигнал для перехода назад

    def __init__(self, project_path=None, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("project_detail_window")

        # Сохраняем словарь путей и путь к проекту
        self.paths = paths or {}
        self.project_path = project_path

        # Атрибуты для хранения ссылок на модальные окна и эффекты
        self.overlay = None
        self.upload_window = None
        self.blur_effect = None

        # Главный вертикальный layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # ---------- ШАПКА ----------
        self.initTopBar()

        # ---------- ОСНОВНОЙ КОНТЕНТ ----------
        self.initContent()

        # Инициализация текущей вкладки
        self.tab_bar.setCurrentIndex(0)
        self.onTabChangedCustom(0)

        # Логика загрузки проекта
        if not self.project_path or not os.path.isdir(self.project_path):
            self.project_path = self._findAnyProject()

        self.metadata = self._loadMetadata()
        if not self.metadata:
            self.metadata = {
                "original_title": "Без названия",
                "folder_name": "",
                "description": "",
                "links": [],
                "cover_image": "",
                "type": "",
                "language": "",
                "country": "",
                "year": "",
            }

        # Заполняем UI
        self.updateUIfromMetadata()

        # Загрузка глав
        self.all_chapters = []
        self.filtered_chapters = []
        self.ensureChaptersFolder()
        self.loadChapters()
        self.applySortAndFilter()

        # Горячая клавиша Ctrl+F
        sc = QShortcut(QKeySequence("Ctrl+F"), self)
        sc.activated.connect(self.focusSearch)

        # Устанавливаем стили для проекта
        self.setStyleSheet("""
            QWidget#project_detail_window {
                background: transparent;
            }
        """)

    # -----------------------------------------------------------------------
    # Шапка (название приложения слева + кнопка назад)
    # -----------------------------------------------------------------------
    def initTopBar(self):
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 20, 20, 20)
        top_bar.setSpacing(10)

        # Сначала добавляем название приложения
        title_label = QLabel("MangaLocalizer")
        title_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        top_bar.addWidget(title_label, 0, Qt.AlignVCenter | Qt.AlignLeft)

        top_bar.addStretch(1)

        # Кнопка "Назад" справа
        back_btn = QPushButton("← Назад")
        back_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.15);
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.25);
            }
        """)
        back_btn.clicked.connect(self.onBackClicked)
        top_bar.addWidget(back_btn, 0, Qt.AlignVCenter | Qt.AlignRight)

        self.main_layout.addLayout(top_bar)

    def onBackClicked(self):
        """Обработка нажатия кнопки 'Назад'"""
        self.back_requested.emit()

    # -----------------------------------------------------------------------
    # Основное содержимое: левая часть (изображение + инфо), правая часть
    # (название, "Описание:", текст, вкладки, и под вкладками — поиск/сорт/добавить,
    # но только для вкладки "Главы").
    # -----------------------------------------------------------------------
    def initContent(self):
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(20, 0, 20, 20)
        content_layout.setSpacing(20)

        # === ЛЕВАЯ ЧАСТЬ ===
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(320)  # Ограничиваем ширину (под изображение + текст)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # (1) Картинка (увеличим ~30%)
        self.cover_label = QLabel()
        self.cover_label.setAlignment(Qt.AlignCenter)
        left_layout.addWidget(self.cover_label, 0, Qt.AlignTop | Qt.AlignHCenter)

        # (2) Кнопка "Редактировать"
        self.edit_btn = QPushButton("Редактировать")
        self.edit_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,0.15);
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: rgba(255,255,255,0.25);
            }
        """)
        self.edit_btn.clicked.connect(self.onEditProject)
        left_layout.addWidget(self.edit_btn, 0, Qt.AlignCenter)

        # (3) Информация (слева, ограничиваем ширину)
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(5)

        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: white; font-size: 13px;")
        self.info_label.setWordWrap(True)
        info_layout.addWidget(self.info_label, 0, Qt.AlignTop)

        left_layout.addLayout(info_layout)

        left_layout.addStretch(1)

        content_layout.addWidget(self.left_panel, 0)

        # === ПРАВАЯ ЧАСТЬ ===
        self.right_panel = QWidget()
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # (1) Название
        self.project_title_label = QLabel()
        self.project_title_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        right_layout.addWidget(self.project_title_label, 0, Qt.AlignTop)

        # (2) "Описание:" + текст
        desc_layout = QVBoxLayout()
        desc_layout.setContentsMargins(0, 0, 0, 0)
        desc_layout.setSpacing(5)

        desc_header_layout = QHBoxLayout()
        desc_header_layout.setContentsMargins(0, 0, 0, 0)
        desc_header_layout.setSpacing(5)

        self.desc_title_label = QLabel("Описание:")
        self.desc_title_label.setStyleSheet("color: white; font-size: 14px; font-weight: bold;")
        desc_header_layout.addWidget(self.desc_title_label, 0, Qt.AlignTop)

        desc_layout.addLayout(desc_header_layout)

        self.desc_label = QLabel()
        self.desc_label.setStyleSheet("color: white; font-size: 14px;")
        self.desc_label.setWordWrap(True)
        self.desc_label.setMaximumHeight(40)  # Ограничение до двух строк (примерно)
        self.desc_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.desc_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        desc_layout.addWidget(self.desc_label, 0, Qt.AlignTop)

        # Добавляем кнопку для открытия/скрытия описания
        self.toggle_description_btn = QPushButton("Подробнее")
        self.toggle_description_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8E2EBF;
                border: none;
                font-size: 12px;
                padding: 0;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self.toggle_description_btn.clicked.connect(self.toggleDescriptionVisibility)
        desc_layout.addWidget(self.toggle_description_btn, 0, Qt.AlignLeft)

        right_layout.addLayout(desc_layout)

        # (3) Вкладки и сортировка в одной строке
        tabs_sort_layout = QHBoxLayout()
        tabs_sort_layout.setContentsMargins(0, 0, 0, 0)
        tabs_sort_layout.setSpacing(10)

        # Создаём QTabBar вместо QTabWidget
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.addTab("Главы")
        self.tab_bar.addTab("Статистика")
        self.tab_bar.addTab("Прочее")
        self.tab_bar.setStyleSheet("""
            QTabBar::tab {
                background: rgba(255,255,255,0.15);
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #7E1E9F;
            }
        """)

        # Добавляем sort_combo
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
        self.sort_combo.addItem("Новые → старые")
        self.sort_combo.addItem("Старые → новые")
        self.sort_combo.currentIndexChanged.connect(self.applySortAndFilter)
        self.sort_combo.setVisible(False)  # Изначально скрыт

        tabs_sort_layout.addWidget(self.tab_bar, 0, Qt.AlignLeft)
        tabs_sort_layout.addWidget(self.sort_combo, 0, Qt.AlignRight)

        right_layout.addLayout(tabs_sort_layout)

        # Создаём QStackedWidget для содержимого вкладок
        self.stacked_widget = QStackedWidget()
        right_layout.addWidget(self.stacked_widget, 1)

        # Вкладка "Главы"
        self.chapters_tab = QWidget()
        ctab_layout = QVBoxLayout(self.chapters_tab)
        ctab_layout.setContentsMargins(0, 0, 0, 0)
        ctab_layout.setSpacing(10)

        # Скролл
        self.chapters_scroll = QScrollArea()
        self.chapters_scroll.setWidgetResizable(True)
        self.chapters_scroll.setStyleSheet("background: rgba(255,255,255,0.07); border-radius:10px; border:none;")

        self.chapters_container = QWidget()
        self.chapters_container_layout = QVBoxLayout(self.chapters_container)
        self.chapters_container_layout.setContentsMargins(10, 10, 10, 10)
        self.chapters_container_layout.setSpacing(10)

        self.chapters_scroll.setWidget(self.chapters_container)

        ctab_layout.addWidget(self.chapters_scroll, 1)

        # Нижняя панель поиска и добавления главы
        self.chapters_footer = QWidget()
        footer_layout = QHBoxLayout(self.chapters_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(10)

        # Строка поиска
        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Поиск...")
        self.search_field.setMaxLength(7)  # ограничим 7 символами
        self.search_field.setFixedWidth(200)  # установим фиксированную ширину
        self.search_field.setStyleSheet("""
            QLineEdit {
                background: rgba(255,255,255,0.1);
                color: rgba(255,255,255,0.5);
                border-radius: 8px;
                padding-left: 8px;
                font-size: 14px;
            }
            QLineEdit:focus {
                background: rgba(255,255,255,0.2);
                color: white;
            }
        """)
        self.search_field.textChanged.connect(self.applySortAndFilter)
        footer_layout.addWidget(self.search_field, 0, Qt.AlignLeft)

        # Кнопка "Добавить главу"
        self.add_chap_btn = QPushButton("Добавить главу")
        self.add_chap_btn.setStyleSheet("""
            QPushButton {
                background: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #8E2EBF;
            }
        """)
        self.add_chap_btn.clicked.connect(self.onAddChapter)
        footer_layout.addWidget(self.add_chap_btn, 0, Qt.AlignRight)

        ctab_layout.addWidget(self.chapters_footer, 0, Qt.AlignRight)

        self.stacked_widget.addWidget(self.chapters_tab)

        # Вкладка "Статистика"
        self.stats_tab = QWidget()
        stats_layout = QVBoxLayout(self.stats_tab)
        lbl_stats = QLabel("Здесь может быть статистика.")
        lbl_stats.setStyleSheet("color: white; font-size: 14px;")
        stats_layout.addWidget(lbl_stats, 0, Qt.AlignTop)
        stats_layout.addStretch(1)
        self.stats_tab.setLayout(stats_layout)
        self.stacked_widget.addWidget(self.stats_tab)

        # Вкладка "Прочее"
        self.misc_tab = QWidget()
        misc_layout = QVBoxLayout(self.misc_tab)
        lbl_misc = QLabel("Прочие функции и т.д.")
        lbl_misc.setStyleSheet("color: white; font-size: 14px;")
        misc_layout.addWidget(lbl_misc, 0, Qt.AlignTop)
        misc_layout.addStretch(1)
        self.misc_tab.setLayout(misc_layout)
        self.stacked_widget.addWidget(self.misc_tab)

        self.stacked_widget.setCurrentIndex(0)  # Устанавливаем вкладку "Главы" по умолчанию

        # Обработка переключения вкладок
        self.tab_bar.currentChanged.connect(self.onTabChangedCustom)

        self.right_panel.setLayout(right_layout)
        content_layout.addWidget(self.right_panel, 1)

        self.main_layout.addLayout(content_layout, 1)

    # -----------------------------------------------------------------------
    # Показ/скрытие сортировки в зависимости от вкладки
    # -----------------------------------------------------------------------
    def onTabChangedCustom(self, index):
        # Переключаемся на соответствующую страницу в QStackedWidget
        self.stacked_widget.setCurrentIndex(index)

        # Показываем sort_combo только на вкладке "Главы" (index 0)
        if index == 0:
            self.sort_combo.setVisible(True)
            self.chapters_footer.show()
        else:
            self.sort_combo.setVisible(False)
            self.chapters_footer.hide()

    # -----------------------------------------------------------------------
    # Заполнение UI из metadata
    # -----------------------------------------------------------------------
    def updateUIfromMetadata(self):
        # Обложка
        self.setCover()

        # Информация (тип, год, ссылки)
        lines = []
        t = self.metadata.get("type", "")
        if t:
            lines.append(f"Тип: {t}")
        lan = self.metadata.get("language", "")
        if lan:
            lines.append(f"Язык: {lan}")
        c = self.metadata.get("country", "")
        if c:
            lines.append(f"Страна: {c}")
        y = self.metadata.get("year", "")
        if y:
            lines.append(f"Год: {y}")

        # Ссылки:
        links = self.metadata.get("links", [])
        if links:
            lines.append("Ссылки:")
            for link in links:
                # Добавляем схему, если её нет
                if not link.startswith(('http://', 'https://')):
                    link_with_scheme = 'http://' + link
                else:
                    link_with_scheme = link

                parsed_url = urlparse(link_with_scheme)
                domain = parsed_url.hostname or ""
                if domain.startswith("www."):
                    domain = domain[4:]

                display_text = domain if domain else link

                # Убедитесь, что каждая ссылка находится на новой строке
                lines.append(f"<a href='{link_with_scheme}'>{display_text}</a>")

        # Убедитесь, что QLabel поддерживает RichText и кликабельные ссылки
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setOpenExternalLinks(True)  # Позволяет открывать ссылки в браузере
        self.info_label.setText("<br>".join(lines))  # Используем <br> для переноса строк в HTML

        # Название
        self.project_title_label.setText(self.metadata.get("original_title", "Без названия"))

        # Описание
        self.desc_label.setText(self.metadata.get("description", ""))
        self.desc_label.setToolTip(self.metadata.get("description", ""))

    # -----------------------------------------------------------------------
    # Обрезка, скругление, масштабирование обложки
    # -----------------------------------------------------------------------
    def setCover(self):
        folder_name = self.metadata.get("folder_name", "")
        cover_file = self.metadata.get("cover_image", "")
        if folder_name and cover_file:
            path_ = os.path.join(self.project_path, cover_file)
            if os.path.isfile(path_):
                pm = QPixmap(path_)
            else:
                pm = QPixmap(273, 390)  # заглушка
                pm.fill(QColor(60, 60, 90))
        else:
            pm = QPixmap(273, 390)
            pm.fill(QColor(60, 60, 90))

        # Пропорции ~ (7:10) * 1.3 => 273x390
        scaled = pm.scaled(273, 390, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        # Обрезаем точно до 273x390
        cropped = cropToSize(scaled, 273, 390)
        # Скругляем углы
        rounded = getRoundedPixmap(cropped, 20)
        self.cover_label.setPixmap(rounded)

    # -----------------------------------------------------------------------
    # Загрузка глав
    # -----------------------------------------------------------------------
    def ensureChaptersFolder(self):
        if not self.project_path:
            return
        cpath = os.path.join(self.project_path, "chapters")
        if not os.path.isdir(cpath):
            os.makedirs(cpath, exist_ok=True)

    def loadChapters(self):
        self.all_chapters.clear()
        if not self.project_path:
            return
        chapters_path = os.path.join(self.project_path, "chapters")
        if not os.path.isdir(chapters_path):
            return

        for sub in os.listdir(chapters_path):
            sp = os.path.join(chapters_path, sub)
            if os.path.isdir(sp):
                cjson = os.path.join(sp, "chapter.json")
                if os.path.isfile(cjson):
                    try:
                        with open(cjson, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        data["_folder"] = sp
                        self.all_chapters.append(data)
                    except:
                        pass

    def applySortAndFilter(self):
        # Ensure self.sort_combo exists
        if not hasattr(self, 'sort_combo'):
            return

        idx = self.sort_combo.currentIndex()

        # 0 => "Новые → старые"
        if idx == 0:
            def keyf(c):
                cat = c.get("created_at", "")
                try:
                    return datetime.datetime.fromisoformat(cat)
                except:
                    return datetime.datetime.min

            sorted_ch = sorted(self.all_chapters, key=keyf, reverse=True)
        elif idx == 1:
            def keyf(c):
                cat = c.get("created_at", "")
                try:
                    return datetime.datetime.fromisoformat(cat)
                except:
                    return datetime.datetime.min

            sorted_ch = sorted(self.all_chapters, key=keyf)
        else:
            # Если sort_combo не виден или индекс неизвестен
            sorted_ch = self.all_chapters.copy()

        # Получаем текст из строки поиска, если она существует
        text = self.search_field.text().lower().strip() if hasattr(self, 'search_field') else ""
        if text:
            self.filtered_chapters = [c for c in sorted_ch if text in c.get("chapter_number", "").lower()]
        else:
            self.filtered_chapters = sorted_ch

        # Очищаем контейнер
        while self.chapters_container_layout.count():
            item = self.chapters_container_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Рисуем главы
        for ch in self.filtered_chapters:
            self.drawChapter(ch)

        # Добавляем stretch только один раз
        if self.chapters_container_layout.count() == 0 or not isinstance(
                self.chapters_container_layout.itemAt(self.chapters_container_layout.count() - 1), QSpacerItem):
            self.chapters_container_layout.addStretch(1)

    # -----------------------------------------------------------------------
    # Отрисовка одной главы
    # "Глава X" | Прогрессбар | Кнопка "Показать этапы" — в одной строке
    # ниже — кнопки этапов (прозрачная подложка)
    # -----------------------------------------------------------------------
    def drawChapter(self, ch_data):
        f = QFrame()
        f.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.15);
                border-radius: 8px;
            }
        """)
        flay = QVBoxLayout(f)
        flay.setContentsMargins(10, 10, 10, 10)
        flay.setSpacing(6)

        top_line = QHBoxLayout()
        top_line.setSpacing(10)

        # Кнопка "Глава X"
        chap_btn = QPushButton(f"Глава {ch_data.get('chapter_number', '')}")
        chap_btn.setStyleSheet("""
            QPushButton {
                background: #4E4E6F;
                color: white;
                border-radius: 5px;
                padding: 6px 12px;
                font-size: 14px;
            }
            QPushButton:hover {
                background: #5E5E7F;
            }
        """)
        chap_btn.clicked.connect(lambda checked, d=ch_data: self.onChapterClicked(d))
        top_line.addWidget(chap_btn, 0)

        # Прогресс
        pbar = QProgressBar()
        pbar.setFixedHeight(16)
        pbar.setStyleSheet("""
            QProgressBar {
                background: #3E3E5F;
                border-radius: 8px;
            }
            QProgressBar::chunk {
                background-color: #7E1E9F;
                border-radius: 8px;
            }
        """)
        st = ch_data.get("stages", {})
        if isinstance(st, dict):
            done = sum(1 for v in st.values() if v is True)
            total = len(st)
            val = int(done / total * 100) if total else 0
            pbar.setValue(val)
        top_line.addWidget(pbar, 1)

        # Кнопка "Показать этапы" (справа)
        toggle_btn = QPushButton("Показать этапы")
        toggle_btn.setStyleSheet("""
            QPushButton {
                background: #3E3E5F;
                color: white;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #5E5E7F;
            }
        """)
        # Выравниваем вправо
        top_line.addWidget(toggle_btn, 0, Qt.AlignRight)

        flay.addLayout(top_line)

        # Блок этапов (прозрачная подложка)
        stages_wid = QWidget()
        st_lay = QHBoxLayout(stages_wid)
        st_lay.setContentsMargins(0, 0, 0, 0)
        st_lay.setSpacing(5)

        for stage_name, stage_value in st.items():
            st_btn = QPushButton(stage_name)

            # Проверим, что за статус у этапа:
            # - True => завершён (зелёный)
            # - "partial" => частичный (оранжевый)
            # - False или любое иное => не выполнен (фиолетовый)
            if stage_value is True:
                # зелёная кнопка
                st_btn.setStyleSheet("""
                    QPushButton {
                        background: #2EA44F;  /* зелёный */
                        color: white;
                        border-radius: 5px;
                        padding: 4px 8px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background: #36CC57;
                    }
                """)
            elif stage_value == "partial":
                # оранжевая кнопка
                st_btn.setStyleSheet("""
                    QPushButton {
                        background: #CB6828;  /* оранжевый */
                        color: white;
                        border-radius: 5px;
                        padding: 4px 8px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background: #E37B31;
                    }
                """)
            else:
                # фиолетовая (как было по умолчанию)
                st_btn.setStyleSheet("""
                    QPushButton {
                        background: #4E4E6F;
                        color: white;
                        border-radius: 5px;
                        padding: 4px 8px;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        background: #5E5E7F;
                    }
                """)

            # Подключаем сигнал
            st_btn.clicked.connect(lambda checked, s=stage_name, cd=ch_data: self.onStageClicked(cd, s))
            st_lay.addWidget(st_btn)

        stages_wid.setStyleSheet("background: transparent;")  # прозрачная подложка
        stages_wid.setVisible(False)
        flay.addWidget(stages_wid)

        # Скрыть/показать
        def onToggle():
            visible = not stages_wid.isVisible()
            stages_wid.setVisible(visible)
            toggle_btn.setText("Скрыть этапы" if visible else "Показать этапы")

        toggle_btn.clicked.connect(onToggle)

        # Контекстное меню (правый клик)
        f.setContextMenuPolicy(Qt.CustomContextMenu)
        f.customContextMenuRequested.connect(lambda pos, data_=ch_data, w=f: self.onChapterContext(pos, data_, w))

        self.chapters_container_layout.addWidget(f)

    # -----------------------------------------------------------------------
    # События и функционал для работы с модальным окном UploadWindow
    # -----------------------------------------------------------------------
    def openUploadWindow(self, chapter_folder, stage_folder="Загрузка"):
        """Открывает окно загрузки изображений с эффектом размытия фона"""
        # Создаём эффект размытия
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(15)
        self.setGraphicsEffect(self.blur_effect)

        self.update()
        QApplication.processEvents()

        # Создаём оверлей
        self.overlay = QWidget(self)
        self.overlay.setGeometry(self.rect())
        self.overlay.setStyleSheet("background-color: rgba(0, 0, 0, 30);")
        self.overlay.setCursor(Qt.ForbiddenCursor)
        self.overlay.setObjectName("modal_overlay")
        self.overlay.show()

        # Создаём окно загрузки
        from ui.windows.m5_0_upload_images import UploadWindow
        self.upload_window = UploadWindow(chapter_path=chapter_folder, stage_folder=stage_folder, parent=self)

        # Центрируем окно
        self._centerChildWindow(self.upload_window)
        self.upload_window.show()
        self.upload_window.finished.connect(self._onUploadWindowFinished)

    def _centerChildWindow(self, window):
        """Центрирование дочернего окна относительно родителя"""
        if not window:
            return

        parent_width = self.width()
        parent_height = self.height()
        dialog_width = window.width()
        dialog_height = window.height()

        x = (parent_width - dialog_width) // 2
        y = (parent_height - dialog_height) // 2

        window.move(x, y)
        window.raise_()

    def _onUploadWindowFinished(self):
        """Обработчик закрытия окна загрузки"""
        # Удаляем оверлей
        if self.overlay:
            self.overlay.deleteLater()
            self.overlay = None

        # Удаляем эффект размытия
        self.setGraphicsEffect(None)
        self.blur_effect = None

        # Обновляем данные главы после загрузки
        self.loadChapters()
        self.applySortAndFilter()

        # Очищаем ссылку на окно
        self.upload_window = None

    # -----------------------------------------------------------------------
    # События
    # -----------------------------------------------------------------------
    def focusSearch(self):
        # Фокус на поле поиска
        self.search_field.setFocus()
        self.search_field.selectAll()

    def onChapterClicked(self, ch_data):
        print("Нажата глава:", ch_data.get("chapter_number"))

    def onStageClicked(self, ch_data, stage_name):
        """
        Обработчик клика на кнопку этапа.
        Если это 'Загрузка' — открываем окно UploadWindow (как было).
        Если это 'Клининг' — открываем окно CleaningWindow.
        Иначе: спрашиваем 'частично'/'завершено' (оранж/зелёный).
        """
        chapter_number = ch_data.get("chapter_number", "???")
        print(f"Нажат этап '{stage_name}' для главы {chapter_number}")

        chapter_folder = ch_data.get("_folder", "")
        if not chapter_folder or not os.path.isdir(chapter_folder):
            QMessageBox.warning(self, "Ошибка", "Не найдена папка главы.", QMessageBox.Ok)
            return

        if stage_name == "Загрузка":
            # Открываем окно загрузки с затемнением и размытием
            self.openUploadWindow(chapter_folder=chapter_folder, stage_folder="Загрузка")

            # Проверяем наличие изображений
            images_json = os.path.join(chapter_folder, "Загрузка", "images.json")
            has_images = False
            if os.path.isfile(images_json):
                try:
                    with open(images_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if data:  # если список не пуст
                        has_images = True
                except Exception as e:
                    print(f"Ошибка чтения images.json: {e}")

            # Сохраняем результат
            st = ch_data.get("stages", {})
            st[stage_name] = has_images  # True/False
            ch_data["stages"] = st

            cjson = os.path.join(chapter_folder, "chapter.json")
            try:
                with open(cjson, "w", encoding="utf-8") as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print("Не удалось сохранить stage:", e)

            self.applySortAndFilter()

        elif stage_name == "Клининг":
            # Создаем и показываем окно клининга
            from ui.windows.m8_0_cleaning_window import CleaningWindow

            # Сохраняем текущее состояние
            self._saved_ui_state = {
                'visible': self.isVisible()
            }

            # Скрываем текущий интерфейс
            self.hide()

            # Создаем и настраиваем окно клининга
            self.cleaning_window = CleaningWindow(chapter_folder=chapter_folder, paths=self.paths, parent=self.parent())

            # Размещаем окно клининга на месте прежнего интерфейса
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.cleaning_window)

            # Создаем временный контейнер для окна клининга
            self.cleaning_container = QWidget(self.parent())
            self.cleaning_container.setLayout(layout)
            self.cleaning_container.setGeometry(self.parent().rect())
            self.cleaning_container.show()

            # Подключаем сигнал закрытия
            self.cleaning_window.back_requested.connect(self._closeCleaningWindow)


        elif stage_name == "Предобработка":

            # Сохраняем текущее состояние UI

            self._saved_ui_state = {

                'visible': self.isVisible()

            }

            # Скрываем текущий интерфейс

            self.hide()

            # Создаем окно предобработки

            from .m6_0_preprocess_images import PreprocessingWindow

            self.preproc_window = PreprocessingWindow(chapter_folder=chapter_folder, paths=self.paths,
                                                      parent=self.parent())

            # Размещаем окно предобработки на месте прежнего интерфейса

            layout = QVBoxLayout()

            layout.setContentsMargins(0, 0, 0, 0)

            layout.addWidget(self.preproc_window)

            # Создаем временный контейнер

            self.preproc_container = QWidget(self.parent())

            self.preproc_container.setLayout(layout)

            self.preproc_container.setGeometry(self.parent().rect())

            self.preproc_container.show()

            # Подключаем сигнал закрытия

            self.preproc_window.back_requested.connect(self._closePreprocessingWindow)

        else:
            # Остальные этапы: запрашиваем новый статус через диалог
            msg = QMessageBox(self)
            msg.setWindowTitle("Обновить статус этапа")
            msg.setText(
                f"Этап '{stage_name}' для главы {chapter_number}\n"
                "Выберите новый статус:"
            )
            partial_btn = msg.addButton("Частично выполнен", QMessageBox.ActionRole)
            done_btn = msg.addButton("Завершён (готово)", QMessageBox.ActionRole)
            cancel_btn = msg.addButton("Отмена", QMessageBox.RejectRole)

            msg.exec()

            chosen = msg.clickedButton()
            if chosen == partial_btn:
                new_status = "partial"  # оранжевая
            elif chosen == done_btn:
                new_status = True  # зелёная
            else:
                return  # "Отмена" — не меняем

            # Сохраняем новый статус
            st = ch_data.get("stages", {})
            st[stage_name] = new_status
            ch_data["stages"] = st

            cjson = os.path.join(chapter_folder, "chapter.json")
            try:
                with open(cjson, "w", encoding="utf-8") as f:
                    json.dump(ch_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                print("Не удалось сохранить stage:", e)

            self.applySortAndFilter()

    def _closePreprocessingWindow(self):
        """Закрывает окно предобработки и возвращает исходный интерфейс"""
        # Удаляем окно предобработки и его контейнер
        if hasattr(self, 'preproc_container') and self.preproc_container:
            self.preproc_container.deleteLater()
            self.preproc_container = None

        # Восстанавливаем видимость исходного интерфейса
        if hasattr(self, '_saved_ui_state'):
            if self._saved_ui_state.get('visible', True):
                self.show()
        else:
            self.show()

        # Обновляем данные (статусы глав могли измениться)
        self.loadChapters()
        self.applySortAndFilter()
    def _closeCleaningWindow(self):
        """Закрывает окно клининга и возвращает исходный интерфейс"""
        # Удаляем окно клининга и его контейнер
        if hasattr(self, 'cleaning_container') and self.cleaning_container:
            self.cleaning_container.deleteLater()
            self.cleaning_container = None

        # Восстанавливаем видимость исходного интерфейса
        if hasattr(self, '_saved_ui_state'):
            if self._saved_ui_state.get('visible', True):
                self.show()
        else:
            self.show()

        # Обновляем данные (статусы глав могли измениться)
        self.loadChapters()
        self.applySortAndFilter()

    def onChapterContext(self, pos, ch_data, widget):
        menu = QMenu(self)
        act_del = menu.addAction("Удалить")
        act_exp = menu.addAction("Экспортировать")
        act_edt = menu.addAction("Редактировать")
        chosen = menu.exec_(widget.mapToGlobal(pos))
        if chosen == act_del:
            self.onDeleteChapter(ch_data)
        elif chosen == act_exp:
            self.onExportChapter(ch_data)
        elif chosen == act_edt:
            self.onEditChapter(ch_data)

    def onDeleteChapter(self, ch_data):
        folder_ = ch_data.get("_folder", "")
        if folder_ and os.path.isdir(folder_):
            import shutil
            try:
                shutil.rmtree(folder_)
                print("Глава удалена:", folder_)
            except:
                pass
        self.loadChapters()
        self.applySortAndFilter()

    def onExportChapter(self, ch_data):
        print("Экспорт главы:", ch_data.get("chapter_number"))

    def onEditChapter(self, ch_data):
        print("Редактирование главы:", ch_data.get("chapter_number"))

    def onEditProject(self):
        print("Нажата кнопка 'Редактировать' (информацию о произведении).")
        project_path = self.project_path
        if not os.path.exists(project_path):
            print(f"[ERROR] Путь к проекту не существует: '{project_path}'")
            return

        # Здесь будет код для открытия окна редактирования проекта
        # ...

    def onAddChapter(self):
        # Получаем список существующих номеров глав
        existing_chapter_numbers = [ch.get("chapter_number", "") for ch in self.all_chapters]
        # Открываем диалог
        dlg = AddChapterDialog(self.addNewChapter, existing_chapter_numbers, self)
        dlg.exec()

    def addNewChapter(self, data):
        """
        Добавляет новую главу в проект после проверки на дубликаты.
        """
        folder_name = self.metadata.get("folder_name", "")
        if not folder_name:
            print("Нет folder_name.")
            return
        croot = os.path.join(self.project_path, "chapters")
        if not os.path.isdir(croot):
            os.makedirs(croot, exist_ok=True)
        subf = data["chapter_number"].replace(".", "_")
        cpath = os.path.join(croot, subf)

        # Проверка на существование папки
        if os.path.exists(cpath):
            QMessageBox.warning(self, "Ошибка", f"Папка для главы '{data['chapter_number']}' уже существует.",
                                QMessageBox.Ok)
            return

        os.makedirs(cpath, exist_ok=True)

        cjson = os.path.join(cpath, "chapter.json")
        try:
            with open(cjson, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить главу: {e}", QMessageBox.Ok)
            return

        # Создаём подпапки
        for st in data["stages"].keys():
            sp = os.path.join(cpath, st)
            os.makedirs(sp, exist_ok=True)

        print("Добавлена глава:", data["chapter_number"])
        self.loadChapters()
        self.applySortAndFilter()

    # -----------------------------------------------------------------------
    # Обработка событий окна
    # -----------------------------------------------------------------------
    def moveEvent(self, event):
        """Обработка перемещения окна проекта"""
        super().moveEvent(event)
        if hasattr(self, 'upload_window') and self.upload_window and self.upload_window.isVisible():
            self._centerChildWindow(self.upload_window)

    def resizeEvent(self, event):
        """Обработка изменения размера окна"""
        super().resizeEvent(event)

        # Обновляем размеры оверлея
        if self.overlay:
            self.overlay.setGeometry(self.rect())

        # Центрируем окно загрузки
        if hasattr(self, 'upload_window') and self.upload_window and self.upload_window.isVisible():
            self._centerChildWindow(self.upload_window)

    # -----------------------------------------------------------------------
    # Вспомогательные
    # -----------------------------------------------------------------------
    def _findAnyProject(self):
        base = self.paths.get('projects', os.path.join("data", "projects"))
        if not os.path.isdir(base):
            return None
        for f in os.listdir(base):
            p = os.path.join(base, f)
            if os.path.isdir(p):
                return p
        return None

    def _loadMetadata(self):
        if not self.project_path:
            return None
        meta = os.path.join(self.project_path, "metadata.json")
        if not os.path.isfile(meta):
            return None
        try:
            with open(meta, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return None

    # -----------------------------------------------------------------------
    # Функция для открытия/скрытия описания через кнопку на правой панели
    # -----------------------------------------------------------------------
    def toggleDescriptionVisibility(self):
        if self.desc_label.maximumHeight() == 40:
            self.desc_label.setMaximumHeight(16777215)  # Убираем ограничение
            self.toggle_description_btn.setText("Скрыть")
        else:
            self.desc_label.setMaximumHeight(40)  # Ограничение до двух строк
            self.toggle_description_btn.setText("Подробнее")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = ProjectDetailWindow()
    w.show()
    sys.exit(app.exec())