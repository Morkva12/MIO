import os
import glob
import numpy as np
from PIL import Image
from typing import Optional, List
from multiprocessing import Pool, cpu_count
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

def download_gfpgan_model(model_path: str):
    """
    Загружает модель GFPGAN, если она не существует.
    """
    url = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.3.pth"
    print(f"⬇️ Загрузка модели GFPGAN с {url}...")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(model_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print(f"✅ Модель GFPGAN успешно загружена и сохранена в {model_path}")
    else:
        raise RuntimeError(f"🚫 Не удалось скачать модель GFPGAN с {url}. Статус код: {response.status_code}")

def download_realesrgan_model(model_path: str):
    """
    Загружает модель Real-ESRGAN, если она не существует.
    """
    url = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5/RealESRGAN_x4plus_anime_6B.pth"
    print(f"⬇️ Загрузка модели Real-ESRGAN с {url}...")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(model_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print(f"✅ Модель Real-ESRGAN успешно загружена и сохранена в {model_path}")
    else:
        raise RuntimeError(f"🚫 Не удалось скачать модель Real-ESRGAN с {url}. Статус код: {response.status_code}")

def init_worker(model_folder: str, model_name: str, denoise_strength: float,
               outscale: float, tile: int, tile_pad: int, pre_pad: int,
               face_enhance: bool, fp32: bool, alpha_upsampler: str,
               gpu_id: Optional[int], gfpgan_model_path: Optional[str]):
    """
    Инициализатор для каждого процесса. Настраивает RealESRGANer и GFPGANer (если требуется).
    """
    global upsampler
    global face_enhancer

    print(f"🔧 Инициализация процесса с моделью {model_name} на GPU {gpu_id}...")

    # Определение модели в зависимости от имени
    model_path = os.path.join(model_folder, f'{model_name}.pth')
    print(f"🔍 Проверка наличия модели по пути: {model_path}")  # Добавлено

    if not os.path.isfile(model_path):
        if model_name == 'RealESRGAN_x4plus_anime_6B':
            print(f"🚫 Модель {model_name} не найдена в папке {model_folder}. Начинаю загрузку...")
            download_realesrgan_model(model_path)
        else:
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
            # Добавьте weights_only=True, если возможно
            # weights_only=True
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
        if not os.path.isfile(gfpgan_model_path):
            print(f"🚫 Модель GFPGAN не найдена по пути {gfpgan_model_path}. Начинаю загрузку...")
            download_gfpgan_model(gfpgan_model_path)
        print("👤 Инициализация GFPGAN для улучшения лиц...")
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

def process_single_image(path: str, idx: int, output_path: str,
                        suffix: str, outscale: float, face_enhance: bool):
    """
    Обрабатывает одно изображение: повышает его разрешение и сохраняет результат.
    """
    global upsampler
    global face_enhancer

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
    num_processes: Optional[int] = None,  # Добавлено для контроля числа процессов
    gfpgan_model_path: Optional[str] = None  # Добавлен путь к модели GFPGAN
):
    """
    Функция для повышения разрешения изображений с использованием Real-ESRGAN.

    :param input_path: Путь к входному изображению или папке с изображениями.
    :param output_path: Путь к папке для сохранения обработанных изображений.
    :param model_folder: Путь к папке с моделями Real-ESRGAN.
    :param model_name: Название модели (по умолчанию 'RealESRGAN_x4plus_anime_6B').
    :param denoise_strength: Сила шумоподавления (только для модели 'realesr-general-x4v3').
    :param outscale: Коэффициент увеличения разрешения.
    :param tile: Размер тайла для обработки (0 для отключения).
    :param tile_pad: Отступ тайла.
    :param pre_pad: Предварительный отступ.
    :param face_enhance: Использовать ли GFPGAN для улучшения лиц.
    :param fp32: Использовать ли точность fp32 (по умолчанию False, используется fp16).
    :param alpha_upsampler: Апсемплер для альфа-каналов ('realesrgan' или 'bicubic').
    :param suffix: Суффикс для сохраненных изображений.
    :param gpu_id: ID GPU для использования (по умолчанию None).
    :param num_processes: Количество процессов для многопроцессорной обработки (по умолчанию число CPU).
    :param gfpgan_model_path: Путь к модели GFPGAN (обязательно, если face_enhance=True).
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

    # Инициализация пула процессов
    pool = Pool(
        processes=num_processes,
        initializer=init_worker,
        initargs=(model_folder, model_name, denoise_strength,
                  outscale, tile, tile_pad, pre_pad,
                  face_enhance, fp32, alpha_upsampler, gpu_id, gfpgan_model_path if face_enhance else None)
    )

    try:
        # Создание частичной функции с фиксированными параметрами
        partial_process = partial(
            process_single_image,
            output_path=output_path,
            suffix=suffix,
            outscale=outscale,
            face_enhance=face_enhance
        )

        # Запуск обработки изображений
        pool.starmap(
            partial_process,
            [(path, idx) for idx, path in enumerate(paths)]
        )
    except Exception as e:
        print(f"❌ Произошла ошибка во время многопроцессорной обработки: {e}")
    finally:
        pool.close()
        pool.join()

    print("🎉 Обработка всех изображений завершена.")

# Пример использования функции
if __name__ == '__main__':
    enhance_image(
        input_path=r"C:\Users\Matve\Desktop\Тест",  # Путь к папке с входными изображениями
        output_path=r"D:\PyCharmProject\GraphicNovelCleaner\GraphicNovelCleaner+\13. MangaLocalizer\data\output",  # Путь для сохранения
        model_folder=r"D:\PyCharmProject\GraphicNovelCleaner\GraphicNovelCleaner+\13. MangaLocalizer\data\model\Real-ESRGAN",  # Исправленный путь
        model_name='RealESRGAN_x4plus_anime_6B',  # Название модели
        denoise_strength=0.5,  # Сила шумоподавления (не используется для данной модели)
        outscale=2,  # Коэффициент увеличения разрешения (рекомендуется 4)
        tile=256,  # Размер тайла (0 для отключения)
        tile_pad=10,  # Отступ тайла
        pre_pad=0,  # Предварительный отступ
        face_enhance=True,  # Установите True, если хотите улучшить лица
        fp32=False,  # Использовать fp16 точность
        alpha_upsampler='realesrgan',  # Апсемплер для альфа-каналов
        suffix='enhanced',  # Суффикс для сохранённых изображений
        gpu_id=0,  # ID вашего GPU или None для автоматического выбора
        num_processes=1,  # Ограничение числа процессов до 1 для одного GPU
        gfpgan_model_path=r"D:\PyCharmProject\GraphicNovelCleaner\GraphicNovelCleaner+\13. MangaLocalizer\data\model\Real-ESRGAN\GFPGANv1.3.pth"  # Путь к модели GFPGAN
    )
