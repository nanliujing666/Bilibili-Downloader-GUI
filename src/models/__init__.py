"""数据模型定义"""

from .video import VideoInfo, VideoPage, VideoQuality
from .user import UserInfo
from .download import DownloadTask, TaskStatus
from .enums import VideoType

__all__ = [
    'VideoInfo',
    'VideoPage', 
    'VideoQuality',
    'UserInfo',
    'DownloadTask',
    'TaskStatus',
    'VideoType',
]
