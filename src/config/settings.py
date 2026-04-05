"""应用配置管理"""
import json
import os
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class Settings:
    """
    应用配置
    
    使用dataclass定义配置项，支持从文件加载和保存
    """
    
    # 下载设置
    download_path: str = "./downloads"
    audio_download_path: str = "./downloads"  # 音频下载路径（默认同视频路径）
    audio_path_follow_video: bool = True      # 音频路径是否跟随视频路径
    temp_path: str = "./temp"
    default_quality: int = 80           # 默认1080P (qn=80)
    auto_quality: bool = True           # 自动选择最佳质量
    max_concurrent: int = 3             # 最大并发下载数
    max_retry: int = 3                  # 最大重试次数

    # 列表加载设置
    page_size: int = 20                 # 每次分段加载的视频数量
    
    # 下载选项
    auto_merge: bool = True             # 自动合并音视频
    keep_temp: bool = False             # 是否保留临时文件
    download_danmaku: bool = False      # 下载弹幕
    download_subtitle: bool = False     # 下载字幕
    download_cover: bool = False        # 下载封面

    # 音频下载设置
    download_type: str = "video"        # 下载类型: video 或 audio
    audio_embed_cover: bool = True      # 音频文件是否嵌入封面
    
    # 文件名模板
    filename_template: str = "{title}"  # 文件名模板
    
    # 网络设置
    timeout: int = 30                   # 请求超时(秒)
    chunk_size: int = 8192              # 下载块大小
    
    # 窗口设置
    window_width: int = 1200
    window_height: int = 800
    minimize_to_tray: bool = False    # 点击×最小化到托盘而不是退出
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def save(self, filepath: str) -> None:
        """
        保存配置到文件
        
        Args:
            filepath: 配置文件路径
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load(cls, filepath: str) -> 'Settings':
        """
        从文件加载配置
        
        Args:
            filepath: 配置文件路径
            
        Returns:
            Settings实例
        """
        if not os.path.exists(filepath):
            # 如果文件不存在，返回默认配置
            return cls()
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 过滤掉不存在的字段
            valid_fields = {f for f in cls.__dataclass_fields__}
            filtered_data = {k: v for k, v in data.items() if k in valid_fields}
            
            return cls(**filtered_data)
        
        except (json.JSONDecodeError, TypeError) as e:
            print(f"加载配置失败: {e}，使用默认配置")
            return cls()
    
    @classmethod
    def get_default_path(cls) -> str:
        """获取默认配置文件路径（项目内）"""
        # 获取项目根目录（src/config 的上级目录）
        project_root = Path(__file__).parent.parent.parent
        config_dir = project_root / 'config'
        config_dir.mkdir(exist_ok=True)
        return str(config_dir / 'config.json')


# 全局配置实例
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局配置实例（懒加载）"""
    global _settings
    if _settings is None:
        _settings = Settings.load(Settings.get_default_path())
    return _settings


def reload_settings() -> Settings:
    """重新加载配置"""
    global _settings
    _settings = Settings.load(Settings.get_default_path())
    return _settings


def save_settings(settings: Optional[Settings] = None) -> None:
    """保存配置"""
    if settings is None:
        settings = get_settings()
    settings.save(Settings.get_default_path())
