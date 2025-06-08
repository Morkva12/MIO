# -*- coding: utf-8 -*-
# ui/windows/m7_2_utils.py

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
    """Возвращает список изображений из папки, отсортированный по имени"""
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


def copy_images(src_folder, dst_folder):
    """Копирует изображения из src_folder в dst_folder"""
    if not os.path.isdir(src_folder):
        logger.debug(f"Исходная папка не найдена: {src_folder}")
        return 0

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


def show_message(parent, title, message, icon=QMessageBox.Information):
    """Отображает сообщение пользователю"""
    msg = QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(message)
    msg.exec()