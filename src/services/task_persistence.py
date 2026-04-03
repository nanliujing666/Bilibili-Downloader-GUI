"""下载任务持久化服务"""
import json
import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from ..models.download import DownloadTask, TaskStatus
from ..models.video import VideoInfo, VideoPage, VideoType

logger = logging.getLogger(__name__)


class TaskPersistenceService:
    """下载任务持久化服务"""

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
        self._file_path = self._get_default_path()

    def _get_default_path(self) -> str:
        """获取默认任务文件路径（项目内）"""
        # 获取项目根目录（src/services 的上上级目录）
        project_root = Path(__file__).parent.parent.parent
        config_dir = project_root / 'config'
        config_dir.mkdir(exist_ok=True)
        return str(config_dir / 'download_tasks.json')

    def _task_to_dict(self, task: DownloadTask) -> dict:
        """将任务转换为字典"""
        # 处理延迟解析的任务（video为None）
        if task.video is None:
            video_data = None
        else:
            video_data = {
                'bvid': task.video.bvid,
                'cid': task.video.cid,
                'aid': task.video.aid,
                'title': task.video.title,
                'description': task.video.description,
                'duration': task.video.duration,
                'owner': task.video.owner,
                'pages': [
                    {
                        'cid': p.cid,
                        'page': p.page,
                        'title': p.title,
                        'duration': p.duration,
                        'part': p.part,
                    }
                    for p in task.video.pages
                ],
                'video_type': task.video.video_type.value if isinstance(task.video.video_type, VideoType) else task.video.video_type,
                'cover_url': task.video.cover_url,
            }

        return {
            'task_id': task.task_id,
            'video': video_data,
            'status': task.status.value if isinstance(task.status, TaskStatus) else task.status,
            'progress': task.progress,
            'download_path': task.download_path,
            'quality': task.quality,
            'video_codec': task.video_codec,
            'audio_quality': task.audio_quality,
            'download_video': task.download_video,
            'download_audio': task.download_audio,
            'download_danmaku': task.download_danmaku,
            'download_subtitle': task.download_subtitle,
            'download_cover': task.download_cover,
            'source': task.source,
            'source_name': task.source_name,
            'source_id': task.source_id,
            'url': getattr(task, 'url', ''),  # 保存URL用于延迟解析
            'created_at': task.created_at.isoformat() if task.created_at else None,
            'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            'error_message': task.error_message,
            'file_size': task.file_size,
            'downloaded_size': task.downloaded_size,
            'download_speed': task.download_speed,
        }

    def _dict_to_task(self, data: dict) -> Optional[DownloadTask]:
        """从字典恢复任务"""
        try:
            video_data = data.get('video')

            # 恢复视频信息（支持延迟解析的任务）
            if video_data is None:
                video = None
            else:
                # 恢复视频页面信息
                pages = [
                    VideoPage(
                        cid=p['cid'],
                        page=p['page'],
                        title=p['title'],
                        duration=p.get('duration', 0),
                        part=p.get('part', ''),
                    )
                    for p in video_data.get('pages', [])
                ]

                video = VideoInfo(
                    bvid=video_data.get('bvid', ''),
                    cid=video_data.get('cid', 0),
                    aid=video_data.get('aid', 0),
                    title=video_data.get('title', '未知标题'),
                    description=video_data.get('description', ''),
                    duration=video_data.get('duration', 0),
                    owner=video_data.get('owner', {}),
                    pages=pages,
                    video_type=VideoType(video_data.get('video_type', 1)),
                    cover_url=video_data.get('cover_url', ''),
                    pub_date=None,
                    stat={},
                    is_charge=False,
                    qualities=[],
                )

            # 解析状态
            status_value = data.get('status', 'pending')
            try:
                status = TaskStatus(status_value)
            except ValueError:
                # 兼容旧格式（数字）
                status_map = {
                    0: TaskStatus.PENDING,
                    1: TaskStatus.PARSING,
                    2: TaskStatus.DOWNLOADING,
                    3: TaskStatus.MERGING,
                    4: TaskStatus.PAUSED,
                    5: TaskStatus.COMPLETED,
                    6: TaskStatus.FAILED,
                    7: TaskStatus.CANCELLED,
                }
                status = status_map.get(status_value, TaskStatus.PENDING)

            # 解析时间
            created_at = datetime.now()
            if data.get('created_at'):
                try:
                    created_at = datetime.fromisoformat(data['created_at'])
                except:
                    pass

            completed_at = None
            if data.get('completed_at'):
                try:
                    completed_at = datetime.fromisoformat(data['completed_at'])
                except:
                    pass

            return DownloadTask(
                task_id=data['task_id'],
                video=video,
                status=status,
                progress=data.get('progress', 0.0),
                download_path=data.get('download_path', ''),
                quality=data.get('quality', 80),
                video_codec=data.get('video_codec', ''),
                audio_quality=data.get('audio_quality', 0),
                download_video=data.get('download_video', True),
                download_audio=data.get('download_audio', True),
                download_danmaku=data.get('download_danmaku', False),
                download_subtitle=data.get('download_subtitle', False),
                download_cover=data.get('download_cover', False),
                source=data.get('source', 'url'),
                url=data.get('url', ''),
                source_name=data.get('source_name'),
                source_id=data.get('source_id'),
                created_at=created_at,
                completed_at=completed_at,
                error_message=data.get('error_message'),
                file_size=data.get('file_size', 0),
                downloaded_size=data.get('downloaded_size', 0),
                download_speed=data.get('download_speed', 0),
            )

        except Exception as e:
            logger.error(f"恢复任务失败: {e}")
            return None

    def save_tasks(self, tasks: List[DownloadTask]) -> None:
        """保存任务列表到文件"""
        try:
            data = {
                'tasks': [self._task_to_dict(task) for task in tasks],
                'saved_at': datetime.now().isoformat(),
            }

            with open(self._file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 移除频繁日志，只在初始化时记录
        except Exception as e:
            logger.error(f"保存任务失败: {e}")

    def load_tasks(self) -> List[DownloadTask]:
        """从文件加载任务列表"""
        if not os.path.exists(self._file_path):
            return []

        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            tasks = []
            for task_data in data.get('tasks', []):
                task = self._dict_to_task(task_data)
                if task:
                    tasks.append(task)

            logger.info(f"已加载 {len(tasks)} 个任务")
            return tasks
        except Exception as e:
            logger.error(f"加载任务失败: {e}")
            return []

    def check_file_exists(self, task: DownloadTask) -> bool:
        """检查任务对应的文件是否存在"""
        if not task.download_path:
            return False
        return os.path.exists(task.download_path)

    def clear_all(self) -> None:
        """清空所有任务"""
        try:
            if os.path.exists(self._file_path):
                os.remove(self._file_path)
            logger.info("已清空任务文件")
        except Exception as e:
            logger.error(f"清空任务失败: {e}")


def get_task_persistence() -> TaskPersistenceService:
    """获取任务持久化服务单例"""
    return TaskPersistenceService()
