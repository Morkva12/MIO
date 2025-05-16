import os
import cv2
import numpy as np
from moviepy.editor import VideoFileClip
from PIL import Image
from tqdm import tqdm  # Для прогресс-бара
from typing import Optional

from basicsr.archs.rrdbnet_arch import RRDBNet
from basicsr.archs.srvgg_arch import SRVGGNetCompact
from realesrgan import RealESRGANer

from gfpgan import GFPGANer  # Если требуется улучшение лиц

# Глобальные переменные
upsampler = None
face_enhancer = None

def init_worker(model_folder: str, model_name: str, denoise_strength: float, outscale: float, tile: int, tile_pad: int, pre_pad: int, face_enhance: bool, fp32: bool, alpha_upsampler: str, gpu_id: Optional[int], gfpgan_model_path: Optional[str]):
    global upsampler
    global face_enhancer

    model_path = os.path.join(model_folder, f'{model_name}.pth')

    if model_name == 'RealESRGAN_x4plus':
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
        raise ValueError(f"Неизвестная модель: {model_name}")

    upsampler = RealESRGANer(
        scale=netscale,
        model_path=model_path,
        model=model,
        tile=tile,
        tile_pad=tile_pad,
        pre_pad=pre_pad,
        half=not fp32,
        gpu_id=gpu_id,
    )

    if face_enhance:
        if gfpgan_model_path is None:
            gfpgan_model_path = os.path.join(model_folder, 'GFPGANv1.3.pth')
        face_enhancer = GFPGANer(
            model_path=gfpgan_model_path,
            upscale=outscale,
            arch='clean',
            channel_multiplier=2,
            bg_upsampler=upsampler
        )

def process_video(input_video, output_video, model_name, denoise_strength, outscale, face_enhance, frames_folder):
    global upsampler
    global face_enhancer

    print(f"Инициализация модели {model_name} с шумоподавлением {denoise_strength}...")
    init_worker(
        model_folder=model_folder,
        model_name=model_name,
        denoise_strength=denoise_strength,
        outscale=outscale,
        tile=512,
        tile_pad=10,
        pre_pad=0,
        face_enhance=face_enhance,
        fp32=False,
        alpha_upsampler='realesrgan',
        gpu_id=None,
        gfpgan_model_path=None
    )

    cap = cv2.VideoCapture(input_video)
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    temp_video = cv2.VideoWriter(output_video, fourcc, fps, (frame_width, frame_height))

    print("Начало обработки кадров...")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if frame_count <= 0:
        print("Ошибка: количество кадров не может быть определено.")
        return

    os.makedirs(frames_folder, exist_ok=True)

    for idx in tqdm(range(frame_count), desc="Обработка видео"):
        ret, frame = cap.read()
        if not ret:
            break

        if idx % 1000 == 0:  # Сохраняем каждый 30-й кадр
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            try:
                if face_enhance and face_enhancer is not None:
                    _, _, enhanced_frame = face_enhancer.enhance(frame_rgb, has_aligned=False, only_center_face=False, paste_back=True)
                else:
                    enhanced_frame, _ = upsampler.enhance(frame_rgb, outscale=outscale)

                enhanced_frame_bgr = cv2.cvtColor(enhanced_frame, cv2.COLOR_RGB2BGR)
                frame_name = f"frame_{idx:05d}_{model_name}_denoise{denoise_strength}.png"
                frame_path = os.path.join(frames_folder, frame_name)
                cv2.imwrite(frame_path, enhanced_frame_bgr)

            except RuntimeError as error:
                print(f"Ошибка обработки кадра {idx}: {error}")
                continue

        enhanced_frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        temp_video.write(enhanced_frame_bgr)

    cap.release()
    temp_video.release()
    print(f"Обработанное видео сохранено в {output_video}")

def add_audio_to_video(video_path: str, audio_path: str, output_path: str):
    video = VideoFileClip(video_path)
    audio = VideoFileClip(audio_path).audio
    final_video = video.set_audio(audio)
    final_video.write_videofile(output_path, codec="libx264", audio_codec="aac")


if __name__ == '__main__':
    main_folder = "D:\\Overlord"  # Папка с видео
    model_folder = "D:\\PyCharmProject\\GraphicNovelCleaner\\GraphicNovelCleaner+\\13. MangaLocalizer\\data\\model\\Real-ESRGAN"

    models = [
        "RealESRGAN_x4plus",
        "RealESRGAN_x4plus_anime_6B",
        "RealESRGAN_x2plus",
        "realesr-animevideov3",
        "realesr-general-x4v3"
    ]
    denoise_strengths = [0.5, 1.0]

    for video_file in os.listdir(main_folder):
        if not video_file.endswith(('.mp4', '.avi', '.mov')):
            continue

        input_video = os.path.join(main_folder, video_file)
        video_name, _ = os.path.splitext(video_file)

        for model_name in models:
            for denoise_strength in denoise_strengths:
                print(f"Обработка видео {video_file} с моделью {model_name} и шумоподавлением {denoise_strength}...")

                temp_video = os.path.join(main_folder, f"{video_name}_temp_{model_name}_denoise{denoise_strength}.mp4")
                output_video = os.path.join(main_folder, f"{video_name}_{model_name}_denoise{denoise_strength}_final.mp4")
                frames_folder = os.path.join(main_folder, f"{video_name}_frames_{model_name}_denoise{denoise_strength}")

                process_video(input_video, temp_video, model_name, denoise_strength, outscale=4.0, face_enhance=False, frames_folder=frames_folder)

                print("🎵 Добавление звуковой дорожки...")
                add_audio_to_video(temp_video, input_video, output_video)

                print(f"✅ Готовое видео: {output_video}")
