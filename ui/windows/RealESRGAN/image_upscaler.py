# GraphicNovelCleaner/13. MangaLocalizer/ui/main_window/RealESRGAN/image_upscaler.py

import os
import glob
import numpy as np
from PIL import Image
from typing import Optional, List, Callable
from multiprocessing import Pool, cpu_count, Manager
from functools import partial
import requests  # Для загрузки модели GFPGAN и Real-ESRGAN

import torch  # Убедитесь, что PyTorch установлен

from basicsr.archs.rrdbnet_arch import RRDBNet
from basicsr.archs.srvgg_arch import SRVGGNetCompact
from realesrgan import RealESRGANer

# Если требуется, импортируйте GFPGAN для улучшения лиц
from gfpgan import GFPGANer

# Глобальные переменные для upsampler и face_enhancer
upsampler = None
face_enhancer = None
stop_flag = None  # Добавлен глобальный флаг остановки


def init_worker(model_folder: str, model_name: str, denoise_strength: float,
                outscale: float, tile: int, tile_pad: int, pre_pad: int,
                face_enhance: bool, fp32: bool, alpha_upsampler: str,
                gpu_id: Optional[int], gfpgan_model_path: Optional[str],
                shared_stop_flag):  # Добавлен параметр для флага остановки
    """
    Инициализатор для каждого процесса. Настраивает RealESRGANer и GFPGANer (если требуется).
    """
    global upsampler
    global face_enhancer
    global stop_flag

    stop_flag = shared_stop_flag  # Сохраняем ссылку на общий флаг

    print(f"🔧 Инициализация процесса с моделью {model_name} на GPU {gpu_id}...")

    # Определение модели в зависимости от имени
    model_path = os.path.join(model_folder, f'{model_name}.pth')
    print(f"🔍 Проверка наличия модели по пути: {model_path}")

    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"🚫 Модель {model_name} не найдена в папке {model_folder}")

    # Настройка модели и масштаба
    if model_name == 'RealESRGAN_x4plus':
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        netscale = 4
    elif model_name == 'RealESRNet_x4plus':
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        netscale = 4
    elif model_name == 'RealESRGAN_x4plus_anime_6B':
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=6, num_grow_ch=32, scale=4)
        netscale = 4
    elif model_name == 'RealESRGAN_x2plus':
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=2)
        netscale = 2
    elif model_name == 'realesr-animevideov3':
        model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=16, upscale=4, act_type='prelu')
        netscale = 4
    elif model_name == 'realesr-general-x4v3':
        model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4, act_type='prelu')
        netscale = 4
    else:
        raise ValueError(f"🚫 Неизвестное имя модели: {model_name}")

    # Контроль силы шумоподавления
    dni_weight: Optional[List[float]] = None
    if model_name == 'realesr-general-x4v3' and denoise_strength != 1:
        wdn_model_name = 'realesr-general-wdn-x4v3'
        wdn_model_path = os.path.join(model_folder, f'{wdn_model_name}.pth')
        if not os.path.isfile(wdn_model_path):
            raise FileNotFoundError(f"🚫 Модель шумоподавления {wdn_model_name} не найдена в папке {model_folder}")
        model_path_updated = [model_path, wdn_model_path]
        dni_weight = [denoise_strength, 1 - denoise_strength]
    else:
        model_path_updated = model_path

    # Инициализация RealESRGANer
    try:
        upsampler = RealESRGANer(
            scale=netscale,
            model_path=model_path_updated,
            dni_weight=dni_weight,
            model=model,
            tile=tile,
            tile_pad=tile_pad,
            pre_pad=pre_pad,
            half=not fp32,
            gpu_id=gpu_id,
        )
        print("🖥️ RealESRGANer инициализирован.")
    except Exception as e:
        print(f"❌ Ошибка инициализации RealESRGANer: {e}")
        raise e

    # Инициализация GFPGAN для улучшения лиц, если требуется
    if face_enhance:
        if gfpgan_model_path is None:
            # Установите путь по умолчанию
            gfpgan_model_path = os.path.join(model_folder, 'GFPGANv1.3.pth')
        try:
            face_enhancer = GFPGANer(
                model_path=gfpgan_model_path,
                upscale=outscale,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=upsampler
            )
            print("🖥️ GFPGANer инициализирован.")
        except Exception as e:
            print(f"❌ Ошибка инициализации GFPGANer: {e}")
            raise e


def process_single_image(args: tuple, output_path: str, suffix: str, outscale: float, face_enhance: bool):
    """
    Обрабатывает одно изображение: повышает его разрешение и сохраняет результат.
    """
    global upsampler
    global face_enhancer
    global stop_flag

    # Проверяем флаг остановки перед началом обработки
    if stop_flag and stop_flag.value:
        print("⏹️ Обработка остановлена пользователем")
        return

    path, idx = args
    imgname, extension = os.path.splitext(os.path.basename(path))
    print(f"🖼️ Обработка {idx + 1}: {imgname}")

    # Загрузка изображения с использованием PIL и преобразование в NumPy массив
    try:
        img = Image.open(path).convert('RGB')
        img_np = np.array(img)
        print(f"📥 Изображение загружено: {path}")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки изображения {path}: {e}")
        return

    # Проверяем флаг остановки перед обработкой
    if stop_flag and stop_flag.value:
        print("⏹️ Обработка остановлена пользователем")
        return

    try:
        if face_enhance and face_enhancer is not None:
            print("🔍 Улучшение лиц с помощью GFPGAN...")
            _, _, output = face_enhancer.enhance(img_np, has_aligned=False, only_center_face=False, paste_back=True)
        else:
            print("⬆️ Повышение разрешения с помощью Real-ESRGAN...")
            output, _ = upsampler.enhance(img_np, outscale=outscale)
        print(f"✅ Обработка завершена для: {imgname}")
    except RuntimeError as error:
        print(f"❌ Ошибка при обработке {imgname}: {error}")
        print('💡 Если вы столкнулись с ошибкой CUDA out of memory, попробуйте установить меньший размер тайла.')
        return

    # Проверяем флаг остановки перед сохранением
    if stop_flag and stop_flag.value:
        print("⏹️ Обработка остановлена пользователем")
        return

    # Определение расширения файла
    if suffix:
        save_extension = extension[1:] if extension else 'png'
        save_filename = f"{imgname}_{suffix}.{save_extension}"
    else:
        save_extension = extension[1:] if extension else 'png'
        save_filename = f"{imgname}.{save_extension}"

    save_path = os.path.join(output_path, save_filename)

    # Сохранение изображения с использованием PIL
    try:
        output_img = Image.fromarray(output)
        output_img.save(save_path)
        print(f"💾 Сохранено: {save_path}")
    except Exception as e:
        print(f"⚠️ Ошибка сохранения изображения {save_path}: {e}")


def enhance_image(
        input_path: str,
        output_path: str,
        model_folder: str,
        model_name: str = 'RealESRGAN_x4plus_anime_6B',
        denoise_strength: float = 0.5,
        outscale: float = 4.0,
        tile: int = 0,
        tile_pad: int = 10,
        pre_pad: int = 0,
        face_enhance: bool = False,
        fp32: bool = False,
        alpha_upsampler: str = 'realesrgan',
        suffix: str = 'out',
        gpu_id: Optional[int] = None,
        num_processes: Optional[int] = None,
        gfpgan_model_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        stop_callback: Optional[Callable[[], bool]] = None  # Добавлен callback для проверки остановки
):
    """
    Функция для повышения разрешения изображений с использованием Real-ESRGAN.

    :param stop_callback: Функция, возвращающая True если нужно остановить процесс
    """
    print("🔍 Начало процесса повышения разрешения изображений...")

    # Создание папки для сохранения результатов, если она не существует
    os.makedirs(output_path, exist_ok=True)
    print(f"📁 Папка для сохранения результатов создана: {output_path}")

    # Определение списка путей к изображениям
    if os.path.isfile(input_path):
        paths = [input_path]
    else:
        # Поддерживаемые расширения
        supported_extensions = ('*.png', '*.jpg', '*.jpeg', '*.bmp', '*.tif', '*.tiff')
        paths = []
        for ext in supported_extensions:
            paths.extend(glob.glob(os.path.join(input_path, ext)))
        paths = sorted(paths)
    total_images = len(paths)
    print(f"📄 Найдено {total_images} изображений для обработки.")

    if total_images == 0:
        print("⚠️ Нет изображений для обработки. Проверьте путь к входным данным.")
        return

    # Ограничение числа процессов
    if num_processes is None:
        # Если используете один GPU, лучше ограничить число процессов до 1
        num_gpus = torch.cuda.device_count()
        if num_gpus <= 1:
            num_processes = 1
        else:
            num_processes = min(cpu_count(), num_gpus)
    else:
        num_processes = min(num_processes, cpu_count())

    print(f"⚙️ Запуск многопроцессорной обработки с использованием {num_processes} процессов...")

    # Если face_enhance=True и путь к модели не указан, установим путь по умолчанию
    if face_enhance and gfpgan_model_path is None:
        gfpgan_model_path = os.path.join(model_folder, 'GFPGANv1.3.pth')

    # Создаем общий флаг остановки для всех процессов
    manager = Manager()
    shared_stop_flag = manager.Value('b', False)  # 'b' для boolean

    # Инициализация пула процессов
    pool = Pool(
        processes=num_processes,
        initializer=init_worker,
        initargs=(model_folder, model_name, denoise_strength,
                  outscale, tile, tile_pad, pre_pad,
                  face_enhance, fp32, alpha_upsampler, gpu_id,
                  gfpgan_model_path if face_enhance else None,
                  shared_stop_flag)  # Передаем флаг остановки
    )

    args = [(path, idx) for idx, path in enumerate(paths)]
    processed = 0
    results = []

    # Функция для обновления прогресса
    def update_progress(_):
        nonlocal processed
        processed += 1

        # Проверяем флаг остановки через callback
        if stop_callback and stop_callback():
            shared_stop_flag.value = True
            print("⏹️ Получен сигнал остановки")

        if progress_callback:
            progress = int((processed / total_images) * 100)
            result = progress_callback(progress)
            # Если callback вернул -1, останавливаем процесс
            if result == -1:
                shared_stop_flag.value = True
                print("⏹️ Получен сигнал остановки через progress_callback")

    partial_process = partial(
        process_single_image,
        output_path=output_path,
        suffix=suffix,
        outscale=outscale,
        face_enhance=face_enhance
    )

    try:
        # Запуск асинхронных задач
        for path_idx in args:
            result = pool.apply_async(
                partial_process,
                args=(path_idx,),
                callback=update_progress
            )
            results.append(result)

        # Периодически проверяем флаг остановки
        while results:
            completed = []
            for i, result in enumerate(results):
                if result.ready():
                    completed.append(i)
                elif shared_stop_flag.value or (stop_callback and stop_callback()):
                    # Если получен сигнал остановки, останавливаем пул
                    shared_stop_flag.value = True
                    print("⏹️ Остановка обработки...")
                    pool.terminate()
                    pool.join()
                    print("✅ Обработка остановлена")
                    return -1  # Возвращаем специальный код остановки

            # Удаляем завершенные задачи
            for i in reversed(completed):
                results.pop(i)

            # Небольшая задержка чтобы не нагружать CPU
            import time
            time.sleep(0.1)

        pool.close()
        pool.join()

    except Exception as e:
        print(f"❌ Произошла ошибка во время многопроцессорной обработки: {e}")
        pool.terminate()
        pool.join()
        return -1
    finally:
        # Убеждаемся, что пул закрыт
        try:
            pool.close()
            pool.join()
        except:
            pass

    print("🔍 Процесс повышения разрешения изображений завершён.")
    return 0  # Успешное завершение


if __name__ == '__main__':
    # Пример использования при запуске скрипта напрямую
    input_path = 'C:\\Users\\Matve\\Desktop\\Тест'
    output_path = 'D:\\PyCharmProject\\GraphicNovelCleaner\\GraphicNovelCleaner+\\13. MangaLocalizer\\data\\output'

    enhance_image(
        input_path=input_path,
        output_path=output_path,
        model_folder=os.path.join(os.path.dirname(__file__), 'RealESRGAN'),
        model_name='RealESRGAN_x4plus_anime_6B',
        denoise_strength=0.5,
        outscale=4.0,
        tile=256,
        tile_pad=10,
        pre_pad=0,
        face_enhance=True,
        fp32=False,
        alpha_upsampler='realesrgan',
        suffix='enhanced',
        gpu_id=0,
        num_processes=1,
        gfpgan_model_path=None,
        progress_callback=lambda p: print(f"🔄 Прогресс: {p}%")
    )