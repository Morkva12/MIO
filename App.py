# app.py
import os
import sys
import json
import logging
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QObject, QEvent, QRect
from ui.windows.m1_0_main_window import MainWindow


class FileManager:
    def __init__(self, app_path=None):
        self.app_path = app_path or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Настройка логгера
        self.logger = logging.getLogger('FileManager')
        self.logger.setLevel(logging.INFO)

        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[m2_file_manager] %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        # Базовые пути
        self.paths = {
            'root': self.app_path,
            'config': None,
            'models': None,
            'modules': None,
            'ui': None,
            'utils': None,
            'models_ai': None,
            'resources': None,
            'data': None,
            'projects': None
        }

        # Загрузка конфигурации
        self.load_config()

    def load_config(self):
        """Загрузка конфигурации из файла настроек"""
        config_file = os.path.join(self.app_path, 'config', 'settings.json')

        # Создаем конфигурационный файл, если его нет
        if not os.path.exists(os.path.dirname(config_file)):
            os.makedirs(os.path.dirname(config_file))

        if not os.path.exists(config_file):
            # Создаем пустой конфиг
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump({"paths": {}}, f, indent=4)
            self.logger.info("✅ Создан конфигурационный файл")

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)

            # Обновляем пути из конфигурации
            if 'paths' in config:
                for key, path in config['paths'].items():
                    if key in self.paths:
                        self.paths[key] = path
        except Exception as e:
            self.logger.error(f"❌ Невозможно прочитать конфигурацию: {e}")

        # Инициализируем стандартные пути
        self.init_default_paths()

    def init_default_paths(self):
        """Инициализация стандартных путей"""
        default_paths = {
            'config': 'config',
            'models': 'models',
            'modules': 'modules',
            'ui': 'ui',
            'utils': 'utils',
            'models_ai': 'models_ai',
            'resources': 'resources',
            'data': 'data',
            'projects': os.path.join('data', 'projects')
        }

        for key, rel_path in default_paths.items():
            if not self.paths[key]:
                self.paths[key] = os.path.join(self.app_path, rel_path)

        # Создаем ключи для подпапок
        self._create_subpath_keys()

    def _create_subpath_keys(self):
        """Создание ключей для подпапок"""
        # UI подпапки
        self.paths['ui_components'] = os.path.join(self.paths['ui'], 'components')
        self.paths['ui_windows'] = os.path.join(self.paths['ui'], 'windows')

        # Resources подпапки
        self.paths['icons'] = os.path.join(self.paths['resources'], 'icons')
        self.paths['backgrounds'] = os.path.join(self.paths['resources'], 'backgrounds')

        # Models_ai подпапки
        self.paths['models_upscale'] = os.path.join(self.paths['models_ai'], 'upscale')
        self.paths['models_cleaner'] = os.path.join(self.paths['models_ai'], 'cleaner')
        self.paths['models_ocr'] = os.path.join(self.paths['models_ai'], 'ocr')

        # Этапы обработки (для глав)
        self.stage_paths = {
            'upload': '1_upload',
            'preprocessing': '2_preprocessing',
            'cleaning': '3_cleaning',
            'translation': '4_translation',
            'editing': '5_editing',
            'typesetting': '6_typesetting',
            'qc': '7_qc'
        }

    def get_path(self, *keys):
        """Получить полный путь по ключам"""
        if not keys:
            return self.app_path

        if keys[0] in self.paths:
            base_path = self.paths[keys[0]]
            if len(keys) == 1:
                return base_path
            else:
                return os.path.join(base_path, *keys[1:])
        else:
            return os.path.join(self.app_path, *keys)

    def get_chapter_stage_path(self, project_name, chapter_number, stage):
        """Получить путь к определенному этапу главы"""
        if stage not in self.stage_paths:
            return None

        return os.path.join(
            self.paths['projects'],
            project_name,
            'chapters',
            f'chapter_{chapter_number}',
            self.stage_paths[stage]
        )

    def ensure_dir_exists(self, *keys):
        """Проверяет наличие директории и создает её при отсутствии"""
        path = self.get_path(*keys)
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    def verify_structure(self):
        """Проверка структуры приложения"""
        self.logger.info("🔍 Проверка структуры приложения...")

        structure = {
            'config': {'settings.json', 'window_positions.json'},
            'modules': {'m0_database.py', 'm1_project_manager.py', 'm2_file_manager.py'},
            'ui': {
                'components': {'gradient_widget.py', 'tile_widget.py'},
                'windows': {
                    'm1_0_main_window.py', 'm1_1_main_helper.py', 'm2_0_create_project.py',
                    'm3_0_edit_project.py', 'm4_0_project_view.py', 'm5_0_upload_images.py',
                    'm6_0_preprocess_images.py', 'm7_0_translation.py'
                }
            },
            'utils': {'workers.py', 'logger.py'},
            'models_ai': {'upscale', 'cleaner', 'ocr'},
            'resources': {'icons', 'backgrounds'},
            'data': {'projects'}
        }

        missing_items = []

        # Проверка директорий
        for dir_key in structure.keys():
            dir_path = self.get_path(dir_key)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                missing_items.append(f"Создана директория: {dir_key}")

        # Рекурсивная проверка структуры
        def check_structure(base_key, items):
            for item in items:
                if isinstance(item, str):
                    file_path = self.get_path(base_key, item)
                    if not os.path.exists(file_path):
                        Path(file_path).touch()
                        missing_items.append(f"Создан файл: {os.path.join(base_key, item)}")
                elif isinstance(item, dict):
                    for subdir, subitems in item.items():
                        subdir_path = self.get_path(base_key, subdir)
                        if not os.path.exists(subdir_path):
                            os.makedirs(subdir_path)
                            missing_items.append(f"Создана директория: {os.path.join(base_key, subdir)}")

                        check_structure(os.path.join(base_key, subdir), subitems)

        # Проверка всей структуры
        for dir_key, items in structure.items():
            if isinstance(items, set):
                for item in items:
                    item_path = self.get_path(dir_key, item)
                    if os.path.isdir(item_path) or '.' not in item:
                        if not os.path.exists(item_path):
                            os.makedirs(item_path)
                            missing_items.append(f"Создана директория: {os.path.join(dir_key, item)}")
                    else:
                        if not os.path.exists(item_path):
                            Path(item_path).touch()
                            missing_items.append(f"Создан файл: {os.path.join(dir_key, item)}")
            else:
                check_structure(dir_key, [items])

        # Вывод результатов
        if missing_items:
            self.logger.warning("⚠️ Обнаружены и исправлены следующие проблемы:")
            for item in missing_items:
                self.logger.warning(f"  - {item}")
        else:
            self.logger.info("✅ Структура приложения в порядке")

        return len(missing_items) == 0

    def create_project_structure(self, project_name):
        """Создать структуру для нового проекта"""
        project_path = self.get_path('projects', project_name)

        if os.path.exists(project_path):
            self.logger.warning(f"⚠️ Проект {project_name} уже существует")
            return False

        # Создаем основные папки и файлы
        os.makedirs(project_path)
        os.makedirs(os.path.join(project_path, 'chapters'))

        metadata = {
            'name': project_name,
            'created_at': '',
            'chapters': []
        }

        with open(os.path.join(project_path, 'metadata.json'), 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=4)

        self.logger.info(f"✅ Создан проект: {project_name}")
        return True

    def create_chapter_structure(self, project_name, chapter_number):
        """Создать структуру для новой главы"""
        chapter_path = self.get_path('projects', project_name, 'chapters', f'chapter_{chapter_number}')

        if os.path.exists(chapter_path):
            self.logger.warning(f"⚠️ Глава {chapter_number} в проекте {project_name} уже существует")
            return False

        # Создаем папки для этапов работы
        os.makedirs(chapter_path)
        for stage_name, stage_dir in self.stage_paths.items():
            os.makedirs(os.path.join(chapter_path, stage_dir))

        # Обновляем метаданные проекта
        self._update_project_metadata(project_name, chapter_number)

        self.logger.info(f"✅ Создана глава {chapter_number} в проекте {project_name}")
        return True

    def _update_project_metadata(self, project_name, chapter_number):
        """Обновление метаданных проекта"""
        metadata_path = self.get_path('projects', project_name, 'metadata.json')
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)

                if 'chapters' not in metadata:
                    metadata['chapters'] = []

                # Добавляем информацию о новой главе
                chapter_info = {
                    'number': chapter_number,
                    'created_at': '',
                    'status': 'new'
                }

                metadata['chapters'].append(chapter_info)

                # Сохраняем обновленные метаданные
                with open(metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=4)

            except Exception as e:
                self.logger.error(f"❌ Ошибка при обновлении метаданных: {e}")

    def get_all_paths(self):
        """Получить словарь всех путей для передачи в другие модули"""
        paths_dict = self.paths.copy()

        # Добавляем пути для этапов
        paths_dict['stages'] = self.stage_paths

        return paths_dict


class WindowManager(QObject):
    def __init__(self, file_manager, enabled=True):
        super().__init__()
        self.file_manager = file_manager
        self.enabled = enabled
        self.windows = {}
        self.positions_file = self.file_manager.get_path('config', 'window_positions.json')

        # Настройка логгера
        self.logger = logging.getLogger('WindowManager')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('[m3_window_manager] %(levelname)s: %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

        self.positions = self.load_positions()

    def load_positions(self):
        """Загрузка сохраненных позиций окон"""
        if not os.path.exists(self.positions_file):
            # Создаем пустой файл с позициями
            with open(self.positions_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=4)
            return {}

        try:
            with open(self.positions_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:  # Файл пустой
                    return {}
                return json.loads(content)
        except Exception as e:
            self.logger.error(f"Ошибка при загрузке позиций окон: {e}")
            # Сбрасываем к пустому файлу при ошибке
            with open(self.positions_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, indent=4)
            return {}

    def save_positions(self):
        """Сохранение позиций окон"""
        if not self.enabled:
            return

        try:
            with open(self.positions_file, 'w', encoding='utf-8') as f:
                json.dump(self.positions, f, indent=4)
        except Exception as e:
            self.logger.error(f"Ошибка при сохранении позиций окон: {e}")

    def register_window(self, window, window_id):
        """Регистрация окна для отслеживания"""
        if not self.enabled:
            return

        self.windows[window_id] = window
        window.installEventFilter(self)

        # Устанавливаем позицию окна при открытии
        self.position_window(window, window_id)

    def position_window(self, window, window_id):
        """Позиционирование окна при открытии"""
        if window_id in self.positions:
            # Получаем сохраненную позицию
            geo = self.positions[window_id]
            x, y, width, height = geo['x'], geo['y'], geo['width'], geo['height']

            # Проверяем, что окно будет видимо на текущих мониторах
            desktop = QApplication.primaryScreen().availableGeometry()
            for screen in QApplication.screens():
                desktop = desktop.united(screen.availableGeometry())

            if self.is_position_visible(x, y, width, height, desktop):
                window.setGeometry(x, y, width, height)
                return True

        # Если нет сохраненной позиции или она невалидна, центрируем окно
        self.center_window(window)
        return False

    def is_position_visible(self, x, y, width, height, desktop):
        """Проверка, что позиция окна видима на текущих мониторах"""
        window_rect = QRect(x, y, width, height)
        intersect = desktop.intersected(window_rect)

        # Окно считается видимым, если хотя бы 30% его площади видно
        visible_area = intersect.width() * intersect.height()
        window_area = width * height

        return visible_area >= window_area * 0.3

    def center_window(self, window):
        """Центрирование окна на экране"""
        screen = QApplication.primaryScreen().availableGeometry()

        # Окно занимает 70% экрана
        width = int(screen.width() * 0.7)
        height = int(screen.height() * 0.7)

        # Центрируем окно
        x = (screen.width() - width) // 2
        y = (screen.height() - height) // 2

        window.setGeometry(x, y, width, height)

    def eventFilter(self, obj, event):
        """Фильтр событий для отслеживания перемещения окон"""
        if not self.enabled:
            return super().eventFilter(obj, event)

        # Отслеживаем только окна, которые мы зарегистрировали
        window_id = None
        for wid, window in self.windows.items():
            if window == obj:
                window_id = wid
                break

        if window_id is None:
            return super().eventFilter(obj, event)

        # Сохраняем позицию при перемещении или изменении размера
        if event.type() == QEvent.Move or event.type() == QEvent.Resize:
            if isinstance(obj, QMainWindow):
                geometry = obj.geometry()
                self.positions[window_id] = {
                    'x': geometry.x(),
                    'y': geometry.y(),
                    'width': geometry.width(),
                    'height': geometry.height()
                }
                self.save_positions()

        return super().eventFilter(obj, event)

    def set_enabled(self, enabled):
        """Включение/выключение отслеживания позиций окон"""
        self.enabled = enabled


def setup_logger():
    """Настройка логгера приложения"""
    logger = logging.getLogger('MangaLocalizer')
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('[app] %(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)

    logger.addHandler(console_handler)
    return logger


def main():
    # Инициализация логгера
    logger = setup_logger()
    logger.info("🚀 Запуск MangaLocalizer...")

    # Создание приложения Qt
    app = QApplication(sys.argv)

    # Получение пути к корневой директории
    app_path = os.path.dirname(os.path.abspath(__file__))

    # Инициализация менеджера файлов
    file_manager = FileManager(app_path)
    file_manager.verify_structure()

    # Получение путей для других модулей
    paths = file_manager.get_all_paths()

    # Инициализация менеджера окон
    window_manager = WindowManager(file_manager, enabled=True)

    # Создание главного окна
    main_window = MainWindow(paths, window_manager=window_manager)
    main_window.show()

    logger.info("✅ Приложение готово к работе")

    # Запуск основного цикла приложения
    sys.exit(app.exec())


if __name__ == "__main__":
    main()