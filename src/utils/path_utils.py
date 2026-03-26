"""路径和文件名工具"""
import os
import re
from pathlib import Path
from typing import Optional


def sanitize_filename(filename: str, replacement: str = '_') -> str:
    """
    清理文件名中的非法字符
    
    Windows不允许的字符: < > : " / \ | ? *
    
    Args:
        filename: 原始文件名
        replacement: 替换字符
        
    Returns:
        清理后的文件名
    """
    # Windows 非法字符
    illegal_chars = r'[<>\:"/\|?*]'
    
    # 替换非法字符
    sanitized = re.sub(illegal_chars, replacement, filename)
    
    # 去除首尾空格和点（Windows不允许）
    sanitized = sanitized.strip(' .')
    
    # 如果为空，返回默认名称
    if not sanitized:
        return "unnamed"
    
    # 限制长度（Windows路径限制）
    if len(sanitized) > 200:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:200 - len(ext)] + ext
    
    return sanitized


def ensure_dir(path: str) -> str:
    """
    确保目录存在，不存在则创建
    
    Args:
        path: 目录路径
        
    Returns:
        目录路径
    """
    Path(path).mkdir(parents=True, exist_ok=True)
    return path


def get_temp_dir(base_dir: str = './temp') -> str:
    """
    获取临时目录
    
    Args:
        base_dir: 基础目录
        
    Returns:
        临时目录路径
    """
    temp_dir = os.path.join(base_dir, 'downloads')
    return ensure_dir(temp_dir)


def get_download_path(base_dir: str, video_title: str, suffix: str = '.mp4') -> str:
    """
    生成下载文件路径
    
    Args:
        base_dir: 基础下载目录
        video_title: 视频标题
        suffix: 文件后缀
        
    Returns:
        完整的下载路径
    """
    safe_title = sanitize_filename(video_title)
    
    # 避免文件名冲突
    filepath = os.path.join(base_dir, f"{safe_title}{suffix}")
    counter = 1
    
    while os.path.exists(filepath):
        filepath = os.path.join(
            base_dir, 
            f"{safe_title}_{counter}{suffix}"
        )
        counter += 1
    
    return filepath


def get_unique_filename(directory: str, filename: str) -> str:
    """
    获取唯一文件名（如果存在则添加序号）
    
    Args:
        directory: 目录
        filename: 原始文件名
        
    Returns:
        唯一的文件名（不含路径）
    """
    filepath = os.path.join(directory, filename)
    
    if not os.path.exists(filepath):
        return filename
    
    name, ext = os.path.splitext(filename)
    counter = 1
    
    while True:
        new_filename = f"{name}_{counter}{ext}"
        new_filepath = os.path.join(directory, new_filename)
        
        if not os.path.exists(new_filepath):
            return new_filename
        
        counter += 1
