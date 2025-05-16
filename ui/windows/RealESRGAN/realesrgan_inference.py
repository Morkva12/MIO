# -*- coding: utf-8 -*-
# ui/main_window//RealESRGAN/realesrgan_inference.py
# Язык и комментарии на русском!

import argparse
import cv2
import glob
import os
from basicsr.archs.rrdbnet_arch import RRDBNet
from basicsr.utils.download_util import load_file_from_url
import shutil

from realesrgan import RealESRGANer
from realesrgan.archs.srvgg_arch import SRVGGNetCompact

try:
    from gfpgan import GFPGANer
except ImportError:
    GFPGANer = None  # Если GFPGAN не установлен


class RealESRGANProcessor:
    """
    Класс для обработки изображений с использованием Real-ESRGAN.
    Позволяет загружать модели, настраивать параметры и обрабатывать изображения.
    """

    def __init__(self, model_dir, device='cuda', fp32=False, temp_dir='/Предобработка'):
        """
        Инициализация процессора Real-ESRGAN.

        :param model_dir: Путь к директории с моделями (относительный путь).
        :param device: Устройство для вычислений ('cuda' или 'cpu').
        :param fp32: Использовать ли fp32 точность. По умолчанию False (fp16).
        :param temp_dir: Путь к временной директории для хранения временных файлов.
        """
        self.model_dir = os.path.abspath(model_dir)
        self.device = device
        self.fp32 = fp32
        self.upsampler = None
        self.face_enhancer = None
        self.temp_dir = os.path.abspath(temp_dir)
        os.makedirs(self.temp_dir, exist_ok=True)

    def load_model(self, model_name, denoise_strength=0.5, outscale=4, tile=0,
                   tile_pad=10, pre_pad=0, face_enhance=False, alpha_upsampler='realesrgan'):
        """
        Загружает модель Real-ESRGAN.

        :param model_name: Название модели.
        :param denoise_strength: Степень денойзинга (только для 'realesr-general-x4v3').
        :param outscale: Коэффициент масштабирования изображения.
        :param tile: Размер тайла для обработки.
        :param tile_pad: Отступ для тайлов.
        :param pre_pad: Предварительный отступ.
        :param face_enhance: Использовать ли GFPGAN для улучшения лиц.
        :param alpha_upsampler: Усилитель для альфа-каналов ('realesrgan' или 'bicubic').
        """
        model_name = model_name.split('.')[0]
        if model_name == 'RealESRGAN_x4plus':  # x4 RRDBNet модель
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                           num_block=23, num_grow_ch=32, scale=4)
            netscale = 4
            file_url = ['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth']
        elif model_name == 'RealESRNet_x4plus':  # x4 RRDBNet модель
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                           num_block=23, num_grow_ch=32, scale=4)
            netscale = 4
            file_url = ['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/RealESRNet_x4plus.pth']
        elif model_name == 'RealESRGAN_x4plus_anime_6B':  # x4 RRDBNet модель с 6 блоками
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                           num_block=6, num_grow_ch=32, scale=4)
            netscale = 4
            file_url = ['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth']
        elif model_name == 'RealESRGAN_x2plus':  # x2 RRDBNet модель
            model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                           num_block=23, num_grow_ch=32, scale=2)
            netscale = 2
            file_url = ['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth']
        elif model_name == 'realesr-animevideov3':  # x4 VGG-style модель (XS размер)
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                    num_conv=16, upscale=4, act_type='prelu')
            netscale = 4
            file_url = ['https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth']
        elif model_name == 'realesr-general-x4v3':  # x4 VGG-style модель (S размер)
            model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                    num_conv=32, upscale=4, act_type='prelu')
            netscale = 4
            file_url = [
                'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth',
                'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth'
            ]
        else:
            raise ValueError(f"Неизвестное название модели: {model_name}")

        # Определение пути к модели
        weights_dir = os.path.join(self.model_dir, 'Real-ESRGAN', 'weights')
        os.makedirs(weights_dir, exist_ok=True)
        model_path = os.path.join(weights_dir, model_name + '.pth')
        if not os.path.isfile(model_path):
            print(f"Отладка: Модель {model_name} не найдена. Скачивание...")
            ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
            for url in file_url:
                model_path = load_file_from_url(
                    url=url,
                    model_dir=weights_dir,
                    progress=True,
                    file_name=None
                )
                if os.path.isfile(model_path):
                    break
            else:
                raise FileNotFoundError(f"Не удалось скачать модель {model_name}.")

        # Использование dni для контроля степени денойзинга
        dni_weight = None
        if model_name == 'realesr-general-x4v3' and denoise_strength != 1:
            wdn_model_path = os.path.join(weights_dir, 'realesr-general-wdn-x4v3.pth')
            if not os.path.isfile(wdn_model_path):
                print("Отладка: Модель WDN для dni не найдена. Скачивание...")
                wdn_url = 'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth'
                wdn_model_path = load_file_from_url(
                    url=wdn_url,
                    model_dir=weights_dir,
                    progress=True,
                    file_name=None
                )
                if not os.path.isfile(wdn_model_path):
                    raise FileNotFoundError("Не удалось скачать модель WDN для dni.")
            dni_weight = [denoise_strength, 1 - denoise_strength]

        # Инициализация RealESRGANer
        self.upsampler = RealESRGANer(
            scale=netscale,
            model_path=model_path,
            dni_weight=dni_weight,
            model=model,
            tile=tile,
            tile_pad=tile_pad,
            pre_pad=pre_pad,
            half=not self.fp32,
            gpu_id=0 if self.device == 'cuda' else None,
            alpha_upsampler=alpha_upsampler
        )

        # Инициализация GFPGAN для улучшения лиц, если требуется
        if face_enhance:
            if GFPGANer is None:
                raise ImportError("Для улучшения лиц требуется установить GFPGAN.")
            gfpgan_weights = os.path.join(self.model_dir, 'GFPGAN', 'weights', 'GFPGANv1.3.pth')
            if not os.path.isfile(gfpgan_weights):
                print("Отладка: Модель GFPGAN не найдена. Скачивание...")
                gfpgan_url = 'https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.3.pth'
                os.makedirs(os.path.dirname(gfpgan_weights), exist_ok=True)
                gfpgan_weights = load_file_from_url(
                    url=gfpgan_url,
                    model_dir=os.path.dirname(gfpgan_weights),
                    progress=True,
                    file_name=None
                )
                if not os.path.isfile(gfpgan_weights):
                    raise FileNotFoundError("Не удалось скачать модель GFPGAN.")
            self.face_enhancer = GFPGANer(
                model_path=gfpgan_weights,
                upscale=outscale,
                arch='clean',
                channel_multiplier=2,
                bg_upsampler=self.upsampler
            )
        else:
            self.face_enhancer = None

    def enhance_image(self, input_path, output_path, suffix='out', ext='auto', face_enhance=False):
        """
        Обрабатывает одно изображение и сохраняет результат.

        :param input_path: Путь к входному изображению.
        :param output_path: Путь к папке для сохранения результата.
        :param suffix: Суффикс для сохраненного изображения.
        :param ext: Расширение сохраняемого изображения ('auto', 'jpg', 'png').
        :param face_enhance: Использовать ли улучшение лиц через GFPGAN.
        """
        if not os.path.isfile(input_path):
            print(f"Отладка: Входной файл не найден: {input_path}")
            return

        os.makedirs(output_path, exist_ok=True)

        imgname, extension = os.path.splitext(os.path.basename(input_path))
        print(f"Отладка: Обработка изображения {imgname}")

        img = cv2.imread(input_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            print(f"Отладка: Не удалось загрузить изображение: {input_path}")
            return

        if len(img.shape) == 3 and img.shape[2] == 4:
            img_mode = 'RGBA'
        else:
            img_mode = None

        try:
            if face_enhance and self.face_enhancer is not None:
                _, _, output = self.face_enhancer.enhance(
                    img, has_aligned=False, only_center_face=False, paste_back=True)
            else:
                output, _ = self.upsampler.enhance(img, outscale=4)
        except RuntimeError as error:
            print(f"Отладка: Ошибка при обработке изображения {input_path}: {error}")
            return

        # Определение расширения для сохранения
        if ext == 'auto':
            save_ext = extension[1:]
        else:
            save_ext = ext

        if img_mode == 'RGBA':
            save_ext = 'png'

        if suffix == '':
            save_filename = f"{imgname}.{save_ext}"
        else:
            save_filename = f"{imgname}_{suffix}.{save_ext}"

        save_filepath = os.path.join(output_path, save_filename)
        cv2.imwrite(save_filepath, output)
        print(f"Отладка: Изображение сохранено как {save_filepath}")

    def enhance_folder(self, input_folder, output_folder, suffix='out', ext='auto', face_enhance=False):
        """
        Обрабатывает все изображения в папке и сохраняет результаты.

        :param input_folder: Путь к папке с входными изображениями.
        :param output_folder: Путь к папке для сохранения результатов.
        :param suffix: Суффикс для сохраняемых изображений.
        :param ext: Расширение сохраняемых изображений ('auto', 'jpg', 'png').
        :param face_enhance: Использовать ли улучшение лиц через GFPGAN.
        """
        if not os.path.isdir(input_folder):
            print(f"Отладка: Входная папка не найдена: {input_folder}")
            return

        paths = sorted(glob.glob(os.path.join(input_folder, '*')))
        if not paths:
            print(f"Отладка: В папке нет изображений для обработки: {input_folder}")
            return

        for idx, path in enumerate(paths):
            imgname, extension = os.path.splitext(os.path.basename(path))
            print(f"Отладка: Обработка {idx + 1}/{len(paths)}: {imgname}")
            self.enhance_image(path, output_folder, suffix, ext, face_enhance)

    def enhance(self, input_path, output_path, suffix='out', ext='auto', face_enhance=False):
        """
        Универсальный метод для обработки одного изображения или папки.

        :param input_path: Путь к входному изображению или папке.
        :param output_path: Путь к папке для сохранения результатов.
        :param suffix: Суффикс для сохраняемых изображений.
        :param ext: Расширение сохраняемых изображений ('auto', 'jpg', 'png').
        :param face_enhance: Использовать ли улучшение лиц через GFPGAN.
        """
        if os.path.isfile(input_path):
            self.enhance_image(input_path, output_path, suffix, ext, face_enhance)
        elif os.path.isdir(input_path):
            self.enhance_folder(input_path, output_path, suffix, ext, face_enhance)
        else:
            print(f"Отладка: Неверный путь: {input_path}")

    def cleanup_temp_dir(self):
        """
        Очищает временную директорию.
        """
        if os.path.isdir(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
                print(f"Отладка: Временная директория очищена: {self.temp_dir}")
                os.makedirs(self.temp_dir, exist_ok=True)
            except Exception as e:
                print(f"Отладка: Ошибка при очистке временной директории: {e}")
