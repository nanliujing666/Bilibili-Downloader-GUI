"""认证服务 - 处理登录和Cookie管理"""
import asyncio
import json
import logging
import os
from typing import Optional, Dict, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

try:
    from bilibili_api import login_v2, Credential
    import qrcode
except ImportError:
    raise ImportError("请先安装: pip install bilibili-api-python qrcode")

from ..models.user import UserInfo

logger = logging.getLogger(__name__)


@dataclass
class LoginStatus:
    """登录状态"""
    is_logged_in: bool
    user_info: Optional[UserInfo] = None
    error_message: Optional[str] = None


class AuthService:
    """认证服务"""

    COOKIE_FILE = "bilibili.session"

    def __init__(self, cookie_file: Optional[str] = None):
        self.cookie_file = cookie_file or self.COOKIE_FILE
        self.credential: Optional[Credential] = None
        self.user_info: Optional[UserInfo] = None
        self._login_callback: Optional[Callable[[LoginStatus], None]] = None
        self._qr_login: Optional[login_v2.QrCodeLogin] = None
    
    def set_login_callback(self, callback: Callable[[LoginStatus], None]) -> None:
        """设置登录状态变更回调"""
        self._login_callback = callback
    
    async def login_with_qrcode(
        self,
        on_qrcode: Optional[Callable[[str], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None
    ) -> LoginStatus:
        """扫码登录"""
        try:
            logger.info("开始扫码登录")
            
            qr = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
            await qr.generate_qrcode()
            
            qr_url = qr.get_qrcode_url()
            logger.info(f"获取到二维码URL")
            
            if on_qrcode:
                on_qrcode(qr_url)
            
            # 轮询登录状态
            while not qr.has_done():
                state = await qr.check_state()
                status_text = str(state)
                
                if on_status_change:
                    on_status_change(status_text)
                
                await asyncio.sleep(1)
            
            # 登录成功
            self.credential = qr.get_credential()
            self.user_info = await self._fetch_user_info()
            await self._save_cookies()
            
            status = LoginStatus(is_logged_in=True, user_info=self.user_info)
            
            if self._login_callback:
                self._login_callback(status)
            
            logger.info(f"登录成功")
            return status
            
        except Exception as e:
            logger.error(f"登录失败: {e}")
            status = LoginStatus(is_logged_in=False, error_message=str(e))
            if self._login_callback:
                self._login_callback(status)
            return status
    
    async def check_login_status(self) -> LoginStatus:
        """检查登录状态"""
        if self.credential is None:
            if not await self._load_cookies():
                return LoginStatus(is_logged_in=False)
        
        try:
            is_valid = await self.credential.check_valid()
            
            if is_valid:
                if self.user_info is None:
                    self.user_info = await self._fetch_user_info()
                
                return LoginStatus(is_logged_in=True, user_info=self.user_info)
            else:
                logger.info("Cookie已过期")
                self.credential = None
                self.user_info = None
                return LoginStatus(is_logged_in=False, error_message="登录已过期")
                
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return LoginStatus(is_logged_in=False, error_message=str(e))
    
    async def logout(self) -> None:
        """登出"""
        self.credential = None
        self.user_info = None
        
        if os.path.exists(self.cookie_file):
            os.remove(self.cookie_file)
            logger.info("已删除Cookie文件")
        
        if self._login_callback:
            self._login_callback(LoginStatus(is_logged_in=False))
    
    def get_credential(self) -> Optional[Credential]:
        """获取当前凭证"""
        return self.credential
    
    def get_cookies_dict(self) -> Dict[str, str]:
        """获取Cookie字典"""
        if self.credential:
            return self.credential.get_cookies()
        return {}
    
    async def _fetch_user_info(self) -> Optional[UserInfo]:
        """获取用户信息"""
        if self.credential is None:
            return None

        try:
            from bilibili_api import user

            # 使用 get_self_info 获取当前登录用户信息（不需要uid）
            info = await user.get_self_info(credential=self.credential)

            return UserInfo(
                mid=info.get('mid', 0),
                name=info.get('name', '未知用户'),
                avatar_url=info.get('face', ''),
                level=info.get('level_info', {}).get('current_level', 0),
                is_vip=info.get('vip', {}).get('status', 0) == 1,
                coins=info.get('coins', 0),
                sign=info.get('sign', ''),
                cookies=self.get_cookies_dict(),
                login_time=datetime.now()
            )

        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None
    
    async def _save_cookies(self) -> None:
        """保存Cookie到文件"""
        if self.credential is None:
            return
        
        try:
            cookies = self.credential.get_cookies()
            data = {
                'cookies': cookies,
                'saved_at': datetime.now().isoformat(),
                'user_info': asdict(self.user_info) if self.user_info else None
            }
            
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Cookie已保存")
            
        except Exception as e:
            logger.error(f"保存Cookie失败: {e}")
    
    async def _load_cookies(self) -> bool:
        """从文件加载Cookie"""
        if not os.path.exists(self.cookie_file):
            return False

        try:
            with open(self.cookie_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cookies = data.get('cookies', {})

            self.credential = Credential(
                sessdata=cookies.get('SESSDATA', ''),
                bili_jct=cookies.get('bili_jct', ''),
                buvid3=cookies.get('buvid3', '')
            )

            user_info_data = data.get('user_info')
            if user_info_data:
                self.user_info = UserInfo(**user_info_data)

            logger.info("Cookie已从文件加载")
            return True

        except Exception as e:
            logger.error(f"加载Cookie失败: {e}")
            return False

    def load_cookies(self) -> bool:
        """同步加载Cookie（供GUI调用）"""
        if not os.path.exists(self.cookie_file):
            return False

        try:
            with open(self.cookie_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            cookies = data.get('cookies', {})

            self.credential = Credential(
                sessdata=cookies.get('SESSDATA', ''),
                bili_jct=cookies.get('bili_jct', ''),
                buvid3=cookies.get('buvid3', '')
            )

            user_info_data = data.get('user_info')
            if user_info_data:
                self.user_info = UserInfo(**user_info_data)

            logger.info("Cookie已同步加载")
            return True

        except Exception as e:
            logger.error(f"同步加载Cookie失败: {e}")
            return False

    def clear_cookies(self) -> None:
        """清除Cookie（供GUI调用）"""
        self.credential = None
        self.user_info = None

        if os.path.exists(self.cookie_file):
            os.remove(self.cookie_file)
            logger.info("已删除Cookie文件")

    async def get_qr_code(self) -> bytes:
        """获取登录二维码图片（供GUI调用）"""
        self._qr_login = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)
        await self._qr_login.generate_qrcode()

        # 获取二维码图片 (Picture对象)
        picture = self._qr_login.get_qrcode_picture()
        # 返回图片字节数据
        return picture.content

    async def check_qr_status(self) -> tuple[bool, str]:
        """检查二维码登录状态（供GUI调用）

        Returns:
            (是否成功, 状态消息)
        """
        if self._qr_login is None:
            return False, "二维码未生成"

        if self._qr_login.has_done():
            # 登录成功
            self.credential = self._qr_login.get_credential()
            self.user_info = await self._fetch_user_info()
            await self._save_cookies()
            return True, "登录成功"

        try:
            state = await self._qr_login.check_state()
            state_str = str(state)

            # 解析状态
            if '扫码成功' in state_str or '已扫码' in state_str:
                return False, "已扫码，请在手机上确认"
            elif '过期' in state_str or '失效' in state_str:
                return False, "二维码已过期"
            else:
                return False, "等待扫码..."

        except Exception as e:
            logger.error(f"检查二维码状态失败: {e}")
            return False, f"检查失败: {e}"
