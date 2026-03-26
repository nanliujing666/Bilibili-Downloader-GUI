"""URL解析器 - 识别和解析各种B站URL"""
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum, auto


class ParseType(Enum):
    """解析结果类型"""
    VIDEO = auto()          # 普通视频
    BANGUMI = auto()        # 番剧
    CHEESE = auto()         # 课程
    FAVORITE = auto()       # 收藏夹
    SPACE = auto()          # 用户空间
    LIVE = auto()           # 直播
    UNKNOWN = auto()        # 未知


@dataclass
class ParseResult:
    """URL解析结果"""
    parse_type: ParseType
    id: str                 # 各种ID (BV号、season_id等)
    page: Optional[int] = None      # 分P号
    original_url: str = ""  # 原始URL
    extra: Dict[str, Any] = None    # 额外信息
    
    def __post_init__(self):
        if self.extra is None:
            self.extra = {}
    
    @property
    def is_valid(self) -> bool:
        """是否是有效的解析结果"""
        return self.parse_type != ParseType.UNKNOWN and bool(self.id)


class URLParser:
    """
    B站URL解析器
    
    支持的URL格式：
    - 视频: https://www.bilibili.com/video/BVxxxx / BVxxxx / avxxxx
    - 番剧: https://www.bilibili.com/bangumi/play/ssxxxx / epxxxx
    - 课程: https://www.bilibili.com/cheese/play/ssxxxx
    - 收藏夹: https://space.bilibili.com/xxxx/favlist?fid=xxxx
    - 用户空间: https://space.bilibili.com/xxxx
    - 直播: https://live.bilibili.com/xxxx
    """
    
    # URL匹配模式
    PATTERNS = {
        'BV': [
            r'(?:https?://)?(?:www\.)?bilibili\.com/video/(BV[\w]+)',
            r'(?:https?://)?b23\.tv/(BV[\w]+)',
            r'^(BV[\w]+)$',
        ],
        'AV': [
            r'(?:https?://)?(?:www\.)?bilibili\.com/video/av(\d+)',
            r'^av(\d+)$',
        ],
        'BANGUMI_SS': [
            r'(?:https?://)?(?:www\.)?bilibili\.com/bangumi/play/ss(\d+)',
            r'^ss(\d+)$',
        ],
        'BANGUMI_EP': [
            r'(?:https?://)?(?:www\.)?bilibili\.com/bangumi/play/ep(\d+)',
            r'^ep(\d+)$',
        ],
        'CHEESE': [
            r'(?:https?://)?(?:www\.)?bilibili\.com/cheese/play/ss(\d+)',
            r'^cheese://ss(\d+)$',
        ],
        'FAVORITE': [
            r'(?:https?://)?space\.bilibili\.com/\d+/favlist\?fid=(\d+)',
        ],
        'SPACE': [
            r'(?:https?://)?space\.bilibili\.com/(\d+)',
        ],
        'LIVE': [
            r'(?:https?://)?live\.bilibili\.com/(\d+)',
        ],
    }
    
    @classmethod
    def parse(cls, url: str) -> ParseResult:
        """
        解析URL
        
        Args:
            url: 要解析的URL或ID字符串
            
        Returns:
            解析结果
        """
        if not url:
            return ParseResult(ParseType.UNKNOWN, "")
        
        url = url.strip()
        
        # 尝试匹配各种模式
        # 1. BV号
        for pattern in cls.PATTERNS['BV']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.VIDEO,
                    match.group(1),
                    original_url=url
                )
        
        # 2. AV号
        for pattern in cls.PATTERNS['AV']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.VIDEO,
                    match.group(1),
                    original_url=url,
                    extra={'type': 'av'}
                )
        
        # 3. 番剧SS
        for pattern in cls.PATTERNS['BANGUMI_SS']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.BANGUMI,
                    match.group(1),
                    original_url=url,
                    extra={'type': 'ss'}
                )
        
        # 4. 番剧EP
        for pattern in cls.PATTERNS['BANGUMI_EP']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.BANGUMI,
                    match.group(1),
                    original_url=url,
                    extra={'type': 'ep'}
                )
        
        # 5. 课程
        for pattern in cls.PATTERNS['CHEESE']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.CHEESE,
                    match.group(1),
                    original_url=url
                )
        
        # 6. 收藏夹
        for pattern in cls.PATTERNS['FAVORITE']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.FAVORITE,
                    match.group(1),
                    original_url=url
                )
        
        # 7. 用户空间
        for pattern in cls.PATTERNS['SPACE']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.SPACE,
                    match.group(1),
                    original_url=url
                )
        
        # 8. 直播
        for pattern in cls.PATTERNS['LIVE']:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return ParseResult(
                    ParseType.LIVE,
                    match.group(1),
                    original_url=url
                )
        
        # 无法识别
        return ParseResult(ParseType.UNKNOWN, "", original_url=url)
    
    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        """
        检查URL是否有效（可解析）
        
        Args:
            url: URL字符串
            
        Returns:
            是否有效
        """
        result = cls.parse(url)
        return result.is_valid
    
    @classmethod
    def extract_bvid(cls, url: str) -> Optional[str]:
        """
        从URL提取BV号
        
        Args:
            url: URL字符串
            
        Returns:
            BV号或None
        """
        result = cls.parse(url)
        if result.parse_type == ParseType.VIDEO:
            return result.id
        return None
