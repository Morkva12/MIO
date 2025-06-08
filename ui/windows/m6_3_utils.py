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
    """Копирует изображения из src_folder в dst_folder."""
    if not os.path.isdir(src_folder):
        logger.debug(f"Исходная папка не найдена: {src_folder}")
        return 0

    # Очищаем папку dst_folder от старых файлов
    logger.debug(f"Очистка папки {dst_folder}")
    for old_file in os.listdir(dst_folder):
        old_path = os.path.join(dst_folder, old_file)
        if os.path.isfile(old_path):
            os.remove(old_path)
            logger.debug(f"Удален старый файл: {old_file}")

    # Копируем все подходящие файлы
    copied_count = 0
    for f in os.listdir(src_folder):
        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')) and not f.startswith('temp_'):
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


def get_file_hash(file_path):
    """Вычисляет хэш файла для определения изменений"""
    import hashlib
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Ошибка при вычислении хэша файла {file_path}: {e}")
        return None


def detect_folder_changes(current_files, previous_files):
    """Определяет изменения между папками с улучшенным определением переименований"""
    changes = {
        'added': [],
        'removed': [],
        'modified': [],
        'renamed': [],
        'reordered': False
    }

    # Получаем информацию о файлах
    current_info = {}  # {filename: (size, width, height, path)}
    previous_info = {}

    try:
        from PIL import Image
    except ImportError:
        Image = None

    # Собираем информацию о текущих файлах
    for path in current_files:
        if os.path.exists(path):
            name = os.path.basename(path)
            size = os.path.getsize(path)
            width, height = 0, 0
            if Image:
                try:
                    with Image.open(path) as img:
                        width, height = img.size
                except:
                    pass
            current_info[name] = (size, width, height, path)

    # Собираем информацию о предыдущих файлах
    for path in previous_files:
        if os.path.exists(path):
            name = os.path.basename(path)
            size = os.path.getsize(path)
            width, height = 0, 0
            if Image:
                try:
                    with Image.open(path) as img:
                        width, height = img.size
                except:
                    pass
            previous_info[name] = (size, width, height, path)

    current_names = set(current_info.keys())
    previous_names = set(previous_info.keys())

    # Сначала ищем взаимные переименования (swap)
    potential_modified = []
    for name in current_names & previous_names:
        curr_sig = current_info[name][:3]  # size, width, height
        prev_sig = previous_info[name][:3]
        if curr_sig != prev_sig:
            potential_modified.append(name)

    # Проверяем, не поменялись ли файлы местами
    processed = set()
    for name1 in potential_modified:
        if name1 in processed:
            continue

        curr_sig1 = current_info[name1][:3]
        prev_sig1 = previous_info[name1][:3]

        # Ищем файл с сигнатурой prev_sig1 в текущих файлах
        for name2 in potential_modified:
            if name2 == name1 or name2 in processed:
                continue

            curr_sig2 = current_info[name2][:3]
            prev_sig2 = previous_info[name2][:3]

            # Проверяем взаимный обмен
            if curr_sig1 == prev_sig2 and curr_sig2 == prev_sig1:
                changes['renamed'].append((name2, name1))
                changes['renamed'].append((name1, name2))
                processed.add(name1)
                processed.add(name2)
                logger.info(f"Обнаружен обмен файлов: {name1} <-> {name2}")
                break

    # Файлы, которые не были обработаны как переименования
    for name in potential_modified:
        if name not in processed:
            changes['modified'].append(name)

    # Простые добавления/удаления
    changes['added'] = list(current_names - previous_names)
    changes['removed'] = list(previous_names - current_names)

    return changes


def get_folder_state(folder_path):
    """Получает текущее состояние папки (файлы и их хэши)"""
    state = {}
    if not os.path.exists(folder_path):
        return state

    for file in sorted(os.listdir(folder_path)):
        if file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
            file_path = os.path.join(folder_path, file)
            file_hash = get_file_hash(file_path)
            if file_hash:
                state[file_path] = file_hash

    return state


def sync_enhanced_images(originals_folder, enhanced_folder, sync_mode='preserve', rename_map=None):
    """Синхронизирует улучшенные изображения с оригинальными"""
    results = {
        'synced': 0,
        'deleted': 0,
        'renamed': 0,
        'errors': []
    }

    if sync_mode == 'none':
        return results

    if sync_mode == 'delete':
        count = delete_all_enhanced(enhanced_folder)
        results['deleted'] = count
        return results

    # Режим preserve
    try:
        if rename_map is None:
            rename_map = {}

        # Собираем все улучшенные файлы
        enhanced_files = {}
        for file in os.listdir(enhanced_folder):
            if '_enhanced' in file and file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                # Извлекаем базовое имя
                base_with_ext = file.replace('_enhanced', '')
                base_name = os.path.splitext(base_with_ext)[0]
                enhanced_files[base_name] = file

        # Обрабатываем переименования
        if rename_map:
            temp_mapping = {}
            for old_name, new_name in rename_map.items():
                old_base = os.path.splitext(old_name)[0]
                new_base = os.path.splitext(new_name)[0]

                if old_base in enhanced_files:
                    old_enhanced = enhanced_files[old_base]
                    new_enhanced = f"{new_base}_enhanced{os.path.splitext(new_name)[1]}"

                    old_path = os.path.join(enhanced_folder, old_enhanced)
                    new_path = os.path.join(enhanced_folder, new_enhanced)

                    if os.path.exists(old_path):
                        os.rename(old_path, new_path)
                        temp_mapping[new_base] = new_enhanced
                        del enhanced_files[old_base]
                        results['renamed'] += 1
                        logger.info(f"Переименовано: {old_enhanced} -> {new_enhanced}")

            # Обновляем словарь улучшенных файлов
            enhanced_files.update({k: v for k, v in temp_mapping.items()})

        # Подсчитываем синхронизированные файлы
        for orig_file in os.listdir(originals_folder):
            if orig_file.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                base = os.path.splitext(orig_file)[0]
                if base in enhanced_files:
                    results['synced'] += 1

        # Удаляем улучшенные без оригиналов
        current_originals = {os.path.splitext(f)[0] for f in os.listdir(originals_folder)
                             if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff'))}

        for base, enhanced_file in list(enhanced_files.items()):
            if base not in current_originals:
                file_path = os.path.join(enhanced_folder, enhanced_file)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    results['deleted'] += 1
                    logger.info(f"Удален улучшенный без оригинала: {enhanced_file}")

    except Exception as e:
        results['errors'].append(f"Ошибка: {str(e)}")
        logger.error(f"Ошибка синхронизации: {e}")

    return results

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

