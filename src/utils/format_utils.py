"""格式化工具"""
from typing import Union


Number = Union[int, float]


def format_size(size_bytes: Number) -> str:
    """
    格式化文件大小
    
    Args:
        size_bytes: 字节数
        
    Returns:
        格式化后的字符串 (如 "1.5 GB")
    """
    if size_bytes < 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.2f} {units[unit_index]}"


def format_speed(speed_bps: Number) -> str:
    """
    格式化下载速度
    
    Args:
        speed_bps: 字节/秒
        
    Returns:
        格式化后的字符串 (如 "2.5 MB/s")
    """
    return format_size(speed_bps) + '/s'


def format_duration(seconds: int) -> str:
    """
    格式化时长
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化后的字符串 (如 "1:30:00" 或 "5:30")
    """
    if seconds < 0:
        seconds = 0
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_number(num: int) -> str:
    """
    格式化数字（添加千分位）
    
    Args:
        num: 数字
        
    Returns:
        格式化后的字符串 (如 "1,234,567")
    """
    return f"{num:,}"


def format_percentage(current: Number, total: Number) -> str:
    """
    格式化百分比
    
    Args:
        current: 当前值
        total: 总值
        
    Returns:
        百分比字符串 (如 "45.5%")
    """
    if total <= 0:
        return "0.0%"
    
    percentage = (current / total) * 100
    return f"{percentage:.1f}%"


def truncate_string(s: str, max_length: int, suffix: str = '...') -> str:
    """
    截断字符串
    
    Args:
        s: 原始字符串
        max_length: 最大长度
        suffix: 后缀
        
    Returns:
        截断后的字符串
    """
    if len(s) <= max_length:
        return s
    
    return s[:max_length - len(suffix)] + suffix
