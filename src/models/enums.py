"""枚举定义"""
from enum import Enum, auto


class VideoType(Enum):
    """视频类型"""
    VIDEO = auto()      # 普通视频
    BANGUMI = auto()    # 番剧
    CHEESE = auto()     # 课程
    LIVE = auto()       # 直播
    MOVIE = auto()      # 电影


class TaskStatus(Enum):
    """下载任务状态"""
    PENDING = auto()      # 等待中
    PARSING = auto()      # 解析中
    DOWNLOADING = auto()  # 下载中
    MERGING = auto()      # 合并中
    PAUSED = auto()       # 已暂停
    COMPLETED = auto()    # 已完成
    FAILED = auto()       # 失败
    CANCELLED = auto()    # 已取消
