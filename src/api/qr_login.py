"""B站网页扫码登录（适配 crossDomain + Set-Cookie 新协议）

背景：
    B 站已调整网页扫码登录协议。扫码确认后，poll 接口返回的 ``url`` 不再是
    直接携带 SESSDATA 等 Cookie 的回调地址，而是一个 crossDomain 票据地址
    （形如 ``https://passport.biligame.com/x/passport-login/web/crossDomain
    ?ticket=...&gourl=...``）。真正的 Cookie 只会在请求该地址时通过
    302 响应的 ``Set-Cookie`` 下发，且 ticket 只能使用一次。

    旧实现（bilibili-api 17.4.x 的 login_v2）仍然从 url 的 query 里
    解析 SESSDATA，因此拿到的 Credential 是空的。

本模块直接用 aiohttp 实现完整流程，不依赖库内部的私有字段。
"""
from __future__ import annotations

import io
import logging
from http.cookies import SimpleCookie
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urljoin, urlsplit

try:
    import aiohttp
    import qrcode
    from bilibili_api import Credential
except ImportError:
    raise ImportError("请先安装: pip install aiohttp qrcode bilibili-api-python")

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

#: 登录相关请求统一携带的请求头（缺少 Referer 时可能拿不到 Set-Cookie）
COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}

GENERATE_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL_API = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

#: 二维码状态（poll 接口 data.code）
STATE_SUCCESS = 0
STATE_WAITING = 86101      # 未扫码
STATE_SCANNED = 86090      # 已扫码未确认
STATE_EXPIRED = 86038      # 二维码已过期

#: 需要从 Set-Cookie / 回调地址中收集的 Cookie 名（不区分大小写）
_WANTED_COOKIES = {
    "sessdata",
    "bili_jct",
    "dedeuserid",
    "buvid3",
    "buvid4",
    "ac_time_value",
}

_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=10)
_MAX_REDIRECTS = 6

# 登录流程内部状态
FLOW_WAITING = "waiting"
FLOW_SCANNED = "scanned"
FLOW_EXPIRED = "expired"
FLOW_DONE = "done"
FLOW_ERROR = "error"


class LoginFlowError(Exception):
    """扫码登录流程异常"""


def _extract_cookie_values(raw_headers) -> Dict[str, str]:
    """从原始响应头中提取 Set-Cookie 的键值对"""
    values: Dict[str, str] = {}
    for name, value in raw_headers:
        if name.decode("latin-1").lower() != "set-cookie":
            continue
        line = value.decode("latin-1")
        jar = SimpleCookie()
        try:
            jar.load(line)
        except Exception:
            continue
        for key, morsel in jar.items():
            if key.lower() in _WANTED_COOKIES:
                values[key] = morsel.value
    return values


def _pick(cookies: Dict[str, str], name: str) -> str:
    """按名称取值，忽略大小写（Set-Cookie 与 URL query 大小写不统一）"""
    lowered = name.lower()
    for key, value in cookies.items():
        if key.lower() == lowered and value:
            return value
    return ""


class QrCodeLoginWeb:
    """网页扫码登录流程"""

    def __init__(self) -> None:
        self._qrcode_key: Optional[str] = None
        self._qrcode_url: str = ""
        self._credential: Optional[Credential] = None
        self._finished: bool = False

    # ------------------------------------------------------------------ 对外状态
    @property
    def qrcode_url(self) -> str:
        return self._qrcode_url

    @property
    def has_done(self) -> bool:
        return self._credential is not None

    def get_credential(self) -> Credential:
        if self._credential is None:
            raise LoginFlowError("尚未完成登录")
        return self._credential

    # ------------------------------------------------------------------ 流程步骤
    async def generate_qrcode(self) -> bytes:
        """生成登录二维码，返回 PNG 图片字节"""
        self._qrcode_key = None
        self._qrcode_url = ""
        self._credential = None
        self._finished = False

        async with aiohttp.ClientSession(headers=COMMON_HEADERS, timeout=_TIMEOUT) as session:
            async with session.get(GENERATE_API, params={"source": "main-fe-header"}) as resp:
                payload = await resp.json()

        if payload.get("code") != 0:
            raise LoginFlowError(f"获取二维码失败: {payload.get('message') or payload.get('code')}")

        data = payload.get("data") or {}
        qrcode_key = data.get("qrcode_key") or ""
        qrcode_url = data.get("url") or ""
        if not qrcode_key or not qrcode_url:
            raise LoginFlowError("获取二维码失败: 接口未返回 qrcode_key / url")

        self._qrcode_key = qrcode_key
        self._qrcode_url = qrcode_url

        image = qrcode.QRCode(border=2)
        image.add_data(qrcode_url)
        image.make(fit=True)
        img = image.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    async def poll(self) -> Tuple[str, str, Optional[Credential]]:
        """轮询登录状态

        Returns:
            (状态, 提示文案, 成功时返回的 Credential)
        """
        if self._qrcode_key is None:
            return FLOW_ERROR, "二维码未生成", None

        if self._finished and self._credential is not None:
            return FLOW_DONE, "登录成功", self._credential

        async with aiohttp.ClientSession(headers=COMMON_HEADERS, timeout=_TIMEOUT) as session:
            async with session.get(
                POLL_API,
                params={"qrcode_key": self._qrcode_key, "source": "main-fe-header"},
            ) as resp:
                payload = await resp.json()
                # 部分情况下 Cookie 会直接下发在 poll 响应里，一并收集
                cookies = _extract_cookie_values(resp.raw_headers)

            if payload.get("code") != 0:
                return FLOW_ERROR, f"登录接口异常: {payload.get('message') or payload.get('code')}", None

            data = payload.get("data") or {}
            code = data.get("code")

            if code == STATE_WAITING:
                return FLOW_WAITING, "等待扫码...", None
            if code == STATE_SCANNED:
                return FLOW_SCANNED, "已扫码，请在手机上确认", None
            if code == STATE_EXPIRED:
                self._finished = True
                return FLOW_EXPIRED, "二维码已过期", None
            if code != STATE_SUCCESS:
                return FLOW_ERROR, f"未知的二维码状态: {code}", None

            # 登录成功：补齐 Cookie
            credential = await self._finish_login(session, data, cookies)
            self._credential = credential
            self._finished = True
            return FLOW_DONE, "登录成功", credential

    # ------------------------------------------------------------------ 内部实现
    async def _finish_login(
        self,
        session: aiohttp.ClientSession,
        data: dict,
        cookies: Dict[str, str],
    ) -> Credential:
        """登录成功后从 crossDomain 地址换取 Cookie 并构造 Credential"""
        url = data.get("url") or ""
        refresh_token = data.get("refresh_token") or ""

        # 兼容旧协议：url 的 query 中可能直接带有 Cookie
        if url:
            query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
            for key, value in query.items():
                if key.lower() in _WANTED_COOKIES and value:
                    cookies[key] = value

        # 新协议：请求 crossDomain 地址，从 302 的 Set-Cookie 中收集
        if not _pick(cookies, "sessdata") and url:
            cookies.update(await self._collect_cookies_from_url(session, url))

        sessdata = _pick(cookies, "sessdata")
        if not sessdata:
            logger.error("登录成功但未获取到 SESSDATA，Cookie: %s", list(cookies))
            raise LoginFlowError(
                "登录成功但未获取到 SESSDATA（票据换取 Cookie 失败），请重新扫码"
            )

        logger.info("扫码登录成功，已获取 SESSDATA")
        return Credential(
            sessdata=sessdata,
            bili_jct=_pick(cookies, "bili_jct") or None,
            dedeuserid=_pick(cookies, "dedeuserid") or None,
            buvid3=_pick(cookies, "buvid3") or None,
            buvid4=_pick(cookies, "buvid4") or None,
            ac_time_value=refresh_token or _pick(cookies, "ac_time_value") or None,
        )

    @staticmethod
    async def _collect_cookies_from_url(
        session: aiohttp.ClientSession, url: str
    ) -> Dict[str, str]:
        """请求 crossDomain 地址，手动跟随跳转并逐跳收集 Set-Cookie

        不能使用自动跟随重定向：SESSDATA 往往在第一次 302 的响应头里，
        自动跟随后无法再读取中间响应的 Set-Cookie。
        """
        cookies: Dict[str, str] = {}
        current = url
        visited = set()

        for _ in range(_MAX_REDIRECTS):
            if not current or current in visited:
                break
            visited.add(current)

            async with session.get(current, allow_redirects=False) as resp:
                cookies.update(_extract_cookie_values(resp.raw_headers))
                location = resp.headers.get("Location")
                if resp.status in (301, 302, 303, 307, 308) and location:
                    current = urljoin(current, location)
                    continue
                break

        return cookies
