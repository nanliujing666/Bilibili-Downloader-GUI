"""下载历史记录模型"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List
from enum import Enum
import json
import os


class DownloadSource(Enum):
    """下载来源类型"""
    URL = "url"                    # 直接URL下载
    FAVORITE = "favorite"          # 收藏夹
    WATCH_LATER = "watch_later"    # 稍后再看
    CHEESE = "cheese"              # 课程


@dataclass
class DownloadHistoryItem:
    """下载历史记录项"""
    # 视频信息
    bvid: str                       # BV号
    title: str                      # 视频标题
    owner_name: str                 # UP主名称
    duration: int                   # 视频时长(秒)

    # 下载信息
    download_path: str              # 下载文件路径
    quality: int                    # 下载质量
    file_size: int                  # 文件大小(字节)

    # 来源信息
    source: str                     # 来源类型 (url/favorite/watch_later/cheese)
    source_name: Optional[str] = None  # 来源名称（如收藏夹名称、课程名称）
    source_id: Optional[str] = None    # 来源ID

    # 时间信息
    downloaded_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'bvid': self.bvid,
            'title': self.title,
            'owner_name': self.owner_name,
            'duration': self.duration,
            'download_path': self.download_path,
            'quality': self.quality,
            'file_size': self.file_size,
            'source': self.source,
            'source_name': self.source_name,
            'source_id': self.source_id,
            'downloaded_at': self.downloaded_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DownloadHistoryItem':
        """从字典创建"""
        downloaded_at = datetime.now()
        if 'downloaded_at' in data:
            try:
                downloaded_at = datetime.fromisoformat(data['downloaded_at'])
            except:
                pass

        return cls(
            bvid=data.get('bvid', ''),
            title=data.get('title', ''),
            owner_name=data.get('owner_name', ''),
            duration=data.get('duration', 0),
            download_path=data.get('download_path', ''),
            quality=data.get('quality', 80),
            file_size=data.get('file_size', 0),
            source=data.get('source', 'url'),
            source_name=data.get('source_name'),
            source_id=data.get('source_id'),
            downloaded_at=downloaded_at,
        )


class DownloadHistory:
    """下载历史记录管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._items: List[DownloadHistoryItem] = []
        self._file_path = self._get_default_path()
        self._load()

    def _get_default_path(self) -> str:
        """获取默认历史文件路径"""
        from pathlib import Path
        home_dir = Path.home()
        config_dir = home_dir / '.bilibili_downloader'
        config_dir.mkdir(exist_ok=True)
        return str(config_dir / 'download_history.json')

    def _load(self):
        """从文件加载历史记录"""
        if not os.path.exists(self._file_path):
            return

        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self._items = [
                DownloadHistoryItem.from_dict(item)
                for item in data.get('items', [])
            ]
        except Exception as e:
            print(f"加载下载历史失败: {e}")

    def _save(self):
        """保存历史记录到文件"""
        try:
            data = {
                'items': [item.to_dict() for item in self._items],
                'updated_at': datetime.now().isoformat(),
            }

            with open(self._file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存下载历史失败: {e}")

    def add(self, item: DownloadHistoryItem):
        """添加历史记录"""
        # 检查是否已存在相同BV号的记录，如果存在则更新
        for i, existing in enumerate(self._items):
            if existing.bvid == item.bvid:
                self._items[i] = item
                self._save()
                return

        # 添加到开头
        self._items.insert(0, item)

        # 限制历史记录数量（最多1000条）
        if len(self._items) > 1000:
            self._items = self._items[:1000]

        self._save()

    def add_course_record(self, course_title: str, episodes_count: int,
                         download_dir: str, quality: int):
        """添加课程下载记录（集合记录）"""
        item = DownloadHistoryItem(
            bvid=f"course_{course_title}",  # 使用特殊前缀标识课程
            title=f"课程: {course_title}",
            owner_name="哔哩哔哩课堂",
            duration=0,
            download_path=download_dir,
            quality=quality,
            file_size=0,
            source='cheese',
            source_name=course_title,
        )
        self.add(item)

    def get_all(self) -> List[DownloadHistoryItem]:
        """获取所有历史记录"""
        return self._items.copy()

    def get_by_bvid(self, bvid: str) -> Optional[DownloadHistoryItem]:
        """根据BV号获取历史记录"""
        for item in self._items:
            if item.bvid == bvid:
                return item
        return None

    def exists(self, bvid: str) -> bool:
        """检查是否已下载过"""
        return any(item.bvid == bvid for item in self._items)

    def clear(self):
        """清空历史记录"""
        self._items.clear()
        self._save()


def get_download_history() -> DownloadHistory:
    """获取下载历史记录实例（单例）"""
    return DownloadHistory()
