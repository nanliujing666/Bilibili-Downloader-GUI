"""下载服务"""
import asyncio
import logging
import os
import uuid
from typing import Optional, Callable, List
from dataclasses import dataclass, field

try:
    import aiohttp
    import aiofiles
except ImportError:
    raise ImportError("请先安装: pip install aiohttp aiofiles")

from ..api.video_api import VideoApiClient
from ..api.favorite_api import FavoriteApiClient
from ..api.auth_service import AuthService
from ..models.download import DownloadTask, TaskStatus
from ..models.download_history import DownloadHistoryItem, get_download_history
from ..utils.path_utils import sanitize_filename, ensure_dir
from ..utils.ffmpeg_utils import merge_video_audio, check_ffmpeg
from ..core.state_manager import get_state_manager
from ..core.event_bus import get_event_bus

logger = logging.getLogger(__name__)


@dataclass
class DownloadProgress:
    """下载进度跟踪"""
    video_total: int = 0
    video_downloaded: int = 0
    audio_total: int = 0
    audio_downloaded: int = 0
    merging: bool = False
    merge_progress: float = 0.0

    @property
    def total_progress(self) -> float:
        """计算总进度

        视频下载占70%，音频下载占20%，合并占10%
        """
        video_progress = (self.video_downloaded / self.video_total * 70.0) if self.video_total > 0 else 0
        audio_progress = (self.audio_downloaded / self.audio_total * 20.0) if self.audio_total > 0 else 0
        merge_progress = self.merge_progress * 10.0 if self.merging else 0

        return min(100.0, video_progress + audio_progress + merge_progress)


class DownloadService:
    """下载服务"""

    def __init__(self, auth_service: Optional[AuthService] = None):
        self.auth_service = auth_service
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()

        # 传递auth_service而不是credential，确保能动态获取最新credential
        credential = auth_service.get_credential() if auth_service else None
        self.video_api = VideoApiClient(credential, auth_service=auth_service)
        self.favorite_api = FavoriteApiClient(credential, auth_service=auth_service)

        # 活动下载任务控制标志 {task_id: should_continue}
        self._active_downloads: dict[str, bool] = {}

        # 临时文件跟踪 {task_id: (video_temp, audio_temp)}
        self._temp_files: dict[str, tuple[str, str]] = {}

        # 下载进度跟踪
        self._progress: dict[str, DownloadProgress] = {}

    async def parse_video(self, url: str):
        """解析视频URL"""
        logger.info(f"解析视频: {url}")
        return await self.video_api.get_video_info(url)

    async def create_download_task(self, url: str, quality: int = 80,
                                   source: str = "url",
                                   source_name: Optional[str] = None,
                                   source_id: Optional[str] = None) -> str:
        """创建下载任务"""
        logger.info(f"[创建任务] URL: {url}, 请求质量: {quality}, source: {source}")
        video_info = await self.parse_video(url)
        task_id = str(uuid.uuid4())[:8]

        from ..config.settings import get_settings
        settings = get_settings()
        download_path = settings.download_path

        safe_title = sanitize_filename(video_info.title)
        output_path = os.path.join(download_path, f"{safe_title}.mp4")

        # 检查文件是否已存在，避免覆盖
        counter = 1
        original_output_path = output_path
        while os.path.exists(output_path):
            base, ext = os.path.splitext(original_output_path)
            output_path = f"{base}_{counter}{ext}"
            counter += 1

        task = DownloadTask(
            task_id=task_id,
            video=video_info,
            status=TaskStatus.PENDING,
            progress=0.0,
            download_path=output_path,
            quality=quality,
            source=source,
            source_name=source_name,
            source_id=source_id,
        )

        self.state_manager.update(lambda s: s.with_task(task))
        self.event_bus.publish('download.created', {'task_id': task_id})

        logger.info(f"创建下载任务: {task_id}, 来源: {source}")
        return task_id

    async def start_download(self, task_id: str) -> None:
        """开始下载"""
        state = self.state_manager.get_state()

        task = None
        for t in state.download_tasks:
            if t.task_id == task_id:
                task = t
                break

        if task is None:
            logger.error(f"任务不存在: {task_id}")
            return

        # 检查FFmpeg是否可用
        if not check_ffmpeg():
            error_msg = "FFmpeg未安装，请先安装FFmpeg"
            logger.error(error_msg)
            self.state_manager.update(
                lambda s: s.update_task(task_id, status=TaskStatus.FAILED, error_message=error_msg)
            )
            self.event_bus.publish('download.error', {'task_id': task_id, 'error': error_msg})
            return

        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.DOWNLOADING)
        )

        self._active_downloads[task_id] = True
        self._progress[task_id] = DownloadProgress()

        try:
            await self._do_download(task)
        except Exception as e:
            logger.error(f"下载失败 {task_id}: {e}")
            self.state_manager.update(
                lambda s: s.update_task(task_id, status=TaskStatus.FAILED, error_message=str(e))
            )
            self.event_bus.publish('download.error', {'task_id': task_id, 'error': str(e)})
        finally:
            self._active_downloads.pop(task_id, None)
            self._progress.pop(task_id, None)
            # 清理临时文件
            await self._cleanup_temp_files(task_id)

    async def _do_download(self, task: DownloadTask) -> None:
        """执行完整下载流程"""
        task_id = task.task_id
        logger.info(f"开始下载任务 {task_id}: {task.video.title}")

        # 检查是否启用自动质量选择
        from ..config.settings import get_settings
        settings = get_settings()
        quality = task.quality

        # 使用第一个分P的cid
        cid = task.video.cid if task.video.cid else (
            task.video.pages[0].cid if task.video.pages else 0
        )

        logger.info(f"[{task_id}] 任务质量设置: {quality}, 自动质量: {settings.auto_quality}, CID: {cid}")

        if settings.auto_quality:
            try:
                # 获取可用质量列表
                logger.info(f"[{task_id}] 正在获取可用质量列表...")
                available_qualities = await self.video_api.get_available_qualities(
                    task.video.bvid, cid
                )
                logger.info(f"[{task_id}] 获取到 {len(available_qualities)} 个可用质量: {available_qualities}")
                if available_qualities:
                    # 选择最高质量
                    best_quality = available_qualities[0]['qn']
                    quality = best_quality
                    logger.info(f"[{task_id}] 自动选择最佳质量: {available_qualities[0]['desc']} ({quality})")
                else:
                    logger.warning(f"[{task_id}] 可用质量列表为空，使用任务设置的质量: {quality}")
            except Exception as e:
                logger.warning(f"[{task_id}] 获取可用质量失败，使用任务设置的质量 {quality}: {e}")
        else:
            logger.info(f"[{task_id}] 使用任务设置的质量: {quality}")

        # 1. 获取下载URL
        try:
            download_info = await self.video_api.get_download_url(
                task.video.bvid, cid, quality
            )
            video_url = download_info['video_url']
            audio_url = download_info['audio_url']
            logger.info(f"获取到下载链接 - 视频: {video_url[:50]}..., 音频: {audio_url[:50]}...")
        except Exception as e:
            raise RuntimeError(f"获取下载链接失败: {e}")

        # 创建临时目录
        temp_dir = os.path.join(os.path.dirname(task.download_path), ".temp")
        ensure_dir(temp_dir)

        video_temp = os.path.join(temp_dir, f"{task_id}_video.m4s")
        audio_temp = os.path.join(temp_dir, f"{task_id}_audio.m4s")
        self._temp_files[task_id] = (video_temp, audio_temp)

        # 2. 下载视频流 (占70%进度)
        logger.info(f"[{task_id}] 开始下载视频流...")
        video_success = await self._download_stream(
            task_id, video_url, video_temp, 'video'
        )
        if not video_success:
            raise RuntimeError("视频流下载失败或被取消")

        # 3. 下载音频流 (占20%进度)
        logger.info(f"[{task_id}] 开始下载音频流...")
        audio_success = await self._download_stream(
            task_id, audio_url, audio_temp, 'audio'
        )
        if not audio_success:
            raise RuntimeError("音频流下载失败或被取消")

        # 4. 合并音视频 (占10%进度)
        logger.info(f"[{task_id}] 开始合并音视频...")
        self._progress[task_id].merging = True
        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.MERGING)
        )

        def merge_progress(p: float):
            """合并进度回调"""
            if task_id in self._progress:
                self._progress[task_id].merge_progress = p / 100.0
                self._update_task_progress(task_id)

        merge_success = await asyncio.to_thread(
            merge_video_audio,
            video_temp,
            audio_temp,
            task.download_path,
            progress_callback=merge_progress
        )

        if not merge_success:
            raise RuntimeError("FFmpeg合并失败")

        # 下载完成
        self.state_manager.update(
            lambda s: s.update_task(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                progress=100.0
            )
        )

        # 保存下载历史记录
        self._save_download_history(task)

        self.event_bus.publish('download.completed', {
            'task_id': task.task_id,
            'output_path': task.download_path
        })

        logger.info(f"下载完成 {task_id}: {task.download_path}")

    def _save_download_history(self, task: DownloadTask):
        """保存下载历史记录"""
        try:
            history = get_download_history()

            # 课程视频不单独记录，只记录课程集合
            if task.source == "cheese":
                logger.info(f"课程视频不单独记录历史: {task.video.title}")
                return

            # 获取UP主名称
            owner_name = "未知UP"
            if isinstance(task.video.owner, dict):
                owner_name = task.video.owner.get('name', '未知UP')

            # 获取文件大小
            file_size = 0
            if os.path.exists(task.download_path):
                file_size = os.path.getsize(task.download_path)

            # 创建历史记录项
            item = DownloadHistoryItem(
                bvid=task.video.bvid,
                title=task.video.title,
                owner_name=owner_name,
                duration=task.video.duration,
                download_path=task.download_path,
                quality=task.quality,
                file_size=file_size,
                source=task.source,
                source_name=task.source_name,
                source_id=task.source_id,
            )

            history.add(item)
            logger.info(f"已保存下载历史: {task.video.title}")

        except Exception as e:
            logger.error(f"保存下载历史失败: {e}")

    async def _download_stream(
        self,
        task_id: str,
        url: str,
        output_path: str,
        stream_type: str
    ) -> bool:
        """下载单个流（视频或音频）

        Args:
            task_id: 任务ID
            url: 下载URL
            output_path: 输出路径
            stream_type: 'video' 或 'audio'

        Returns:
            是否成功完成下载
        """
        # 检查是否已取消
        if not self._active_downloads.get(task_id, True):
            logger.info(f"[{task_id}] 下载已取消")
            return False

        # 获取referer（B站需要）
        headers = {
            'Referer': 'https://www.bilibili.com',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"下载失败，HTTP状态码: {response.status}")
                        return False

                    # 获取文件大小
                    total_size = int(response.headers.get('content-length', 0))
                    if total_size > 0:
                        if stream_type == 'video':
                            self._progress[task_id].video_total = total_size
                        else:
                            self._progress[task_id].audio_total = total_size

                    # 下载文件
                    downloaded = 0
                    chunk_size = 8192

                    async with aiofiles.open(output_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            # 检查是否被取消
                            if not self._active_downloads.get(task_id, True):
                                logger.info(f"[{task_id}] 下载中断")
                                return False

                            await f.write(chunk)
                            downloaded += len(chunk)

                            # 更新进度
                            if stream_type == 'video':
                                self._progress[task_id].video_downloaded = downloaded
                            else:
                                self._progress[task_id].audio_downloaded = downloaded

                            self._update_task_progress(task_id)

        except Exception as e:
            logger.error(f"[{task_id}] 下载流失败: {e}")
            return False

        return True

    def _update_task_progress(self, task_id: str) -> None:
        """更新任务进度到状态管理器"""
        if task_id not in self._progress:
            return

        progress = self._progress[task_id].total_progress
        self.state_manager.update(
            lambda s: s.update_task(task_id, progress=progress)
        )

    async def _cleanup_temp_files(self, task_id: str) -> None:
        """清理临时文件"""
        if task_id not in self._temp_files:
            return

        video_temp, audio_temp = self._temp_files[task_id]

        for temp_file in [video_temp, audio_temp]:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                    logger.debug(f"删除临时文件: {temp_file}")
                except Exception as e:
                    logger.warning(f"删除临时文件失败: {temp_file}, {e}")

        del self._temp_files[task_id]

    def pause_download(self, task_id: str) -> None:
        """暂停下载（实际上标记为取消，下次可以重新开始）"""
        self._active_downloads[task_id] = False
        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.PAUSED)
        )
        logger.info(f"暂停下载: {task_id}")

    def cancel_download(self, task_id: str) -> None:
        """取消下载"""
        self._active_downloads[task_id] = False
        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.CANCELLED)
        )
        logger.info(f"取消下载: {task_id}")

    def resume_download(self, task_id: str) -> None:
        """恢复下载（重新启动下载流程）"""
        state = self.state_manager.get_state()
        task = None
        for t in state.download_tasks:
            if t.task_id == task_id:
                task = t
                break

        if task and task.status in [TaskStatus.PAUSED, TaskStatus.FAILED]:
            # 重置状态并重新开始
            self.state_manager.update(
                lambda s: s.update_task(
                    task_id,
                    status=TaskStatus.PENDING,
                    progress=0.0,
                    error_message=None
                )
            )
            # 异步启动下载
            asyncio.create_task(self.start_download(task_id))
            logger.info(f"恢复下载: {task_id}")

    async def download_favorite(self, favorite_id: str, favorite_name: str,
                                videos: List, quality: int = 80) -> List[str]:
        """下载收藏夹中的视频

        Args:
            favorite_id: 收藏夹ID
            favorite_name: 收藏夹名称
            videos: 视频列表
            quality: 视频质量

        Returns:
            创建的任务ID列表
        """
        task_ids = []
        for video in videos:
            try:
                # 构建视频URL
                if hasattr(video, 'bvid') and video.bvid:
                    url = f"https://www.bilibili.com/video/{video.bvid}"
                elif hasattr(video, 'aid') and video.aid:
                    url = f"https://www.bilibili.com/video/av{video.aid}"
                else:
                    logger.warning(f"视频缺少bvid/aid，跳过: {video}")
                    continue

                task_id = await self.create_download_task(
                    url, quality,
                    source="favorite",
                    source_name=favorite_name,
                    source_id=str(favorite_id)
                )
                task_ids.append(task_id)

            except Exception as e:
                logger.error(f"创建收藏夹视频任务失败: {e}")
                continue

        logger.info(f"收藏夹 {favorite_name} 创建了 {len(task_ids)} 个下载任务")
        return task_ids

    async def batch_download(self, urls: List[str], quality: int = 80) -> List[str]:
        """批量下载多个视频URL

        Args:
            urls: 视频URL列表
            quality: 视频质量

        Returns:
            创建的任务ID列表
        """
        task_ids = []
        for url in urls:
            try:
                task_id = await self.create_download_task(url, quality)
                task_ids.append(task_id)
            except Exception as e:
                logger.error(f"创建批量任务失败 {url}: {e}")

        return task_ids
