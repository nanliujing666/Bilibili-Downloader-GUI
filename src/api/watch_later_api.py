"""稍后再看API客户端"""
import logging
from typing import List, Optional
from datetime import datetime

try:
    from bilibili_api import user, Credential
except ImportError:
    raise ImportError("请先安装: pip install bilibili-api-python")

from .base_client import ApiError
from ..models.video import VideoInfo, VideoPage, VideoType

logger = logging.getLogger(__name__)


class WatchLaterApiClient:
    """稍后再看API客户端"""

    def __init__(self, credential: Optional[Credential] = None):
        self.credential = credential

    async def get_watch_later_videos(self) -> List[VideoInfo]:
        """
        获取稍后再看列表中的视频

        Returns:
            视频列表
        """
        try:
            if not self.credential:
                raise ApiError("获取稍后再看需要登录")

            # 获取稍后再看列表
            result = await user.get_toview_list(credential=self.credential)

            videos = []
            for item in result.get('list', []):
                # 提取视频信息
                bv_id = item.get('bvid', '')
                if not bv_id:
                    continue

                # 解析UP主信息
                owner_info = item.get('owner', {})
                owner = {
                    'mid': owner_info.get('mid', 0),
                    'name': owner_info.get('name', '未知UP'),
                    'face': owner_info.get('face', '')
                }

                # 解析时长（秒转分钟）
                duration = item.get('duration', 0)

                # 解析统计信息
                stat_info = item.get('stat', {})
                stat = {
                    'view': stat_info.get('view', 0),
                    'like': stat_info.get('like', 0),
                    'coin': stat_info.get('coin', 0),
                    'favorite': stat_info.get('favorite', 0)
                }

                # 解析分页信息
                pages_data = item.get('pages', [])
                pages = []
                if pages_data:
                    for i, page in enumerate(pages_data):
                        pages.append(VideoPage(
                            cid=page.get('cid', 0),
                            page=i + 1,
                            title=page.get('part', f'P{i+1}'),
                            duration=page.get('duration', 0)
                        ))
                else:
                    # 如果没有pages信息，创建一个默认页面
                    pages = [VideoPage(cid=0, page=1, title='P1', duration=duration)]

                # 解析发布时间
                pub_date = None
                pubdate_ts = item.get('pubdate', 0)
                if pubdate_ts:
                    try:
                        pub_date = datetime.fromtimestamp(pubdate_ts)
                    except:
                        pass

                videos.append(VideoInfo(
                    bvid=bv_id,
                    cid=item.get('cid', 0),
                    aid=item.get('aid', 0),
                    title=item.get('title', '未知标题'),
                    description=item.get('desc', ''),
                    duration=duration,
                    owner=owner,
                    pages=pages,
                    video_type=VideoType.VIDEO,
                    cover_url=item.get('pic', ''),
                    pub_date=pub_date,
                    stat=stat,
                    is_charge=False,
                    qualities=[]
                ))

            return videos

        except Exception as e:
            logger.error(f"获取稍后再看列表失败: {e}")
            raise ApiError(f"获取稍后再看列表失败: {e}")

    async def clear_watch_later(self) -> bool:
        """
        清空稍后再看列表

        Returns:
            是否成功
        """
        try:
            if not self.credential:
                raise ApiError("清空稍后再看需要登录")

            await user.clear_toview_list(credential=self.credential)
            return True

        except Exception as e:
            logger.error(f"清空稍后再看失败: {e}")
            raise ApiError(f"清空稍后再看失败: {e}")

    async def delete_viewed_videos(self) -> bool:
        """
        删除稍后再看中已观看的视频

        Returns:
            是否成功
        """
        try:
            if not self.credential:
                raise ApiError("操作稍后再看需要登录")

            await user.delete_viewed_videos_from_toview(credential=self.credential)
            return True

        except Exception as e:
            logger.error(f"删除已看视频失败: {e}")
            raise ApiError(f"删除已看视频失败: {e}")
