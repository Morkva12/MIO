# -*- coding: utf-8 -*-
# ui/windows/m8_3_utils.py
import cv2
import numpy as np
import os
import logging
import torch
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal, QEvent, QRectF, QPointF
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QPolygonF, QColor, QImage
from ui.windows.m8_1_graphics_items import EditableMask, EditablePolygonMask, BrushStroke

logger = logging.getLogger(__name__)


class ImageLoadedEvent(QEvent):
    """Событие загрузки изображения"""
    EventType = QEvent.Type(QEvent.User + 1)

    def __init__(self, index=None):
        super().__init__(ImageLoadedEvent.EventType)
        self.index = index


class AllImagesLoadedEvent(QEvent):
    """Событие загрузки всех изображений"""
    EventType = QEvent.Type(QEvent.User + 2)

    def __init__(self):
        super().__init__(AllImagesLoadedEvent.EventType)


class ImageLoader(QObject):
    """Загрузчик изображений"""
    image_loaded = Signal(int, QImage, str)
    loading_progress = Signal(int, int, str)
    loading_complete = Signal()
    loading_cancelled = Signal()

    def __init__(self, image_paths, thread_pool=None):
        super().__init__()
        self.image_paths = image_paths
        self.thread_pool = thread_pool or ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 2))
        self.cancel_loading = False

    def start_loading(self, priority_index=0):
        """Запуск загрузки изображений"""
        # Определяем порядок: текущая, соседние, остальные
        load_order = self._get_load_order(priority_index)

        self.loaded_count = 0
        self.total_count = len(self.image_paths)
        self.loading_progress.emit(0, self.total_count, "")

        # Запускаем загрузку в отдельном потоке
        thread = threading.Thread(target=self._load_thread, args=(load_order,), daemon=True)
        thread.start()

    def _get_load_order(self, priority_index):
        """Определяет порядок загрузки изображений"""
        load_order = [priority_index]

        # Добавляем соседние
        for offset in range(1, 4):
            if priority_index + offset < len(self.image_paths):
                load_order.append(priority_index + offset)
            if priority_index - offset >= 0:
                load_order.append(priority_index - offset)

        # Добавляем остальные
        for i in range(len(self.image_paths)):
            if i not in load_order:
                load_order.append(i)

        return load_order

    def _load_image(self, idx):
        """Загрузка одного изображения"""
        if self.cancel_loading:
            return None, idx, ""

        try:
            path = self.image_paths[idx]
            current_file = path.split('/')[-1] if '/' in path else path
            image = QImage(path)

            if not image.isNull():
                return image, idx, current_file
        except Exception as e:
            logger.error(f"Ошибка загрузки {idx}: {str(e)}")

        return None, idx, ""

    def _load_thread(self, load_order):
        """Поток загрузки"""
        try:
            futures = []

            # Создаем задачи
            for idx in load_order:
                if self.cancel_loading:
                    break

                future = self.thread_pool.submit(self._load_image, idx)
                futures.append(future)

                # Задержка чтобы не блокировать GUI
                time.sleep(0.05)

            # Обработка результатов
            for future in as_completed(futures):
                if self.cancel_loading:
                    break

                image, idx, current_file = future.result()
                if image is not None:
                    self.image_loaded.emit(idx, image, current_file)
                    self.loaded_count += 1
                    self.loading_progress.emit(self.loaded_count, self.total_count, current_file)

            # Сигнал о завершении
            if not self.cancel_loading:
                self.loading_complete.emit()
                QApplication.postEvent(QApplication.instance().activeWindow(), AllImagesLoadedEvent())
            else:
                self.loading_cancelled.emit()

        except Exception as e:
            logger.error(f"Ошибка загрузки: {str(e)}")
            self.loading_cancelled.emit()

    def cancel(self):
        """Отмена загрузки"""
        logger.info("Отмена загрузки изображений")
        self.cancel_loading = True


def enable_cuda_cudnn():
    """Включает CUDA и настраивает cuDNN"""
    try:
        if torch.cuda.is_available():
            torch.backends.cudnn.enabled = True
            torch.backends.cudnn.benchmark = True
            logger.info(f"CUDA: {torch.cuda.device_count()} устройств, {torch.cuda.get_device_name(0)}")
            return True
        else:
            logger.info("CUDA недоступна")
            return False
    except Exception as e:
        logger.error(f"Ошибка CUDA: {str(e)}")
        return False


def get_device():
    """Определяет доступное устройство"""
    try:
        return 'cuda' if torch.cuda.is_available() else 'cpu'
    except:
        return 'cpu'


class DetectionManager(QObject):
    """Управление детекцией и сегментацией"""

    def __init__(self, ai_models, detect_classes, segm_classes):
        super().__init__()
        self.ai_models = ai_models
        # Создаем новые словари, чтобы избежать проблем с ссылками
        self.detect_classes = detect_classes
        self.segm_classes = segm_classes
        self.detection_model = None
        self.segmentation_model = None
        enable_cuda_cudnn()

        # Выводим состояние для отладки
        logger.info("=== DetectionManager инициализирован с классами ===")
        for cls, info in self.detect_classes.items():
            logger.info(f"- {cls}: enabled={info['enabled']}, threshold={info['threshold']}")

    def load_detection_model(self):
        """Загружает модель детекции"""
        if self.detection_model is not None:
            return self.detection_model

        try:
            model_path = self.ai_models.get("detect")
            if not model_path or not os.path.exists(model_path):
                logger.error(f"Модель детекции не найдена: {model_path}")
                return None

            logger.info(f"Загрузка модели детекции: {model_path}")
            from ultralytics import YOLO

            device = get_device()
            model = YOLO(model_path)
            logger.info(f"Модель детекции загружена на {device}")

            self.detection_model = model
            return model
        except Exception as e:
            logger.error(f"Ошибка загрузки модели детекции: {str(e)}")
            return None

    def _gen_color_for(self, name):
        # пример генерации цвета по имени
        import random
        random.seed(hash(name))
        return tuple(random.randint(0,255) for _ in range(3))

    def load_segmentation_model(self):
        """Загружает модель сегментации"""
        if self.segmentation_model is not None:
            return self.segmentation_model

        try:
            model_path = self.ai_models.get("segm")
            if not model_path or not os.path.exists(model_path):
                logger.error(f"Модель сегментации не найдена: {model_path}")
                return None

            logger.info(f"Загрузка модели сегментации: {model_path}")
            from ultralytics import YOLO

            device = get_device()
            model = YOLO(model_path)
            logger.info(f"Модель сегментации загружена на {device}")

            self.segmentation_model = model
            return model
        except Exception as e:
            logger.error(f"Ошибка загрузки модели сегментации: {str(e)}")
            return None

    def detect_page(self, image_path, page_idx, expansion=10):
        """Выполняет детекцию объектов на странице"""
        try:
            if not os.path.exists(image_path):
                logger.warning(f"Файл не существует: {image_path}")
                return None

            model = self.load_detection_model()
            if model is None:
                return None

            device = get_device()
            logger.info(f"Детекция страницы {page_idx + 1} на {device}")

            # Запускаем детекцию и получаем результаты в формате YOLO
            results = model(image_path, conf=0.25, device=device)
            logger.info(f"Детекция завершена для страницы {page_idx + 1}")

            # Очистка памяти
            if device == 'cuda':
                torch.cuda.empty_cache()

            return results
        except Exception as e:
            logger.error(f"Ошибка детекции страницы {page_idx}: {str(e)}")
            return None

    def segment_page(self, image_path, page_idx, expansion=10):
        """Выполняет сегментацию объектов на странице"""
        try:
            if not os.path.exists(image_path):
                logger.warning(f"Файл не существует: {image_path}")
                return None

            model = self.load_segmentation_model()
            if model is None:
                return None

            device = get_device()
            logger.info(f"Сегментация страницы {page_idx + 1} на {device}")

            results = model(image_path, conf=0.25, device=device)
            logger.info(f"Сегментация завершена для страницы {page_idx + 1}")

            # Очистка памяти
            if device == 'cuda':
                torch.cuda.empty_cache()

            return results
        except Exception as e:
            logger.error(f"Ошибка сегментации страницы {page_idx}: {str(e)}")
            return None

    def detect_area(self, image_path, page_idx, selection_rect, expansion=10):
        """Выполняет детекцию объектов в выбранной области"""
        try:
            img = cv2.imread(image_path)
            if img is None:
                logger.warning(f"Не удалось загрузить изображение: {image_path}")
                return None, (0, 0)

            # Координаты выделения
            x, y = int(selection_rect.x()), int(selection_rect.y())
            w, h = int(selection_rect.width()), int(selection_rect.height())

            # Проверка границ
            h_img, w_img = img.shape[:2]
            x = max(0, min(x, w_img - 1))
            y = max(0, min(y, h_img - 1))
            w = max(1, min(w, w_img - x))
            h = max(1, min(h, h_img - y))

            # Обрезаем изображение
            roi = img[y:y + h, x:x + w]

            # Сохраняем временный файл
            temp_path = os.path.join(os.path.dirname(image_path), f"temp_roi_{page_idx}.jpg")
            cv2.imwrite(temp_path, roi)

            model = self.load_detection_model()
            if model is None:
                return None, (0, 0)

            device = get_device()
            logger.info(f"Детекция области на странице {page_idx + 1}")

            results = model(temp_path, conf=0.25, device=device)

            # Удаляем временный файл
            if os.path.exists(temp_path):
                os.remove(temp_path)

            # Очистка памяти
            if device == 'cuda':
                torch.cuda.empty_cache()

            return results, (x, y)
        except Exception as e:
            logger.error(f"Ошибка детекции области: {str(e)}")
            return None, (0, 0)

    def _normalize_cls_name(self, cls_name: str) -> str:
        """
        Приводит имя класса из модели к одному из четырех классов:
        Text, Sound, FonText, ComplexText
        """
        # Для Bubble всегда возвращаем Sound
        if cls_name == 'Bubble':
            return 'Sound'

        # Для других имен используем стандартное преобразование
        if cls_name == 'Text' or cls_name == 'Texts':
            return 'Text'
        elif cls_name == 'Sound' or cls_name == 'Sounds':
            return 'Sound'
        elif cls_name == 'Fon' or cls_name == 'FonText' or cls_name == 'Watermark':
            return 'FonText'
        elif cls_name == 'Segm' or cls_name == 'ComplexText' or cls_name == 'Aura':
            return 'ComplexText'
        else:
            # Если неизвестный класс, возвращаем по ключевым словам
            lower_name = cls_name.lower()
            if 'bubble' in lower_name:
                return 'Sound'
            elif 'fon' in lower_name:
                return 'FonText'
            elif 'complex' in lower_name or 'segm' in lower_name:
                return 'ComplexText'
            else:
                return 'Text'  # По умолчанию

    def process_detection_results(self, results, viewer, page_idx, expansion_value=0, img_shape=None, offset=None, scale_factor=1.0):


        """Обработка результатов детекции для отображения в просмотрщике"""
        if page_idx not in viewer.masks:
            viewer.masks[page_idx] = []

        logger.info(f"Обработка результатов детекции для страницы {page_idx}: {len(results)} объектов")

        # Определяем размеры изображения
        if img_shape is None:
            if 0 <= page_idx < len(viewer.pixmaps) and not viewer.pixmaps[page_idx].isNull():
                h = viewer.pixmaps[page_idx].height()
                w = viewer.pixmaps[page_idx].width()
                img_shape = (h, w)
            else:
                logger.error(f"Не удалось определить размеры изображения для страницы {page_idx}")
                return

        x_offset, y_offset = offset or (0, 0)

        # Убираем старые детекционные маски
        existing = []
        for m in viewer.masks[page_idx]:
            if getattr(m, 'mask_type', None) == 'detect':
                if m.scene():
                    m.scene().removeItem(m)
            else:
                existing.append(m)
        viewer.masks[page_idx] = existing

        try:
            if hasattr(results[0], 'boxes'):
                boxes = results[0].boxes
                names = results[0].names

                # Получаем состояние чекбоксов из родительского окна
                parent_window = viewer.window()
                sound_enabled = False
                complex_enabled = False
                fontext_enabled = False

                if hasattr(parent_window, 'cb_sound'):
                    sound_enabled = parent_window.cb_sound.isChecked()
                    logger.info(f"Состояние чекбокса Sound: {sound_enabled}")

                if hasattr(parent_window, 'cb_complex_text'):
                    complex_enabled = parent_window.cb_complex_text.isChecked()
                    logger.info(f"Состояние чекбокса ComplexText: {complex_enabled}")

                if hasattr(parent_window, 'cb_fontext'):
                    fontext_enabled = parent_window.cb_fontext.isChecked()
                    logger.info(f"Состояние чекбокса FonText: {fontext_enabled}")

                # Принудительно обновляем состояние в классах
                self.detect_classes['Sound']['enabled'] = sound_enabled
                self.detect_classes['ComplexText']['enabled'] = complex_enabled
                self.detect_classes['FonText']['enabled'] = fontext_enabled

                # Логируем доступные классы для отладки
                logger.debug(f"Доступные классы в модели: {names}")
                active_classes = [name for name, info in self.detect_classes.items() if info.get('enabled', False)]
                logger.debug(f"Активные классы детекции: {active_classes}")

                added = 0
                class_counts = {}

                for box in boxes:
                    cls_id = int(box.cls.item())
                    raw_name = names.get(cls_id, str(cls_id))
                    conf = float(box.conf.item())

                    # Нормализация имени класса к одному из четырех поддерживаемых
                    cls_name = self._normalize_cls_name(raw_name)
                    if cls_name is None:
                        logger.debug(f"Класс '{raw_name}' не входит в список допустимых классов, пропускаем")
                        continue

                    # Получаем информацию о классе
                    cls_info = self.detect_classes.get(cls_name)
                    if cls_info is None:
                        logger.warning(f"Класс {cls_name} не найден в словаре detect_classes, пропускаем")
                        continue

                    logger.debug(f"Confidence: {conf}, threshold: {cls_info.get('threshold', 0.5)}")

                    # ПРЯМАЯ ОБРАБОТКА КЛАССА SOUND ПО ЧЕКБОКСУ
                    if cls_name == 'Sound' and sound_enabled:
                        # Не проверяем enabled, а напрямую используем состояние чекбокса
                        pass
                    elif cls_name == 'ComplexText' and complex_enabled:
                        # Не проверяем enabled, а напрямую используем состояние чекбокса
                        pass
                    elif cls_name == 'FonText' and fontext_enabled:
                        # Не проверяем enabled, а напрямую используем состояние чекбокса
                        pass
                    # Для остальных классов используем стандартную проверку
                    elif not cls_info.get('enabled', False):
                        logger.debug(f"Класс {cls_name} отключен, пропускаем")
                        continue

                    # Координаты
                    x1, y1, x2, y2 = box.xyxy.cpu().numpy()[0]
                    x1 += x_offset
                    y1 += y_offset
                    x2 += x_offset
                    y2 += y_offset

                    # Расширение маски если указано
                    if expansion_value:
                        x1 -= expansion_value
                        y1 -= expansion_value
                        x2 += expansion_value
                        y2 += expansion_value

                    # Обрезка по границам
                    x1 = max(0, min(x1, img_shape[1]))
                    y1 = max(0, min(y1, img_shape[0]))
                    x2 = max(0, min(x2, img_shape[1]))
                    y2 = max(0, min(y2, img_shape[0]))
                    w, h = x2 - x1, y2 - y1

                    if w <= 0 or h <= 0:
                        logger.debug(f"Пропускаем объект с невалидными размерами: {w}x{h}")
                        continue

                    # Создаем и добавляем маску
                    color = cls_info.get('color', (255, 0, 0))
                    mask = EditableMask(x1, y1, w, h, 'detect', cls_name, conf, color)
                    mask.last_expansion = expansion_value
                    mask.set_page_index(page_idx)
                    viewer.masks[page_idx].append(mask)

                    # Добавляем на сцену если это текущая страница
                    if viewer.cur_page == page_idx:
                        viewer.scene_.addItem(mask)
                        mask.setVisible(True)

                    # Для статистики
                    class_counts[cls_name] = class_counts.get(cls_name, 0) + 1
                    added += 1

                logger.info(f"Добавлено {added} масок детекции для страницы {page_idx}")

                if class_counts:
                    logger.info(f"Распределение по классам: {class_counts}")

            # Обновляем сцену
            if viewer.cur_page == page_idx:
                viewer.scene_.update()
                QApplication.processEvents()

        except Exception as e:
            logger.error(f"Ошибка при обработке результатов детекции: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def process_segmentation_results(self, results, viewer, page_idx, expansion=10, img_shape=None, offset=(0, 0)):
        """Обрабатывает результаты сегментации и создает маски"""
        if not results or not hasattr(results[0], 'masks') or results[0].masks is None:
            return

        try:
            masks = results[0].masks

            # Определяем размеры изображения
            if img_shape:
                h, w = img_shape[:2]
            elif page_idx in [0, 4, 45]:
                w, h = 800, 1126
                if page_idx in viewer.pixmaps and not viewer.pixmaps[page_idx].isNull():
                    w = viewer.pixmaps[page_idx].width()
                    h = viewer.pixmaps[page_idx].height()
                logger.info(f"Используем размеры по умолчанию для проблемной страницы {page_idx}: {w}x{h}")
            elif page_idx in viewer.pixmaps and not viewer.pixmaps[page_idx].isNull():
                h, w = viewer.pixmaps[page_idx].height(), viewer.pixmaps[page_idx].width()
            else:
                h, w = 1000, 1000
                logger.warning(f"Используем стандартные размеры 1000x1000 для страницы {page_idx}")

            if page_idx not in viewer.masks:
                viewer.masks[page_idx] = []

            active_classes = [cls for cls, info in self.segm_classes.items() if info['enabled']]
            masks_added = False

            for i in range(len(masks)):
                segment = masks.xy[i]
                cls_id = int(results[0].boxes[i].cls.item())
                conf = float(results[0].boxes[i].conf.item())
                cls_name = results[0].names[cls_id]

                if cls_name in self.segm_classes and cls_name in active_classes:
                    if conf < self.segm_classes[cls_name]['threshold']:
                        continue

                    if expansion > 0:
                        segment = self._expand_polygon(segment, expansion)

                    # Смещение точек
                    offset_segment = [[
                        max(0, min(x+offset[0], w-1)),
                        max(0, min(y+offset[1], h-1))
                    ] for x, y in segment]

                    if len(offset_segment) < 3:
                        continue

                    color = self.segm_classes[cls_name]['color']
                    poly_mask = EditablePolygonMask(offset_segment, 'segm', cls_name, conf, color)
                    poly_mask.last_expansion = expansion
                    poly_mask.set_page_index(page_idx)

                    viewer.scene_.addItem(poly_mask)
                    viewer.masks[page_idx].append(poly_mask)
                    poly_mask.setVisible(viewer.cur_page == page_idx)
                    poly_mask.setZValue(100)
                    masks_added = True

            if masks_added:
                window = viewer.window()
                if hasattr(window, 'update_combined_mask_from_visual'):
                    window.update_combined_mask_from_visual(page_idx)
                if hasattr(window, 'force_update_thumbnail'):
                    window.force_update_thumbnail(page_idx)
                viewer.mask_updated.emit(page_idx)

        except Exception as e:
            logger.error(f"Ошибка обработки результатов сегментации: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    def _expand_polygon(self, polygon, expansion):
        """Расширяет полигон на указанное количество пикселей"""
        if len(polygon) < 3:
            return polygon

        # Находим центр
        center_x = sum(p[0] for p in polygon) / len(polygon)
        center_y = sum(p[1] for p in polygon) / len(polygon)

        # Расширяем от центра
        expanded = []
        for p in polygon:
            dx = p[0] - center_x
            dy = p[1] - center_y

            # Нормализация
            length = max(0.0001, (dx * dx + dy * dy) ** 0.5)
            dx /= length
            dy /= length

            # Расширение
            expanded.append([p[0] + dx * expansion, p[1] + dy * expansion])

        return expanded

    def add_mask_to_combined(self, mask, combined_mask, w, h):
        """Добавляет маску в объединенную маску"""
        try:
            if hasattr(mask, 'deleted') and mask.deleted:
                return

            if isinstance(mask, EditableMask):
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
                    logger.debug(f"Добавлен прямоугольник: ({x1},{y1})-({x2},{y2})")

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
                    logger.debug(f"Добавлен полигон с {len(points)} точками")

            elif isinstance(mask, BrushStroke):
                if mask.path.isEmpty():
                    return

                # Позиция
                pos_x, pos_y = 0, 0
                if mask.pos() is not None:
                    pos_x, pos_y = mask.pos().x(), mask.pos().y()

                # Растеризуем путь
                temp_mask = np.zeros((h, w), dtype=np.uint8)
                path = mask.path
                points = []

                for i in range(path.elementCount()):
                    elem = path.elementAt(i)
                    x = max(0, min(int(elem.x + pos_x), w - 1))
                    y = max(0, min(int(elem.y + pos_y), h - 1))
                    points.append((x, y))

                # Рисуем линии
                if len(points) > 1:
                    for i in range(1, len(points)):
                        thickness = getattr(mask, 'stroke_size', 5)
                        cv2.line(temp_mask, points[i - 1], points[i], 255,
                                 thickness=thickness, lineType=cv2.LINE_AA)

                    logger.debug(
                        f"Добавлена линия кисти с {len(points)} точками, толщина: {getattr(mask, 'stroke_size', 5)}")
                elif len(points) == 1:
                    x, y = points[0]
                    radius = max(1, getattr(mask, 'stroke_size', 5) // 2)
                    cv2.circle(temp_mask, (x, y), radius, 255, -1, lineType=cv2.LINE_AA)
                    logger.debug(f"Добавлена точка кисти ({x},{y}), радиус: {radius}")

                # Объединяем
                if np.any(temp_mask):
                    points_count = np.sum(temp_mask > 0)
                    combined_mask[temp_mask > 0] = 255
                    logger.debug(f"Добавлено {points_count} пикселей от кисти")

        except Exception as e:
            logger.error(f"Ошибка добавления маски: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    # В классе DetectionManager:
    def process_detection_pages(self, window, pages_to_process, expansion_value):
        """Последовательная обработка страниц для детекции"""
        from PySide6.QtCore import QTimer

        # Постоянное значение вместо зависимости от атрибута окна
        PROGRESS_AUTO_HIDE_MS = 5000

        if window.detection_cancelled:
            window._restore_detect_button()
            window._update_progress_bar_immediate(window.detect_progress, 0, 1, "Детекция отменена")
            QTimer.singleShot(PROGRESS_AUTO_HIDE_MS, lambda: window.detect_progress.setVisible(False))
            window.unlock_interface()
            return

        if window.current_page_index >= window.total_pages:
            # Завершаем обработку
            window._update_progress_bar_immediate(
                window.detect_progress, window.total_pages, window.total_pages, "Детекция завершена")

            # Обновляем отображение
            window.viewer.display_current_page()

            # Восстанавливаем интерфейс
            window._restore_detect_button()
            QTimer.singleShot(PROGRESS_AUTO_HIDE_MS, lambda: window.detect_progress.setVisible(False))
            window.unlock_interface()
            return

        # Текущий индекс страницы для обработки
        page_idx = window.pages_to_process[window.current_page_index]

        # Формируем сообщение о прогрессе
        if window.total_pages == 1:
            progress_msg = "Детекция страницы..."
        else:
            progress_msg = f"Детекция страницы {page_idx + 1} ({window.current_page_index + 1}/{window.total_pages})"

        # Обновляем прогресс
        window._update_progress_bar_immediate(
            window.detect_progress, window.current_page_index, window.total_pages, progress_msg)

        try:
            # Очищаем существующие маски
            window._clear_page_masks(page_idx, 'detect')

            # Получаем путь к изображению
            image_path = window.image_paths[page_idx]

            # Запускаем детекцию
            results = self.detect_page(image_path, page_idx, expansion_value)

            if results is not None:
                # Вместо прямой обработки, вызываем обработчик
                window._on_detection_completed(page_idx, results)
            else:
                logger.warning(f"Не получены результаты детекции для страницы {page_idx}")

            # Увеличиваем индекс для следующего шага
            window.current_page_index += 1

            # Планируем следующий шаг
            QTimer.singleShot(100, lambda: self.process_detection_pages(window, pages_to_process, expansion_value))

        except Exception as e:
            logger.error(f"Ошибка детекции страницы {page_idx}: {str(e)}")
            # При ошибке также увеличиваем индекс и продолжаем
            window.current_page_index += 1
            QTimer.singleShot(100, lambda: self.process_detection_pages(window, pages_to_process, expansion_value))

    def process_segmentation_pages(self, window, pages_to_process, expansion_value):
        """Последовательная обработка страниц для сегментации"""
        from PySide6.QtCore import QTimer

        # Постоянное значение вместо зависимости от атрибута окна
        PROGRESS_AUTO_HIDE_MS = 5000

        if window.segmentation_cancelled:
            window._restore_segm_button()
            window._update_progress_bar_immediate(window.segm_progress, 0, 1, "Сегментация отменена")
            QTimer.singleShot(PROGRESS_AUTO_HIDE_MS, lambda: window.segm_progress.setVisible(False))
            window.unlock_interface()
            return

        if window.segm_current_page_index >= window.segm_total_pages:
            # Завершаем обработку
            window._update_progress_bar_immediate(
                window.segm_progress, window.segm_total_pages, window.segm_total_pages, "Сегментация завершена")

            # Обновляем отображение
            window.viewer.display_current_page()

            # Восстанавливаем интерфейс
            window._restore_segm_button()
            QTimer.singleShot(PROGRESS_AUTO_HIDE_MS, lambda: window.segm_progress.setVisible(False))
            window.unlock_interface()
            return

        # Текущий индекс страницы для обработки
        page_idx = window.segm_pages_to_process[window.segm_current_page_index]

        # Формируем сообщение о прогрессе
        if window.segm_total_pages == 1:
            progress_msg = "Сегментация страницы..."
        else:
            progress_msg = f"Сегментация страницы {page_idx + 1} ({window.segm_current_page_index + 1}/{window.segm_total_pages})"

        # Обновляем прогресс
        window._update_progress_bar_immediate(
            window.segm_progress, window.segm_current_page_index, window.segm_total_pages, progress_msg)

        try:
            # Очищаем существующие маски
            window._clear_page_masks(page_idx, 'segm')

            # Получаем путь к изображению
            image_path = window.image_paths[page_idx]

            # Запускаем сегментацию
            results = self.segment_page(image_path, page_idx, expansion_value)

            if results is not None:
                # Обрабатываем результаты
                self.process_segmentation_results(
                    results, window.viewer, page_idx, expansion_value)

                # Обновляем миниатюру
                window.update_combined_mask_from_visual(page_idx)

            # Увеличиваем индекс и планируем следующий шаг
            window.segm_current_page_index += 1
            QTimer.singleShot(100, lambda: self.process_segmentation_pages(
                window, pages_to_process, expansion_value))

        except Exception as e:
            logger.error(f"Ошибка сегментации страницы {page_idx}: {str(e)}")
            window.segm_current_page_index += 1
            QTimer.singleShot(100, lambda: self.process_segmentation_pages(
                window, pages_to_process, expansion_value))