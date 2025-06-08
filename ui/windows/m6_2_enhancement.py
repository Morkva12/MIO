# -*- coding: utf-8 -*-
# ui/windows/m6_2_enhancement.py

import os
import sys
import logging
import shutil
import tempfile
from PySide6.QtCore import QRunnable, QObject, Signal
from PySide6.QtCore import QProcess

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


class StopProcessingException(Exception):
    """Исключение для остановки обработки"""
    pass


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
        self.process = QProcess()
        self.input_path = input_path
        self.output_path = output_path
        self.settings = settings
        self.signals = EnhancementSignals()
        self._stop_flag = False
        self._current_temp_dir = None

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
                logger.debug("Обработка завершена досрочно по запросу пользователя")

        except StopProcessingException:
            logger.info("Обработка остановлена пользователем")

        except Exception as e:
            error_msg = f"Ошибка в EnhancementWorker: {repr(e)}"
            logger.error(error_msg)
            if not self._stop_flag:  # Отправляем ошибку только если не была запрошена остановка
                self.signals.error.emit(error_msg)

    def _run_upscaler(self):
        """Запуск улучшения изображений через RealESRGAN с обработкой по одному"""
        # Определение относительных путей для моделей
        model_folder = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'model', 'Real-ESRGAN'))
        gfpgan_model_path = os.path.abspath(os.path.join(model_folder, 'GFPGANv1.3.pth'))

        # Проверка существования путей
        if not os.path.isdir(model_folder):
            raise FileNotFoundError(f"Папка с моделями Real-ESRGAN не найдена: {model_folder}")
        if not os.path.isfile(gfpgan_model_path):
            raise FileNotFoundError(f"Файл модели GFPGAN не найден: {gfpgan_model_path}")

        # Получаем список изображений для обработки
        image_files = []

        if os.path.isdir(self.input_path):
            # Если передана папка - получаем все изображения
            for f in sorted(os.listdir(self.input_path)):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff')):
                    image_files.append(f)
        else:
            # Если передан файл - обрабатываем только его
            if os.path.isfile(self.input_path):
                image_files = [os.path.basename(self.input_path)]
                self.input_path = os.path.dirname(self.input_path)

        if not image_files:
            logger.warning("Не найдено изображений для обработки")
            return

        total_images = len(image_files)
        logger.info(f"Найдено {total_images} изображений для обработки")

        # Обрабатываем изображения по одному
        for i, image_file in enumerate(image_files):
            # Проверяем флаг остановки перед каждым изображением
            if self._stop_flag:
                logger.info(f"Процесс остановлен на изображении {i + 1} из {total_images}")
                raise StopProcessingException()

            logger.info(f"Обработка изображения {i + 1}/{total_images}: {image_file}")

            # Обрабатываем одно изображение
            try:
                self._process_single_image(
                    image_file,
                    model_folder,
                    gfpgan_model_path,
                    i,
                    total_images
                )

                # Обновляем общий прогресс
                overall_progress = int(((i + 1) / total_images) * 100)
                self.signals.progress.emit(overall_progress)

            except StopProcessingException:
                raise

            except RuntimeError as e:
                # Специальная обработка ошибок CUDA
                if "CUDA out of memory" in str(e):
                    error_msg = "Недостаточно памяти GPU. Попробуйте уменьшить размер плитки или масштаб."
                    logger.error(error_msg)
                    self.signals.error.emit(error_msg)
                    return
                raise

            except Exception as e:
                logger.error(f"Ошибка при обработке {image_file}: {e}")
                # Продолжаем обработку остальных изображений
                continue

    def _process_single_image(self, image_file, model_folder, gfpgan_model_path, current_idx, total_count):
        """Обработка одного изображения с проверкой остановки"""
        # Создаем временную директорию для одного изображения
        self._current_temp_dir = tempfile.mkdtemp(prefix="enhance_temp_")

        try:
            # Проверяем флаг остановки перед началом
            if self._stop_flag:
                logger.info(f"Обработка отменена перед началом для {image_file}")
                raise StopProcessingException()

            # Копируем изображение во временную папку
            src_path = os.path.join(self.input_path, image_file)
            temp_path = os.path.join(self._current_temp_dir, image_file)

            shutil.copy2(src_path, temp_path)
            logger.debug(f"Изображение скопировано во временную папку: {temp_path}")

            # Создаем callback для проверки остановки
            def progress_callback(tile_progress):
                # Проверяем флаг остановки
                if self._stop_flag:
                    logger.debug("Обнаружен флаг остановки в progress_callback")
                    return -1  # Возвращаем -1 как сигнал остановки
                return tile_progress

            # Создаем callback для проверки флага остановки
            def stop_callback():
                return self._stop_flag

            # Извлекаем настройки
            settings = self.settings

            # Вызываем функцию enhance_image с добавленным stop_callback
            result = enhance_image(
                input_path=self._current_temp_dir,
                output_path=self.output_path,
                model_folder=model_folder,
                model_name=settings.get('model_name', 'RealESRGAN_x4plus_anime_6B'),
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
                num_processes=1,  # Всегда 1 для контроля процесса
                gfpgan_model_path=gfpgan_model_path,
                progress_callback=progress_callback,
                stop_callback=stop_callback  # Добавляем stop_callback
            )

            # Проверяем результат
            if result == -1 or self._stop_flag:
                logger.info(f"Обработка изображения {image_file} была остановлена")
                raise StopProcessingException()

            logger.info(f"Изображение {image_file} успешно обработано")

        except StopProcessingException:
            logger.info(f"Обработка изображения {image_file} прервана")
            raise

        finally:
            # Удаляем временную папку только после завершения всех операций
            if os.path.exists(self._current_temp_dir):
                try:
                    # Даем время завершиться всем операциям с файлами
                    import time
                    time.sleep(0.5)  # Увеличиваем задержку
                    shutil.rmtree(self._current_temp_dir)
                    logger.debug(f"Временная папка удалена: {self._current_temp_dir}")
                except Exception as e:
                    logger.error(f"Ошибка при удалении временной папки: {e}")
            self._current_temp_dir = None

    def _check_stop(self):
        """Функция для проверки флага остановки"""
        return self._stop_flag