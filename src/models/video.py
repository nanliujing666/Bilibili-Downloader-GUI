"""视频相关模型"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional
from .enums import VideoType


@dataclass
class VideoPage:
    """视频分P信息"""
    cid: int                    # 内容ID
    page: int                   # 分P序号
    title: str                  # 分P标题
    duration: int               # 时长(秒)
    part: str = ""              # 分P标识


@dataclass 
class VideoQuality:
    """视频清晰度"""
    qn: int                     # 清晰度编号
    description: str            # 描述(如"1080P 高清")
    codecs: List[str] = field(default_factory=list)  # 支持的编码格式


@dataclass
class VideoInfo:
    """视频信息模型 - 不可变"""
    bvid: str                           # BV号
    cid: int                            # 内容ID
    aid: int                            # AV号
    title: str                          # 标题
    description: str                    # 简介
    duration: int                       # 时长(秒)
    owner: Dict[str, Any]               # UP主信息 {mid, name, face}
    pages: List[VideoPage]              # 分P列表
    video_type: VideoType               # 视频类型
    cover_url: str                      # 封面URL
    pub_date: datetime                  # 发布时间
    stat: Dict[str, int]                # 统计数据
    is_charge: bool = False             # 是否付费
    qualities: List[VideoQuality] = field(default_factory=list)  # 可用清晰度
    
    def __post_init__(self):
        """初始化后处理"""
        # 确保pages是列表
        if self.pages is None:
            object.__setattr__(self, 'pages', [])
    
    @property
    def owner_name(self) -> str:
        """获取UP主名称"""
        return self.owner.get('name', '未知')
    
    @property
    def view_count(self) -> int:
        """获取播放量"""
        return self.stat.get('view', 0)
    
    @property
    def formatted_duration(self) -> str:
        """格式化时长"""
        hours = self.duration // 3600
        minutes = (self.duration % 3600) // 60
        seconds = self.duration % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
