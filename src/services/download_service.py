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


import time

@dataclass
class DownloadProgress:
    """下载进度跟踪"""
    video_total: int = 0
    video_downloaded: int = 0
    audio_total: int = 0
    audio_downloaded: int = 0
    merging: bool = False
    merge_progress: float = 0.0
    last_update_time: float = field(default_factory=time.time)

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
    """下载服务 - 支持任务队列和可配置并发数"""

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

        # 任务队列和并发控制
        self._download_queue: asyncio.Queue[str] = asyncio.Queue()
        self._queue_workers: list[asyncio.Task] = []
        self._max_concurrent: int = 3  # 默认并发数
        self._queue_started: bool = False

    async def set_max_concurrent(self, max_concurrent: int) -> None:
        """设置最大并发下载数"""
        if max_concurrent < 1:
            max_concurrent = 1
        if max_concurrent > 10:
            max_concurrent = 10  # 限制最大10个并发

        old_value = self._max_concurrent
        self._max_concurrent = max_concurrent

        # 如果并发数增加，需要启动新的worker
        if max_concurrent > old_value and self._queue_started:
            for i in range(max_concurrent - old_value):
                worker = asyncio.create_task(self._queue_worker(f"worker-{old_value + i}"))
                self._queue_workers.append(worker)
                logger.info(f"新增下载工作线程: worker-{old_value + i}")

        # 如果并发数减少，需要取消多余的worker
        if max_concurrent < old_value and self._queue_started:
            # 取消多余的worker（从列表末尾开始）
            workers_to_cancel = self._queue_workers[max_concurrent:]
            self._queue_workers = self._queue_workers[:max_concurrent]

            for worker in workers_to_cancel:
                worker.cancel()

            # 等待被取消的worker完成
            if workers_to_cancel:
                await asyncio.gather(*workers_to_cancel, return_exceptions=True)
                logger.info(f"已取消 {len(workers_to_cancel)} 个下载工作线程")

        logger.info(f"最大并发下载数设置为: {max_concurrent}")

    def get_max_concurrent(self) -> int:
        """获取当前最大并发下载数"""
        return self._max_concurrent

    def start_queue(self) -> None:
        """启动下载队列（如果不已启动）"""
        if self._queue_started:
            return

        self._queue_started = True
        for i in range(self._max_concurrent):
            worker = asyncio.create_task(self._queue_worker(f"worker-{i}"))
            self._queue_workers.append(worker)
            logger.info(f"启动下载工作线程: worker-{i}")

    async def stop_queue(self) -> None:
        """停止下载队列"""
        if not self._queue_started:
            return

        self._queue_started = False

        # 取消所有worker
        for worker in self._queue_workers:
            worker.cancel()

        # 等待所有worker完成
        if self._queue_workers:
            await asyncio.gather(*self._queue_workers, return_exceptions=True)

        self._queue_workers.clear()
        logger.info("下载队列已停止")

    async def _queue_worker(self, worker_name: str) -> None:
        """队列工作线程 - 一次处理一个任务，完成后再取下一个"""
        logger.info(f"{worker_name} 开始运行")

        while self._queue_started:
            try:
                # 从队列获取任务（等待新任务）
                task_id = await asyncio.wait_for(
                    self._download_queue.get(),
                    timeout=1.0
                )

                logger.info(f"{worker_name} 开始处理任务: {task_id}")

                # 处理这个任务直到完成
                await self._process_task(task_id)

                # 标记任务完成
                self._download_queue.task_done()

                logger.info(f"{worker_name} 完成任务: {task_id}")

            except asyncio.TimeoutError:
                # 超时，继续循环检查是否还应该运行
                continue
            except asyncio.CancelledError:
                logger.info(f"{worker_name} 被取消")
                break
            except Exception as e:
                logger.error(f"{worker_name} 处理任务时出错: {e}")

        logger.info(f"{worker_name} 停止运行")

    async def _process_task(self, task_id: str) -> None:
        """处理单个下载任务"""
        state = self.state_manager.get_state()

        task = None
        for t in state.download_tasks:
            if t.task_id == task_id:
                task = t
                break

        if task is None:
            logger.error(f"任务不存在: {task_id}")
            return

        # 检查任务状态 - PAUSED状态的任务不应该被处理（已被用户暂停）
        if task.status not in [TaskStatus.PENDING, TaskStatus.FAILED]:
            logger.info(f"任务 {task_id} 状态为 {task.status}，跳过")
            return

        # 检查是否已被暂停（_active_downloads被设为False）
        if not self._active_downloads.get(task_id, True):
            logger.info(f"任务 {task_id} 已被暂停，跳过处理")
            return

        # 检查FFmpeg
        if not check_ffmpeg():
            error_msg = "FFmpeg未安装，请先安装FFmpeg"
            logger.error(error_msg)
            self.state_manager.update(
                lambda s: s.update_task(task_id, status=TaskStatus.FAILED, error_message=error_msg)
            )
            self.event_bus.publish('download.error', {'task_id': task_id, 'error': error_msg})
            return

        # 延迟解析：如果视频信息为空，先解析
        if task.video is None:
            logger.info(f"任务 {task_id} 需要解析视频信息")
            self.state_manager.update(
                lambda s: s.update_task(task_id, status=TaskStatus.PARSING)
            )
            try:
                video_info = await self.parse_video(task.url)
                # 设置下载路径
                from ..config.settings import get_settings
                settings = get_settings()
                safe_title = sanitize_filename(video_info.title)
                output_path = os.path.join(settings.download_path, f"{safe_title}.mp4")
                # 检查文件是否已存在
                counter = 1
                original_output_path = output_path
                while os.path.exists(output_path):
                    base, ext = os.path.splitext(original_output_path)
                    output_path = f"{base}_{counter}{ext}"
                    counter += 1
                # 更新任务信息
                self.state_manager.update(
                    lambda s: s.update_task(
                        task_id,
                        video=video_info,
                        download_path=output_path
                    )
                )
                # 重新获取更新后的任务
                state = self.state_manager.get_state()
                for t in state.download_tasks:
                    if t.task_id == task_id:
                        task = t
                        break
            except Exception as e:
                logger.error(f"解析视频失败 {task_id}: {e}")
                self.state_manager.update(
                    lambda s: s.update_task(task_id, status=TaskStatus.FAILED, error_message=str(e))
                )
                self.event_bus.publish('download.error', {'task_id': task_id, 'error': str(e)})
                return

        # 开始下载
        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.DOWNLOADING)
        )

        self._active_downloads[task_id] = True
        self._progress[task_id] = DownloadProgress()

        try:
            await self._do_download(task)
        except Exception as e:
            # 检查任务是否被暂停（暂停后状态为PAUSED，不应该显示失败）
            current_state = self.state_manager.get_state()
            current_task = None
            for t in current_state.download_tasks:
                if t.task_id == task_id:
                    current_task = t
                    break

            if current_task and current_task.status == TaskStatus.PAUSED:
                logger.info(f"任务 {task_id} 已暂停，不显示失败")
            else:
                logger.error(f"下载失败 {task_id}: {e}")
                self.state_manager.update(
                    lambda s: s.update_task(task_id, status=TaskStatus.FAILED, error_message=str(e))
                )
                self.event_bus.publish('download.error', {'task_id': task_id, 'error': str(e)})
        finally:
            self._active_downloads.pop(task_id, None)
            self._progress.pop(task_id, None)
            await self._cleanup_temp_files(task_id)

    async def parse_video(self, url: str):
        """解析视频URL"""
        logger.info(f"解析视频: {url}")
        return await self.video_api.get_video_info(url)

    async def create_download_task(self, url: str, quality: int = 80,
                                   source: str = "url",
                                   source_name: Optional[str] = None,
                                   source_id: Optional[str] = None) -> str:
        """创建下载任务（轻量级，不解析视频信息）

        视频解析延迟到开始下载时进行，避免创建任务时卡顿
        """
        logger.info(f"[创建任务] URL: {url}, source: {source}")

        # 从URL提取BV号作为任务ID
        bvid = self._extract_bvid(url)
        task_id = bvid if bvid else str(uuid.uuid4())[:8]

        # 创建轻量级任务，不解析视频信息
        task = DownloadTask(
            task_id=task_id,
            video=None,  # 延迟加载
            status=TaskStatus.PENDING,
            progress=0.0,
            download_path="",  # 延迟设置
            quality=quality,
            source=source,
            source_name=source_name,
            source_id=source_id,
            url=url,  # 保存URL用于后续解析
        )

        self.state_manager.update(lambda s: s.with_task(task))
        self.event_bus.publish('download.created', {'task_id': task_id})

        logger.info(f"创建下载任务: {task_id}, 来源: {source}")
        return task_id

    def _extract_bvid(self, url: str) -> Optional[str]:
        """从URL中提取BV号"""
        import re
        # 匹配BV号 (如 BV1xx411c7mD)
        match = re.search(r'BV[a-zA-Z0-9]{10}', url)
        if match:
            return match.group(0)
        return None

    async def start_download(self, task_id: str) -> None:
        """开始下载 - 将任务添加到队列"""
        # 确保队列已启动
        self.start_queue()

        # 检查任务是否存在
        state = self.state_manager.get_state()
        task = None
        for t in state.download_tasks:
            if t.task_id == task_id:
                task = t
                break

        if task is None:
            logger.error(f"任务不存在: {task_id}")
            return

        # 检查任务状态
        if task.status not in [TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.FAILED]:
            logger.info(f"任务 {task_id} 已经在下载中或已完成，跳过")
            return

        # 重置_active_downloads标志，确保任务可以正常开始
        self._active_downloads[task_id] = True

        # 将任务添加到队列
        await self._download_queue.put(task_id)
        logger.info(f"任务 {task_id} 已加入下载队列，当前队列长度: {self._download_queue.qsize()}")

        # 更新状态为等待中
        self.state_manager.update(
            lambda s: s.update_task(task_id, status=TaskStatus.PENDING)
        )

    async def _do_download(self, task: DownloadTask) -> None:
        """执行完整下载流程（带重试机制）"""
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

        # 创建临时目录
        temp_dir = os.path.join(os.path.dirname(task.download_path), ".temp")
        ensure_dir(temp_dir)

        video_temp = os.path.join(temp_dir, f"{task_id}_video.m4s")
        audio_temp = os.path.join(temp_dir, f"{task_id}_audio.m4s")
        self._temp_files[task_id] = (video_temp, audio_temp)

        # 下载视频流（带重试，用户无感知）
        logger.info(f"[{task_id}] 开始下载视频流...")
        video_success = await self._download_stream_with_retry(
            task_id, task.video.bvid, cid, quality, video_temp, 'video'
        )
        if not video_success:
            if not self._active_downloads.get(task_id, True):
                raise RuntimeError("下载已取消")
            raise RuntimeError("网络连接失败，请检查网络后重新开始下载")

        # 下载音频流（带重试，用户无感知）
        logger.info(f"[{task_id}] 开始下载音频流...")
        audio_success = await self._download_stream_with_retry(
            task_id, task.video.bvid, cid, quality, audio_temp, 'audio'
        )
        if not audio_success:
            if not self._active_downloads.get(task_id, True):
                raise RuntimeError("下载已取消")
            raise RuntimeError("网络连接失败，请检查网络后重新开始下载")

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

    async def _download_stream_with_retry(
        self,
        task_id: str,
        bvid: str,
        cid: int,
        quality: int,
        output_path: str,
        stream_type: str,
        max_retries: int = 5
    ) -> bool:
        """下载单个流（带重试和链接刷新，用户无感知）

        Args:
            task_id: 任务ID
            bvid: 视频BV号
            cid: 视频CID
            quality: 视频质量
            output_path: 输出路径
            stream_type: 'video' 或 'audio'
            max_retries: 最大重试次数（默认5次）

        Returns:
            是否成功完成下载
        """
        for attempt in range(max_retries):
            # 检查是否被取消
            if not self._active_downloads.get(task_id, True):
                logger.info(f"[{task_id}] 下载已取消，停止重试")
                return False

            # 获取（或重新获取）下载链接
            try:
                download_info = await self.video_api.get_download_url(bvid, cid, quality)
                url = download_info['video_url'] if stream_type == 'video' else download_info['audio_url']
                if attempt > 0:
                    logger.info(f"[{task_id}] 重新获取{stream_type}下载链接 (第{attempt + 1}次尝试)")
            except Exception as e:
                logger.warning(f"[{task_id}] 获取下载链接失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    wait_time = min(2 ** attempt, 10)  # 指数退避，最大10秒
                    await asyncio.sleep(wait_time)
                    continue
                return False

            # 尝试下载
            success = await self._download_stream(task_id, url, output_path, stream_type)
            if success:
                if attempt > 0:
                    logger.info(f"[{task_id}] {stream_type}下载在第{attempt + 1}次尝试后成功")
                return True

            # 下载失败，检查是否被取消
            if not self._active_downloads.get(task_id, True):
                logger.info(f"[{task_id}] 下载已取消，停止重试")
                return False

            # 下载失败，等待后重试（用户无感知，只记录日志）
            if attempt < max_retries - 1:
                wait_time = min(2 ** attempt, 10)  # 指数退避：1s, 2s, 4s, 8s, 10s
                logger.info(f"[{task_id}] {stream_type}下载遇到问题，{wait_time}秒后自动重试...")
                await asyncio.sleep(wait_time)

        logger.error(f"[{task_id}] {stream_type}下载失败，已重试{max_retries}次，请检查网络连接")
        return False

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

        # 优化 aiohttp 配置
        timeout = aiohttp.ClientTimeout(
            total=None,  # 总超时无限制（大文件）
            connect=30,  # 连接超时30秒
            sock_read=60  # 读取超时60秒
        )

        # TCP连接配置优化
        connector = aiohttp.TCPConnector(
            limit=10,  # 连接池大小
            limit_per_host=5,  # 每个主机最大连接数
            enable_cleanup_closed=True,
            force_close=False,
        )

        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            ) as session:
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
                    chunk_size = 65536  # 64KB chunks for better throughput
                    last_progress_update = 0
                    progress_update_interval = 524288  # 每512KB更新一次进度

                    async with aiofiles.open(output_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(chunk_size):
                            # 检查是否被取消
                            if not self._active_downloads.get(task_id, True):
                                logger.info(f"[{task_id}] 下载中断")
                                return False

                            await f.write(chunk)
                            downloaded += len(chunk)

                            # 更新进度（限制频率，避免UI卡顿）
                            if downloaded - last_progress_update >= progress_update_interval:
                                if stream_type == 'video':
                                    self._progress[task_id].video_downloaded = downloaded
                                else:
                                    self._progress[task_id].audio_downloaded = downloaded
                                self._update_task_progress(task_id)
                                last_progress_update = downloaded

                        # 最后更新一次确保进度完整
                        if stream_type == 'video':
                            self._progress[task_id].video_downloaded = downloaded
                        else:
                            self._progress[task_id].audio_downloaded = downloaded
                        self._update_task_progress(task_id)

        except asyncio.TimeoutError:
            logger.error(f"[{task_id}] 下载超时")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"[{task_id}] 网络错误: {e}")
            return False
        except Exception as e:
            logger.error(f"[{task_id}] 下载流失败: {e}")
            return False

        return True

    def _update_task_progress(self, task_id: str) -> None:
        """更新任务进度到状态管理器（带频率限制）"""
        if task_id not in self._progress:
            return

        progress = self._progress[task_id].total_progress

        # 限制更新频率，每200ms最多更新一次
        current_time = time.time()
        if current_time - self._progress[task_id].last_update_time < 0.2:
            return

        self._progress[task_id].last_update_time = current_time
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
        """暂停下载（只有下载中或等待中的任务才能被暂停）"""
        # 检查任务状态，只有PENDING或DOWNLOADING状态才能暂停
        state = self.state_manager.get_state()
        task = None
        for t in state.download_tasks:
            if t.task_id == task_id:
                task = t
                break

        if task is None:
            logger.warning(f"暂停失败，任务不存在: {task_id}")
            return

        # 已完成的任务不能被暂停
        if task.status == TaskStatus.COMPLETED:
            logger.info(f"任务 {task_id} 已完成，跳过暂停")
            return

        # 只有特定状态的任务才能被暂停
        if task.status not in [TaskStatus.PENDING, TaskStatus.DOWNLOADING]:
            logger.info(f"任务 {task_id} 状态为 {task.status}，不能被暂停")
            return

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
            # 重置_active_downloads标志，确保任务可以被正常处理
            self._active_downloads[task_id] = True
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
