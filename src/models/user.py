"""用户相关模型"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional


@dataclass
class UserInfo:
    """用户信息模型"""
    mid: int                    # 用户ID
    name: str                   # 用户名
    avatar_url: str             # 头像URL
    level: int = 0              # 等级
    is_vip: bool = False        # 是否大会员
    coins: int = 0              # 硬币数
    sign: str = ""              # 签名
    
    # 登录相关
    cookies: Optional[Dict[str, str]] = None
    login_time: Optional[datetime] = None
    
    @property
    def is_logged_in(self) -> bool:
        """是否已登录"""
        return self.cookies is not None and len(self.cookies) > 0
