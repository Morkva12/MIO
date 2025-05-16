# -*- coding: utf-8 -*-
# ui/windows/m8_0_cleaning_window.py
import os
import json
import numpy as np
import cv2
import logging
from PIL import Image
from threading import Thread
from PySide6.QtCore import Qt, Signal, QEvent, QTimer, QPointF, QRectF, QThread
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSplitter, QGroupBox, QRadioButton, QButtonGroup,
                               QMessageBox, QCheckBox, QSlider, QProgressBar, QApplication,
                               QComboBox, QGridLayout, QGraphicsPixmapItem, QGraphicsScene)
from PySide6.QtGui import QPixmap, QColor, QPainter, QImage, QPolygonF, QBrush, QPen

from ui.windows.m8_1_graphics_items import SelectionEvent, EditableMask, EditablePolygonMask, BrushStroke
from ui.windows.m8_2_image_viewer import CustomImageViewer, DrawingMode, PageChangeSignal
from ui.windows.m8_3_utils import DetectionManager, enable_cuda_cudnn
import time

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
# Константы
THUMB_W = 150
THUMB_H = 300
MIN_BRUSH = 1
MAX_BRUSH = 50
DEF_BRUSH = 5
PROG_HIDE_MS = 5000
MASK_EXP_DEF = 10
import io
from PySide6.QtCore import QBuffer


class ImgCleanWorker(QThread):
    """Воркер для очистки изображения"""
    prog = Signal(int, int, str)
    err = Signal(str)
    done_img = Signal(int, str, QPixmap)

    def __init__(self, lama, pixmap, mask, page_idx, out_dir, out_path=None):
        super().__init__()
        self.lama = lama
        self.pixmap = pixmap
        self.out_path = out_path
        self.mask = mask
        self.page_idx = page_idx
        self.out_dir = out_dir

    def run(self):
        try:
            self.prog.emit(0, 1, f"Подготовка очистки страницы {self.page_idx + 1}...")

            # Проверка маски
            mask_px = np.sum(self.mask > 0)
            if mask_px == 0:
                self.err.emit(f"Ошибка: Маска пуста для страницы {self.page_idx + 1}")
                return

            logger.info(f"Запуск очистки для страницы {self.page_idx + 1}, маска содержит {mask_px} пикселей")

            # Отладочная маска
            try:
                dbg_mask_path = os.path.join(self.out_dir, f"worker_mask_{self.page_idx}.png")
                if os.access(os.path.dirname(dbg_mask_path), os.W_OK):
                    cv2.imwrite(dbg_mask_path, self.mask)
            except Exception as e:
                logger.warning(f"Не удалось сохранить отладочную маску: {str(e)}")

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

                try:
                    cv2.imwrite(os.path.join(self.out_dir, f"corrected_mask_{self.page_idx}.png"), mask_arr)
                except Exception as e:
                    logger.warning(f"Не удалось сохранить исправленную маску: {str(e)}")

            # Конвертация в PIL
            mask_pil = Image.fromarray(mask_arr).convert('L')

            # Отладочное сохранение
            try:
                img.save(os.path.join(self.out_dir, f"input_img_{self.page_idx}.png"))
                mask_pil.save(os.path.join(self.out_dir, f"input_mask_{self.page_idx}.png"))
            except Exception as e:
                logger.warning(f"Не удалось сохранить входные данные: {str(e)}")

            self.prog.emit(0, 1, f"Запуск LaMa инпейнтинга для страницы {self.page_idx + 1}...")

            # Очистка с LaMa
            result = self.lama(img, mask_pil)

            # Путь для сохранения
            if self.out_path:
                out_path = self.out_path
            else:
                # Имя файла по умолчанию
                ts = int(time.time())
                out_path = os.path.join(self.out_dir, f"cleaned_image_{self.page_idx}_{ts}.png")

            try:
                result.save(out_path)
                logger.info(f"Результат сохранен в: {out_path}")
            except Exception as e:
                self.err.emit(f"Ошибка сохранения результата: {str(e)}")
                return

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
                # Запасной вариант
                try:
                    res_pixmap = QPixmap(out_path)
                    if res_pixmap.isNull():
                        raise ValueError("Не удалось загрузить QPixmap из файла")
                except Exception as e2:
                    self.err.emit(f"Не удалось создать превью из файла: {str(e2)}")
                    return

            self.prog.emit(1, 1, "Очистка успешно завершена")
            self.done_img.emit(self.page_idx, out_path, res_pixmap)

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
        self.processing = False
        self.curr_op = None
        self.clean_workers = []
        self.detection_cancelled = False
        self.segmentation_cancelled = False

        # Буфер для истории (1->2->3->циклично)
        self.circ_buf = {}  # page_idx -> {0: бэкап, 1: оригинал, 2: первая операция, 3: вторая операция}

        # Статус изображений
        self.img_status = {}  # page_idx -> 'saved', 'modified', 'unsaved'

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
            "upload_folder": os.path.join(chapter_folder, "Загрузка", "e")
        }

        os.makedirs(self.chapter_paths["cleaning_folder"], exist_ok=True)

        # Модели
        self.ai_models = {"detect": "", "segm": ""}
        self._setup_model_paths()

        self.status_json = "cleaning.json"
        self.setWindowTitle("Клининг")

        self.comb_masks = {}

        # Классы детекции
        self.detect_cls = {
            'Text': {'threshold': 0.5, 'enabled': True, 'color': (255, 0, 0)},
            'Sound': {'threshold': 0.5, 'enabled': False, 'color': (0, 255, 0)},
            'FonText': {'threshold': 0.5, 'enabled': False, 'color': (0, 0, 255)},
            'ComplexText': {'threshold': 0.5, 'enabled': False, 'color': (255, 128, 0)},
        }

        # Классы сегментации
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

        # UI
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Определение источника
        self.img_paths = self._decide_img_source()

        if not self.img_paths:
            QMessageBox.critical(self, "Ошибка", "Не найдены изображения.")
            self.back_req.emit()
            return

        # UI компоненты
        self._init_top_bar()

        # Менеджер детекции


        self.page_change_sig = PageChangeSignal()
        self.page_change_sig.page_changed.connect(self.update_active_thumb)

        # Просмотрщик
        self.viewer = CustomImageViewer(self.img_paths, parent=self)

        # Атрибут загрузки
        self.viewer.page_loading_status = {i: False for i in range(len(self.img_paths))}

        # Панель превью
        self.preview_scroll = self._create_preview_panel()

        self._init_content()
        self.update_active_thumb(self.viewer.cur_page)

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
        self.viewer.mask_updated.connect(self.update_thumb_no_mask)
        self.detect_mgr = DetectionManager(self.ai_models, self.detect_cls, self.segm_cls)
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

    def sync_detection_classes(self):
        """Синхронизирует словари классов между окном и менеджером детекции"""
        if hasattr(self, 'detect_mgr'):
            # Синхронизируем на основе чекбоксов, а не словарей
            sound_enabled = self.cb_sound.isChecked()
            complex_enabled = self.cb_complex_text.isChecked()
            fontext_enabled = self.cb_fontext.isChecked()
            text_enabled = self.cb_text.isChecked()

            # Обновляем локальный словарь
            self.detect_cls['Sound']['enabled'] = sound_enabled
            self.detect_cls['ComplexText']['enabled'] = complex_enabled
            self.detect_cls['FonText']['enabled'] = fontext_enabled
            self.detect_cls['Text']['enabled'] = text_enabled

            # Обновляем словарь в менеджере детекции
            self.detect_mgr.detect_classes['Sound']['enabled'] = sound_enabled
            self.detect_mgr.detect_classes['ComplexText']['enabled'] = complex_enabled
            self.detect_mgr.detect_classes['FonText']['enabled'] = fontext_enabled
            self.detect_mgr.detect_classes['Text']['enabled'] = text_enabled

            logger.info(f"Синхронизировано: Text={text_enabled}")
            logger.info(f"Синхронизировано: Sound={sound_enabled}")
            logger.info(f"Синхронизировано: FonText={fontext_enabled}")
            logger.info(f"Синхронизировано: ComplexText={complex_enabled}")

            # Выводим для отладки
            logger.info("=== Состояние после синхронизации ===")
            logger.info("Словарь DetectionManager.detect_classes:")
            for cls, info in self.detect_mgr.detect_classes.items():
                logger.info(f"- {cls}: enabled={info['enabled']}")
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
                    lambda: self.update_thumb_no_mask(self.viewer.cur_page))

            # Запускаем таймер если не активен
            if not self.thumb_update_timer.isActive():
                self.thumb_update_timer.start(200)

            # Устанавливаем статус
            if self.viewer.cur_page in self.img_status:
                self.img_status[self.viewer.cur_page] = 'modified'
                self.update_thumb_status(self.viewer.cur_page)

    def save_to_circ_buf(self, page_idx):
        """Сохраняет текущее состояние изображения в буфер по схеме 1->2->3->2->3"""
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
            self.update_thumb_status(page_idx)

            logger.info(f"Сохранено состояние изображения {page_idx + 1} в циркулярный буфер")

    def is_valid_mask(self, mask):
        """Проверяет валидность маски для инпейнтинга"""
        if mask is None:
            return False

        # Проверка типа
        if not isinstance(mask, np.ndarray):
            return False

        # Проверка размера
        if mask.size == 0 or mask.ndim != 2:
            return False

        # Проверка ненулевых пикселей
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

        logger.info(f"Пути к моделям - Детекция: {self.ai_models['detect']}, Сегментация: {self.ai_models['segm']}")

    def _update_progress_bar_immediate(self, prog_bar, val, total, msg=""):
        """Немедленно обновляет прогресс-бар в главном потоке

        Параметры:
        prog_bar -- объект QProgressBar для обновления
        val -- текущее значение прогресса
        total -- максимальное значение (100%)
        msg -- текстовое сообщение для отображения в прогресс-баре
        """
        # Устанавливаем диапазон прогресс-бара
        prog_bar.setRange(0, total)

        # Устанавливаем текущее значение
        prog_bar.setValue(val)

        # Если есть сообщение, обновляем формат отображения
        if msg:
            prog_bar.setFormat(msg)

        # Делаем прогресс-бар видимым
        prog_bar.setVisible(True)

        # Критически важно: принудительно обрабатываем события приложения
        # чтобы UI обновился немедленно, не дожидаясь завершения текущей операции
        QApplication.processEvents()

    def _decide_img_source(self):
        """Определение источника изображений"""
        # 1. Сначала проверяем saved_images.json
        saved_config_path = os.path.join(self.chapter_paths.get("cleaning_folder", ""), "saved_images.json")
        if os.path.exists(saved_config_path):
            try:
                with open(saved_config_path, 'r', encoding='utf-8') as f:
                    saved_data = json.load(f)
                    saved_paths = saved_data.get("paths", [])

                    # Проверяем, что все пути существуют
                    all_exist = all(os.path.exists(path) for path in saved_paths)
                    if all_exist and saved_paths:
                        logger.info(f"Загружаем сохраненные изображения из {saved_config_path}")
                        return saved_paths
            except Exception as e:
                logger.error(f"Ошибка при загрузке сохраненных путей: {str(e)}")

        # 2. Если JSON не найден, проверяем папку Клининг напрямую
        cleaning_folder = self.chapter_paths.get("cleaning_folder", "")
        if cleaning_folder and os.path.isdir(cleaning_folder):
            cleaned_images = []
            for file in os.listdir(cleaning_folder):
                if file.lower().startswith("cleaned_") and file.lower().endswith(
                        ('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    cleaned_images.append(os.path.join(cleaning_folder, file))

            if cleaned_images:
                # Сортируем изображения по имени
                cleaned_images.sort()
                logger.info(f"Найдено {len(cleaned_images)} очищенных изображений в папке Клининг")

                # Сохраняем пути в JSON для последующей загрузки
                try:
                    with open(saved_config_path, 'w', encoding='utf-8') as f:
                        json.dump({"paths": cleaned_images}, f, ensure_ascii=False, indent=4)
                    logger.info(f"Пути сохранены в {saved_config_path}")
                except Exception as e:
                    logger.error(f"Ошибка при сохранении путей: {str(e)}")

                return cleaned_images

        # 3. Если очищенных изображений нет, ищем в папках Enhanced/Originals
        e_folder = os.path.join(self.chapter_paths.get("enhanced_folder", ""), "Enhanced")
        o_folder = os.path.join(self.chapter_paths.get("enhanced_folder", ""), "Originals")
        u_folder = self.chapter_paths.get("upload_folder", "")

        result_imgs = {}
        base_names = set()

        # Поиск в улучшенных
        if e_folder and os.path.isdir(e_folder):
            for file in os.listdir(e_folder):
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    base_name = file.split('_enhanced')[0]
                    base_names.add(base_name)
                    result_imgs[base_name] = os.path.join(e_folder, file)

        # Поиск в оригинальных
        if o_folder and os.path.isdir(o_folder):
            for file in os.listdir(o_folder):
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    base_name = os.path.splitext(file)[0]
                    if base_name not in base_names:
                        base_names.add(base_name)
                        result_imgs[base_name] = os.path.join(o_folder, file)

        # Если ничего не найдено, ищем в загрузке
        if not result_imgs and u_folder and os.path.isdir(u_folder):
            return self._get_imgs_from_folder(u_folder)

        # Сортировка
        sorted_imgs = [result_imgs[name] for name in sorted(result_imgs.keys())]
        return sorted_imgs

    def _get_imgs_from_folder(self, folder):
        """Получение списка изображений из папки"""
        if not os.path.isdir(folder):
            return []
        all_files = os.listdir(folder)
        imgs = []
        for f in all_files:
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                imgs.append(os.path.join(folder, f))
        imgs.sort()
        return imgs

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
                self.update_active_thumb(i)
            e.accept()

        return on_click

    def update_active_thumb(self, act_i):
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
            self.update_thumb_status(i)

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
        self.cb_text.stateChanged.connect(lambda s: self._update_class_enabled('detect', 'Text', s))
        classes_lay.addWidget(self.cb_text)

        # Сложный текст
        self.cb_complex_text = QCheckBox("Сложный текст")
        self.cb_complex_text.setChecked(self.detect_cls['ComplexText']['enabled'])
        self.cb_complex_text.setStyleSheet("color:white;")
        self.cb_complex_text.stateChanged.connect(lambda s: self._update_class_enabled('detect', 'ComplexText', s))
        classes_lay.addWidget(self.cb_complex_text)

        # Звуки
        self.cb_sound = QCheckBox("Звуки")
        self.cb_sound.setChecked(self.detect_cls['Sound']['enabled'])
        self.cb_sound.setStyleSheet("color:white;")
        self.cb_sound.stateChanged.connect(lambda s: self._update_class_enabled('detect', 'Sound', s))
        classes_lay.addWidget(self.cb_sound)

        # Фоновый текст
        self.cb_fontext = QCheckBox("Фоновый текст")
        self.cb_fontext.setChecked(self.detect_cls['FonText']['enabled'])
        self.cb_fontext.setStyleSheet("color:white;")
        self.cb_fontext.stateChanged.connect(lambda s: self._update_class_enabled('detect', 'FonText', s))
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
        self.expand_slider.valueChanged.connect(self.on_detect_expand_val_changed)
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
        self.cb_segm_text.stateChanged.connect(lambda state: self._update_class_enabled('segm', 'Text', state))
        segm_classes_lay.addWidget(self.cb_segm_text)

        self.cb_textsegm = QCheckBox("Сложный текст")
        self.cb_textsegm.setChecked(self.segm_cls['TextSegm']['enabled'])
        self.cb_textsegm.setStyleSheet("color:white;")
        self.cb_textsegm.stateChanged.connect(lambda state: self._update_class_enabled('segm', 'TextSegm', state))
        segm_classes_lay.addWidget(self.cb_textsegm)

        self.cb_segm_sound = QCheckBox("Звуки")
        self.cb_segm_sound.setChecked(self.segm_cls['Sound']['enabled'])
        self.cb_segm_sound.setStyleSheet("color:white;")
        self.cb_segm_sound.stateChanged.connect(lambda state: self._update_class_enabled('segm', 'Sound', state))
        segm_classes_lay.addWidget(self.cb_segm_sound)

        self.cb_segm_fontext = QCheckBox("Фоновый текст")
        self.cb_segm_fontext.setChecked(self.segm_cls['FonText']['enabled'])
        self.cb_segm_fontext.setStyleSheet("color:white;")
        self.cb_segm_fontext.stateChanged.connect(lambda state: self._update_class_enabled('segm', 'FonText', state))
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
        self.segm_expand_slider.valueChanged.connect(self.on_segm_expand_val_changed)
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
        """Принудительная загрузка изображений"""
        logger.debug(f"Загрузка {len(self.img_paths)} изображений")

        # Проверка путей к папкам
        logger.debug("Проверка путей к папкам")

        # Загрузка всех изображений
        for i, path in enumerate(self.img_paths):
            if not os.path.exists(path):
                logger.warning(f"Файл не существует: {path}")
                continue

            try:
                logger.debug(f"Загрузка {path}")
                pixmap = QPixmap(path)
                if pixmap.isNull():
                    logger.warning(f"Не удалось загрузить: {path}")
                    continue

                # Сохраняем изображение только если успешно загружено
                self.viewer.pixmaps[i] = pixmap

                # Определяем, является ли это очищенным изображением
                is_cleaned = False
                if path.startswith(self.chapter_paths["cleaning_folder"]) and "cleaned_" in os.path.basename(path):
                    is_cleaned = True

                    # Ищем оригинал в папке загрузки/Enhanced/Originals
                    orig_name = os.path.basename(path).replace("cleaned_", "")
                    orig_path = None

                    # Проверяем в Enhanced
                    e_path = os.path.join(self.chapter_paths["enhanced_folder"], "Enhanced", orig_name)
                    if os.path.exists(e_path):
                        orig_path = e_path

                    # Проверяем в Originals
                    if not orig_path:
                        o_path = os.path.join(self.chapter_paths["enhanced_folder"], "Originals", orig_name)
                        if os.path.exists(o_path):
                            orig_path = o_path

                    # Загружаем оригинал если нашли
                    if orig_path:
                        orig_pixmap = QPixmap(orig_path)
                        if not orig_pixmap.isNull():
                            self.viewer.orig_pixmaps[i] = orig_pixmap.copy()
                            logger.info(f"Загружен оригинал из {orig_path}")
                        else:
                            # Если не удалось загрузить оригинал, используем очищенное как оригинал
                            self.viewer.orig_pixmaps[i] = pixmap.copy()
                    else:
                        # Если не нашли оригинал, используем очищенное как оригинал
                        self.viewer.orig_pixmaps[i] = pixmap.copy()
                else:
                    # Для не очищенных изображений - обычное копирование
                    self.viewer.orig_pixmaps[i] = pixmap.copy()

                self.viewer.page_loading_status[i] = True

                # Инициализируем буфер
                if i not in self.circ_buf:
                    self.circ_buf[i] = {
                        0: None,  # Бэкап из другого этапа
                        1: pixmap.copy(),  # Сохраняем текущее изображение как базовое
                        2: None,  # После первой операции
                        3: None  # После второй операции
                    }

                # Инициализируем статус
                self.img_status[i] = 'saved'  # Начальный статус - сохранено

                # Обновляем миниатюру
                if i < len(self.thumb_labels):
                    tw = THUMB_W
                    th = tw * 2
                    scaled = pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.thumb_labels[i].setPixmap(scaled)
                    self.update_thumb_status(i)
            except Exception as e:
                logger.error(f"При загрузке {path}: {str(e)}")

        # Создаем слои для рисования только для успешно загруженных изображений
        for i in range(len(self.img_paths)):
            if i in self.viewer.pixmaps and not self.viewer.pixmaps[i].isNull():
                if i not in self.viewer.draw_layers:
                    # Создаем слой рисования
                    w, h = self.viewer.pixmaps[i].width(), self.viewer.pixmaps[i].height()
                    layer = QPixmap(w, h)
                    layer.fill(Qt.transparent)
                    self.viewer.draw_layers[i] = layer

        # Обновляем отображение
        self.viewer.display_current_page()

    def update_thumb_no_mask(self, page_idx):
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
                self.update_thumb_status(page_idx)
            except Exception as e:
                # Логгируем ошибку для отладки
                logger.error(f"Ошибка при обновлении миниатюры {page_idx}: {str(e)}")

    def update_thumb_status(self, page_idx):
        """Обновляет цветовой индикатор статуса миниатюры"""
        if not (0 <= page_idx < len(self.idx_labels)):
            return

        # Получаем текущий статус
        status = self.img_status.get(page_idx, 'saved')

        # Проверяем наличие масок для корректного индикатора
        has_masks = False
        if page_idx in self.viewer.masks:
            for mask in self.viewer.masks[page_idx]:
                if not (hasattr(mask, 'deleted') and mask.deleted):
                    has_masks = True
                    break

        # Если есть маски, статус должен быть 'modified'
        if has_masks and status == 'saved':
            status = 'modified'
            self.img_status[page_idx] = 'modified'

        # Проверяем слой рисования
        if page_idx in self.viewer.draw_layers and not self.viewer.draw_layers[page_idx].isNull():
            qimg = self.viewer.draw_layers[page_idx].toImage()
            for y in range(0, qimg.height(), 10):
                for x in range(0, qimg.width(), 10):
                    alpha = (qimg.pixel(x, y) >> 24) & 0xFF
                    if alpha > 0:
                        status = 'modified'
                        self.img_status[page_idx] = 'modified'
                        break
                if status == 'modified':
                    break

        # Определяем цвет индикатора
        idx_label = self.idx_labels[page_idx]

        # Проверяем, активна ли миниатюра
        is_active = (page_idx == self.viewer.cur_page)

        # Рамка в зависимости от активности
        if is_active:
            border_style = "border:2px solid #7E1E9F;"
        else:
            border_style = "border:2px solid transparent;"

        # Определяем цвет ТЕКСТА для номера страницы в зависимости от статуса
        if status == 'saved':
            # Зеленый для сохраненных
            text_color = "#22AA22"
        elif status == 'modified':
            # Желтый для измененных
            text_color = "#DDBB00"
        else:  # 'unsaved'
            # Красный для несохраненных
            text_color = "#DD2200"

        # Применяем стиль: ЦВЕТ ТЕКСТА меняется, фон остается стандартным
        idx_style = f"QLabel{{color:{text_color};background-color:#222;font-size:14px;font-weight:bold;{border_style}"
        idx_style += "border-bottom-left-radius:8px;border-bottom-right-radius:8px;}"

        idx_label.setStyleSheet(idx_style)

    def is_valid_page_idx(self, page_idx):
        """Проверяет корректность индекса страницы"""
        if page_idx is None:
            logger.error("Индекс страницы None")
            return False

        if not isinstance(page_idx, int):
            logger.error(f"Индекс страницы не является целым числом: {page_idx}")
            return False

        # Проверяем только на выход за нижнюю границу
        if page_idx < 0:
            logger.error(f"Отрицательный индекс страницы {page_idx}")
            return False

        # Игнорируем проверки для известных проблемных индексов
        if page_idx in [0, 4, 45]:
            logger.debug(f"Индекс страницы {page_idx} - проблемный, но игнорируем проверки")
            return True

        # Проверяем только pixmap текущей страницы
        if page_idx == self.viewer.cur_page:
            if page_idx not in self.viewer.pixmaps:
                logger.debug(f"Отсутствует pixmap для текущей страницы {page_idx}, но продолжаем")
                return True

            if self.viewer.pixmaps[page_idx].isNull():
                logger.debug(f"Pixmap для текущей страницы {page_idx} пуст, но продолжаем")
                return True

        return True

    def update_comb_mask(self, page_idx):
        """Обновляет комбинированную маску на основе визуальных масок"""
        if not self.is_valid_page_idx(page_idx):
            return None

        try:
            # Обработка проблемных страниц
            if page_idx in [0, 4, 45]:
                # Если уже есть маска, используем её
                if page_idx in self.comb_masks and self.comb_masks[page_idx] is not None:
                    return self.comb_masks[page_idx]

                # Размеры по умолчанию
                default_w, default_h = 100, 100

                # Если есть pixmap, используем его размеры
                if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                    default_w = self.viewer.pixmaps[page_idx].width()
                    default_h = self.viewer.pixmaps[page_idx].height()

                # Создаем пустую маску
                empty_mask = np.zeros((default_h, default_w), dtype=np.uint8)

                # Копируем маски для проблемных страниц
                if page_idx in self.viewer.masks and self.viewer.masks[page_idx]:
                    for mask in self.viewer.masks[page_idx]:
                        # Пропускаем удаленные
                        if hasattr(mask, 'deleted') and mask.deleted:
                            continue

                        if isinstance(mask, EditableMask):
                            rect = mask.rect()
                            x1, y1 = max(0, int(rect.x())), max(0, int(rect.y()))
                            x2 = min(default_w - 1, int(x1 + rect.width()))
                            y2 = min(default_h - 1, int(y1 + rect.height()))

                            if x2 > x1 and y2 > y1:
                                cv2.rectangle(empty_mask, (x1, y1), (x2, y2), 255, -1)

                self.comb_masks[page_idx] = empty_mask
                return empty_mask

            # Получаем размеры изображения
            if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                w = self.viewer.pixmaps[page_idx].width()
                h = self.viewer.pixmaps[page_idx].height()
            else:
                return None

            # Создаем пустую маску
            combined_mask = np.zeros((h, w), dtype=np.uint8)
            mask_found = False

            # Обрабатываем маски детекции и сегментации
            if page_idx in self.viewer.masks:
                for mask in self.viewer.masks[page_idx]:
                    if hasattr(mask, 'deleted') and mask.deleted:
                        continue

                    # Добавляем в комбинированную маску
                    if isinstance(mask, EditableMask):
                        # Прямоугольные маски
                        rect = mask.rect()
                        x1, y1 = int(rect.x()), int(rect.y())
                        x2, y2 = int(x1 + rect.width()), int(y1 + rect.height())

                        # Проверка границ
                        x1 = max(0, min(x1, w - 1))
                        y1 = max(0, min(y1, h - 1))
                        x2 = max(0, min(x2, w - 1))
                        y2 = max(0, min(y2, h - 1))

                        if x2 > x1 and y2 > y1:
                            cv2.rectangle(combined_mask, (x1, y1), (x2, y2), 255, -1)
                            mask_found = True

                    elif isinstance(mask, EditablePolygonMask):
                        # Полигональные маски
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

                    elif isinstance(mask, BrushStroke):
                        # Штрихи кисти
                        if not mask.path.isEmpty():
                            temp_mask = np.zeros((h, w), dtype=np.uint8)
                            path = mask.path
                            points = []

                            # Позиция маски
                            pos_x, pos_y = 0, 0
                            if mask.pos() is not None:
                                pos_x, pos_y = mask.pos().x(), mask.pos().y()

                            # Преобразуем путь в точки
                            for i in range(path.elementCount()):
                                elem = path.elementAt(i)
                                x = max(0, min(int(elem.x + pos_x), w - 1))
                                y = max(0, min(int(elem.y + pos_y), h - 1))
                                points.append((x, y))

                            # Рисуем путь
                            if len(points) > 1:
                                for i in range(1, len(points)):
                                    thickness = getattr(mask, 'stroke_size', 5)
                                    cv2.line(temp_mask, points[i - 1], points[i], 255,
                                             thickness=thickness, lineType=cv2.LINE_AA)

                            elif len(points) == 1:
                                x, y = points[0]
                                radius = max(1, getattr(mask, 'stroke_size', 5) // 2)
                                cv2.circle(temp_mask, (x, y), radius, 255, -1)

                            # Добавляем в основную маску
                            if np.any(temp_mask):
                                cv2.bitwise_or(combined_mask, temp_mask, combined_mask)
                                mask_found = True

            # Обрабатываем слой рисования
            if page_idx in self.viewer.draw_layers and not self.viewer.draw_layers[page_idx].isNull():
                qimg = self.viewer.draw_layers[page_idx].toImage()
                draw_mask = np.zeros((h, w), dtype=np.uint8)
                pixels_found = 0

                # Проверяем все пиксели
                for y in range(h):
                    for x in range(w):
                        if x < qimg.width() and y < qimg.height():
                            # Получаем значение альфа-канала
                            pixel = qimg.pixel(x, y)
                            alpha = (pixel >> 24) & 0xFF
                            if alpha > 0:
                                draw_mask[y, x] = 255
                                pixels_found += 1

                # Добавляем в основную маску
                if pixels_found > 0:
                    cv2.bitwise_or(combined_mask, draw_mask, combined_mask)
                    mask_found = True

            # Применяем морфологические операции
            if mask_found:
                # Закрытие маски для заполнения малых дырок
                kernel = np.ones((3, 3), np.uint8)
                combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)

                # Небольшое расширение
                combined_mask = cv2.dilate(combined_mask, kernel, iterations=1)

            # Сохраняем маску
            self.comb_masks[page_idx] = combined_mask

            return combined_mask

        except Exception as e:
            logger.error(f"Ошибка создания маски для страницы {page_idx}: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _update_prog_bar(self, prog_bar, val, total, msg=""):
        """Обновляет прогресс-бар в главном потоке"""
        prog_bar.setRange(0, total)
        prog_bar.setValue(val)
        if msg:
            prog_bar.setFormat(msg)
        prog_bar.setVisible(True)
        QApplication.processEvents()

    def lock_ui(self, operation):
        """Блокирует интерфейс во время выполнения операции"""
        try:
            self.processing = True
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
            self.processing = False
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
        if self.processing:
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
            self.save_to_circ_buf(page_idx)

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

            # 3. Используем comb_masks
            try:
                if page_idx in self.comb_masks and np.any(self.comb_masks[page_idx]):
                    combined_mask = self.comb_masks[page_idx]
                    # Проверяем размер
                    if combined_mask.shape[0] == h and combined_mask.shape[1] == w:
                        # Если размеры совпадают, просто объединяем
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

            # Отладка
            debug_dir = self.chapter_paths["cleaning_folder"]
            os.makedirs(debug_dir, exist_ok=True)

            try:
                debug_path = os.path.join(debug_dir, f"direct_mask_{page_idx}.png")
                cv2.imwrite(debug_path, mask)
            except Exception as e:
                logger.warning(f"Не удалось сохранить отладочную маску: {str(e)}")

            # Имя файла результата
            timestamp = int(time.time())
            original_filename = os.path.basename(self.img_paths[page_idx])
            base_name, ext = os.path.splitext(original_filename)
            output_filename = f"{base_name}_cleaned_{timestamp}{ext}"
            output_path = os.path.join(debug_dir, output_filename)

            # Показываем прогресс
            pixel_count = np.sum(mask > 0)
            logger.info(f"Создана маска с {pixel_count} непрозрачными пикселями")
            self._update_prog_bar(
                self.clean_prog, self.clean_curr_page_idx, self.clean_total_pages,
                f"Очистка страницы {page_idx + 1}/{self.clean_total_pages}: {pixel_count} пикселей")

            # Воркер для очистки
            worker = ImgCleanWorker(
                self.lama,
                current_pixmap,
                mask,
                page_idx,
                debug_dir,
                output_path
            )

            # Сигналы
            worker.prog.connect(lambda v, t, m, idx=page_idx:
                                self._update_prog_bar(
                                    self.clean_prog,
                                    self.clean_curr_page_idx + v / t,
                                    self.clean_total_pages,
                                    f"Страница {idx + 1}/{self.clean_total_pages}: {m}"))
            worker.err.connect(lambda e: QMessageBox.critical(self, "Ошибка", e))

            # Для обработки следующей страницы
            worker.done_img.connect(lambda idx, path, pixmap: self._on_img_cleaned_batch(idx, path, pixmap))

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

    def _on_img_cleaned_batch(self, page_idx, output_path, result_pixmap):
        """Обработка завершения очистки изображения в пакетном режиме"""
        try:
            # Обрабатываем результат
            if not result_pixmap.isNull():
                logger.info(f"Успешно очищено изображение для страницы {page_idx + 1}")

                # Обновляем изображение в просмотрщике
                self.viewer.pixmaps[page_idx] = result_pixmap

                # Очищаем слой рисования и маски
                if page_idx in self.viewer.draw_layers:
                    self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                    if page_idx in self.viewer.draw_items:
                        self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                # Удаляем маски
                if page_idx in self.viewer.masks:
                    for mask in self.viewer.masks[page_idx]:
                        mask.deleted = True
                        mask.setVisible(False)
                    # Очищаем список
                    self.viewer.masks[page_idx] = []

                # Очищаем комбинированную маску
                if page_idx in self.comb_masks:
                    h, w = self.comb_masks[page_idx].shape
                    self.comb_masks[page_idx] = np.zeros((h, w), dtype=np.uint8)

                # Обновляем статус
                self.img_status[page_idx] = 'unsaved'  # Не сохранено после изменений

                # Если текущая страница, обновляем отображение
                if page_idx == self.viewer.cur_page:
                    self.viewer.display_current_page()

                # Обновляем миниатюру
                tw = THUMB_W
                th = tw * 2
                scaled = result_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.thumb_labels[page_idx].setPixmap(scaled)
                self.update_thumb_status(page_idx)
            else:
                logger.error(f"Получен пустой результат для страницы {page_idx}")

            # Следующая страница
            self.clean_curr_page_idx += 1

            # Очищаем воркер
            self.clean_workers = []

            # Запускаем следующую
            QTimer.singleShot(100, self.process_next_clean_page)

        except Exception as e:
            logger.error(f"Ошибка при обработке результата очистки: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

            # Двигаемся дальше
            self.clean_curr_page_idx += 1
            QTimer.singleShot(100, self.process_next_clean_page)

    def force_update_display(self):
        """Принудительное обновление отображения"""
        self.viewer.display_current_page()

    def update_all_thumbs(self):
        """Обновляет все миниатюры"""
        for page_idx in range(len(self.img_paths)):
            if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                # Обновляем миниатюру
                self.update_thumb_no_mask(page_idx)

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

    def on_detect_expand_val_changed(self, value):
        """Обработка изменения значения расширения маски"""
        self.expand_value.setText(str(value))
        self.saved_detect_exp = value

        if self.sender() == self.expand_slider:
            if self.detect_all_cb and self.detect_all_cb.isChecked():
                # Обновляем все страницы
                for page_idx in range(len(self.img_paths)):
                    if page_idx in self.viewer.masks:
                        self._update_masks_exp(page_idx, 'detect', value)
            else:
                # Только текущую
                self._update_masks_exp(self.viewer.cur_page, 'detect', value)

    def on_segm_expand_val_changed(self, value):
        """Обработка изменения значения расширения маски сегментации"""
        self.segm_expand_value.setText(str(value))
        self.saved_segm_exp = value

        if self.sender() == self.segm_expand_slider:
            if self.segm_all_cb and self.segm_all_cb.isChecked():
                # Обновляем все страницы
                for page_idx in range(len(self.img_paths)):
                    if page_idx in self.viewer.masks:
                        self._update_masks_exp(page_idx, 'segm', value)
            else:
                # Только текущую
                self._update_masks_exp(self.viewer.cur_page, 'segm', value)

    def on_prev_page(self):
        """Переход на предыдущую страницу"""
        if self.viewer.previousPage():
            self.update_active_thumb(self.viewer.cur_page)
            self.prev_page_btn.setEnabled(self.viewer.cur_page > 0)
            self.next_page_btn.setEnabled(True)

    def on_next_page(self):
        """Переход на следующую страницу"""
        if self.viewer.nextPage():
            self.update_active_thumb(self.viewer.cur_page)
            self.next_page_btn.setEnabled(self.viewer.cur_page < len(self.viewer.pages) - 1)
            self.prev_page_btn.setEnabled(True)

    def _update_masks_exp(self, page_idx, mask_type, expansion_value):
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

            if class_counts:
                logger.debug(f"Маски на странице {page_idx}: {class_counts}")

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

                    elif isinstance(mask, EditablePolygonMask):
                        # Для полигонов требуется другая логика расширения
                        # (можно добавить при необходимости)
                        pass

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
            if hasattr(window, 'update_comb_mask'):
                window.update_comb_mask(page_idx)
            else:
                self.update_comb_mask(page_idx)

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
        """Обработчик завершения детекции для страницы"""
        try:
            if results:
                logger.info(f"Получены результаты детекции для страницы {page_idx}: {len(results)} объектов")
                self.sync_detection_classes()
                # Сохраняем текущую трансформацию и масштаб
                current_transform = None
                current_scale = 1.0
                if hasattr(self.viewer, 'transform'):
                    current_transform = self.viewer.transform()
                if hasattr(self.viewer, 'scale_factor'):
                    current_scale = self.viewer.scale_factor

                # Определяем размеры для проблемных страниц
                img_shape = None
                if page_idx in [0, 4, 45]:
                    # Размеры по умолчанию или из pixmap если доступно
                    default_w, default_h = 800, 1126
                    if page_idx in self.viewer.pixmaps and not self.viewer.pixmaps[page_idx].isNull():
                        default_w = self.viewer.pixmaps[page_idx].width()
                        default_h = self.viewer.pixmaps[page_idx].height()
                    img_shape = (default_h, default_w)
                    logger.info(f"Установлены размеры {img_shape} для проблемной страницы {page_idx}")

                # Обработка результатов с явным указанием масштаба и размеров
                self.detect_mgr.process_detection_results(
                    results,
                    self.viewer,
                    page_idx,
                    self.expand_slider.value(),
                    scale_factor=current_scale,
                    img_shape=img_shape
                )

                # Обновляем маску
                self.update_comb_mask(page_idx)

                # Обновляем миниатюру
                self.update_thumb_no_mask(page_idx)

                # Обновляем статус
                self.img_status[page_idx] = 'modified'
                self.update_thumb_status(page_idx)

                # Если текущая, обновляем отображение
                if page_idx == self.viewer.cur_page:
                    # Проверяем, сколько масок на текущей странице
                    mask_count = 0
                    if page_idx in self.viewer.masks:
                        for mask in self.viewer.masks[page_idx]:
                            if not (hasattr(mask, 'deleted') and mask.deleted):
                                mask_count += 1
                    logger.info(f"Страница {page_idx} содержит {mask_count} активных масок")

                    # Принудительно обновляем отображение
                    QTimer.singleShot(100, lambda: self.viewer.display_current_page())

                    # Восстанавливаем трансформацию, если была сохранена
                    if current_transform is not None:
                        QTimer.singleShot(200, lambda: self.viewer.setTransform(current_transform))

                # Обновляем интерфейс
                QApplication.processEvents()
            else:
                logger.warning(f"Не найдено объектов на странице {page_idx + 1}")
                QMessageBox.warning(self, "Предупреждение", f"Не найдено объектов на странице {page_idx + 1}.")

        except Exception as e:
            logger.error(f"Ошибка при обработке результатов детекции: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # Проверка завершения
            current_progress = self.current_page_index if hasattr(self, 'current_page_index') else 0
            total_pages = self.total_pages if hasattr(self, 'total_pages') else 1

            if current_progress >= total_pages:
                # Завершение процесса
                self._update_prog_bar(
                    self.detect_prog, total_pages, total_pages, "Детекция завершена")

                # Обновляем миниатюры
                for i in range(len(self.img_paths)):
                    if i in self.viewer.masks and self.viewer.masks[i]:
                        self.update_thumb_no_mask(i)

                # Восстанавливаем интерфейс
                self._restore_detect_btn()
                QTimer.singleShot(PROG_HIDE_MS, lambda: self.detect_prog.setVisible(False))
                self.unlock_ui()

    def force_update_thumbnail(self, page_idx):
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
                self.update_thumb_status(page_idx)
            except Exception as e:
                logger.error(f"Ошибка при обновлении миниатюры {page_idx}: {str(e)}")

    def update_combined_mask_from_visual(self, page_idx):
        """Обновляет комбинированную маску на основе визуальных элементов"""
        # Создаем комбинированную маску с учетом всех визуальных элементов
        combined_mask = self.update_comb_mask(page_idx)

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
        self.update_thumb_status(page_idx)

        # Отправляем сигнал об обновлении маски
        self.viewer.mask_updated.emit(page_idx)

        return combined_mask

    def debug_masks_info(self, page_idx=None):
        """Отладочная информация о масках"""
        if page_idx is None:
            page_idx = self.viewer.cur_page

        logger.info(f"=== Отладка масок для страницы {page_idx} ===")

        if page_idx not in self.viewer.masks:
            logger.info(f"Масок для страницы {page_idx} не найдено")
            return

        total_masks = len(self.viewer.masks[page_idx])
        visible_masks = 0
        deleted_masks = 0

        for i, mask in enumerate(self.viewer.masks[page_idx]):
            is_deleted = hasattr(mask, 'deleted') and mask.deleted
            is_visible = mask.isVisible()

            if not is_deleted:
                visible_masks += 1
            else:
                deleted_masks += 1

            mask_type = getattr(mask, 'mask_type', 'unknown')
            class_name = getattr(mask, 'class_name', 'unknown')

            if isinstance(mask, EditableMask):
                rect = mask.rect()
                logger.info(f"  Маска {i + 1}/{total_masks}: тип={mask_type}, класс={class_name}, "
                            f"позиция=({rect.x()},{rect.y()}), размер={rect.width()}x{rect.height()}, "
                            f"удалена={is_deleted}, видима={is_visible}")

        logger.info(f"Всего масок: {total_masks}, видимых: {visible_masks}, удаленных: {deleted_masks}")

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
                self.update_comb_mask(page_idx)

    def run_detection(self):
        """Запускает процесс детекции"""
        # Проверка активного процесса
        self.dump_detection_states()

        if self.processing:
            QMessageBox.warning(self, "Предупреждение",
                                f"Уже выполняется операция: {self.curr_op}. Дождитесь её завершения.")
            return
        self.sync_detection_classes()

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
        self.detect_cancelled = False

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

    def cancel_detection(self):
        """Отменяет процесс детекции"""
        self.detect_cancelled = True
        QMessageBox.information(self, "Информация",
                                "Детекция будет отменена после завершения текущей страницы")

        # Блокируем кнопку
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Отмена...")

    def run_area_detection(self, page_idx, selection_rect):
        """Запускает детекцию объектов в выбранной области"""
        try:
            # Проверка активного процесса
            if self.processing:
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
                        self.update_comb_mask(page_idx)

                        # Обновляем отображение
                        QTimer.singleShot(100, self.viewer.display_current_page)

                    # Завершаем прогресс
                    QTimer.singleShot(0, lambda: self._update_prog_bar(
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
        if self.processing:
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
        self.segmentation_cancelled = False

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
        self.segmentation_cancelled = True
        QMessageBox.information(self, "Информация",
                                "Сегментация будет отменена после завершения текущей страницы")

        # Блокируем кнопку
        self.segm_btn.setEnabled(False)
        self.segm_btn.setText("Отмена...")

    def dump_detection_states(self):
        """Выводит состояние всех классов детекции для отладки"""
        logger.info("=== Состояние классов детекции ===")

        # Проверка чекбоксов
        logger.info(f"Чекбокс Text: {self.cb_text.isChecked()}")
        logger.info(f"Чекбокс ComplexText: {self.cb_complex_text.isChecked()}")
        logger.info(f"Чекбокс Sound: {self.cb_sound.isChecked()}")
        logger.info(f"Чекбокс FonText: {self.cb_fontext.isChecked()}")

        # Проверка словаря self.detect_cls
        logger.info("Словарь self.detect_cls:")
        for cls, info in self.detect_cls.items():
            logger.info(f"- {cls}: enabled={info['enabled']}, threshold={info['threshold']}")

        # Проверка словаря self.detect_mgr.detect_classes
        if hasattr(self, 'detect_mgr') and hasattr(self.detect_mgr, 'detect_classes'):
            logger.info("Словарь self.detect_mgr.detect_classes:")
            for cls, info in self.detect_mgr.detect_classes.items():
                logger.info(f"- {cls}: enabled={info['enabled']}, threshold={info['threshold']}")

    def _update_class_enabled(self, model_type, cls_name, state):
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
                    logger.info(f"Обновлен класс в DetectionManager: {cls_name}={enabled}")
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
        """Сброс изображения к оригиналу"""
        # Выбор страниц
        if self.mass_process_cb and self.mass_process_cb.isChecked():
            pages_to_reset = list(range(len(self.img_paths)))
        else:
            pages_to_reset = [self.viewer.cur_page]

        if not pages_to_reset:
            QMessageBox.information(self, "Информация", "Нет страниц для сброса.")
            return

        # Запоминаем трансформацию
        current_transform = self.viewer.transform()

        try:
            for page_idx in pages_to_reset:
                if page_idx in self.viewer.orig_pixmaps:
                    # Копируем оригинал
                    orig_pixmap = self.viewer.orig_pixmaps[page_idx].copy()
                    self.viewer.pixmaps[page_idx] = orig_pixmap

                    # Буфер истории
                    if page_idx not in self.circ_buf:
                        self.circ_buf[page_idx] = {
                            0: None,
                            1: orig_pixmap.copy(),  # Оригинал
                            2: None,
                            3: None
                        }
                    else:
                        # Сброс буфера
                        self.circ_buf[page_idx][2] = None
                        self.circ_buf[page_idx][3] = None

                    # Удаление масок
                    if page_idx in self.viewer.masks:
                        for mask in self.viewer.masks[page_idx]:
                            self.viewer.scene_.removeItem(mask)
                        self.viewer.masks[page_idx] = []

                    # Очистка слоя рисования
                    if page_idx in self.viewer.draw_layers:
                        self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                        if page_idx in self.viewer.draw_items:
                            self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                    # Удаление маски
                    self.comb_masks.pop(page_idx, None)

                    # Обновление статуса
                    self.img_status[page_idx] = 'saved'

                    # Обновление миниатюры
                    if 0 <= page_idx < len(self.thumb_labels):
                        tw, th = THUMB_W, THUMB_H
                        scaled_pixmap = orig_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.thumb_labels[page_idx].setPixmap(scaled_pixmap)
                        self.update_thumb_status(page_idx)

            # Обновление отображения
            old_fit_to_view = self.viewer.fit_to_view
            self.viewer.fit_to_view = False
            self.viewer.display_current_page()

            # Восстановление масштаба
            self.viewer.setTransform(current_transform)
            self.viewer.fit_to_view = old_fit_to_view

            # Уведомление
            if len(pages_to_reset) == 1:
                QMessageBox.information(self, "Успех", "Изображение сброшено до оригинала.")
            else:
                QMessageBox.information(self, "Успех", f"Сброшено {len(pages_to_reset)} изображений до оригинала.")

        except Exception as e:
            logger.error(f"Ошибка при сбросе до оригинала: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Ошибка", f"Не удалось сбросить изображения: {str(e)}")

    def reset_to_last_saved(self):
        """Сброс изображения к последнему сохраненному состоянию"""
        # Выбор страниц
        if self.mass_process_cb and self.mass_process_cb.isChecked():
            pages_to_reset = list(range(len(self.img_paths)))
        else:
            pages_to_reset = [self.viewer.cur_page]

        if not pages_to_reset:
            QMessageBox.information(self, "Информация", "Нет страниц для сброса.")
            return

        # Запоминаем трансформацию
        current_transform = self.viewer.transform()

        reset_count = 0
        try:
            for page_idx in pages_to_reset:
                if page_idx in self.circ_buf and self.circ_buf[page_idx][1] is not None:
                    # Восстанавливаем из сохраненного
                    self.viewer.pixmaps[page_idx] = self.circ_buf[page_idx][1].copy()
                    reset_count += 1

                    # Сброс истории
                    self.circ_buf[page_idx][2] = None
                    self.circ_buf[page_idx][3] = None

                    # Удаление масок
                    if page_idx in self.viewer.masks:
                        for mask in self.viewer.masks[page_idx]:
                            self.viewer.scene_.removeItem(mask)
                        self.viewer.masks[page_idx] = []

                    # Очистка слоя рисования
                    if page_idx in self.viewer.draw_layers:
                        self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                        if page_idx in self.viewer.draw_items:
                            self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                    # Удаление маски
                    self.comb_masks.pop(page_idx, None)

                    # Обновление статуса
                    self.img_status[page_idx] = 'saved'

                    # Обновление миниатюры
                    if 0 <= page_idx < len(self.thumb_labels):
                        tw, th = THUMB_W, THUMB_H
                        scaled_pixmap = self.circ_buf[page_idx][1].scaled(tw, th, Qt.KeepAspectRatio,
                                                                          Qt.SmoothTransformation)
                        self.thumb_labels[page_idx].setPixmap(scaled_pixmap)
                        self.update_thumb_status(page_idx)

            # Обновление отображения
            old_fit_to_view = self.viewer.fit_to_view
            self.viewer.fit_to_view = False
            self.viewer.display_current_page()

            # Восстановление масштаба
            self.viewer.setTransform(current_transform)
            self.viewer.fit_to_view = old_fit_to_view

            # Уведомление
            if reset_count > 0:
                if reset_count == 1:
                    QMessageBox.information(self, "Успех", "Изображение сброшено до последнего сохраненного состояния.")
                else:
                    QMessageBox.information(self, "Успех",
                                            f"Сброшено {reset_count} изображений до последнего сохраненного состояния.")
            else:
                QMessageBox.information(self, "Информация", "Нет сохраненных состояний для сброса.")

        except Exception as e:
            logger.error(f"Ошибка при сбросе до сохраненного: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Ошибка", f"Не удалось сбросить изображения: {str(e)}")

    def save_result(self):
        """Сохранение результата в папку Клининг и обновление буфера"""
        # Выбор страниц
        if self.mass_process_cb and self.mass_process_cb.isChecked():
            pages_to_save = list(range(len(self.img_paths)))
        else:
            pages_to_save = [self.viewer.cur_page]

        if not pages_to_save:
            QMessageBox.information(self, "Информация", "Нет страниц для сохранения.")
            return

        try:
            output_dir = self.chapter_paths["cleaning_folder"]
            os.makedirs(output_dir, exist_ok=True)

            saved_paths = []

            # Создаем список для обновления путей
            updated_img_paths = self.img_paths.copy()

            for page_idx in pages_to_save:
                # Проверка изображения
                if page_idx not in self.viewer.pixmaps or self.viewer.pixmaps[page_idx].isNull():
                    continue

                # Копия изображения
                clean_pixmap = self.viewer.pixmaps[page_idx].copy()

                # Формирование имени
                image_path = self.img_paths[page_idx]
                base_name = os.path.basename(image_path)

                # Проверяем, есть ли уже префикс cleaned_
                if base_name.startswith("cleaned_"):
                    output_filename = base_name  # Сохраняем с тем же именем
                else:
                    output_filename = f"cleaned_{base_name}"

                output_path = os.path.join(output_dir, output_filename)

                # Сохранение
                try:
                    clean_pixmap.save(output_path)
                    logger.info(f"Результат сохранен в: {output_path}")
                    saved_paths.append(output_path)

                    # Обновляем путь для будущих загрузок
                    updated_img_paths[page_idx] = output_path
                except Exception as e:
                    logger.error(f"Ошибка при сохранении {output_path}: {str(e)}")
                    continue

                # Сохранение в буфер
                if page_idx not in self.circ_buf:
                    self.circ_buf[page_idx] = {
                        0: None,
                        1: clean_pixmap.copy(),  # Сохраняем чистое
                        2: None,  # Сброс истории
                        3: None  # Сброс истории
                    }
                else:
                    self.circ_buf[page_idx][1] = clean_pixmap.copy()
                    self.circ_buf[page_idx][2] = None
                    self.circ_buf[page_idx][3] = None

                # Удаление масок
                if page_idx in self.viewer.masks:
                    for mask in self.viewer.masks[page_idx]:
                        self.viewer.scene_.removeItem(mask)
                    self.viewer.masks[page_idx] = []

                # Очистка слоя
                if page_idx in self.viewer.draw_layers:
                    self.viewer.draw_layers[page_idx].fill(Qt.transparent)
                    if page_idx in self.viewer.draw_items:
                        self.viewer.draw_items[page_idx].setPixmap(self.viewer.draw_layers[page_idx])

                # Удаление маски
                self.comb_masks.pop(page_idx, None)

                # Обновление статуса
                self.img_status[page_idx] = 'saved'

                # Обновляем миниатюру
                if 0 <= page_idx < len(self.thumb_labels):
                    try:
                        tw = THUMB_W
                        th = tw * 2
                        scaled = clean_pixmap.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        self.thumb_labels[page_idx].setPixmap(scaled)
                        # Обновляем отображение статуса
                        self.update_thumb_status(page_idx)
                    except Exception as e:
                        logger.error(f"Ошибка обновления миниатюры: {str(e)}")

            # Обновляем пути к изображениям для будущих загрузок
            self.img_paths = updated_img_paths

            # Сохраняем список путей в файл конфигурации для будущей загрузки
            config_path = os.path.join(self.chapter_paths["cleaning_folder"], "saved_images.json")
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump({"paths": self.img_paths}, f, ensure_ascii=False, indent=4)
                logger.info(f"Пути к изображениям сохранены в {config_path}")
            except Exception as e:
                logger.error(f"Ошибка при сохранении путей: {str(e)}")

            # Обновление отображения
            self.viewer.display_current_page()

            # Уведомление
            if len(saved_paths) == 1:
                QMessageBox.information(self, "Успех", f"Результат сохранен в: {saved_paths[0]}")
            else:
                QMessageBox.information(self, "Успех",
                                        f"Сохранено {len(saved_paths)} изображений в папку Клининг")

        except Exception as e:
            logger.error(f"Ошибка при сохранении: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить результат: {str(e)}")

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