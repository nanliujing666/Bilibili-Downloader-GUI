"""登录对话框"""
import asyncio
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional
from PIL import Image, ImageTk
import io

from ..api.auth_service import AuthService
from ..core.event_bus import get_event_bus

logger = logging.getLogger(__name__)


class LoginDialog:
    """二维码登录对话框"""

    def __init__(self, parent: tk.Tk, auth_service: AuthService,
                 on_login_success: Optional[Callable] = None):
        self.parent = parent
        self.auth_service = auth_service
        self.on_login_success = on_login_success

        # 创建对话框窗口
        self.window = tk.Toplevel(parent)
        self.window.title("扫码登录 B站")
        self.window.geometry("400x500")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.grab_set()

        # 居中显示
        self._center_window()

        # QR码图片引用
        self.qr_photo: Optional[ImageTk.PhotoImage] = None
        self.qr_label: Optional[ttk.Label] = None

        # 状态标签
        self.status_var = tk.StringVar(value="准备获取二维码...")

        # 登录检查控制
        self._check_thread: Optional[threading.Thread] = None
        self._is_closed = False
        self._stop_checking = False

        # 创建事件循环（用于异步操作）
        self._loop = asyncio.new_event_loop()
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

        self._build_ui()
        self._start_login_flow()

    def _start_loop(self):
        """在后台线程启动事件循环"""
        def run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()

    def _run_async(self, coro):
        """在事件循环中运行异步协程"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _center_window(self):
        """居中窗口"""
        self.window.update_idletasks()
        width = 400
        height = 500
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _build_ui(self):
        """构建UI"""
        # 标题
        ttk.Label(
            self.window,
            text="Bilibili 扫码登录",
            font=('Microsoft YaHei', 16, 'bold')
        ).pack(pady=20)

        # 二维码显示区域
        self.qr_frame = ttk.Frame(self.window)
        self.qr_frame.pack(pady=10)

        # 占位标签
        self.qr_label = ttk.Label(self.qr_frame, text="加载中...")
        self.qr_label.pack()

        # 说明文字
        ttk.Label(
            self.window,
            text="请使用哔哩哔哩APP扫码登录",
            font=('Microsoft YaHei', 10)
        ).pack(pady=10)

        # 状态标签
        self.status_label = ttk.Label(
            self.window,
            textvariable=self.status_var,
            font=('Microsoft YaHei', 9),
            foreground='gray'
        )
        self.status_label.pack(pady=10)

        # 刷新按钮
        self.refresh_btn = ttk.Button(
            self.window,
            text="刷新二维码",
            command=self._refresh_qr
        )
        self.refresh_btn.pack(pady=10)

        # 取消按钮
        ttk.Button(
            self.window,
            text="取消",
            command=self.close
        ).pack(pady=10)

        # 绑定关闭事件
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    async def _get_qr_code(self):
        """获取二维码"""
        try:
            self.status_var.set("正在获取二维码...")

            # 获取二维码
            qr_bytes = await self.auth_service.get_qr_code()

            if self._is_closed:
                return

            # 在主线程显示二维码
            self.window.after(0, lambda: self._display_qr(qr_bytes))
            self.window.after(0, lambda: self.status_var.set("请使用B站APP扫码"))

            # 开始检查登录状态
            self.window.after(100, self._start_checking_login)

        except Exception as e:
            logger.error(f"获取二维码失败: {e}")
            if not self._is_closed:
                self.window.after(0, lambda: self.status_var.set(f"获取失败: {e}"))
                self.window.after(0, lambda: self.qr_label.config(text="获取失败，请重试"))

    def _display_qr(self, qr_bytes: bytes):
        """显示二维码图片"""
        try:
            # 打开图片
            image = Image.open(io.BytesIO(qr_bytes))

            # 调整大小
            image = image.resize((250, 250), Image.Resampling.LANCZOS)

            # 转换为PhotoImage
            self.qr_photo = ImageTk.PhotoImage(image)

            # 更新标签
            self.qr_label.config(image=self.qr_photo, text="")

        except Exception as e:
            logger.error(f"显示二维码失败: {e}")
            self.qr_label.config(text="图片加载失败")

    def _start_checking_login(self):
        """开始检查登录状态（在后台线程）"""
        self._stop_checking = False
        self._check_thread = threading.Thread(target=self._check_login_thread, daemon=True)
        self._check_thread.start()

    def _check_login_thread(self):
        """检查登录状态的线程"""
        async def check_loop():
            max_attempts = 180  # 3分钟
            attempt = 0

            while not self._is_closed and not self._stop_checking and attempt < max_attempts:
                try:
                    success, message = await self.auth_service.check_qr_status()

                    if self._is_closed or self._stop_checking:
                        return

                    # 在主线程更新UI
                    self.window.after(0, lambda m=message: self.status_var.set(m))

                    if success:
                        # 登录成功
                        self.window.after(0, self._on_login_success)
                        return
                    elif "过期" in message or "失效" in message:
                        # 二维码过期
                        self.window.after(0, lambda: self.status_var.set("二维码已过期，请刷新"))
                        return

                    attempt += 1
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.error(f"检查登录状态失败: {e}")
                    await asyncio.sleep(2)

            if not self._is_closed and not self._stop_checking:
                self.window.after(0, lambda: self.status_var.set("登录超时，请刷新二维码"))

        # 在事件循环中运行
        asyncio.run_coroutine_threadsafe(check_loop(), self._loop)

    def _on_login_success(self):
        """登录成功处理"""
        self.status_var.set("登录成功！")
        self.status_label.config(foreground='green')

        # 回调
        if self.on_login_success:
            try:
                self.on_login_success()
            except Exception as e:
                logger.error(f"登录回调失败: {e}")

        # 延迟关闭
        self.window.after(1000, self.close)

    def _refresh_qr(self):
        """刷新二维码"""
        self._stop_checking = True
        self._run_async(self._get_qr_code())

    def _start_login_flow(self):
        """启动登录流程"""
        self._run_async(self._get_qr_code())

    def close(self):
        """关闭对话框"""
        self._is_closed = True
        self._stop_checking = True

        # 停止事件循环
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        self.window.destroy()
