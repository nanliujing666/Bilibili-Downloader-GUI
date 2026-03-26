"""FFmpeg工具 - 音视频合并"""
import logging
import os
import subprocess
import json
import sys
from typing import Optional, Callable, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# 全局GPU支持状态
NVIDIA_GPU_SUPPORTED: Optional[bool] = None


def get_ffmpeg_dir() -> Optional[Path]:
    """获取项目内FFmpeg目录路径"""
    # 从当前文件向上查找项目根目录
    current_file = Path(__file__).resolve()
    # src/utils/ffmpeg_utils.py -> 项目根目录
    project_root = current_file.parent.parent.parent

    ffmpeg_dir = project_root / "ffm"
    if ffmpeg_dir.exists() and (ffmpeg_dir / "ffmpeg.exe").exists():
        return ffmpeg_dir
    return None


def get_ffmpeg_cmd(cmd: str = "ffmpeg") -> list:
    """获取FFmpeg命令（优先使用项目内的）

    Args:
        cmd: 命令名称 ('ffmpeg' 或 'ffprobe')

    Returns:
        命令列表，可直接用于subprocess
    """
    ffmpeg_dir = get_ffmpeg_dir()
    if ffmpeg_dir:
        # 使用项目内的FFmpeg
        exe_path = ffmpeg_dir / f"{cmd}.exe"
        return [str(exe_path)]
    else:
        # 使用系统PATH中的FFmpeg
        return [cmd]


def check_nvidia_gpu() -> bool:
    """检查是否支持NVIDIA GPU加速"""
    global NVIDIA_GPU_SUPPORTED

    if NVIDIA_GPU_SUPPORTED is not None:
        return NVIDIA_GPU_SUPPORTED

    try:
        # 使用项目内的ffmpeg检查
        ffmpeg_cmd = get_ffmpeg_cmd("ffmpeg")
        result = subprocess.run(
            ffmpeg_cmd + ['-encoders'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        has_nvenc = 'h264_nvenc' in result.stdout.lower()

        # 检查nvidia-smi
        nvidia_smi = subprocess.run(
            ['nvidia-smi'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        has_nvidia = nvidia_smi.returncode == 0

        NVIDIA_GPU_SUPPORTED = has_nvenc and has_nvidia

        if NVIDIA_GPU_SUPPORTED:
            logger.info("检测到NVIDIA GPU加速支持")
        else:
            logger.info("未检测到NVIDIA GPU加速，使用CPU模式")

    except Exception as e:
        logger.debug(f"GPU检测失败: {e}")
        NVIDIA_GPU_SUPPORTED = False

    return NVIDIA_GPU_SUPPORTED


def get_video_info(video_file: str) -> Tuple[int, int, str]:
    """
    获取视频信息

    Returns:
        (width, height, codec_name)
    """
    try:
        ffprobe_cmd = get_ffmpeg_cmd("ffprobe")
        cmd = ffprobe_cmd + [
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,codec_name',
            '-of', 'json',
            video_file
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        info = json.loads(result.stdout)

        stream = info['streams'][0]
        return (
            stream.get('width', 1920),
            stream.get('height', 1080),
            stream.get('codec_name', 'h264')
        )
    except Exception as e:
        logger.warning(f"无法获取视频信息: {e}，使用默认值")
        return (1920, 1080, 'h264')


def build_ffmpeg_cmd(
    video_file: str,
    audio_file: str,
    output_file: str,
    use_gpu: bool = False,
    width: int = 1920,
    height: int = 1080,
    attempt: int = 0
) -> list:
    """
    构建FFmpeg命令

    Args:
        video_file: 视频文件路径
        audio_file: 音频文件路径
        output_file: 输出文件路径
        use_gpu: 是否使用GPU加速
        width: 视频宽度
        height: 视频高度
        attempt: 尝试次数（用于选择不同方案）

    Returns:
        FFmpeg命令列表
    """
    ffmpeg_cmd = get_ffmpeg_cmd("ffmpeg")

    # GPU模式命令选项
    gpu_cmds = [
        # 最基本的NVENC配置
        ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
         '-c:v', 'h264_nvenc', '-preset', 'slow', '-profile:v', 'main',
         '-c:a', 'copy', '-map', '0:v:0', '-map', '1:a:0',
         '-shortest', output_file],

        # 不使用任何预设
        ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
         '-c:v', 'h264_nvenc', '-c:a', 'copy',
         '-map', '0:v:0', '-map', '1:a:0',
         '-shortest', output_file],

        # CUDA硬件加速 + 视频缩放
        ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
         '-vf', f'scale={width}:{height},format=yuv420p',
         '-c:v', 'h264_nvenc', '-c:a', 'copy',
         '-map', '0:v:0', '-map', '1:a:0',
         '-shortest', output_file]
    ]

    # CPU模式命令选项
    cpu_cmds = [
        # 流复制模式（最快）
        ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
         '-c:v', 'copy', '-c:a', 'copy',
         '-map', '0:v:0', '-map', '1:a:0',
         '-shortest', output_file],

        # CPU编码模式
        ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
         '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
         '-c:a', 'copy', '-map', '0:v:0', '-map', '1:a:0',
         '-shortest', output_file]
    ]

    # 第一次尝试：流复制
    if attempt == 0:
        return ffmpeg_cmd + ['-y', '-i', video_file, '-i', audio_file,
                '-c:v', 'copy', '-c:a', 'copy',
                '-map', '0:v:0', '-map', '1:a:0',
                '-shortest', output_file]

    # 根据GPU支持选择方案
    if use_gpu and attempt - 1 < len(gpu_cmds):
        return gpu_cmds[attempt - 1]

    # 使用CPU方案
    cpu_index = attempt - 1 if not use_gpu else attempt - len(gpu_cmds) - 1
    if cpu_index < len(cpu_cmds):
        return cpu_cmds[cpu_index]

    return cpu_cmds[-1]  # 返回最基本的CPU命令


def merge_video_audio(
    video_file: str,
    audio_file: str,
    output_file: str,
    progress_callback: Optional[Callable[[float], None]] = None
) -> bool:
    """
    合并音视频

    Args:
        video_file: 视频文件路径
        audio_file: 音频文件路径
        output_file: 输出文件路径
        progress_callback: 进度回调函数(0-100)

    Returns:
        是否成功
    """
    # 检查GPU支持
    use_gpu = check_nvidia_gpu()

    # 获取视频信息
    width, height, _ = get_video_info(video_file)

    max_attempts = 6
    attempt = 0

    while attempt < max_attempts:
        cmd = build_ffmpeg_cmd(
            video_file, audio_file, output_file,
            use_gpu=use_gpu, width=width, height=height, attempt=attempt
        )

        mode = "流复制" if attempt == 0 else "GPU" if use_gpu and attempt < 3 else "CPU"
        logger.info(f"尝试{mode}方案 {attempt + 1}/{max_attempts}")

        try:
            # 设置环境变量，确保能找到DLL
            ffmpeg_dir = get_ffmpeg_dir()
            env = os.environ.copy()
            if ffmpeg_dir:
                # 将FFmpeg目录添加到PATH
                env['PATH'] = str(ffmpeg_dir) + os.pathsep + env.get('PATH', '')

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding='utf-8',
                env=env
            )

            # 读取输出（FFmpeg进度信息在stderr/stdout）
            for line in process.stdout:
                # 解析进度信息（简化版）
                if progress_callback and 'time=' in line:
                    # 这里可以解析时间信息计算进度
                    pass

            return_code = process.wait()

            if return_code == 0:
                logger.info(f"合并成功！")
                return True
            else:
                logger.warning(f"方案 {attempt + 1} 失败")
                attempt += 1

        except Exception as e:
            logger.error(f"合并异常: {e}")
            attempt += 1

    logger.error("所有合并方案都失败")
    return False


def check_ffmpeg() -> bool:
    """检查FFmpeg是否可用（优先检查项目内的）"""
    # 首先检查项目内的FFmpeg
    ffmpeg_dir = get_ffmpeg_dir()
    if ffmpeg_dir:
        exe_path = ffmpeg_dir / "ffmpeg.exe"
        try:
            result = subprocess.run(
                [str(exe_path), '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if result.returncode == 0:
                logger.info(f"使用项目内FFmpeg: {ffmpeg_dir}")
                return True
        except Exception:
            pass

    # 然后检查系统PATH中的FFmpeg
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
