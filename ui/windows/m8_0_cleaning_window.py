# -*- coding: utf-8 -*-
import os, json, numpy as np, cv2, logging, time
from PIL import Image
from threading import Thread
from PySide6.QtCore import Qt, Signal, QEvent, QTimer, QPointF, QRectF, QThread, QBuffer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSplitter, QGroupBox, QRadioButton, QButtonGroup,
                               QMessageBox, QCheckBox, QSlider, QProgressBar, QApplication,
                               QComboBox, QGridLayout,QGraphicsScene)
from PySide6.QtGui import QPixmap, QColor, QPainter, QImage

from ui.windows.m8_1_graphics_items import SelectionEvent, EditableMask, EditablePolygonMask, BrushStroke
from ui.windows.m8_2_image_viewer import CustomImageViewer, DrawingMode, PageChangeSignal
from ui.windows.m8_3_utils import DetectionManager, enable_cuda_cudnn
import io

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Константы
THUMB_W, THUMB_H = 150, 300
MIN_BRUSH, MAX_BRUSH, DEF_BRUSH = 1, 50, 5
PROG_HIDE_MS = 5000
MASK_EXP_DEF = 10

class ImgCleanWorker(QThread):
    """Воркер для очистки изображения"""
    prog = Signal(int, int, str)
    err = Signal(str)
    done_img = Signal(int, QPixmap)  # page_idx, result_pixmap

    def __init__(self, lama, pixmap, mask, page_idx):
        super().__init__()
        self.lama = lama
        self.pixmap = pixmap
        self.mask = mask
        self.page_idx = page_idx

    def run(self):
        try:
            self.prog.emit(0, 1, f"Подготовка очистки страницы {self.page_idx + 1}...")

            # Проверка маски
            mask_px = np.sum(self.mask > 0)
            if mask_px == 0:
                self.err.emit(f"Ошибка: Маска пуста для страницы {self.page_idx + 1}")
                return

            logger.info(f"Запуск очистки для страницы {self.page_idx + 1}, маска содержит {mask_px} пикселей")

            # Конвертация QPixmap в PIL
            try:
                qimg = self.pixmap.toImage()
                buffer = QBuffer()
                buffer.open(QBuffer.ReadWrite)
                qimg.save(buffer, "PNG")
                buffer.seek(0)
                img = Image.open(io.BytesIO(buffer.data())).convert('RGB')
            except Exception as e:
                self.err.emit(f"Ошибка конвертации QPixmap в PIL Image: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                return

            img_w, img_h = img.size

            # Подготовка маски
            mask_arr = self.mask.copy()
            mask_arr[mask_arr > 0] = 255

            # Проверка размеров
            if mask_arr.shape[0] != img_h or mask_arr.shape[1] != img_w:
                logger.warning(f"Коррекция размеров маски: {mask_arr.shape} -> {(img_h, img_w)}")
                fixed_mask = np.zeros((img_h, img_w), dtype=np.uint8)
                h = min(mask_arr.shape[0], img_h)
                w = min(mask_arr.shape[1], img_w)
                fixed_mask[:h, :w] = mask_arr[:h, :w]
                mask_arr = fixed_mask

            # Конвертация в PIL
            mask_pil = Image.fromarray(mask_arr).convert('L')

            self.prog.emit(0, 1, f"Запуск LaMa инпейнтинга для страницы {self.page_idx + 1}...")

            # Очистка с LaMa
            result = self.lama(img, mask_pil)

            # QPixmap из результата
            try:
                buffer = io.BytesIO()
                result.save(buffer, format="PNG")
                buffer.seek(0)
                bytes_data = buffer.getvalue()

                res_pixmap = QPixmap()
                res_pixmap.loadFromData(bytes_data)

                if res_pixmap.isNull():
                    raise ValueError("Не удалось создать QPixmap из результата")
            except Exception as e:
                self.err.emit(f"Ошибка создания превью: {str(e)}")
                return

            self.prog.emit(1, 1, "Очистка успешно завершена")
            self.done_img.emit(self.page_idx, res_pixmap)

        except Exception as e:
            logger.error(f"Ошибка очистки изображения {self.page_idx}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.err.emit(f"Ошибка очистки: {str(e)}")


class UpdateProgEvent(QEvent):
    Type = QEvent.Type(QEvent.User + 100)

    def __init__(self, prog_bar, val, total, msg=""):
        super().__init__(UpdateProgEvent.Type)
        self.prog_bar = prog_bar
        self.val = val
        self.total = total
        self.msg = msg


class CleaningWindow(QWidget):
    back_requested = Signal()

    def __init__(self, chapter_folder, paths=None, parent=None):
        super().__init__(parent)
        self.setObjectName("cleaning_window")
        self.chapter_folder = chapter_folder

        # Состояние
        self.proc = False
        self.curr_op = None
        self.clean_workers = []
        self.det_canc = False
        self.segm_canc = False

        # Буфер для истории (1->2->3->циклично)
        self.circ_buf = {}
        self.img_status = {}

        # Проверка AI
        try:
            self.ai_avail = True
            try:
                from simple_lama_inpainting import SimpleLama
                self.lama = SimpleLama()
                self.inpaint_avail = True
                logger.info("SimpleLama успешно инициализирован")
            except ImportError:
                self.inpaint_avail = False
                logger.info("SimpleLama не установлена")
        except ImportError:
            self.ai_avail = False
            logger.warning("OpenCV или NumPy не установлены")

        self.paths = paths or {}

        # Пути
        self.chapter_paths = {
            "cleaning_folder": os.path.join(chapter_folder, "Клининг"),
            "enhanced_folder": os.path.join(chapter_folder, "Предобработка"),
            "originals_folder": os.path.join(chapter_folder, "Загрузка", "originals"),
            "upload_folder": os.path.join(chapter_folder, "Загрузка")
        }

        os.makedirs(self.chapter_paths["cleaning_folder"], exist_ok=True)

        # Модели
        self.ai_models = {"detect": "", "segm": ""}
        self._setup_model_paths()

        self.status_json = "cleaning.json"
        self.setWindowTitle("Клининг")

        self.comb_masks = {}

        # Классы детекции и сегментации
        self.detect_cls = {
            'Text': {'threshold': 0.5, 'enabled': True, 'color': (255, 0, 0)},
            'Sound': {'threshold': 0.5, 'enabled': False, 'color': (0, 255, 0)},
            'FonText': {'threshold': 0.5, 'enabled': False, 'color': (0, 0, 255)},
            'ComplexText': {'threshold': 0.5, 'enabled': False, 'color': (255, 128, 0)},
        }

        self.segm_cls = {
            'TextSegm': {'threshold': 0.5, 'enabled': True, 'color': (255, 255, 0)},
            'Sound': {'threshold': 0.5, 'enabled': False, 'color': (255, 0, 255)},
            'FonText': {'threshold': 0.5, 'enabled': False, 'color': (0, 255, 255)},
            'Text': {'threshold': 0.5, 'enabled': True, 'color': (0, 128, 255)},
        }

        # Настройки рисования
        self.curr_draw_mode = DrawingMode.NONE
        self.curr_draw_color = (255, 0, 0)
        self.curr_draw_size = DEF_BRUSH

        # Расширение масок
        self.saved_detect_exp = MASK_EXP_DEF
        self.saved_segm_exp = MASK_EXP_DEF

        # Определение источника
        self.img_paths = self._decide_img_source()

        if not self.img_paths:
            # Если пользователь отменил выбор, планируем закрытие
            QTimer.singleShot(100, self._handleCancelledInit)
            return

        # UI
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # UI компоненты
        self._init_top_bar()

        self.page_change_sig = PageChangeSignal()
        self.page_change_sig.page_changed.connect(self.upd_active_thumb)

        # Просмотрщик
        self.viewer = CustomImageViewer(self.img_paths, parent=self)
        self.viewer.page_loading_status = {i: False for i in range(len(self.img_paths))}

        # Панель превью
        self.preview_scroll = self._create_preview_panel()
        self._init_content()
        self.upd_active_thumb(self.viewer.cur_page)

        self.prog_timers = {}

        # Загрузка статуса
        self.load_status()

        # Стили
        self.setStyleSheet("QWidget#cleaning_window {background: transparent;}")

        # Горячие клавиши
        self.shortcut_timer = QTimer(self)
        self.shortcut_timer.setSingleShot(True)
        self.shortcut_timer.timeout.connect(self.process_shortcuts)
        self.shortcut_timer.start(100)

        # Сигналы
        self.viewer.mask_updated.connect(self.upd_thumb_no_mask)
        self.detect_mgr = DetectionManager(self.ai_models, self.detect_cls, self.segm_cls)
        self.detect_mgr.set_viewer(self.viewer)
        self.sync_detection_manager()
        for cls_name, info in self.detect_cls.items():
            if cls_name == 'Text':
                self.cb_text.setChecked(info['enabled'])
            elif cls_name == 'ComplexText':
                self.cb_complex_text.setChecked(info['enabled'])
            elif cls_name == 'Sound':
                self.cb_sound.setChecked(info['enabled'])
            elif cls_name == 'FonText':
                self.cb_fontext.setChecked(info['enabled'])

        # Рисование в реальном времени
        if hasattr(self.viewer, 'scene_'):
            self.viewer.scene_.update = self.on_scene_update

        # Загрузка изображений
        QTimer.singleShot(500, self.force_load_imgs)

    def _handleCancelledInit(self):
        """Обработчик отмены инициализации"""
        self.back_requested.emit()
    def sync_det_classes(self):
        """Синхронизирует словари классов"""
        if hasattr(self, 'detect_mgr'):
            sound_en = self.cb_sound.isChecked()
            complex_en = self.cb_complex_text.isChecked()
            fontext_en = self.cb_fontext.isChecked()
            text_en = self.cb_text.isChecked()

            # Обновляем локальный словарь
            self.detect_cls['Sound']['enabled'] = sound_en
            self.detect_cls['ComplexText']['enabled'] = complex_en
            self.detect_cls['FonText']['enabled'] = fontext_en
            self.detect_cls['Text']['enabled'] = text_en

            # Обновляем словарь в менеджере детекции
            self.detect_mgr.detect_classes['Sound']['enabled'] = sound_en
            self.detect_mgr.detect_classes['ComplexText']['enabled'] = complex_en
            self.detect_mgr.detect_classes['FonText']['enabled'] = fontext_en
            self.detect_mgr.detect_classes['Text']['enabled'] = text_en

    @property
    def detect_progress(self):
        """Свойство для совместимости с m8_3_utils.py"""
        return self.detect_prog

    @property
    def image_paths(self):
        """Свойство для совместимости с m8_3_utils.py"""
        return self.img_paths

    def _restore_detect_button(self):
        """Метод для совместимости с m8_3_utils.py"""
        return self._restore_detect_btn()

    def unlock_interface(self):
        """Метод для совместимости с m8_3_utils.py"""
        return self.unlock_ui()

    def on_scene_update(self):
        """Обработчик обновления сцены"""
        # Оригинальный метод
        QGraphicsScene.update(self.viewer.scene_)

        # При рисовании обновляем статус
        if hasattr(self.viewer, 'drawing') and self.viewer.drawing:
            # Таймер для ограничения частоты
            if not hasattr(self, 'thumb_update_timer'):
                self.thumb_update_timer = QTimer()
                self.thumb_update_timer.setSingleShot(True)
                self.thumb_update_timer.timeout.connect(
                    lambda: self.upd_thumb_no_mask(self.viewer.cur_page))

            # Запускаем таймер если не активен
            if not self.thumb_update_timer.isActive():
                self.thumb_update_timer.start(200)

            # Устанавливаем статус
            if self.viewer.cur_page in self.img_status:
                self.img_status[self.viewer.cur_page] = 'modified'
                self.upd_thumb_status(self.viewer.cur_page)

    def save_to_buf(self, page_idx):
        """Сохраняет текущее состояние изображения в буфер"""
        if page_idx not in self.circ_buf:
            self.circ_buf[page_idx] = {
                0: None,  # Бэкап из другого этапа
                1: None,  # Оригинал (сохраняется)
                2: None,  # После первой операции
                3: None  # После второй операции
            }

        # Если есть состояние 3, оно становится состоянием 2
        if self.circ_buf[page_idx][3] is not None:
            self.circ_buf[page_idx][2] = self.circ_buf[page_idx][3].copy()

        # Текущее состояние становится состоянием 3
        if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
            self.circ_buf[page_idx][3] = self.viewer.pixmaps[page_idx].copy()

            # Обновляем статус
            self.img_status[page_idx] = 'modified'
            self.upd_thumb_status(page_idx)

    def is_valid_mask(self, mask):
        """Проверяет валидность маски для инпейнтинга"""
        if mask is None:
            return False
        if not isinstance(mask, np.ndarray):
            return False
        if mask.size == 0 or mask.ndim != 2:
            return False
        if np.sum(mask > 0) == 0:
            return False
        return True

    def _setup_model_paths(self):
        """Настройка путей к моделям AI"""
        if 'models_ai' in self.paths:
            if isinstance(self.paths['models_ai'], dict):
                if 'cleaner' in self.paths['models_ai']:
                    if isinstance(self.paths['models_ai']['cleaner'], dict):
                        if 'detect' in self.paths['models_ai']['cleaner']:
                            self.ai_models["detect"] = self.paths['models_ai']['cleaner']['detect']
                        if 'segm' in self.paths['models_ai']['cleaner']:
                            self.ai_models["segm"] = self.paths['models_ai']['cleaner']['segm']
                    else:
                        cleaner_dir = self.paths['models_ai']['cleaner']
                        if isinstance(cleaner_dir, str) and os.path.isdir(cleaner_dir):
                            detect_path = os.path.join(cleaner_dir, "detect.pt")
                            segm_path = os.path.join(cleaner_dir, "segm.pt")
                            if os.path.exists(detect_path):
                                self.ai_models["detect"] = detect_path
                            if os.path.exists(segm_path):
                                self.ai_models["segm"] = segm_path
            else:
                models_dir = self.paths['models_ai']
                if isinstance(models_dir, str) and os.path.isdir(models_dir):
                    cleaner_dir = os.path.join(models_dir, "cleaner")
                    if os.path.isdir(cleaner_dir):
                        detect_path = os.path.join(cleaner_dir, "detect.pt")
                        segm_path = os.path.join(cleaner_dir, "segm.pt")
                        if os.path.exists(detect_path):
                            self.ai_models["detect"] = detect_path
                        if os.path.exists(segm_path):
                            self.ai_models["segm"] = segm_path

        if 'models_cleaner' in self.paths:
            cleaner_dir = self.paths['models_cleaner']
            if isinstance(cleaner_dir, str) and os.path.isdir(cleaner_dir):
                detect_path = os.path.join(cleaner_dir, "detect.pt")
                segm_path = os.path.join(cleaner_dir, "segm.pt")
                if os.path.exists(detect_path):
                    self.ai_models["detect"] = detect_path
                if os.path.exists(segm_path):
                    self.ai_models["segm"] = segm_path

    def _upd_prog_bar(self, prog_bar, val, total, msg=""):
        """Обновляет прогресс-бар"""
        prog_bar.setRange(0, total)
        prog_bar.setValue(val)
        if msg:
            prog_bar.setFormat(msg)
        prog_bar.setVisible(True)
        QApplication.processEvents()

    def _decide_img_source(self):
        """Определение источника изображений с диалогом выбора"""
        cleaning_folder = self.chapter_paths["cleaning_folder"]

        # Проверяем наличие сохраненной конфигурации
        config_path = os.path.join(cleaning_folder, "image_source_config.json")

        # Если в папке клининг уже есть изображения - используем их
        existing_cleaning_images = self._get_imgs_from_folder(cleaning_folder)
        if existing_cleaning_images:
            logger.info(f"Найдено {len(existing_cleaning_images)} изображений в папке Клининг")
            return existing_cleaning_images

        # Проверяем доступные источники
        sources = {}

        # Предобработка/Save
        save_folder = os.path.join(self.chapter_paths["enhanced_folder"], "Save")
        if os.path.exists(save_folder):
            save_images = self._get_imgs_from_folder(save_folder)
            if save_images:
                sources["preprocess_save"] = {
                    "path": save_folder,
                    "images": save_images,
                    "name": "Предобработка (Save)",
                    "count": len(save_images)
                }

        # Загрузка
        upload_images = self._get_imgs_from_folder(self.chapter_paths["upload_folder"])
        if upload_images:
            sources["upload"] = {
                "path": self.chapter_paths["upload_folder"],
                "images": upload_images,
                "name": "Загрузка",
                "count": len(upload_images)
            }

        if not sources:
            QMessageBox.critical(self, "Ошибка", "Не найдены изображения для обработки")
            return []

        # Если есть сохраненная конфигурация
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    saved_source = config.get("source")
                    if saved_source in sources:
                        # Копируем изображения в папку клининг
                        self._copy_images_to_cleaning(sources[saved_source]["images"])
                        return self._get_imgs_from_folder(cleaning_folder)
            except:
                pass

        # Показываем диалог выбора источника
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника изображений")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник изображений для клининга:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()
        selected_source = None

        for key, source_info in sources.items():
            radio = QRadioButton(f"{source_info['name']} ({source_info['count']} изображений)")
            radio.setStyleSheet("margin: 10px 0;")
            button_group.addButton(radio)
            radio.toggled.connect(lambda checked, k=key: setattr(dialog, 'selected_source', k if checked else None))
            layout.addWidget(radio)

            # Выбираем первый по умолчанию
            if selected_source is None:
                radio.setChecked(True)
                dialog.selected_source = key

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        ok_btn.clicked.connect(dialog.accept)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #777;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if dialog.exec() == QDialog.Accepted and hasattr(dialog, 'selected_source'):
            selected = dialog.selected_source

            # Сохраняем выбор
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({"source": selected}, f, ensure_ascii=False, indent=4)

            # Копируем изображения в папку клининг
            self._copy_images_to_cleaning(sources[selected]["images"])

            return self._get_imgs_from_folder(cleaning_folder)

        return []

    def _get_imgs_from_folder(self, folder):
        """Получение списка изображений из папки"""
        if not os.path.isdir(folder):
            return []
        imgs = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff'))]
        return imgs

    def _copy_images_to_cleaning(self, source_images):
        """Копирует изображения в папку клининг с сохранением имен"""
        cleaning_folder = self.chapter_paths["cleaning_folder"]

        for i, src_path in enumerate(source_images):
            try:
                # Сохраняем оригинальное имя файла
                filename = os.path.basename(src_path)
                dst_path = os.path.join(cleaning_folder, filename)

                # Если файл уже существует, добавляем номер
                if os.path.exists(dst_path):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(dst_path):
                        dst_path = os.path.join(cleaning_folder, f"{base}_{counter}{ext}")
                        counter += 1

                import shutil
                shutil.copy2(src_path, dst_path)
                logger.info(f"Скопирован файл: {filename}")

            except Exception as e:
                logger.error(f"Ошибка копирования {src_path}: {str(e)}")

    def check_sync_needed(self):
        """Проверяет необходимость синхронизации"""
        config_path = os.path.join(self.chapter_paths["cleaning_folder"], "image_source_config.json")

        if not os.path.exists(config_path):
            return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                source_type = config.get("source")
        except:
            return

        # Проверяем только если изначально брали из загрузки
        if source_type == "upload":
            # Проверяем появились ли файлы в предобработке
            save_folder = os.path.join(self.chapter_paths["enhanced_folder"], "Save")
            if os.path.exists(save_folder):
                save_images = self._get_imgs_from_folder(save_folder)
                if save_images and not hasattr(self, '_sync_offered'):
                    self._sync_offered = True
                    self._show_sync_dialog(save_images)

    def _show_sync_dialog(self, new_images):
        """Показывает диалог синхронизации"""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Обнаружены новые изображения")
        dialog.setModal(True)
        dialog.setFixedWidth(500)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("В папке предобработки появились новые изображения.\nВыберите действие:")
        label.setStyleSheet("font-size: 14px; margin-bottom: 15px;")
        layout.addWidget(label)

        button_group = QButtonGroup()

        # Вариант 1: Обновить
        update_radio = QRadioButton("Обновить изображения из предобработки")
        update_radio.setChecked(True)
        button_group.addButton(update_radio, 1)
        layout.addWidget(update_radio)

        update_desc = QLabel("Все текущие изображения будут заменены новыми из предобработки")
        update_desc.setStyleSheet("color: #888; margin-left: 25px; margin-bottom: 10px;")
        layout.addWidget(update_desc)

        # Вариант 2: Игнорировать
        ignore_radio = QRadioButton("Продолжить с текущими изображениями")
        button_group.addButton(ignore_radio, 2)
        layout.addWidget(ignore_radio)

        ignore_desc = QLabel("Новые изображения будут проигнорированы")
        ignore_desc.setStyleSheet("color: #888; margin-left: 25px; margin-bottom: 10px;")
        layout.addWidget(ignore_desc)

        # Кнопки
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_btn = QPushButton("Применить")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        ok_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if dialog.exec() == QDialog.Accepted:
            if button_group.checkedId() == 1:
                # Обновляем изображения
                self._update_images_from_source(new_images)

    def _update_images_from_source(self, new_images):
        """Обновляет изображения из нового источника"""
        cleaning_folder = self.chapter_paths["cleaning_folder"]

        # Очищаем папку клининг
        for file in os.listdir(cleaning_folder):
            if file.endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                try:
                    os.remove(os.path.join(cleaning_folder, file))
                except:
                    pass

        # Копируем новые изображения
        self._copy_images_to_cleaning(new_images)

        # Перезагружаем изображения
        self.img_paths = self._get_imgs_from_folder(cleaning_folder)
        self.force_load_imgs()

    def _init_top_bar(self):
        """Инициализация верхней панели"""
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(20, 10, 20, 10)
        top_bar.setSpacing(10)

        title = QLabel("MangaLocalizer")
        title.setStyleSheet("color:white;font-size:24px;font-weight:bold;")
        top_bar.addWidget(title, 0, Qt.AlignVCenter | Qt.AlignLeft)

        top_bar.addStretch(1)

        close_btn = QPushButton("Назад")
        close_btn.setStyleSheet(
            "QPushButton{background-color:#4E4E6F;color:white;border-radius:8px;"
            "padding:6px 12px;font-size:14px;}QPushButton:hover{background-color:#6E6E9F;}")
        close_btn.clicked.connect(self.on_back_clicked)
        top_bar.addWidget(close_btn, 0, Qt.AlignRight)

        self.main_layout.addLayout(top_bar)

    def on_back_clicked(self):
        """Обработка кнопки Назад"""
        self.back_requested.emit()

    def _init_content(self):
        """Инициализация основного содержимого"""
        c_layout = QHBoxLayout()
        c_layout.setContentsMargins(20, 0, 20, 20)
        c_layout.setSpacing(20)
        c_layout.addWidget(self.preview_scroll, stretch=1)

        v_layout = QVBoxLayout()
        v_layout.setContentsMargins(0, 0, 0, 0)
        v_layout.setSpacing(5)
        v_layout.addWidget(self.viewer, stretch=1)

        self.viewer.operation_started.connect(self._on_op_started)
        self.viewer.operation_finished.connect(self._on_op_finished)

        w = QWidget()
        w.setLayout(v_layout)
        c_layout.addWidget(w, stretch=4)

        r_panel = self._create_right_panel()
        c_layout.addWidget(r_panel, stretch=0)

        self.main_layout.addLayout(c_layout, stretch=1)

    def _on_op_started(self, message):
        """Обработка начала операции"""
        pass

    def _on_op_finished(self):
        """Обработка завершения операции"""
        pass

    def _create_preview_panel(self):
        """Создание панели превью"""
        sp = QSplitter(Qt.Horizontal)
        sp.setStyleSheet("background-color:#333;")
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setMaximumWidth(250)
        sa.setStyleSheet("QScrollArea{background-color:#333;"
                         "border-radius:8px;border-top-left-radius:0px;}")
        cont = QWidget()
        c_lay = QVBoxLayout(cont)
        c_lay.setContentsMargins(5, 5, 5, 0)
        c_lay.setSpacing(5)
        self.thumb_labels, self.idx_labels = [], []

        tw = THUMB_W
        th = tw * 2
        for i, path in enumerate(self.img_paths):
            thumb_c = QWidget()
            thumb_lay = QVBoxLayout(thumb_c)
            thumb_lay.setContentsMargins(0, 0, 0, 0)
            thumb_lay.setSpacing(0)
            lbl = QLabel()
            pm = QPixmap(path)
            if not pm.isNull():
                scl = pm.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                lbl.setPixmap(scl)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("QLabel{background-color:#222;border:2px solid transparent;"
                              "border-top-left-radius:8px;border-top-right-radius:8px;}")
            idx = QLabel(str(i + 1))
            idx.setAlignment(Qt.AlignCenter)
            idx.setStyleSheet("QLabel{color:white;background-color:#222;font-size:14px;"
                              "font-weight:bold;border:2px solid transparent;"
                              "border-bottom-left-radius:8px;border-bottom-right-radius:8px;}")
            thumb_lay.addWidget(lbl)
            thumb_lay.addWidget(idx)
            lbl.mousePressEvent = self._make_preview_click_handler(i)
            self.thumb_labels.append(lbl)
            self.idx_labels.append(idx)
            c_lay.addWidget(thumb_c)
        c_lay.addStretch(1)
        sa.setWidget(cont)
        sp.addWidget(sa)
        return sp

    def _make_preview_click_handler(self, i):
        """Создает обработчик клика по превью"""

        def on_click(e):
            if e.button() == Qt.LeftButton:
                self.viewer.cur_page = i
                self.viewer.display_current_page()
                self.upd_active_thumb(i)
            e.accept()

        return on_click

    def upd_active_thumb(self, act_i):
        """Обновляет активную миниатюру"""
        for i, (lbl, idx) in enumerate(zip(self.thumb_labels, self.idx_labels)):
            if i == act_i:
                # Активная миниатюра - фиолетовая рамка
                lbl.setStyleSheet("QLabel{border:2px solid #7E1E9F;"
                                  "border-top-left-radius:8px;border-top-right-radius:8px;}")
                # Активный номер страницы - фиолетовая рамка, фон темный
                idx.setStyleSheet("QLabel{color:white;background-color:#222;font-size:14px;font-weight:bold;"
                                  "border:2px solid #7E1E9F;border-bottom-left-radius:8px;"
                                  "border-bottom-right-radius:8px;}")
            else:
                # Неактивная миниатюра - прозрачная рамка
                lbl.setStyleSheet("QLabel{background-color:#222;border:2px solid transparent;"
                                  "border-top-left-radius:8px;border-top-right-radius:8px;}")
                # Неактивный номер страницы - прозрачная рамка, фон темный
                idx.setStyleSheet("QLabel{color:white;background-color:#222;font-size:14px;"
                                  "font-weight:bold;border:2px solid transparent;"
                                  "border-bottom-left-radius:8px;border-bottom-right-radius:8px;}")

        # Обновляем статусы после установки рамок
        for i in range(len(self.thumb_labels)):
            self.upd_thumb_status(i)

    def _create_right_panel(self):
        """Создание правой панели управления"""
        w = QWidget()
        w.setMaximumWidth(300)
        w.setStyleSheet("QWidget{background:#5E0E7F;border-top-left-radius:15px;"
                        "border-bottom-left-radius:15px;}")
        l = QVBoxLayout(w)
        l.setContentsMargins(15, 15, 15, 15)
        l.setSpacing(10)

        # Статус обработки
        st_grp = QGroupBox("Статус обработки")
        st_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                             "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                             "left:10px;padding:0 5px;}")
        st_lay = QHBoxLayout()
        self.status_group = QButtonGroup(self)
        self.status_not_started = QRadioButton("Не начат")
        self.status_in_progress = QRadioButton("В работе")
        self.status_completed = QRadioButton("Завершен")
        for btn in (self.status_not_started, self.status_in_progress, self.status_completed):
            btn.setStyleSheet("color:white;")
            self.status_group.addButton(btn)
            st_lay.addWidget(btn)
        self.status_not_started.setChecked(True)
        self.status_group.buttonClicked.connect(self.on_status_changed)
        st_grp.setLayout(st_lay)
        l.addWidget(st_grp)

        # Инструменты рисования
        tools_grp = QGroupBox("Инструменты рисования")
        tools_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                                "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                                "left:10px;padding:0 5px;}")
        tools_lay = QGridLayout()
        tools_lay.setContentsMargins(5, 5, 5, 5)
        tools_lay.setSpacing(5)

        self.tools_btn_group = QButtonGroup(self)
        self.tools_btn_group.setExclusive(True)

        self.tool_btnNone = QPushButton("👆")
        self.tool_btnNone.setCheckable(True)
        self.tool_btnNone.setChecked(True)
        self.tool_btnNone.setToolTip("Режим просмотра (Esc)")
        self.tool_btnNone.clicked.connect(lambda: self.set_drawing_tool(DrawingMode.NONE))

        self.tool_btnBrush = QPushButton("🖌️")
        self.tool_btnBrush.setCheckable(True)
        self.tool_btnBrush.setToolTip("Кисть (правый клик для ластика)")
        self.tool_btnBrush.clicked.connect(lambda: self.set_drawing_tool(DrawingMode.BRUSH))

        self.tool_btnEraser = QPushButton("🧽")
        self.tool_btnEraser.setCheckable(True)
        self.tool_btnEraser.setToolTip("Ластик")
        self.tool_btnEraser.clicked.connect(lambda: self.set_drawing_tool(DrawingMode.ERASER))

        button_size = 40
        for btn in [self.tool_btnNone, self.tool_btnBrush, self.tool_btnEraser]:
            btn.setStyleSheet("QPushButton{background-color:#4E4E6F;color:white;border-radius:4px;"
                              "padding:6px 6px;font-size:16px;}QPushButton:hover{background-color:#6E6E9F;}"
                              "QPushButton:checked{background-color:#7E1E9F;}")
            self.tools_btn_group.addButton(btn)

        tools_lay.addWidget(self.tool_btnNone, 0, 0)
        tools_lay.addWidget(self.tool_btnBrush, 0, 1)
        tools_lay.addWidget(self.tool_btnEraser, 0, 2)

        tools_lay.setColumnStretch(0, 1)
        tools_lay.setColumnStretch(1, 1)
        tools_lay.setColumnStretch(2, 1)

        # Выбор цвета
        self.color_combo = QComboBox()
        self.color_combo.addItem("Красный", (255, 0, 0))
        self.color_combo.addItem("Зеленый", (0, 255, 0))
        self.color_combo.addItem("Синий", (0, 0, 255))
        self.color_combo.addItem("Желтый", (255, 255, 0))
        self.color_combo.addItem("Пурпурный", (255, 0, 255))
        self.color_combo.currentIndexChanged.connect(self.on_color_changed)
        self.color_combo.setStyleSheet("QComboBox{background-color:#4E4E6F;color:white;border-radius:4px;padding:4px;}")
        tools_lay.addWidget(self.color_combo, 1, 1, 1, 2)

        # Выбор размера
        size_lbl = QLabel("Размер:")
        size_lbl.setStyleSheet("color:white;")
        tools_lay.addWidget(size_lbl, 2, 0)

        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setMinimum(MIN_BRUSH)
        self.size_slider.setMaximum(MAX_BRUSH)
        self.size_slider.setValue(DEF_BRUSH)
        self.size_slider.valueChanged.connect(self.on_size_changed)
        self.size_slider.setStyleSheet("QSlider::groove:horizontal{height:4px;background:#666;}"
                                       "QSlider::handle:horizontal{width:10px;background:#fff;margin:-3px 0;}")
        tools_lay.addWidget(self.size_slider, 2, 1)

        self.size_value = QLabel(str(DEF_BRUSH))
        self.size_value.setStyleSheet("color:white; min-width: 30px; text-align: center;")
        self.size_value.setAlignment(Qt.AlignCenter)
        tools_lay.addWidget(self.size_value, 2, 2)

        tools_grp.setLayout(tools_lay)
        l.addWidget(tools_grp)

        # Секция детекции
        detection_grp = QGroupBox("Детекция")
        detection_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                                    "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                                    "left:10px;padding:0 5px;}")
        detection_lay = QVBoxLayout()

        classes_lay = QVBoxLayout()

        self.cb_text = QCheckBox("Текст")
        self.cb_text.setChecked(self.detect_cls['Text']['enabled'])
        self.cb_text.setStyleSheet("color:white;")
        self.cb_text.stateChanged.connect(lambda s: self._upd_class_enabled('detect', 'Text', s))
        classes_lay.addWidget(self.cb_text)

        # Сложный текст
        self.cb_complex_text = QCheckBox("Сложный текст")
        self.cb_complex_text.setChecked(self.detect_cls['ComplexText']['enabled'])
        self.cb_complex_text.setStyleSheet("color:white;")
        self.cb_complex_text.stateChanged.connect(lambda s: self._upd_class_enabled('detect', 'ComplexText', s))
        classes_lay.addWidget(self.cb_complex_text)

        # Звуки
        self.cb_sound = QCheckBox("Звуки")
        self.cb_sound.setChecked(self.detect_cls['Sound']['enabled'])
        self.cb_sound.setStyleSheet("color:white;")
        self.cb_sound.stateChanged.connect(lambda s: self._upd_class_enabled('detect', 'Sound', s))
        classes_lay.addWidget(self.cb_sound)

        # Фоновый текст
        self.cb_fontext = QCheckBox("Фоновый текст")
        self.cb_fontext.setChecked(self.detect_cls['FonText']['enabled'])
        self.cb_fontext.setStyleSheet("color:white;")
        self.cb_fontext.stateChanged.connect(lambda s: self._upd_class_enabled('detect', 'FonText', s))
        classes_lay.addWidget(self.cb_fontext)

        detection_lay.addLayout(classes_lay)

        # Чекбокс "Обработать все картинки" для детекции
        detect_options_lay = QHBoxLayout()
        self.detect_all_cb = QCheckBox("Обработать все картинки")
        self.detect_all_cb.setStyleSheet("color:white;")
        detect_options_lay.addWidget(self.detect_all_cb)
        detection_lay.addLayout(detect_options_lay)

        # Настройка расширения маски
        expand_lay = QHBoxLayout()
        expand_lbl = QLabel("Расширение маски:")
        expand_lbl.setStyleSheet("color:white;")
        self.expand_slider = QSlider(Qt.Horizontal)
        self.expand_slider.setMinimum(0)
        self.expand_slider.setMaximum(20)
        self.expand_slider.setValue(self.saved_detect_exp)
        self.expand_slider.valueChanged.connect(self.on_detect_exp_val_changed)
        self.expand_slider.setStyleSheet("QSlider::groove:horizontal{height:4px;background:#666;}"
                                         "QSlider::handle:horizontal{width:10px;background:#fff;margin:-3px 0;}")

        self.expand_value = QLabel(str(self.saved_detect_exp))
        self.expand_value.setStyleSheet("color:white; min-width:25px;")
        self.expand_value.setAlignment(Qt.AlignCenter)

        expand_lay.addWidget(expand_lbl)
        expand_lay.addWidget(self.expand_slider)
        expand_lay.addWidget(self.expand_value)
        detection_lay.addLayout(expand_lay)

        self.detect_prog = QProgressBar()
        self.detect_prog.setRange(0, 100)
        self.detect_prog.setValue(0)
        self.detect_prog.setVisible(False)
        self.detect_prog.setStyleSheet("""
            QProgressBar {
                border: 1px solid #999;
                border-radius: 4px;
                text-align: center;
                background-color: #444;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #2EA44F;
                border-radius: 3px;
            }
        """)
        detection_lay.addWidget(self.detect_prog)

        self.detect_btn = QPushButton("Запустить детекцию")
        self.detect_btn.setStyleSheet("QPushButton{background-color:#2EA44F;color:white;"
                                      "border-radius:8px;padding:6px 12px;"
                                      "font-size:14px;}QPushButton:hover{background-color:#36CC57;}")
        self.detect_btn.clicked.connect(self.run_detection)
        detection_lay.addWidget(self.detect_btn)

        detection_grp.setLayout(detection_lay)
        l.addWidget(detection_grp)

        # Секция сегментации
        segmentation_grp = QGroupBox("Сегментация")
        segmentation_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                                       "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                                       "left:10px;padding:0 5px;}")
        segmentation_lay = QVBoxLayout()

        segm_classes_lay = QVBoxLayout()

        self.cb_segm_text = QCheckBox("Текст")
        self.cb_segm_text.setChecked(self.segm_cls['Text']['enabled'])
        self.cb_segm_text.setStyleSheet("color:white;")
        self.cb_segm_text.stateChanged.connect(lambda state: self._upd_class_enabled('segm', 'Text', state))
        segm_classes_lay.addWidget(self.cb_segm_text)

        self.cb_textsegm = QCheckBox("Сложный текст")
        self.cb_textsegm.setChecked(self.segm_cls['TextSegm']['enabled'])
        self.cb_textsegm.setStyleSheet("color:white;")
        self.cb_textsegm.stateChanged.connect(lambda state: self._upd_class_enabled('segm', 'TextSegm', state))
        segm_classes_lay.addWidget(self.cb_textsegm)

        self.cb_segm_sound = QCheckBox("Звуки")
        self.cb_segm_sound.setChecked(self.segm_cls['Sound']['enabled'])
        self.cb_segm_sound.setStyleSheet("color:white;")
        self.cb_segm_sound.stateChanged.connect(lambda state: self._upd_class_enabled('segm', 'Sound', state))
        segm_classes_lay.addWidget(self.cb_segm_sound)

        self.cb_segm_fontext = QCheckBox("Фоновый текст")
        self.cb_segm_fontext.setChecked(self.segm_cls['FonText']['enabled'])
        self.cb_segm_fontext.setStyleSheet("color:white;")
        self.cb_segm_fontext.stateChanged.connect(lambda state: self._upd_class_enabled('segm', 'FonText', state))
        segm_classes_lay.addWidget(self.cb_segm_fontext)

        segmentation_lay.addLayout(segm_classes_lay)

        # Чекбокс "Обработать все картинки" для сегментации
        segm_options_lay = QHBoxLayout()
        self.segm_all_cb = QCheckBox("Обработать все картинки")
        self.segm_all_cb.setStyleSheet("color:white;")
        segm_options_lay.addWidget(self.segm_all_cb)
        segmentation_lay.addLayout(segm_options_lay)

        # Настройка расширения маски сегментации
        segm_expand_lay = QHBoxLayout()
        segm_expand_lbl = QLabel("Расширение маски:")
        segm_expand_lbl.setStyleSheet("color:white;")
        self.segm_expand_slider = QSlider(Qt.Horizontal)
        self.segm_expand_slider.setMinimum(0)
        self.segm_expand_slider.setMaximum(20)
        self.segm_expand_slider.setValue(self.saved_segm_exp)
        self.segm_expand_slider.valueChanged.connect(self.on_segm_exp_val_changed)
        self.segm_expand_slider.setStyleSheet("QSlider::groove:horizontal{height:4px;background:#666;}"
                                              "QSlider::handle:horizontal{width:10px;background:#fff;margin:-3px 0;}")

        self.segm_expand_value = QLabel(str(self.saved_segm_exp))
        self.segm_expand_value.setStyleSheet("color:white; min-width:25px;")
        self.segm_expand_value.setAlignment(Qt.AlignCenter)

        segm_expand_lay.addWidget(segm_expand_lbl)
        segm_expand_lay.addWidget(self.segm_expand_slider)
        segm_expand_lay.addWidget(self.segm_expand_value)
        segmentation_lay.addLayout(segm_expand_lay)

        self.segm_prog = QProgressBar()
        self.segm_prog.setRange(0, 100)
        self.segm_prog.setValue(0)
        self.segm_prog.setVisible(False)
        self.segm_prog.setStyleSheet("""
            QProgressBar {
                border: 1px solid #999;
                border-radius: 4px;
                text-align: center;
                background-color: #444;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #CB6828;
                border-radius: 3px;
            }
        """)
        segmentation_lay.addWidget(self.segm_prog)

        self.segm_btn = QPushButton("Запустить сегментацию")
        self.segm_btn.setStyleSheet("QPushButton{background-color:#CB6828;color:white;"
                                    "border-radius:8px;padding:6px 12px;"
                                    "font-size:14px;}QPushButton:hover{background-color:#E37B31;}")
        self.segm_btn.clicked.connect(self.run_segmentation)
        segmentation_lay.addWidget(self.segm_btn)

        segmentation_grp.setLayout(segmentation_lay)
        l.addWidget(segmentation_grp)

        # Секция очистки изображений
        clean_grp = QGroupBox("Очистка изображений")
        clean_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                                "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                                "left:10px;padding:0 5px;}")
        clean_lay = QVBoxLayout()

        # Чекбокс для очистки
        clean_options_lay = QHBoxLayout()
        self.clean_all_cb = QCheckBox("Обработать все картинки")
        self.clean_all_cb.setStyleSheet("color:white;")
        clean_options_lay.addWidget(self.clean_all_cb)
        clean_lay.addLayout(clean_options_lay)

        self.clean_prog = QProgressBar()
        self.clean_prog.setRange(0, 100)
        self.clean_prog.setValue(0)
        self.clean_prog.setVisible(False)
        self.clean_prog.setStyleSheet("""
            QProgressBar {
                border: 1px solid #999;
                border-radius: 4px;
                text-align: center;
                background-color: #444;
                color: white;
            }
            QProgressBar::chunk {
                background-color: #CC3333;
                border-radius: 3px;
            }
        """)
        clean_lay.addWidget(self.clean_prog)

        self.clean_btn = QPushButton("Очистить изображение")
        self.clean_btn.setStyleSheet("QPushButton{background-color:#CC3333;color:white;"
                                     "border-radius:8px;padding:6px 12px;"
                                     "font-size:14px;}QPushButton:hover{background-color:#FF4444;}")
        self.clean_btn.clicked.connect(self.clean_img)
        clean_lay.addWidget(self.clean_btn)

        clean_grp.setLayout(clean_lay)
        l.addWidget(clean_grp)

        # Секция управления изображениями
        proc_grp = QGroupBox("Управление изображениями")
        proc_grp.setStyleSheet("QGroupBox{border:2px solid white;border-radius:10px;"
                               "margin-top:10px;color:white;}QGroupBox::title{subcontrol-origin:margin;"
                               "left:10px;padding:0 5px;}")
        proc_lay = QVBoxLayout()

        # Кнопки для сохранения/восстановления
        self.reset_to_saved_btn = QPushButton("Сбросить до сохраненного")
        self.reset_to_saved_btn.setStyleSheet("QPushButton{background-color:#995500;color:white;"
                                              "border-radius:8px;padding:6px 12px;"
                                              "font-size:14px;}QPushButton:hover{background-color:#AA6600;}")
        self.reset_to_saved_btn.clicked.connect(self.reset_to_last_saved)

        self.reset_to_orig_btn = QPushButton("Сбросить до оригинала")
        self.reset_to_orig_btn.setStyleSheet("QPushButton{background-color:#886600;color:white;"
                                             "border-radius:8px;padding:6px 12px;"
                                             "font-size:14px;}QPushButton:hover{background-color:#997700;}")
        self.reset_to_orig_btn.clicked.connect(self.reset_to_orig)

        self.save_btn = QPushButton("Сохранить результат")
        self.save_btn.setStyleSheet("QPushButton{background-color:#663399;color:white;"
                                    "border-radius:8px;padding:6px 12px;"
                                    "font-size:14px;}QPushButton:hover{background-color:#7744AA;}")
        self.save_btn.clicked.connect(self.save_result)

        # Опции массовой обработки
        mass_options_lay = QHBoxLayout()
        self.mass_process_cb = QCheckBox("Обработать все изображения")
        self.mass_process_cb.setStyleSheet("color:white;")
        mass_options_lay.addWidget(self.mass_process_cb)
        proc_lay.addLayout(mass_options_lay)

        # Добавляем кнопки
        proc_lay.addWidget(self.reset_to_saved_btn)
        proc_lay.addWidget(self.reset_to_orig_btn)
        proc_lay.addWidget(self.save_btn)

        proc_grp.setLayout(proc_lay)
        l.addWidget(proc_grp)

        l.addStretch(1)

        # Кнопки навигации
        nav_lay = QHBoxLayout()

        self.prev_page_btn = QPushButton("Предыдущая")
        self.prev_page_btn.setStyleSheet("QPushButton{background-color:#7E1E9F;color:white;"
                                         "border-bottom-left-radius:8px; border-top-left-radius: 8px; "
                                         "border-top-right-radius: 0px; border-bottom-right-radius: 0px;"
                                         "padding:6px 12px;font-size:14px;}"
                                         "QPushButton:hover{background-color:#9E3EAF;}")
        self.prev_page_btn.clicked.connect(self.on_prev_page)
        nav_lay.addWidget(self.prev_page_btn)

        self.next_page_btn = QPushButton("Следующая")
        self.next_page_btn.setStyleSheet("QPushButton{background-color:#7E1E9F;color:white;"
                                         "border-bottom-right-radius:8px; border-top-right-radius: 8px; "
                                         "border-top-left-radius: 0px; border-bottom-left-radius: 0px;"
                                         "padding:6px 12px;font-size:14px;}"
                                         "QPushButton:hover{background-color:#9E3EAF;}")
        self.next_page_btn.clicked.connect(self.on_next_page)
        nav_lay.addWidget(self.next_page_btn)

        l.addLayout(nav_lay)
        return w

    def event(self, event):
        # Обработка событий выделения для детекции
        if event.type() == SelectionEvent.Type:
            self.run_area_detection(self.viewer.cur_page, event.rect)
            return True
        # Обработка событий обновления прогресса
        elif event.type() == UpdateProgEvent.Type:
            prog_bar = event.prog_bar
            prog_bar.setRange(0, event.total)
            prog_bar.setValue(event.val)
            if event.msg:
                prog_bar.setFormat(event.msg)
            prog_bar.setVisible(True)
            QApplication.processEvents()
            return True

        return super().event(event)

    def process_shortcuts(self):
        """Установка фильтра событий для горячих клавиш"""
        QApplication.instance().installEventFilter(self)

    def force_load_imgs(self):
        """Загрузка изображений с корректной инициализацией буфера"""
        logger.debug(f"Загрузка {len(self.img_paths)} изображений")

        valid_paths = [path for path in self.img_paths if os.path.exists(path)]
        if not valid_paths:
            logger.error("Не найдено доступных изображений")
            return

        self.img_paths = valid_paths

        for i, path in enumerate(self.img_paths):
            try:
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    continue

                # Определение оригинала
                orig_path = self._find_original_path(path, i)
                logger.debug(f"Страница {i}: путь={path}, оригинал={orig_path}")

                # Загрузка оригинала
                orig_pixmap = QPixmap(orig_path) if os.path.exists(orig_path) else pixmap.copy()

                # Инициализация буфера с правильными копиями
                if i not in self.circ_buf:
                    self.circ_buf[i] = {
                        0: QPixmap(orig_pixmap),  # Оригинал
                        1: QPixmap(pixmap),  # Текущее состояние
                        2: None,  # Промежуточное 1
                        3: None  # Промежуточное 2
                    }
                    logger.debug(f"Инициализация буфера для страницы {i}")

                # Установка в viewer
                self.viewer.pixmaps[i] = pixmap
                self.viewer.orig_pixmaps[i] = orig_pixmap
                self.viewer.page_loading_status[i] = True

                # Обновление остальных компонентов
                if i < len(self.thumb_labels):
                    self.thumb_labels[i].setPixmap(
                        pixmap.scaled(THUMB_W, THUMB_W * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation))

                # Инициализация слоя рисования
                if i not in self.viewer.draw_layers:
                    layer = QPixmap(pixmap.width(), pixmap.height())
                    layer.fill(Qt.transparent)
                    self.viewer.draw_layers[i] = layer

            except Exception as e:
                logger.error(f"Ошибка загрузки {path}: {str(e)}")

        # Проверка корректности текущей страницы
        if self.viewer.cur_page >= len(self.img_paths):
            self.viewer.cur_page = 0

        self.viewer.display_current_page()

    def _find_original_path(self, current_path, index):
        """Поиск пути к оригинальному изображению в Предобработка/Originals"""
        # Извлекаем базовое имя файла
        filename = os.path.basename(current_path)
        base_name = os.path.splitext(filename)[0]

        # Строим путь к оригиналу в папке Предобработка/Originals
        originals_folder = self.chapter_paths.get("originals_folder", "")
        enhanced_originals = os.path.join(self.chapter_paths.get("enhanced_folder", ""), "Originals")

        # Варианты имен для поиска
        possible_names = [
            f"{base_name}.png",
            f"{base_name}.jpg",
            f"{index + 1:04d}.png",
            f"{index + 1:04d}.jpg"
        ]

        # Сначала проверяем папку originals_folder
        if os.path.isdir(originals_folder):
            for name in possible_names:
                orig_path = os.path.join(originals_folder, name)
                if os.path.exists(orig_path):
                    return orig_path

        # Затем проверяем папку enhanced_originals
        if os.path.isdir(enhanced_originals):
            for name in possible_names:
                orig_path = os.path.join(enhanced_originals, name)
                if os.path.exists(orig_path):
                    return orig_path

        # Если оригинал не найден, возвращаем текущий путь
        return current_path

    def upd_thumb_no_mask(self, page_idx):
        """Обновляет миниатюру без масок"""
        if 0 <= page_idx < len(self.thumb_labels):
            try:
                # Если нет изображения — ничего не делаем
                if page_idx not in self.viewer.pixmaps or self.viewer.pixmaps[page_idx].isNull():
                    return

                # Копируем оригинал для миниатюры
                pixmap = self.viewer.pixmaps[page_idx].copy()

                # Масштабируем для миниатюры
                tw = THUMB_W
                th = tw * 2
                scaled = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumb_labels[page_idx].setPixmap(scaled)
                self.upd_thumb_status(page_idx)
            except Exception as e:
                # Логгируем ошибку для отладки
                logger.error(f"Ошибка при обновлении миниатюры {page_idx}: {str(e)}")

    def upd_thumb_status(self, page_idx):
        if not (0 <= page_idx < len(self.idx_labels)):
            return

        status = self.img_status.get(page_idx, 'saved')

        has_active_masks = False
        if page_idx in self.viewer.masks:
            for mask in self.viewer.masks[page_idx]:
                if not (hasattr(mask, 'deleted') and mask.deleted):
                    has_active_masks = True
                    break

        has_drawing = False
        if page_idx in self.viewer.draw_layers and not self.viewer.draw_layers[page_idx].isNull():
            qimg = self.viewer.draw_layers[page_idx].toImage()
            for y in range(0, qimg.height(), 10):
                for x in range(0, qimg.width(), 10):
                    alpha = (qimg.pixel(x, y) >> 24) & 0xFF
                    if alpha > 0:
                        has_drawing = True
                        break
                if has_drawing:
                    break

        # Логика определения статуса
        if status == 'unsaved':
            # Красный приоритет
            final_status = 'unsaved'
        elif has_active_masks or has_drawing:
            # Желтый если есть маски
            final_status = 'modified'
            self.img_status[page_idx] = 'modified'
        else:
            # Зеленый если нет масок
            final_status = 'saved'
            self.img_status[page_idx] = 'saved'

        idx_label = self.idx_labels[page_idx]
        is_active = (page_idx == self.viewer.cur_page)

        border_style = "border:2px solid #7E1E9F;" if is_active else "border:2px solid transparent;"

        color_map = {
            'saved': "#22AA22",  # Зеленый
            'modified': "#DDBB00",  # Желтый
            'unsaved': "#DD2200"  # Красный
        }

        text_color = color_map.get(final_status, "#22AA22")

        idx_style = f"QLabel{{color:{text_color};background-color:#222;font-size:14px;font-weight:bold;{border_style}"
        idx_style += "border-bottom-left-radius:8px;border-bottom-right-radius:8px;}"

        idx_label.setStyleSheet(idx_style)

    def is_valid_page_idx(self, page_idx):
        if page_idx is None or not isinstance(page_idx, int) or page_idx < 0:
            return False
        return True

    def upd_comb_mask(self, page_idx):
        if not self.is_valid_page_idx(page_idx):
            logger.debug(f"Невалидный индекс страницы: {page_idx}")
            return None

        try:
            if page_idx >= len(self.img_paths):
                logger.debug(f"Индекс {page_idx} выходит за границы")
                return None

            if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                w = self.viewer.pixmaps[page_idx].width()
                h = self.viewer.pixmaps[page_idx].height()
                logger.debug(f"Размеры изображения {page_idx}: {w}x{h}")
            else:
                logger.debug(f"Отсутствует pixmap для страницы {page_idx}")
                return None

            combined_mask = np.zeros((h, w), dtype=np.uint8)
            mask_found = False
            mask_count = 0

            if page_idx in self.viewer.masks:
                for mask in self.viewer.masks[page_idx]:
                    if hasattr(mask, 'deleted') and mask.deleted:
                        continue
                    mask_count += 1

                    if isinstance(mask, EditableMask):
                        rect = mask.rect()
                        x1, y1 = int(rect.x()), int(rect.y())
                        x2, y2 = int(x1 + rect.width()), int(y1 + rect.height())

                        x1 = max(0, min(x1, w - 1))
                        y1 = max(0, min(y1, h - 1))
                        x2 = max(0, min(x2, w - 1))
                        y2 = max(0, min(y2, h - 1))

                        if x2 > x1 and y2 > y1:
                            cv2.rectangle(combined_mask, (x1, y1), (x2, y2), 255, -1)
                            mask_found = True

                    elif isinstance(mask, EditablePolygonMask):
                        polygon = mask.polygon()
                        points = []
                        for i in range(polygon.count()):
                            point = polygon.at(i)
                            x = max(0, min(int(point.x()), w - 1))
                            y = max(0, min(int(point.y()), h - 1))
                            points.append((x, y))

                        if len(points) > 2:
                            pts = np.array(points, np.int32).reshape((-1, 1, 2))
                            cv2.fillPoly(combined_mask, [pts], 255)
                            mask_found = True

            logger.debug(f"Обработано {mask_count} масок")

            if page_idx in self.viewer.draw_layers and not self.viewer.draw_layers[page_idx].isNull():
                qimg = self.viewer.draw_layers[page_idx].toImage()
                draw_mask = np.zeros((h, w), dtype=np.uint8)
                pixels_found = 0

                for y in range(h):
                    for x in range(w):
                        if x < qimg.width() and y < qimg.height():
                            pixel = qimg.pixel(x, y)
                            alpha = (pixel >> 24) & 0xFF
                            if alpha > 0:
                                draw_mask[y, x] = 255
                                pixels_found += 1

                if pixels_found > 0:
                    cv2.bitwise_or(combined_mask, draw_mask, combined_mask)
                    mask_found = True
                    logger.debug(f"Добавлено {pixels_found} пикселей из слоя рисования")

            if mask_found:
                kernel = np.ones((3, 3), np.uint8)
                combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
                combined_mask = cv2.dilate(combined_mask, kernel, iterations=1)

            self.comb_masks[page_idx] = combined_mask
            logger.debug(f"Маска создана, найдено пикселей: {np.sum(combined_mask > 0)}")
            return combined_mask

        except Exception as e:
            logger.error(f"Ошибка создания маски для страницы {page_idx}: {str(e)}")
            return None

    def lock_ui(self, operation):
        """Блокирует интерфейс во время выполнения операции"""
        try:
            self.proc = True
            self.curr_op = operation
            logger.info(f"Интерфейс заблокирован: операция {operation}")

            # Отключаем элементы
            self.detect_btn.setEnabled(False)
            self.segm_btn.setEnabled(False)
            self.clean_btn.setEnabled(False)
            self.reset_to_saved_btn.setEnabled(False)
            self.reset_to_orig_btn.setEnabled(False)
            self.save_btn.setEnabled(False)
            self.tool_btnNone.setEnabled(False)
            self.tool_btnBrush.setEnabled(False)
            self.tool_btnEraser.setEnabled(False)

            QApplication.processEvents()
        except Exception as e:
            logger.error(f"Ошибка блокировки интерфейса: {str(e)}")

    def unlock_ui(self):
        """Разблокирует интерфейс после операции"""
        try:
            self.proc = False
            self.curr_op = None
            logger.info("Интерфейс разблокирован")

            # Включаем элементы
            self.detect_btn.setEnabled(True)
            self.segm_btn.setEnabled(True)
            self.clean_btn.setEnabled(True)
            self.reset_to_saved_btn.setEnabled(True)
            self.reset_to_orig_btn.setEnabled(True)
            self.save_btn.setEnabled(True)
            self.tool_btnNone.setEnabled(True)
            self.tool_btnBrush.setEnabled(True)
            self.tool_btnEraser.setEnabled(True)

            # Восстановление режима рисования
            if hasattr(self.viewer, 'draw_mode') and self.viewer.draw_mode != DrawingMode.NONE:
                current_mode = self.viewer.draw_mode
                QTimer.singleShot(100, lambda: self.viewer.set_draw_mode(current_mode))
                QTimer.singleShot(200, lambda: self.set_drawing_tool(current_mode))

            QApplication.processEvents()
        except Exception as e:
            logger.error(f"Ошибка разблокировки интерфейса: {str(e)}")

    def clean_img(self):
        """Очистка изображения с помощью LaMa"""
        # Проверка активного процесса
        if self.proc:
            QMessageBox.warning(self, "Предупреждение",
                                f"Уже выполняется операция: {self.curr_op}. Дождитесь её завершения.")
            return

        # Проверка LaMa
        if not self.inpaint_avail:
            try:
                from simple_lama_inpainting import SimpleLama
                self.lama = SimpleLama()
                self.inpaint_avail = True
                logger.info("SimpleLama успешно инициализирован")
            except ImportError:
                QMessageBox.critical(self, "Ошибка",
                                     "Модуль SimpleLama не установлен. Установите пакет simple_lama_inpainting.")
                return
            except Exception as e:
                QMessageBox.critical(self, "Ошибка",
                                     f"Не удалось инициализировать SimpleLama: {str(e)}")
                return

        # Определяем страницы для обработки
        if self.clean_all_cb and self.clean_all_cb.isChecked():
            # Все страницы
            pages_to_clean = list(range(len(self.img_paths)))
        else:
            # Только текущая
            pages_to_clean = [self.viewer.cur_page]

        if not pages_to_clean:
            QMessageBox.information(self, "Информация", "Нет страниц для очистки.")
            return

        # Начинаем с первой страницы
        self.clean_curr_page_idx = 0
        self.clean_total_pages = len(pages_to_clean)
        self.pages_to_clean = pages_to_clean

        # Запускаем очистку первой страницы
        self.process_next_clean_page()

    def process_next_clean_page(self):
        """Обработка следующей страницы для очистки"""
        # Проверяем, закончили ли все страницы
        if self.clean_curr_page_idx >= len(self.pages_to_clean):
            QMessageBox.information(self, "Успех", "Очистка всех изображений завершена")
            self.clean_prog.setVisible(False)
            self.unlock_ui()
            return

        # Получаем текущую страницу
        page_idx = self.pages_to_clean[self.clean_curr_page_idx]

        # Блокируем интерфейс
        self.lock_ui("Очистка")

        # Показываем прогресс
        self.clean_prog.setRange(0, self.clean_total_pages)
        self.clean_prog.setValue(self.clean_curr_page_idx)
        self.clean_prog.setFormat(f"Подготовка очистки страницы {page_idx + 1}/{self.clean_total_pages}...")
        self.clean_prog.setVisible(True)
        QApplication.processEvents()

        try:
            # Сохраняем текущее состояние в буфер
            self.save_to_buf(page_idx)

            # Получаем текущее изображение
            current_pixmap = None

            if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                current_pixmap = self.viewer.pixmaps[page_idx]
                logger.info(f"Используем текущее изображение для страницы {page_idx + 1}")
            else:
                # Если нет в памяти, используем из буфера
                if page_idx in self.circ_buf:
                    if self.circ_buf[page_idx][1] is not None:
                        current_pixmap = self.circ_buf[page_idx][1].copy()
                        self.viewer.pixmaps[page_idx] = current_pixmap
                        logger.info(f"Загружено сохраненное изображение для страницы {page_idx + 1}")
                    elif self.circ_buf[page_idx][3] is not None:
                        current_pixmap = self.circ_buf[page_idx][3].copy()
                        self.viewer.pixmaps[page_idx] = current_pixmap
                        logger.info(f"Загружено последнее состояние для страницы {page_idx + 1}")
                else:
                    # Если нет истории, используем исходное
                    original_path = self.img_paths[page_idx]
                    current_pixmap = QPixmap(original_path)
                    if not current_pixmap.isNull():
                        self.viewer.pixmaps[page_idx] = current_pixmap
                        logger.info(f"Загружено исходное изображение: {original_path}")
                    else:
                        raise ValueError(f"Не удалось загрузить исходное изображение: {original_path}")

            # Размеры изображения
            w, h = current_pixmap.width(), current_pixmap.height()
            logger.info(f"Изображение {page_idx + 1} размером {w}x{h}")

            # Создаем пустую маску
            mask = np.zeros((h, w), dtype=np.uint8)

            # Получаем маски для текущей страницы
            masks_drawn = False

            # 1. Добавляем все видимые маски
            if page_idx in self.viewer.masks:
                mask_count = 0

                for mask_item in self.viewer.masks[page_idx]:
                    try:
                        # Пропускаем удаленные
                        if hasattr(mask_item, 'deleted') and mask_item.deleted:
                            continue

                        # НОВОЕ: Пропускаем уже обработанные маски
                        if hasattr(mask_item, 'processed') and mask_item.processed:
                            continue

                        if isinstance(mask_item, EditableMask):
                            rect = mask_item.rect()
                            x, y, width, height = int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())

                            # Проверка границ
                            x1 = max(0, min(x, w - 1))
                            y1 = max(0, min(y, h - 1))
                            x2 = max(0, min(x + width, w - 1))
                            y2 = max(0, min(y + height, h - 1))

                            if x2 > x1 and y2 > y1:
                                cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
                                masks_drawn = True
                                mask_count += 1

                        elif isinstance(mask_item, EditablePolygonMask):
                            polygon = mask_item.polygon()
                            points = []

                            for i in range(polygon.count()):
                                point = polygon.at(i)
                                x = max(0, min(int(point.x()), w - 1))
                                y = max(0, min(int(point.y()), h - 1))
                                points.append([x, y])

                            if len(points) > 2:
                                points_array = np.array(points, np.int32)
                                cv2.fillPoly(mask, [points_array], 255)
                                masks_drawn = True
                                mask_count += 1

                    except Exception as e:
                        logger.error(f"Ошибка при обработке маски: {str(e)}")
                        continue

                logger.info(f"Добавлено {mask_count} масок из viewer.masks")

            # 2. Добавляем слой рисования
            try:
                if page_idx in self.viewer.draw_layers and not self.viewer.draw_layers[page_idx].isNull():
                    draw_layer = self.viewer.draw_layers[page_idx].toImage()
                    pixels_found = 0
                    for y in range(min(h, draw_layer.height())):
                        for x in range(min(w, draw_layer.width())):
                            pixel = draw_layer.pixel(x, y)
                            alpha = (pixel >> 24) & 0xFF
                            if alpha > 10:
                                mask[y, x] = 255
                                pixels_found += 1

                    if pixels_found > 0:
                        masks_drawn = True
                        logger.info(f"Добавлен слой рисования: {pixels_found} пикселей")
            except Exception as e:
                logger.error(f"Ошибка при обработке слоя рисования: {str(e)}")

            # 3. Используем comb_masks (только необработанные)
            try:
                if page_idx in self.comb_masks and np.any(self.comb_masks[page_idx]):
                    combined_mask = self.comb_masks[page_idx]
                    # Проверяем размер
                    if combined_mask.shape[0] == h and combined_mask.shape[1] == w:
                        mask = cv2.bitwise_or(mask, combined_mask)
                        masks_drawn = True
                    else:
                        # Если не совпадают, делаем ресайз
                        combined_resized = cv2.resize(combined_mask, (w, h), interpolation=cv2.INTER_NEAREST)
                        mask = cv2.bitwise_or(mask, combined_resized)
                        masks_drawn = True
            except Exception as e:
                logger.error(f"Ошибка при обработке комбинированной маски: {str(e)}")

            # Проверяем, есть ли маска
            if not masks_drawn or np.sum(mask) == 0:
                logger.warning(f"Страница {page_idx + 1} не имеет масок для очистки, пропускаем")

                # Переходим к следующей
                self.clean_curr_page_idx += 1
                QTimer.singleShot(100, self.process_next_clean_page)
                return

            # Улучшаем маску
            kernel = np.ones((3, 3), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            mask = cv2.dilate(mask, kernel, iterations=1)

            # НЕ СОХРАНЯЕМ отладочную маску на диск

            # Показываем прогресс
            pixel_count = np.sum(mask > 0)
            logger.info(f"Создана маска с {pixel_count} непрозрачными пикселями")
            self._upd_prog_bar(
                self.clean_prog, self.clean_curr_page_idx, self.clean_total_pages,
                f"Очистка страницы {page_idx + 1}/{self.clean_total_pages}: {pixel_count} пикселей")

            # Воркер для очистки БЕЗ сохранения на диск
            worker = ImgCleanWorker(
                self.lama,
                current_pixmap,
                mask,
                page_idx
            )

            # Сигналы
            worker.prog.connect(lambda v, t, m, idx=page_idx:
                                self._upd_prog_bar(
                                    self.clean_prog,
                                    self.clean_curr_page_idx + v / t,
                                    self.clean_total_pages,
                                    f"Страница {idx + 1}/{self.clean_total_pages}: {m}"))
            worker.err.connect(lambda e: QMessageBox.critical(self, "Ошибка", e))

            # Для обработки следующей страницы (БЕЗ пути к файлу)
            worker.done_img.connect(self._on_img_cleaned_batch)
            # Запускаем очистку
            self.clean_workers = [worker]
            worker.start()

        except Exception as e:
            logger.error(f"Ошибка при подготовке очистки страницы {page_idx}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Ошибка", f"Не удалось запустить очистку страницы {page_idx + 1}: {str(e)}")

            # Переходим к следующей
            self.clean_curr_page_idx += 1
            QTimer.singleShot(100, self.process_next_clean_page)

    def _on_img_cleaned_batch(self, page_idx, result_pixmap):
        try:
            if not result_pixmap.isNull():
                logger.info(f"Успешно очищено изображение для страницы {page_idx + 1}")

                self.viewer.pixmaps[page_idx] = result_pixmap

                if page_idx not in self.circ_buf:
                    self.circ_buf[page_idx] = {
                        0: None,
                        1: None,
                        2: None,
                        3: None
                    }

                if self.circ_buf[page_idx][1] is not None:
                    self.circ_buf[page_idx][2] = self.circ_buf[page_idx][1].copy()

                self.circ_buf[page_idx][1] = result_pixmap.copy()

                # Полное удаление масок
                if page_idx in self.viewer.masks:
                    for mask in self.viewer.masks[page_idx]:
                        if mask.scene():
                            self.viewer.scene_.removeItem(mask)
                    self.viewer.masks[page_idx] = []

                # Очистка слоя рисования
                if page_idx in self.viewer.draw_layers:
                    self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                    if page_idx in self.viewer.draw_items:
                        self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                # Очистка комбинированной маски
                if page_idx in self.comb_masks:
                    del self.comb_masks[page_idx]

                self.img_status[page_idx] = 'unsaved'

                if page_idx == self.viewer.cur_page:
                    self.viewer.display_current_page()

                tw = THUMB_W
                th = tw * 2
                scaled = result_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumb_labels[page_idx].setPixmap(scaled)
                self.upd_thumb_status(page_idx)

            self.clean_curr_page_idx += 1
            self.clean_workers = []
            QTimer.singleShot(100, self.process_next_clean_page)

        except Exception as e:
            logger.error(f"Ошибка при обработке результата очистки: {str(e)}")
            self.clean_curr_page_idx += 1
            QTimer.singleShot(100, self.process_next_clean_page)

    def force_upd_display(self):
        """Принудительное обновление отображения"""
        self.viewer.display_current_page()

    def upd_all_thumbs(self):
        """Обновляет все миниатюры"""
        for page_idx in range(len(self.img_paths)):
            if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                # Обновляем миниатюру
                self.upd_thumb_no_mask(page_idx)

    def set_drawing_tool(self, mode):
        """Установка режима рисования"""
        self.curr_draw_mode = mode

        # Устанавливаем режим в просмотрщике
        if hasattr(self.viewer, 'set_draw_mode'):
            self.viewer.set_draw_mode(mode)
            self.viewer.set_draw_color(self.curr_draw_color)
            self.viewer.set_draw_size(self.curr_draw_size)
        elif hasattr(self.viewer, 'setDrawMode'):
            self.viewer.setDrawMode(mode)
            if hasattr(self.viewer, 'setDrawColor'):
                self.viewer.setDrawColor(self.curr_draw_color)
            if hasattr(self.viewer, 'setDrawSize'):
                self.viewer.setDrawSize(self.curr_draw_size)

        # Обновляем кнопки
        for btn in [self.tool_btnNone, self.tool_btnBrush, self.tool_btnEraser]:
            btn.setChecked(False)

        if mode == DrawingMode.NONE:
            self.tool_btnNone.setChecked(True)
        elif mode == DrawingMode.BRUSH:
            self.tool_btnBrush.setChecked(True)
        elif mode == DrawingMode.ERASER:
            self.tool_btnEraser.setChecked(True)

    def on_color_changed(self, index):
        """Обработка изменения цвета"""
        color = self.color_combo.itemData(index)
        if color:
            self.curr_draw_color = color
            if hasattr(self.viewer, 'set_draw_color'):
                self.viewer.set_draw_color(color)
            elif hasattr(self.viewer, 'setDrawColor'):
                self.viewer.setDrawColor(color)

    def on_size_changed(self, value):
        """Обработка изменения размера кисти"""
        self.curr_draw_size = value
        self.size_value.setText(str(value))
        if hasattr(self.viewer, 'set_draw_size'):
            self.viewer.set_draw_size(value)
        elif hasattr(self.viewer, 'setDrawSize'):
            self.viewer.setDrawSize(value)

    def on_detect_exp_val_changed(self, value):
        """Обработка изменения значения расширения маски"""
        self.expand_value.setText(str(value))
        self.saved_detect_exp = value

        if self.sender() == self.expand_slider:
            if self.detect_all_cb and self.detect_all_cb.isChecked():
                # Обновляем все страницы
                for page_idx in range(len(self.img_paths)):
                    if page_idx in self.viewer.masks:
                        self._upd_masks_exp(page_idx, 'detect', value)
            else:
                # Только текущую
                self._upd_masks_exp(self.viewer.cur_page, 'detect', value)

    def on_segm_exp_val_changed(self, value):
        """Обработка изменения значения расширения маски сегментации"""
        self.segm_expand_value.setText(str(value))
        self.saved_segm_exp = value

        if self.sender() == self.segm_expand_slider:
            if self.segm_all_cb and self.segm_all_cb.isChecked():
                # Обновляем все страницы
                for page_idx in range(len(self.img_paths)):
                    if page_idx in self.viewer.masks:
                        self._upd_masks_exp(page_idx, 'segm', value)
            else:
                # Только текущую
                self._upd_masks_exp(self.viewer.cur_page, 'segm', value)

    def on_prev_page(self):
        """Переход на предыдущую страницу"""
        if self.viewer.previousPage():
            self.upd_active_thumb(self.viewer.cur_page)
            self.prev_page_btn.setEnabled(self.viewer.cur_page > 0)
            self.next_page_btn.setEnabled(True)

    def on_next_page(self):
        """Переход на следующую страницу"""
        if self.viewer.nextPage():
            self.upd_active_thumb(self.viewer.cur_page)
            self.next_page_btn.setEnabled(self.viewer.cur_page < len(self.viewer.pages) - 1)
            self.prev_page_btn.setEnabled(True)

    def _upd_masks_exp(self, page_idx, mask_type, expansion_value):
        """Обновляет размер масок на странице с учетом масштаба"""
        if page_idx not in self.viewer.masks:
            return

        try:
            original_data = []
            masks_to_update = []

            # Отладка для всех классов
            class_counts = {}
            for mask in self.viewer.masks[page_idx]:
                if hasattr(mask, 'class_name'):
                    cls = mask.class_name
                    class_counts[cls] = class_counts.get(cls, 0) + 1

            # Масштаб отображения
            scale_factor = 1.0
            if hasattr(self.viewer, 'scale_factor'):
                scale_factor = self.viewer.scale_factor

            for mask in self.viewer.masks[page_idx]:
                if hasattr(mask, 'mask_type') and mask.mask_type == mask_type and not mask.deleted:
                    # Получаем нужный словарь классов в зависимости от типа
                    classes_dict = self.detect_cls if mask_type == 'detect' else self.segm_cls

                    # Проверка включения класса
                    if not classes_dict.get(mask.class_name, {}).get('enabled', False):
                        logger.debug(f"Класс {mask.class_name} отключен, пропускаем маску")
                        continue

                    if isinstance(mask, EditableMask):
                        rect = mask.rect()
                        center_x = rect.x() + rect.width() / 2
                        center_y = rect.y() + rect.height() / 2

                        # Оригинальный размер без расширения
                        original_width = rect.width()
                        original_height = rect.height()
                        if hasattr(mask, 'last_expansion') and mask.last_expansion:
                            original_width -= (mask.last_expansion * 2)
                            original_height -= (mask.last_expansion * 2)

                        original_data.append((center_x, center_y, original_width, original_height))
                        masks_to_update.append(mask)

            # Обновляем размеры
            for mask, (cx, cy, w, h) in zip(masks_to_update, original_data):
                if isinstance(mask, EditableMask):
                    # Новое расширение
                    new_width = w + expansion_value * 2
                    new_height = h + expansion_value * 2
                    new_x = cx - new_width / 2
                    new_y = cy - new_height / 2

                    logger.debug(f"Обновляем маску класса {mask.class_name}: {w}x{h} -> {new_width}x{new_height}")
                    mask.setRect(new_x, new_y, new_width, new_height)
                    mask.last_expansion = expansion_value

            # Обновляем маску и отображение
            window = self.window()
            if hasattr(window, 'upd_comb_mask'):
                window.upd_comb_mask(page_idx)
            else:
                self.upd_comb_mask(page_idx)

            # Только для текущей страницы
            if page_idx == self.viewer.cur_page:
                self.viewer.display_current_page()
                # Сбрасываем рисование
                if hasattr(self.viewer, 'drawing'):
                    self.viewer.drawing = False
                    self.viewer.last_pt = None

        except Exception as e:
            logger.error(f"Ошибка обновления масок: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    def _on_detection_completed(self, page_idx, results):
        try:
            if results:
                logger.info(f"Получены результаты детекции для страницы {page_idx}: {len(results)} объектов")
                self.sync_det_classes()

                current_transform = None
                current_scale = 1.0
                if hasattr(self.viewer, 'transform'):
                    current_transform = self.viewer.transform()
                if hasattr(self.viewer, 'scale_factor'):
                    current_scale = self.viewer.scale_factor

                img_shape = None
                if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                    w = self.viewer.pixmaps[page_idx].width()
                    h = self.viewer.pixmaps[page_idx].height()
                    img_shape = (h, w)

                self.detect_mgr.process_detection_results(
                    results,
                    self.viewer,
                    page_idx,
                    self.expand_slider.value(),
                    scale_factor=current_scale,
                    img_shape=img_shape
                )

                self.upd_comb_mask(page_idx)
                self.upd_thumb_no_mask(page_idx)
                self.img_status[page_idx] = 'modified'
                self.upd_thumb_status(page_idx)

                if page_idx == self.viewer.cur_page:
                    QTimer.singleShot(100, lambda: self.viewer.display_current_page())
                    if current_transform is not None:
                        QTimer.singleShot(200, lambda: self.viewer.setTransform(current_transform))

                QApplication.processEvents()
            else:
                logger.warning(f"Не найдено объектов на странице {page_idx + 1}")

        except Exception as e:
            logger.error(f"Ошибка при обработке результатов детекции: {str(e)}")
        finally:
            current_progress = self.current_page_index if hasattr(self, 'current_page_index') else 0
            total_pages = self.total_pages if hasattr(self, 'total_pages') else 1

            if current_progress >= total_pages:
                self._upd_prog_bar(self.detect_prog, total_pages, total_pages, "Детекция завершена")
                for i in range(len(self.img_paths)):
                    if i in self.viewer.masks and self.viewer.masks[i]:
                        self.upd_thumb_no_mask(i)
                self._restore_detect_btn()
                QTimer.singleShot(PROG_HIDE_MS, lambda: self.detect_prog.setVisible(False))
                self.unlock_ui()

    def force_upd_thumb(self, page_idx):
        """Принудительное обновление миниатюры страницы"""
        if 0 <= page_idx < len(self.thumb_labels):
            try:
                # Если нет изображения — ничего не делаем
                if page_idx not in self.viewer.pixmaps or self.viewer.pixmaps[page_idx].isNull():
                    return

                # Копируем оригинал для миниатюры
                pixmap = self.viewer.pixmaps[page_idx].copy()

                # Масштабируем для миниатюры
                tw = THUMB_W
                th = tw * 2
                scaled = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumb_labels[page_idx].setPixmap(scaled)
                self.upd_thumb_status(page_idx)
            except Exception as e:
                logger.error(f"Ошибка при обновлении миниатюры {page_idx}: {str(e)}")

    def upd_comb_mask_from_visual(self, page_idx):
        """Обновляет комбинированную маску на основе визуальных элементов"""
        # Создаем комбинированную маску с учетом всех визуальных элементов
        combined_mask = self.upd_comb_mask(page_idx)

        # Обновляем статус страницы
        if combined_mask is not None and np.any(combined_mask > 0):
            self.img_status[page_idx] = 'modified'
        else:
            # Проверяем, есть ли активные маски
            has_active_masks = False
            if page_idx in self.viewer.masks:
                for mask in self.viewer.masks[page_idx]:
                    if not (hasattr(mask, 'deleted') and mask.deleted):
                        has_active_masks = True
                        break

            if has_active_masks:
                self.img_status[page_idx] = 'modified'
            else:
                self.img_status[page_idx] = 'saved'

        # Обновляем индикатор статуса
        self.upd_thumb_status(page_idx)

        # Отправляем сигнал об обновлении маски
        self.viewer.mask_updated.emit(page_idx)

        return combined_mask

    def _clear_page_masks(self, page_idx, mask_type=None):
        """Удаляет маски определенного типа на странице"""
        if page_idx in self.viewer.masks:
            masks_to_remove = []
            for mask in self.viewer.masks[page_idx]:
                if mask_type is None or (hasattr(mask, 'mask_type') and mask.mask_type == mask_type):
                    masks_to_remove.append(mask)
                    self.viewer.scene_.removeItem(mask)
            self.viewer.masks[page_idx] = [m for m in self.viewer.masks[page_idx] if
                                           m not in masks_to_remove]
            if page_idx in self.comb_masks:
                self.upd_comb_mask(page_idx)

    def _clear_all_masks_for_page(self, page_idx):
        """Полностью очищает все маски и слои для страницы"""
        # Удаляем все маски
        if page_idx in self.viewer.masks:
            for mask in self.viewer.masks[page_idx]:
                if mask.scene():
                    self.viewer.scene_.removeItem(mask)
            self.viewer.masks[page_idx] = []

        # Очищаем слой рисования
        if page_idx in self.viewer.draw_layers:
            self.viewer.draw_layers[page_idx].fill(Qt.transparent)
            if page_idx in self.viewer.draw_items:
                self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

        # Удаляем комбинированную маску
        if page_idx in self.comb_masks:
            del self.comb_masks[page_idx]

    def run_detection(self):
        """Запускает процесс детекции"""
        # Проверка активного процесса
        if self.proc:
            QMessageBox.warning(self, "Предупреждение",
                                f"Уже выполняется операция: {self.curr_op}. Дождитесь её завершения.")
            return
        self.sync_det_classes()
        self.sync_detection_manager()  # Добавьте эту строку

        # Блокируем интерфейс
        self.lock_ui("Детекция")

        # Изменяем кнопку на "Отмена"
        self.detect_btn.setText("Отменить")
        self.detect_btn.setStyleSheet(
            "QPushButton{background-color:#CC3333;color:white;border-radius:8px;padding:6px 12px;font-size:14px;}"
            "QPushButton:hover{background-color:#FF4444;}")

        # Отключаем обработчики
        self.detect_btn.setEnabled(True)
        try:
            self.detect_btn.clicked.disconnect()
        except:
            pass
        self.detect_btn.clicked.connect(self.cancel_detection)

        # Прогресс
        self.detect_prog.setRange(0, 100)
        self.detect_prog.setValue(0)
        self.detect_prog.setFormat("Подготовка детекции...")
        self.detect_prog.setVisible(True)
        QApplication.processEvents()

        # Страницы для обработки
        if self.detect_all_cb and self.detect_all_cb.isChecked():
            # Все страницы
            self.pages_to_process = list(range(len(self.img_paths)))
        else:
            # Только текущая
            self.pages_to_process = [self.viewer.cur_page]

        # Статус
        self.total_pages = len(self.pages_to_process)
        self.current_page_index = 0
        self.expansion_value = self.expand_slider.value()
        self.saved_detect_exp = self.expansion_value

        # Флаг отмены
        self.det_canc = False

        if self.total_pages == 0:
            QMessageBox.information(self, "Информация", "Нет страниц для обработки")
            self.unlock_ui()
            self._restore_detect_btn()
            self.detect_prog.setVisible(False)
            return

        # Запускаем
        logger.info("Запуск процесса детекции")
        self.detect_mgr.process_detection_pages(self, self.pages_to_process, self.expansion_value)

    def _restore_detect_btn(self):
        """Восстанавливает кнопку детекции"""
        self.detect_btn.setText("Запустить детекцию")
        self.detect_btn.setStyleSheet(
            "QPushButton{background-color:#2EA44F;color:white;border-radius:8px;padding:6px 12px;font-size:14px;}"
            "QPushButton:hover{background-color:#36CC57;}")
        try:
            self.detect_btn.clicked.disconnect()
        except:
            pass
        self.detect_btn.clicked.connect(self.run_detection)
        self.detect_btn.setEnabled(True)

    def cancel_detection(self):
        """Отменяет процесс детекции"""
        self.det_canc = True
        QMessageBox.information(self, "Информация",
                                "Детекция будет отменена после завершения текущей страницы")

        # Блокируем кнопку
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Отмена...")

    def run_area_detection(self, page_idx, selection_rect):
        """Запускает детекцию объектов в выбранной области"""
        try:
            # Проверка активного процесса
            if self.proc:
                QMessageBox.warning(self, "Предупреждение",
                                    f"Уже выполняется операция: {self.curr_op}. Дождитесь её завершения.")
                return

            # Путь к изображению
            image_path = self.img_paths[page_idx]

            # Прогресс
            self.detect_prog.setVisible(True)
            self.detect_prog.setValue(0)
            self.detect_prog.setFormat("Подготовка детекции области...")
            QApplication.processEvents()

            # Блокируем интерфейс
            self.lock_ui("Детекция области")

            # Отдельный поток
            def run_detection_thread():
                try:
                    # Запускаем через менеджер
                    results, offset = self.detect_mgr.detect_area(
                        image_path, page_idx, selection_rect, self.expand_slider.value())

                    if results is not None:
                        self.detect_mgr.process_detection_results(
                            results, self.viewer, page_idx, self.expand_slider.value(),
                            img_shape=None, offset=offset)

                        # Обновляем маску
                        self.upd_comb_mask(page_idx)

                        # Обновляем отображение
                        QTimer.singleShot(100, self.viewer.display_current_page)

                    # Завершаем прогресс
                    QTimer.singleShot(0, lambda: self._upd_prog_bar(
                        self.detect_prog, 1, 1, "Детекция области завершена"))

                    # Скрываем прогресс
                    QTimer.singleShot(PROG_HIDE_MS,
                                      lambda: self.detect_prog.setVisible(False))

                    # Разблокируем
                    QTimer.singleShot(100, self.unlock_ui)

                except Exception as e:
                    logger.error(f"Ошибка детекции области: {str(e)}")
                    QTimer.singleShot(0, lambda: QMessageBox.critical(self, "Ошибка",
                                                                      f"Ошибка детекции области: {str(e)}"))
                    QTimer.singleShot(0, self.unlock_ui)

            # Запускаем поток
            thread = Thread(target=run_detection_thread)
            thread.daemon = True
            thread.start()

        except Exception as e:
            logger.error(f"Ошибка запуска детекции области: {str(e)}")
            self.unlock_ui()

    def run_segmentation(self):
        """Запускает процесс сегментации"""
        # Проверка активного процесса
        if self.proc:
            QMessageBox.warning(self, "Предупреждение",
                                f"Уже выполняется операция: {self.curr_op}. Дождитесь её завершения.")
            return

        # Блокируем интерфейс
        self.lock_ui("Сегментация")

        # Изменяем кнопку
        self.segm_btn.setText("Отменить")
        self.segm_btn.setStyleSheet(
            "QPushButton{background-color:#CC3333;color:white;border-radius:8px;padding:6px 12px;font-size:14px;}"
            "QPushButton:hover{background-color:#FF4444;}")

        # Отключаем обработчики
        self.segm_btn.setEnabled(True)
        try:
            self.segm_btn.clicked.disconnect()
        except:
            pass
        self.segm_btn.clicked.connect(self.cancel_segmentation)

        # Прогресс
        self.segm_prog.setRange(0, 100)
        self.segm_prog.setValue(0)
        self.segm_prog.setFormat("Подготовка сегментации...")
        self.segm_prog.setVisible(True)
        QApplication.processEvents()

        # Страницы для обработки
        if self.segm_all_cb and self.segm_all_cb.isChecked():
            # Все страницы
            self.segm_pages_to_process = list(range(len(self.img_paths)))
        else:
            # Только текущая
            self.segm_pages_to_process = [self.viewer.cur_page]

        # Статус
        self.segm_total_pages = len(self.segm_pages_to_process)
        self.segm_current_page_index = 0
        self.segm_expansion_value = self.segm_expand_slider.value()
        self.saved_segm_exp = self.segm_expansion_value

        # Флаг отмены
        self.segm_canc = False

        if self.segm_total_pages == 0:
            QMessageBox.information(self, "Информация", "Нет страниц для обработки")
            self.unlock_ui()
            self._restore_segm_btn()
            return

        # Запускаем
        logger.info("Запуск процесса сегментации")
        self.detect_mgr.process_segmentation_pages(self, self.segm_pages_to_process, self.segm_expansion_value)

    def _restore_segm_btn(self):
        """Восстанавливает кнопку сегментации"""
        self.segm_btn.setText("Запустить сегментацию")
        self.segm_btn.setStyleSheet(
            "QPushButton{background-color:#CB6828;color:white;border-radius:8px;padding:6px 12px;font-size:14px;}"
            "QPushButton:hover{background-color:#E37B31;}")
        try:
            self.segm_btn.clicked.disconnect()
        except:
            pass
        self.segm_btn.clicked.connect(self.run_segmentation)

    def cancel_segmentation(self):
        """Отменяет процесс сегментации"""
        self.segm_canc = True
        QMessageBox.information(self, "Информация",
                                "Сегментация будет отменена после завершения текущей страницы")

        # Блокируем кнопку
        self.segm_btn.setEnabled(False)
        self.segm_btn.setText("Отмена...")

    def _upd_class_enabled(self, model_type, cls_name, state):
        """Обновляет активность класса"""
        enabled = state == Qt.Checked

        if model_type == 'detect':
            if cls_name in self.detect_cls:
                # Исправление: состояние берется прямо из чекбокса
                if cls_name == 'Sound':
                    enabled = self.cb_sound.isChecked()
                elif cls_name == 'ComplexText':
                    enabled = self.cb_complex_text.isChecked()
                elif cls_name == 'FonText':
                    enabled = self.cb_fontext.isChecked()
                elif cls_name == 'Text':
                    enabled = self.cb_text.isChecked()

                # Обновляем локальный словарь
                self.detect_cls[cls_name]['enabled'] = enabled
                logger.info(f"Класс детекции {cls_name} {'включен' if enabled else 'отключен'}")

                # Обновляем словарь в DetectionManager
                if hasattr(self, 'detect_mgr') and hasattr(self.detect_mgr, 'detect_classes'):
                    self.detect_mgr.detect_classes[cls_name]['enabled'] = enabled
                    logger.info(f"Прямое обновление в DetectionManager: {cls_name}={enabled}")

            else:
                logger.warning(f"Класс детекции {cls_name} не найден в словаре")
        else:  # segm
            if cls_name in self.segm_cls:
                if cls_name == 'Sound':
                    enabled = self.cb_segm_sound.isChecked()
                elif cls_name == 'TextSegm':
                    enabled = self.cb_textsegm.isChecked()
                elif cls_name == 'FonText':
                    enabled = self.cb_segm_fontext.isChecked()
                elif cls_name == 'Text':
                    enabled = self.cb_segm_text.isChecked()

                self.segm_cls[cls_name]['enabled'] = enabled
                logger.info(f"Класс сегментации {cls_name} {'включен' if enabled else 'отключен'}")

                if hasattr(self, 'detect_mgr') and hasattr(self.detect_mgr, 'segm_classes'):
                    self.detect_mgr.segm_classes[cls_name]['enabled'] = enabled
            else:
                logger.warning(f"Класс сегментации {cls_name} не найден в словаре")

                # Обновляем текущую страницу
            if self.viewer.cur_page is not None:
                for mask in self.viewer.masks.get(self.viewer.cur_page, []):
                    if hasattr(mask, 'class_name') and hasattr(mask, 'mask_type'):
                        classes_dict = self.detect_cls if mask.mask_type == 'detect' else self.segm_cls
                        if mask.class_name in classes_dict:
                            mask.setVisible(classes_dict[mask.class_name]['enabled'])

                # Обновляем отображение
                self.viewer.display_current_page()

    def reset_to_orig(self):
        """Сброс изображения к исходному оригиналу с диска как при первом запуске"""
        # Диалог выбора источника
        sources = {}

        # Предобработка/Save
        save_folder = os.path.join(self.chapter_paths["enhanced_folder"], "Save")
        if os.path.exists(save_folder):
            save_images = self._get_imgs_from_folder(save_folder)
            if save_images:
                sources["preprocess_save"] = {
                    "images": save_images,
                    "name": "Предобработка (Save)"
                }

        # Загрузка
        upload_images = self._get_imgs_from_folder(self.chapter_paths["upload_folder"])
        if upload_images:
            sources["upload"] = {
                "images": upload_images,
                "name": "Загрузка"
            }

        if not sources:
            QMessageBox.warning(self, "Предупреждение", "Не найдены оригинальные изображения")
            return

        # Показываем диалог выбора
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QRadioButton, QPushButton, QButtonGroup, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("Выбор источника для сброса")
        dialog.setModal(True)
        dialog.setFixedWidth(400)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("Выберите источник для сброса изображений:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        button_group = QButtonGroup()

        for key, source_info in sources.items():
            radio = QRadioButton(f"{source_info['name']} ({len(source_info['images'])} изображений)")
            radio.setStyleSheet("margin: 10px 0;")
            button_group.addButton(radio)
            radio.toggled.connect(lambda checked, k=key: setattr(dialog, 'selected_source', k if checked else None))
            layout.addWidget(radio)

            # Выбираем первый по умолчанию
            if not hasattr(dialog, 'selected_source'):
                radio.setChecked(True)
                dialog.selected_source = key

        # Кнопки
        button_layout = QHBoxLayout()

        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #7E1E9F;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #9E3EAF;
            }
        """)
        ok_btn.clicked.connect(dialog.accept)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #666;
                color: white;
                border-radius: 8px;
                padding: 8px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #777;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

        if dialog.exec() != QDialog.Accepted or not hasattr(dialog, 'selected_source'):
            return

        # Получаем выбранные изображения
        source_images = sources[dialog.selected_source]["images"]

        # Определение страниц для обработки
        if self.mass_process_cb and self.mass_process_cb.isChecked():
            pages_to_reset = list(range(len(self.img_paths)))
        else:
            pages_to_reset = [self.viewer.cur_page]

        # Сохранение трансформации
        current_transform = self.viewer.transform()

        reset_count = 0
        try:
            for page_idx in pages_to_reset:
                # Проверка границ массива
                if page_idx < 0 or page_idx >= len(self.img_paths):
                    logger.error(f"Недопустимый индекс страницы: {page_idx}")
                    continue

                # Берем соответствующий файл из выбранного источника
                if page_idx < len(source_images):
                    orig_path = source_images[page_idx]
                else:
                    # Если индекс выходит за границы, пытаемся найти по имени
                    current_filename = os.path.basename(self.img_paths[page_idx])
                    base_name = os.path.splitext(current_filename)[0]

                    # Ищем файл с похожим именем
                    orig_path = None
                    for src_path in source_images:
                        src_filename = os.path.basename(src_path)
                        src_base = os.path.splitext(src_filename)[0]
                        if src_base == base_name:
                            orig_path = src_path
                            break

                    if not orig_path:
                        logger.warning(f"Не найден оригинал для страницы {page_idx}")
                        continue

                # Загружаем оригинал с диска
                if os.path.exists(orig_path):
                    orig_pixmap = QPixmap(orig_path)

                    if not orig_pixmap.isNull():
                        # Создаем копию для установки
                        restored_pixmap = QPixmap(orig_pixmap)

                        # Устанавливаем как текущее изображение
                        self.viewer.pixmaps[page_idx] = restored_pixmap

                        # ВАЖНО: Удаляем старый слой рисования
                        if page_idx in self.viewer.draw_layers:
                            if page_idx in self.viewer.draw_items:
                                self.viewer.scene_.removeItem(self.viewer.draw_items[page_idx])
                                del self.viewer.draw_items[page_idx]
                            del self.viewer.draw_layers[page_idx]

                        # Создаем новый слой с правильными размерами
                        self.viewer._create_drawing_layer(page_idx)

                        # Обновляем также оригинал в viewer если есть
                        if hasattr(self.viewer, 'orig_pixmaps'):
                            self.viewer.orig_pixmaps[page_idx] = QPixmap(orig_pixmap)

                        reset_count += 1

                        # Обновляем буфер как при первом запуске
                        if page_idx not in self.circ_buf:
                            self.circ_buf[page_idx] = {
                                0: None,
                                1: None,
                                2: None,
                                3: None
                            }

                        # Обновляем позиции буфера
                        self.circ_buf[page_idx][0] = QPixmap(orig_pixmap)  # Сохраняем оригинал
                        self.circ_buf[page_idx][1] = QPixmap(orig_pixmap)  # Текущее состояние = оригинал
                        self.circ_buf[page_idx][2] = None  # Очищаем промежуточные
                        self.circ_buf[page_idx][3] = None

                        # Удаление ВСЕХ масок
                        if page_idx in self.viewer.masks:
                            for mask in self.viewer.masks[page_idx]:
                                mask.deleted = True
                                if mask.scene():
                                    self.viewer.scene_.removeItem(mask)
                            self.viewer.masks[page_idx] = []

                        # Принудительная очистка сцены от ВСЕХ масок (включая обработанные)
                        items_to_remove = []
                        for item in self.viewer.scene_.items():
                            if isinstance(item, (EditableMask, EditablePolygonMask, BrushStroke)):
                                # Удаляем независимо от статуса processed
                                items_to_remove.append(item)

                        for item in items_to_remove:
                            # Удаляем из сцены
                            if item.scene():
                                self.viewer.scene_.removeItem(item)

                            # Удаляем из всех массивов масок
                            for p_idx in self.viewer.masks:
                                if item in self.viewer.masks[p_idx]:
                                    self.viewer.masks[p_idx].remove(item)

                        # Дополнительная очистка для текущей страницы
                        if page_idx == self.viewer.cur_page:
                            # Еще раз проходим по items сцены
                            for item in list(self.viewer.scene_.items()):
                                if isinstance(item, (EditableMask, EditablePolygonMask, BrushStroke)):
                                    self.viewer.scene_.removeItem(item)

                        # Очистка комбинированной маски
                        if page_idx in self.comb_masks:
                            del self.comb_masks[page_idx]

                        # Принудительное обновление сцены
                        self.viewer.scene_.update()

                        # Обновление статуса
                        self.img_status[page_idx] = 'saved'

                        # Обновление миниатюры
                        if 0 <= page_idx < len(self.thumb_labels):
                            tw = THUMB_W
                            th = tw * 2
                            scaled = restored_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            self.thumb_labels[page_idx].setPixmap(scaled)
                            self.upd_thumb_status(page_idx)
                    else:
                        logger.error(f"Не удалось загрузить изображение: {orig_path}")
                else:
                    logger.error(f"Файл не существует: {orig_path}")

            # Принудительное обновление интерфейса
            self.viewer.display_current_page()
            self.viewer.setTransform(current_transform)
            QApplication.processEvents()

            # Вывод сообщения о результате
            if reset_count > 0:
                QMessageBox.information(self, "Успех",
                                        f"{'Изображение сброшено' if reset_count == 1 else f'{reset_count} изображений сброшено'} до оригинала.")
            else:
                QMessageBox.warning(self, "Предупреждение",
                                    "Не удалось найти оригинальные изображения.")

        except Exception as e:
            logger.error(f"Ошибка при сбросе до оригинала: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Ошибка", f"Не удалось сбросить изображения: {str(e)}")

    def sync_detection_manager(self):
        """Синхронизирует состояние классов с менеджером детекции"""
        if hasattr(self, 'detect_mgr'):
            # Синхронизация классов детекции
            self.detect_mgr.detect_classes = self.detect_cls.copy()
            # Синхронизация классов сегментации
            self.detect_mgr.segm_classes = self.segm_cls.copy()
            logger.info(f"Синхронизированы классы детекции: {self.detect_mgr.detect_classes}")
            logger.info(f"Синхронизированы классы сегментации: {self.detect_mgr.segm_classes}")

    def reset_to_last_saved(self):
        """Сброс изображения к последнему сохраненному состоянию"""
        pages_to_reset = list(range(len(self.img_paths))) if self.mass_process_cb.isChecked() else [
            self.viewer.cur_page]

        current_transform = self.viewer.transform()
        reset_count = 0

        try:
            for page_idx in pages_to_reset:
                # Загружаем сохраненное изображение из папки клининг
                saved_path = self.img_paths[page_idx]
                if os.path.exists(saved_path):
                    saved_pixmap = QPixmap(saved_path)

                    if not saved_pixmap.isNull():
                        self.viewer.pixmaps[page_idx] = saved_pixmap
                        reset_count += 1

                        # Обновляем буфер
                        if page_idx not in self.circ_buf:
                            self.circ_buf[page_idx] = {0: None, 1: None, 2: None, 3: None}

                        self.circ_buf[page_idx][1] = saved_pixmap.copy()
                        self.circ_buf[page_idx][2] = None
                        self.circ_buf[page_idx][3] = None

                        # Очищаем маски и слои
                        self._clear_all_masks_for_page(page_idx)

                        # Обновляем статус
                        self.img_status[page_idx] = 'saved'

                        # Обновляем миниатюру
                        if 0 <= page_idx < len(self.thumb_labels):
                            tw, th = THUMB_W, THUMB_H
                            scaled = saved_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            self.thumb_labels[page_idx].setPixmap(scaled)
                            self.upd_thumb_status(page_idx)

            # Обновляем отображение
            self.viewer.display_current_page()
            self.viewer.setTransform(current_transform)

            if reset_count > 0:
                QMessageBox.information(self, "Успех",
                                        f"Сброшено {reset_count} изображений до последнего сохраненного состояния.")

        except Exception as e:
            logger.error(f"Ошибка при сбросе: {str(e)}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сбросить изображения: {str(e)}")

    def save_result(self):
        """Сохранение изображений с принудительной заменой"""
        output_dir = self.chapter_paths["cleaning_folder"]
        os.makedirs(output_dir, exist_ok=True)

        # Определение страниц для сохранения
        pages_to_save = list(range(len(self.img_paths))) if self.mass_process_cb.isChecked() else [self.viewer.cur_page]

        saved_count = 0

        for page_idx in pages_to_save:
            # Правильная проверка для списка
            if page_idx >= len(self.viewer.pixmaps) or self.viewer.pixmaps[page_idx].isNull():
                continue

            pixmap = self.viewer.pixmaps[page_idx]

            # Формирование имени файла
            filename = os.path.basename(self.img_paths[page_idx])
            save_path = os.path.join(output_dir, filename)

            # Сохранение
            if pixmap.save(save_path, "PNG"):
                saved_count += 1
                self.img_paths[page_idx] = save_path

                # Обновляем буфер
                if page_idx not in self.circ_buf:
                    self.circ_buf[page_idx] = {0: None, 1: pixmap.copy(), 2: None, 3: None}
                else:
                    self.circ_buf[page_idx][1] = pixmap.copy()

                # Помечаем маски как обработанные
                if page_idx in self.viewer.masks:
                    for mask in self.viewer.masks[page_idx]:
                        mask.processed = True
                        mask.setVisible(False)

                # Очистка слоя рисования
                if page_idx in self.viewer.draw_layers:
                    self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                    if page_idx in self.viewer.draw_items:
                        self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                self.img_status[page_idx] = 'saved'
                self.upd_thumb_status(page_idx)

        # Обновление конфигурации
        if saved_count > 0:
            config_path = os.path.join(output_dir, "saved_images.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({"paths": self.img_paths}, f, ensure_ascii=False, indent=4)

        if saved_count > 0:
            QMessageBox.information(self, "Успех", f"Сохранено {saved_count} изображений")

    def force_save_current(self):
        """Принудительное сохранение текущего изображения"""
        page_idx = self.viewer.cur_page
        if page_idx < 0 or page_idx >= len(self.img_paths):
            return

        pixmap = self.viewer.pixmaps[page_idx]
        if pixmap.isNull():
            return

        output_dir = self.chapter_paths["cleaning_folder"]
        os.makedirs(output_dir, exist_ok=True)

        save_path = os.path.join(output_dir, f"{page_idx:04d}.png")

        if pixmap.save(save_path, "PNG"):
            self.img_paths[page_idx] = save_path
            self.img_status[page_idx] = 'saved'
            self.upd_thumb_status(page_idx)

            # Обновление конфигурации
            config_path = os.path.join(output_dir, "saved_images.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump({"paths": self.img_paths}, f, ensure_ascii=False, indent=4)

            QMessageBox.information(self, "Успех", "Изображение сохранено")

    def force_save_result(self):
        """Принудительное сохранение изображения"""
        page_idx = self.viewer.cur_page
        if page_idx < 0 or page_idx >= len(self.viewer.pixmaps):
            QMessageBox.warning(self, "Ошибка", "Некорректный индекс страницы")
            return

        pixmap = self.viewer.pixmaps[page_idx]
        if pixmap.isNull():
            QMessageBox.warning(self, "Ошибка", "Отсутствует изображение для сохранения")
            return

        output_dir = self.chapter_paths["cleaning_folder"]
        os.makedirs(output_dir, exist_ok=True)
        timestamp = int(time.time())
        output_path = os.path.join(output_dir, f"cleaned_page_{page_idx + 1}_{timestamp}.png")

        # Сохранение изображения
        temp_img = pixmap.toImage()
        save_result = temp_img.save(output_path, "PNG")

        if save_result:
            QMessageBox.information(self, "Успех", f"Изображение сохранено в: {output_path}")
            self.img_paths[page_idx] = output_path
            config_path = os.path.join(output_dir, "saved_images.json")
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump({"paths": self.img_paths}, f, ensure_ascii=False, indent=4)
            except Exception:
                pass
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить изображение")

    def on_status_changed(self):
        """Обновление статуса обработки главы"""
        btn = self.status_group.checkedButton()
        if not btn: return
        st = btn.text()
        c_folder = self.chapter_paths.get("cleaning_folder", "")
        if not os.path.isdir(c_folder): return
        json_path = os.path.join(c_folder, self.status_json)

        chapter_json_path = os.path.join(self.chapter_folder, "chapter.json")
        if os.path.exists(chapter_json_path):
            try:
                with open(chapter_json_path, 'r', encoding='utf-8') as f:
                    chapter_data = json.load(f)

                if "stages" in chapter_data:
                    if st == "Не начат":
                        chapter_data["stages"]["Клининг"] = False
                    elif st == "В работе":
                        chapter_data["stages"]["Клининг"] = "partial"
                    elif st == "Завершен":
                        chapter_data["stages"]["Клининг"] = True

                with open(chapter_json_path, 'w', encoding='utf-8') as f:
                    json.dump(chapter_data, f, ensure_ascii=False, indent=4)
            except Exception as e:
                logger.error(f"Ошибка при обновлении chapter.json: {e}")

        data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                pass

        data["status"] = st
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)




    @property
    def segm_progress(self):
        """Свойство для совместимости с m8_3_utils.py"""
        return self.segm_prog

    def load_status(self):
        """Загрузка статуса обработки главы"""
        c_folder = self.chapter_paths.get("cleaning_folder", "")
        if not os.path.isdir(c_folder): return
        json_path = os.path.join(c_folder, self.status_json)

        chapter_json_path = os.path.join(self.chapter_folder, "chapter.json")
        if os.path.exists(chapter_json_path):
            try:
                with open(chapter_json_path, 'r', encoding='utf-8') as f:
                    chapter_data = json.load(f)

                cleaning_status = chapter_data.get("stages", {}).get("Клининг", False)

                if cleaning_status is True:
                    self.status_completed.setChecked(True)
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump({"status": "Завершен"}, f, ensure_ascii=False, indent=4)
                    return
                elif cleaning_status == "partial":
                    self.status_in_progress.setChecked(True)
                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump({"status": "В работе"}, f, ensure_ascii=False, indent=4)
                    return
            except:
                pass

        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    st = data.get("status", "Не начат")
                    if st == "Не начат":
                        self.status_not_started.setChecked(True)
                    elif st == "В работе":
                        self.status_in_progress.setChecked(True)
                    elif st == "Завершен":
                        self.status_completed.setChecked(True)
            except:
                pass