"""B站API基础客户端"""
import asyncio
import json
import logging
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass

try:
    import aiohttp
    import aiofiles
except ImportError:
    raise ImportError("请先安装依赖: pip install aiohttp aiofiles")

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """API错误"""
    def __init__(self, message: str, code: int = 0, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class RiskControlError(ApiError):
    """被风控拦截"""
    pass


class AuthError(ApiError):
    """认证错误"""
    pass


@dataclass
class ApiResponse:
    """API响应"""
    code: int
    message: str
    data: Any
    
    @property
    def is_success(self) -> bool:
        return self.code == 0


class BaseApiClient:
    """
    B站API基础客户端
    
    功能：
    - 统一的HTTP请求封装
    - 自动重试机制
    - 请求频率限制（防风控）
    - Cookie管理
    """
    
    BASE_URL = "https://api.bilibili.com"
    PASSPORT_URL = "https://passport.bilibili.com"
    
    # 请求间隔（秒）- 防风控
    MIN_REQUEST_INTERVAL = 0.5
    
    def __init__(self, cookies: Optional[Dict[str, str]] = None):
        self.session: Optional[aiohttp.ClientSession] = None
        self.cookies = cookies or {}
        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._closed = True
        
    async def __aenter__(self):
        await self.open()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        
    async def open(self) -> None:
        """打开会话"""
        if self.session is None or self.session.closed:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.bilibili.com',
                'Origin': 'https://www.bilibili.com',
            }
            
            cookie_jar = aiohttp.CookieJar()
            for name, value in self.cookies.items():
                cookie_jar.update_cookies({name: value})
            
            self.session = aiohttp.ClientSession(
                headers=headers,
                cookie_jar=cookie_jar,
                timeout=aiohttp.ClientTimeout(total=30)
            )
            self._closed = False
            logger.info("API客户端会话已打开")
    
    async def close(self) -> None:
        """关闭会话"""
        if self.session and not self.session.closed:
            await self.session.close()
            self._closed = True
            logger.info("API客户端会话已关闭")
    
    async def _rate_limit(self) -> None:
        """请求频率限制"""
        async with self._request_lock:
            import time
            now = time.time()
            elapsed = now - self._last_request_time
            
            if elapsed < self.MIN_REQUEST_INTERVAL:
                wait_time = self.MIN_REQUEST_INTERVAL - elapsed
                logger.debug(f"请求频率限制，等待 {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
            
            self._last_request_time = time.time()
    
    async def _request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        retry: int = 3
    ) -> ApiResponse:
        """
        发送HTTP请求
        
        Args:
            method: HTTP方法
            url: 请求URL
            params: URL参数
            data: 请求体数据
            headers: 额外请求头
            retry: 重试次数
            
        Returns:
            API响应
        """
        if self._closed or self.session is None:
            raise ApiError("会话未打开，请先调用open()")
        
        # 频率限制
        await self._rate_limit()
        
        last_error = None
        
        for attempt in range(retry):
            try:
                async with self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=data,
                    headers=headers
                ) as response:
                    # 检查HTTP状态
                    if response.status == 412:
                        raise RiskControlError("请求被风控拦截，请稍后重试")
                    
                    if response.status == 401:
                        raise AuthError("登录已过期，请重新登录")
                    
                    response.raise_for_status()
                    
                    # 解析JSON
                    try:
                        result = await response.json()
                    except json.JSONDecodeError as e:
                        raise ApiError(f"JSON解析失败: {e}")
                    
                    # 检查B站API返回码
                    code = result.get('code', 0)
                    message = result.get('message', '')
                    data = result.get('data')
                    
                    if code == -412:
                        raise RiskControlError("请求被风控拦截，请稍后重试")
                    
                    if code == -101:
                        raise AuthError("未登录或登录已过期")
                    
                    if code != 0:
                        raise ApiError(f"API错误: {message}", code=code, data=data)
                    
                    return ApiResponse(code=code, message=message, data=data)
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    wait = 2 ** attempt  # 指数退避
                    await asyncio.sleep(wait)
                continue
        
        # 所有重试都失败了
        raise ApiError(f"请求失败，已重试{retry}次: {last_error}")
    
    async def get(self, url: str, params: Optional[Dict] = None, **kwargs) -> ApiResponse:
        """GET请求"""
        return await self._request('GET', url, params=params, **kwargs)
    
    async def post(self, url: str, data: Optional[Dict] = None, **kwargs) -> ApiResponse:
        """POST请求"""
        return await self._request('POST', url, data=data, **kwargs)
