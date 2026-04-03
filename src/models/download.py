"""下载相关模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from .enums import TaskStatus
from .video import VideoInfo


@dataclass
class DownloadTask:
    """下载任务模型 - 不可变"""
    task_id: str                        # 任务唯一ID
    video: Optional[VideoInfo] = None   # 视频信息（延迟加载）
    status: TaskStatus = TaskStatus.PENDING  # 任务状态
    progress: float = 0.0               # 进度 0-100
    download_path: str = ""             # 下载路径
    quality: int = 80                   # 视频质量qn (默认1080P)
    video_codec: str = ""               # 视频编码
    audio_quality: int = 0              # 音频质量

    # 下载选项
    download_video: bool = True         # 下载视频
    download_audio: bool = True         # 下载音频
    download_danmaku: bool = False      # 下载弹幕
    download_subtitle: bool = False     # 下载字幕
    download_cover: bool = False        # 下载封面

    # 来源信息
    source: str = "url"                 # 下载来源 (url/favorite/watch_later/cheese)
    source_name: Optional[str] = None   # 来源名称
    source_id: Optional[str] = None     # 来源ID

    # 延迟解析用的URL
    url: str = ""                       # 视频URL（用于延迟解析）

    # 时间相关
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    # 状态信息
    error_message: Optional[str] = None
    file_size: int = 0                  # 文件总大小(字节)
    downloaded_size: int = 0            # 已下载大小(字节)
    download_speed: int = 0             # 下载速度(B/s)
    
    def __post_init__(self):
        """初始化后处理"""
        # 确保进度在0-100之间
        if self.progress < 0:
            object.__setattr__(self, 'progress', 0.0)
        elif self.progress > 100:
            object.__setattr__(self, 'progress', 100.0)
    
    @property
    def formatted_speed(self) -> str:
        """格式化速度显示"""
        if self.download_speed >= 1024 * 1024:
            return f"{self.download_speed / 1024 / 1024:.2f} MB/s"
        elif self.download_speed >= 1024:
            return f"{self.download_speed / 1024:.2f} KB/s"
        else:
            return f"{self.download_speed} B/s"
    
    @property
    def formatted_size(self) -> str:
        """格式化大小显示"""
        if self.file_size >= 1024 * 1024 * 1024:
            return f"{self.file_size / 1024 / 1024 / 1024:.2f} GB"
        elif self.file_size >= 1024 * 1024:
            return f"{self.file_size / 1024 / 1024:.2f} MB"
        else:
            return f"{self.file_size / 1024:.2f} KB"
    
    @property
    def status_text(self) -> str:
        """状态文字描述"""
        status_map = {
            TaskStatus.PENDING: "等待中",
            TaskStatus.PARSING: "解析中",
            TaskStatus.DOWNLOADING: "下载中",
            TaskStatus.MERGING: "合并中",
            TaskStatus.PAUSED: "已暂停",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: "失败",
            TaskStatus.CANCELLED: "已取消",
        }
        return status_map.get(self.status, "未知")
