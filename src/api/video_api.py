"""视频API客户端"""
import logging
from typing import Optional, List, Dict, Any

try:
    from bilibili_api import video, Credential, bangumi, cheese
except ImportError:
    raise ImportError("请先安装: pip install bilibili-api-python")

from .base_client import ApiError
from ..models.video import VideoInfo, VideoPage, VideoQuality, VideoType
from ..parsers.url_parser import URLParser, ParseType

logger = logging.getLogger(__name__)


class VideoApiClient:
    """视频API客户端"""

    def __init__(self, credential: Optional[Credential] = None, auth_service=None):
        self._credential = credential
        self._auth_service = auth_service
        self.url_parser = URLParser()

    @property
    def credential(self) -> Optional[Credential]:
        """动态获取最新的credential"""
        if self._auth_service:
            return self._auth_service.get_credential()
        return self._credential
    
    async def get_video_info(self, url_or_id: str) -> VideoInfo:
        """获取视频信息"""
        parse_result = self.url_parser.parse(url_or_id)
        
        if not parse_result.is_valid:
            raise ApiError(f"无法解析URL: {url_or_id}")
        
        if parse_result.parse_type == ParseType.VIDEO:
            return await self._get_video_info(parse_result.id)
        elif parse_result.parse_type == ParseType.BANGUMI:
            return await self._get_bangumi_info(parse_result.id)
        elif parse_result.parse_type == ParseType.CHEESE:
            return await self._get_cheese_info(parse_result.id)
        else:
            raise ApiError(f"不支持的类型: {parse_result.parse_type}")
    
    async def _get_video_info(self, bvid: str) -> VideoInfo:
        """获取普通视频信息"""
        v = video.Video(bvid=bvid, credential=self.credential)
        info = await v.get_info()
        
        pages_data = info.get('pages', [])
        pages = [
            VideoPage(
                cid=p['cid'],
                page=p['page'],
                title=p.get('part', f"P{p['page']}"),
                duration=p['duration'],
                part=p.get('part', '')
            )
            for p in pages_data
        ]
        
        owner = info.get('owner', {})
        stat = info.get('stat', {})
        
        return VideoInfo(
            bvid=info.get('bvid', bvid),
            cid=pages[0].cid if pages else 0,
            aid=info.get('aid', 0),
            title=info.get('title', '未知标题'),
            description=info.get('desc', ''),
            duration=info.get('duration', 0),
            owner={
                'mid': owner.get('mid', 0),
                'name': owner.get('name', '未知UP'),
                'face': owner.get('face', '')
            },
            pages=pages,
            video_type=VideoType.VIDEO,
            cover_url=info.get('pic', ''),
            pub_date=None,
            stat={'view': stat.get('view', 0), 'like': stat.get('like', 0)},
            is_charge=False,
            qualities=[]
        )
    
    async def _get_bangumi_info(self, season_id: str) -> VideoInfo:
        """获取番剧信息"""
        bg = bangumi.Bangumi(season_id=int(season_id), credential=self.credential)
        info = await bg.get_info()
        
        episodes = await bg.get_episodes()
        pages = [
            VideoPage(
                cid=ep.get_cid(),
                page=i + 1,
                title=ep.get_title(),
                duration=0
            )
            for i, ep in enumerate(episodes)
        ]
        
        return VideoInfo(
            bvid=f"ss{season_id}",
            cid=pages[0].cid if pages else 0,
            aid=0,
            title=info.get('title', '未知番剧'),
            description=info.get('evaluate', ''),
            duration=0,
            owner={'mid': 0, 'name': '哔哩哔哩番剧', 'face': ''},
            pages=pages,
            video_type=VideoType.BANGUMI,
            cover_url=info.get('cover', ''),
            pub_date=None,
            stat={},
            is_charge=False,
            qualities=[]
        )
    
    async def _get_cheese_info(self, season_id: str) -> VideoInfo:
        """获取课程信息"""
        cs = cheese.CheeseList(season_id=int(season_id), credential=self.credential)
        info = await cs.get_meta()
        
        episodes = await cs.get_list()
        pages = [
            VideoPage(
                cid=ep.get_epid(),
                page=i + 1,
                title=f"第{i+1}节",
                duration=0
            )
            for i, ep in enumerate(episodes)
        ]
        
        return VideoInfo(
            bvid=f"cheese{season_id}",
            cid=pages[0].cid if pages else 0,
            aid=0,
            title=info.get('title', '未知课程'),
            description=info.get('summary', ''),
            duration=0,
            owner={'mid': 0, 'name': '哔哩哔哩课堂', 'face': ''},
            pages=pages,
            video_type=VideoType.CHEESE,
            cover_url=info.get('cover', ''),
            pub_date=None,
            stat={},
            is_charge=True,
            qualities=[]
        )
    
    async def get_download_url(self, bvid: str, cid: int, quality: int = 80):
        """获取视频下载链接

        Args:
            bvid: 视频BV号
            cid: 视频分P的cid
            quality: 视频质量qn编号 (默认80=1080P)
        """
        logger.info(f"[视频API] 开始获取下载链接: bvid={bvid}, cid={cid}, 请求质量={quality}")

        # 课程视频特殊处理
        if bvid.startswith("cheese"):
            # 提取season_id
            season_id = bvid.replace("cheese", "")
            if season_id.startswith("_"):
                season_id = season_id.split("_")[1] if "_" in season_id else season_id[1:]
            cs = cheese.CheeseList(season_id=int(season_id), credential=self.credential)
            # 获取课程列表，找到对应cid的episode
            episodes = await cs.get_list()
            target_ep = None
            for ep in episodes:
                if ep.get_epid() == cid:
                    target_ep = ep
                    break
            if target_ep is None:
                raise ApiError(f"课程中未找到cid={cid}的章节")
            # 获取下载URL
            download_url_data = await target_ep.get_download_url()
        else:
            v = video.Video(bvid=bvid, credential=self.credential)
            download_url_data = await v.get_download_url(cid=cid)

        # 记录原始返回数据中的质量信息
        if 'dash' in download_url_data and download_url_data['dash']:
            dash = download_url_data['dash']
            if 'video' in dash and len(dash['video']) > 0:
                available_ids = [s.get('id') for s in dash['video']]
                logger.info(f"[视频API] API返回的可用质量ID: {available_ids}")

        from bilibili_api import video as video_module
        detector = video_module.VideoDownloadURLDataDetecter(data=download_url_data)

        # 将qn编号转换为VideoQuality枚举
        video_quality = self._qn_to_video_quality(quality)
        logger.info(f"[视频API] 请求质量 {quality} 转换为 VideoQuality: {video_quality}")

        # 根据指定的质量选择流
        streams = detector.detect_best_streams(video_max_quality=video_quality)

        # 记录返回的流信息
        if len(streams) > 0:
            video_stream = streams[0]
            stream_quality = getattr(video_stream, 'quality', 'unknown')
            stream_codec = getattr(video_stream, 'codecs', 'unknown')
            logger.info(f"[视频API] 选择的视频流: quality={stream_quality}, codec={stream_codec}")

        video_url = streams[0].url if len(streams) > 0 else ""
        audio_url = streams[1].url if len(streams) > 1 else ""

        logger.info(f"[视频API] 返回下载链接: quality={quality}, 视频URL长度={len(video_url)}, 音频URL长度={len(audio_url)}")

        return {
            'video_url': video_url,
            'audio_url': audio_url,
            'quality': quality
        }

    async def get_available_qualities(self, bvid: str, cid: int) -> List[dict]:
        """获取视频可用的清晰度列表

        Returns:
            质量列表，每个包含 qn(质量编号) 和 desc(描述)
        """
        try:
            logger.info(f"[视频API] 获取可用质量: bvid={bvid}, cid={cid}")

            # 课程视频特殊处理
            if bvid.startswith("cheese"):
                season_id = bvid.replace("cheese", "")
                if season_id.startswith("_"):
                    season_id = season_id.split("_")[1] if "_" in season_id else season_id[1:]
                cs = cheese.CheeseList(season_id=int(season_id), credential=self.credential)
                episodes = await cs.get_list()
                target_ep = None
                for ep in episodes:
                    if ep.get_epid() == cid:
                        target_ep = ep
                        break
                if target_ep is None:
                    raise ApiError(f"课程中未找到cid={cid}的章节")
                download_url_data = await target_ep.get_download_url()
                logger.info(f"[视频API] 获取课程下载URL数据成功")
            else:
                v = video.Video(bvid=bvid, credential=self.credential)
                # 尝试获取播放器信息，包含可用质量
                info = await v.get_info()
                logger.info(f"[视频API] 获取视频信息成功: title={info.get('title', 'unknown')}")

                # 从 dash 数据中获取质量信息
                download_url_data = await v.get_download_url(cid=cid)
                logger.info(f"[视频API] 获取下载URL数据成功")

            qualities = []
            if 'dash' in download_url_data and download_url_data['dash']:
                dash = download_url_data['dash']
                if 'video' in dash:
                    # 获取所有可用视频流的质量
                    seen_qn = set()
                    for stream in dash['video']:
                        qn = stream.get('id')
                        if qn and qn not in seen_qn:
                            seen_qn.add(qn)
                            # 获取质量描述
                            desc = self._get_quality_desc(qn)
                            qualities.append({'qn': qn, 'desc': desc})
                    logger.info(f"[视频API] 从dash数据解析到 {len(qualities)} 个质量: {qualities}")
            else:
                logger.warning(f"[视频API] dash数据不存在或为空: {list(download_url_data.keys())}")

            # 如果没有获取到，返回默认质量
            if not qualities:
                qualities = [
                    {'qn': 127, 'desc': '8K'},
                    {'qn': 126, 'desc': '杜比视界'},
                    {'qn': 125, 'desc': 'HDR'},
                    {'qn': 120, 'desc': '4K'},
                    {'qn': 116, 'desc': '1080P60'},
                    {'qn': 112, 'desc': '1080P+'},
                    {'qn': 80, 'desc': '1080P'},
                    {'qn': 64, 'desc': '720P'},
                    {'qn': 32, 'desc': '480P'},
                    {'qn': 16, 'desc': '360P'},
                ]

            # 按质量从高到低排序
            qualities.sort(key=lambda x: x['qn'], reverse=True)
            return qualities

        except Exception as e:
            logger.error(f"获取可用质量失败: {e}")
            # 返回默认质量列表
            return [
                {'qn': 80, 'desc': '1080P'},
                {'qn': 64, 'desc': '720P'},
                {'qn': 32, 'desc': '480P'},
                {'qn': 16, 'desc': '360P'},
            ]

    def _get_quality_desc(self, qn: int) -> str:
        """根据质量编号获取描述"""
        quality_map = {
            127: '8K',
            126: '杜比视界',
            125: 'HDR',
            120: '4K',
            116: '1080P60',
            112: '1080P+',
            80: '1080P',
            74: '720P60',
            64: '720P',
            32: '480P',
            16: '360P',
        }
        return quality_map.get(qn, f'{qn}')

    def _qn_to_video_quality(self, qn: int):
        """将qn编号转换为VideoQuality枚举"""
        from bilibili_api import video as video_module
        quality_map = {
            127: video_module.VideoQuality._8K,
            126: video_module.VideoQuality.DOLBY,
            125: video_module.VideoQuality.HDR,
            120: video_module.VideoQuality._4K,
            116: video_module.VideoQuality._1080P_60,
            112: video_module.VideoQuality._1080P_PLUS,
            80: video_module.VideoQuality._1080P,
            74: video_module.VideoQuality._720P,  # 720P60不存在，用720P
            64: video_module.VideoQuality._720P,
            32: video_module.VideoQuality._480P,
            16: video_module.VideoQuality._360P,
        }
        return quality_map.get(qn, video_module.VideoQuality._1080P)
