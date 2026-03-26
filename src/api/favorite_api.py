"""收藏夹API客户端"""
import asyncio
import logging
from typing import List, Optional, Dict, Any

try:
    from bilibili_api import favorite_list, Credential
except ImportError:
    raise ImportError("请先安装: pip install bilibili-api-python")

from .base_client import ApiError
from ..models.video import VideoInfo, VideoPage, VideoType

logger = logging.getLogger(__name__)


class FavoriteInfo:
    """收藏夹信息"""
    def __init__(self, fid: int, title: str, media_count: int):
        self.fid = fid
        self.title = title
        self.media_count = media_count


class FavoriteApiClient:
    """收藏夹API客户端"""

    def __init__(self, credential: Optional[Credential] = None, auth_service=None):
        self._credential = credential
        self._auth_service = auth_service

    @property
    def credential(self) -> Optional[Credential]:
        """动态获取最新的credential"""
        if self._auth_service:
            return self._auth_service.get_credential()
        return self._credential
    
    async def get_user_folders(self, uid: Optional[int] = None) -> List[FavoriteInfo]:
        """
        获取用户的收藏夹列表

        Args:
            uid: 用户ID，None则获取当前登录用户的

        Returns:
            收藏夹列表
        """
        try:
            if uid is None and self.credential:
                # 获取当前用户的UID
                from bilibili_api import user
                info = await user.get_self_info(credential=self.credential)
                uid = info.get('mid')

            if uid is None:
                raise ApiError("未提供用户ID且未登录")
            
            # 获取收藏夹列表
            folders = await favorite_list.get_video_favorite_list(uid=uid, credential=self.credential)
            
            result = []
            for folder in folders.get('list', []):
                result.append(FavoriteInfo(
                    fid=folder['id'],
                    title=folder['title'],
                    media_count=folder['media_count']
                ))
            
            return result
            
        except Exception as e:
            logger.error(f"获取收藏夹列表失败: {e}")
            raise ApiError(f"获取收藏夹列表失败: {e}")
    
    async def get_favorite_videos(self, fid: int, page: int = 1, page_size: int = 20) -> List[VideoInfo]:
        """
        获取收藏夹中的视频
        
        Args:
            fid: 收藏夹ID
            page: 页码
            page_size: 每页数量
            
        Returns:
            视频列表
        """
        try:
            # 添加延迟避免触发B站风控(412错误)
            await asyncio.sleep(0.5)
            result = await favorite_list.get_video_favorite_list_content(
                media_id=int(fid),
                page=page,
                credential=self.credential
            )
            
            videos = []
            medias = result.get('medias') or []  # 处理 None 情况
            for item in medias:
                # 从收藏夹项中提取视频信息
                bv_id = item.get('bv_id', '')
                if not bv_id:
                    continue
                
                videos.append(VideoInfo(
                    bvid=bv_id,
                    cid=0,  # 需要后续获取
                    aid=item.get('id', 0),
                    title=item.get('title', '未知标题'),
                    description=item.get('intro', ''),
                    duration=item.get('duration', 0),
                    owner={
                        'mid': item.get('upper', {}).get('mid', 0),
                        'name': item.get('upper', {}).get('name', '未知UP'),
                        'face': ''
                    },
                    pages=[VideoPage(cid=0, page=1, title='P1', duration=item.get('duration', 0))],
                    video_type=VideoType.VIDEO,
                    cover_url=item.get('cover', ''),
                    pub_date=None,
                    stat={
                        'view': item.get('cnt_info', {}).get('play', 0),
                        'like': 0,
                        'coin': 0,
                        'favorite': 0
                    },
                    is_charge=False,
                    qualities=[]
                ))
            
            return videos
            
        except Exception as e:
            logger.error(f"获取收藏夹视频失败 fid={fid}: {e}")
            raise ApiError(f"获取收藏夹视频失败: {e}")
    
    async def get_all_favorite_videos(self, fid: int) -> List[VideoInfo]:
        """
        获取收藏夹中的所有视频（自动分页）
        
        Args:
            fid: 收藏夹ID
            
        Returns:
            所有视频列表
        """
        all_videos = []
        page = 1
        
        while True:
            videos = await self.get_favorite_videos(fid, page=page, page_size=20)
            if not videos:
                break
            
            all_videos.extend(videos)
            page += 1
            
            # 安全限制
            if page > 100:
                logger.warning("收藏夹视频数量过多，已限制")
                break
        
        return all_videos
