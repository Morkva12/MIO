# -*- coding: utf-8 -*-
# ui/windows/m6_2_enhancement.py

import os
import sys
import logging
from PySide6.QtCore import QRunnable, QObject, Signal

logger = logging.getLogger(__name__)

# Добавляем путь к пакету RealESRGAN для корректного импорта
current_dir = os.path.dirname(os.path.abspath(__file__))
real_esrgan_path = os.path.join(current_dir, 'RealESRGAN')
if real_esrgan_path not in sys.path:
    sys.path.insert(0, real_esrgan_path)

try:
    from image_upscaler import enhance_image
except ImportError:
    logger.error("Не удалось импортировать image_upscaler. Проверьте путь к RealESRGAN.")


class EnhancementSignals(QObject):
    """Сигналы для процесса улучшения изображений"""
    progress = Signal(int)  # Прогресс в процентах
    finished = Signal()  # Сигнал завершения
    error = Signal(str)  # Сигнал ошибки (с текстом)


class EnhancementWorker(QRunnable):
    """
    Worker для обработки изображений с помощью Real-ESRGAN.
    Поддерживает отмену процесса и отправку статуса.
    """

    def __init__(self, input_path, output_path, settings):
        """
        Инициализация воркера

        Args:
            input_path: Путь к исходным изображениям
            output_path: Путь для сохранения улучшенных изображений
            settings: Словарь с настройками улучшения
        """
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.settings = settings
        self.signals = EnhancementSignals()
        self._stop_flag = False

    def stop(self):
        """Остановка процесса улучшения"""
        self._stop_flag = True
        logger.debug("Запрошена остановка процесса улучшения")

    def run(self):
        """Запуск процесса улучшения изображений"""
        try:
            # Отправляем сигнал о начале процесса
            self.signals.progress.emit(0)
            logger.debug(f"Начало обработки изображений из {self.input_path}")

            # Проверяем, не отменен ли процесс до запуска
            if self._stop_flag:
                logger.debug("Процесс остановлен до запуска")
                return

            # Создаем папку назначения если не существует
            os.makedirs(self.output_path, exist_ok=True)
            logger.debug(f"Папка для результатов создана: {self.output_path}")

            # Запускаем процесс улучшения
            self._run_upscaler()

            # Проверяем, не был ли остановлен процесс
            if not self._stop_flag:
                logger.debug("Обработка изображений завершена успешно")
                self.signals.finished.emit()
            else:
                logger.debug("Обработка завершена досрочно")

        except Exception as e:
            error_msg = f"Ошибка в EnhancementWorker: {repr(e)}"
            logger.error(error_msg)
            self.signals.error.emit(error_msg)

    def _run_upscaler(self):
        """Запуск улучшения изображений через RealESRGAN"""
        # Определение относительных путей для моделей
        model_folder = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'model', 'Real-ESRGAN'))
        gfpgan_model_path = os.path.abspath(os.path.join(model_folder, 'GFPGANv1.3.pth'))

        # Проверка существования путей
        if not os.path.isdir(model_folder):
            raise FileNotFoundError(f"Папка с моделями Real-ESRGAN не найдена: {model_folder}")
        if not os.path.isfile(gfpgan_model_path):
            raise FileNotFoundError(f"Файл модели GFPGAN не найден: {gfpgan_model_path}")

        # Извлекаем настройки
        settings = self.settings

        try:
            # Вызываем функцию enhance_image с переданными настройками
            enhance_image(
                input_path=self.input_path,
                output_path=self.output_path,
                model_folder=model_folder,
                model_name=settings.get('model_name', 'RealESRGAN_x4plus'),
                denoise_strength=settings.get('denoise_strength', 0.5),
                outscale=settings.get('outscale', 4),
                tile=settings.get('tile', 0),
                tile_pad=settings.get('tile_pad', 10),
                pre_pad=settings.get('pre_pad', 0),
                face_enhance=settings.get('face_enhance', False),
                fp32=settings.get('fp32', False),
                alpha_upsampler=settings.get('alpha_upsampler', 'realesrgan'),
                suffix=settings.get('suffix', 'enhanced'),
                gpu_id=settings.get('gpu_id', 0),
                num_processes=settings.get('num_processes', 1),
                gfpgan_model_path=gfpgan_model_path,
                progress_callback=self.signals.progress.emit
            )

            # Проверяем флаг остановки
            if self._stop_flag:
                logger.debug("Процесс улучшения был остановлен по запросу пользователя")

        except RuntimeError as e:
            # Специальная обработка ошибок CUDA
            if "CUDA out of memory" in str(e):
                error_msg = "Недостаточно памяти GPU. Попробуйте уменьшить размер плитки или масштаб."
                logger.error(error_msg)
                self.signals.error.emit(error_msg)
                return
            # Проходим дальше для общей обработки ошибок
            raise

    def _check_stop(self):
        """Функция для проверки флага остановки"""
        return self._stop_flag