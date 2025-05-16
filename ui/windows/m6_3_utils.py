# -*- coding: utf-8 -*-
# ui/windows/m6_3_utils.py

import os
import shutil
import logging
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

# Настройка логгера
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class PageChangeSignal(QObject):
    """Сигнал для уведомления об изменении страницы"""
    page_changed = Signal(int)


def get_images_from_folder(folder):
    """
    Возвращает список изображений из папки (jpg, png, и т.д.),
    отсортированный по имени файла.
    """
    if not os.path.isdir(folder):
        logger.debug(f"Папка не найдена: {folder}")
        return []

    all_files = os.listdir(folder)
    images = []

    for f in all_files:
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
            images.append(os.path.join(folder, f))

    images.sort()
    logger.debug(f"Найдено {len(images)} изображений в папке {folder}")
    return images


def prepare_images_and_folders(base_input_folder, base_preprocessing_folder):
    """
    1. Создаёт (при необходимости) папки 'Originals' и 'Enhanced' внутри base_preprocessing_folder.
    2. Если в 'Originals' нет изображений, копирует их из base_input_folder.
    3. Возвращает пути к папкам:
        originals_folder,  # там лежат исходные
        enhanced_folder    # туда будут складываться улучшенные
    """
    # Папка Originals
    originals_folder = os.path.join(base_preprocessing_folder, 'Originals')
    # Папка Enhanced
    enhanced_folder = os.path.join(base_preprocessing_folder, 'Enhanced')

    os.makedirs(originals_folder, exist_ok=True)
    os.makedirs(enhanced_folder, exist_ok=True)

    # Проверяем, есть ли уже файлы в папке Originals
    existing_images = get_images_from_folder(originals_folder)
    if len(existing_images) == 0:
        # Если там пусто, копируем все изображения из base_input_folder
        copy_images(base_input_folder, originals_folder)

    return originals_folder, enhanced_folder


def copy_images(src_folder, dst_folder):
    """
    Копирует изображения из src_folder в dst_folder.
    """
    if not os.path.isdir(src_folder):
        logger.debug(f"Исходная папка не найдена: {src_folder}")
        return

    # Очищаем папку dst_folder от старых файлов
    for old_file in os.listdir(dst_folder):
        old_path = os.path.join(dst_folder, old_file)
        if os.path.isfile(old_path):
            os.remove(old_path)

    # Копируем все подходящие файлы
    copied_count = 0
    for f in os.listdir(src_folder):
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
            src_path = os.path.join(src_folder, f)
            dst_path = os.path.join(dst_folder, f)
            try:
                shutil.copy2(src_path, dst_path)
                copied_count += 1
                logger.debug(f"Скопирован файл {src_path} -> {dst_path}")
            except Exception as e:
                logger.error(f"Ошибка при копировании {src_path}: {e}")

    logger.info(f"Скопировано {copied_count} изображений")
    return copied_count


def populate_gpu_options(gpu_select, parent):
    """
    Заполняет выпадающий список доступными GPU и CPU.
    """
    try:
        import torch
        if torch.cuda.is_available():
            num_gpus = torch.cuda.device_count()
            for i in range(num_gpus):
                gpu_name = torch.cuda.get_device_name(i)
                gpu_select.addItem(f"GPU {i}: {gpu_name}", i)
            gpu_select.addItem("CPU", -1)
        else:
            gpu_select.addItem("CPU", -1)
            show_message(parent, "GPU не доступен", "Не найдены доступные GPU. Будет использован CPU.",
                         QMessageBox.Warning)
    except ImportError:
        # Если torch не установлен, предложим ручной выбор
        gpu_select.addItem("CPU", -1)
        show_message(parent, "Отсутствие PyTorch",
                     "PyTorch не установлен. GPU не может быть определен. Будет использован CPU.", QMessageBox.Warning)


def handle_sync_slider(slider, input_field, min_val, max_val, multiplier=100, step=0.25):
    """
    Возвращает функцию для синхронизации слайдера с полем ввода с учётом шага.
    """

    def sync():
        text = input_field.text()
        try:
            # Преобразуем текст в число
            val = float(text)
            # Ограничиваем значение диапазоном и округляем до ближайшего шага
            val = max(min_val, min(max_val, round(val / step) * step))
            # Преобразуем в значение слайдера (умножаем на multiplier)
            slider_value = int(val * multiplier)
            slider.setValue(slider_value)
            # Обновляем поле ввода с учётом двух знаков после запятой
            input_field.setText(f"{val:.2f}")
        except ValueError:
            # При ошибке ввода устанавливаем текущее значение слайдера с учётом шага
            current_slider_value = slider.value() / multiplier
            rounded_value = round(current_slider_value / step) * step
            rounded_value = max(min_val, min(max_val, rounded_value))
            slider.setValue(int(rounded_value * multiplier))
            input_field.setText(f"{rounded_value:.2f}")

    return sync


def show_message(parent, title, message, icon=QMessageBox.Information):
    """
    Отображает сообщение пользователю.
    """
    msg = QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(message)
    msg.exec()


def check_enhanced_availability(image_paths, enhanced_folder):
    """
    Проверяет наличие улучшенных изображений.
    Возвращает True, если хотя бы одно улучшенное изображение существует.
    """
    if not image_paths:
        return False

    # Проверяем наличие хотя бы одного улучшенного изображения
    for orig_path in image_paths:
        base = os.path.splitext(os.path.basename(orig_path))[0]
        ext = os.path.splitext(orig_path)[1]
        enh_name = f"{base}_enhanced{ext}"
        enh_path = os.path.join(enhanced_folder, enh_name)
        if os.path.isfile(enh_path):
            return True

    return False


def delete_enhanced_image(image_path, enhanced_folder):
    """
    Удаляет улучшенное изображение для указанного оригинального.
    Возвращает True в случае успеха, False при ошибке.
    """
    try:
        base = os.path.splitext(os.path.basename(image_path))[0]
        ext = os.path.splitext(image_path)[1]
        enh_path = os.path.join(enhanced_folder, f"{base}_enhanced{ext}")

        if os.path.exists(enh_path):
            os.remove(enh_path)
            logger.info(f"Улучшенное изображение удалено: {enh_path}")
            return True
        else:
            logger.warning(f"Улучшенное изображение не найдено: {enh_path}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при удалении улучшенного изображения: {e}")
        return False


def delete_all_enhanced(enhanced_folder):
    """
    Удаляет все улучшенные изображения из папки.
    Возвращает количество удаленных файлов.
    """
    deleted_count = 0
    try:
        for file in os.listdir(enhanced_folder):
            if "_enhanced" in file and file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                file_path = os.path.join(enhanced_folder, file)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при удалении {file_path}: {e}")

        logger.info(f"Удалено {deleted_count} улучшенных изображений")
        return deleted_count
    except Exception as e:
        logger.error(f"Ошибка при обработке папки {enhanced_folder}: {e}")
        return 0