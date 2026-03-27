"""稍后再看下载对话框"""
import asyncio
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable, List

from ..api.watch_later_api import WatchLaterApiClient
from ..api.auth_service import AuthService
from ..models.video import VideoInfo
from ..config.settings import get_settings

logger = logging.getLogger(__name__)


class WatchLaterDialog:
    """稍后再看选择对话框 - 带复选框选择，支持滚动和分段加载"""

    def __init__(
        self,
        parent: tk.Tk,
        auth_service: AuthService,
        on_download: Optional[Callable[[List[VideoInfo]], None]] = None
    ):
        self.parent = parent
        self.auth_service = auth_service
        self.on_download = on_download

        # API客户端
        credential = auth_service.get_credential() if auth_service else None
        self.watch_later_api = WatchLaterApiClient(credential)

        # 创建窗口
        self.window = tk.Toplevel(parent)
        self.window.title("稍后再看下载")
        self.window.geometry("800x600")
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.grab_set()

        self._center_window()

        # 数据
        self.all_videos: List[VideoInfo] = []  # 所有视频
        self.displayed_count: int = 0  # 已显示的视频数量
        self.video_checkboxes: dict = {}  # {index: BooleanVar}

        # 分页设置 - 稍后再看一次性加载所有数据，前端分段显示
        try:
            settings = get_settings()
            self.page_size: int = getattr(settings, 'page_size', 20)
            if not isinstance(self.page_size, int) or self.page_size < 10:
                self.page_size = 20
        except Exception:
            self.page_size = 20
        self.is_loading: bool = False
        self.has_more: bool = True

        # 创建事件循环
        self._loop = asyncio.new_event_loop()
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

        self._build_ui()
        self._run_async(self._load_videos())

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
        width = 800
        height = 600
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _format_duration(self, seconds: int) -> str:
        """格式化时长"""
        if seconds <= 0:
            return "--"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes >= 60:
            hours = minutes // 60
            minutes = minutes % 60
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _build_ui(self):
        """构建UI"""
        # 主容器
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill='both', expand=True)

        # 标题
        ttk.Label(
            main_frame,
            text="⏱ 稍后再看",
            font=('Microsoft YaHei', 14, 'bold')
        ).pack(pady=5)

        # 说明
        ttk.Label(
            main_frame,
            text="选择要下载的视频（滚动加载更多）",
            font=('Microsoft YaHei', 9),
            foreground='gray'
        ).pack(pady=2)

        # 视频列表容器（带复选框）
        list_frame = ttk.LabelFrame(main_frame, text="视频列表", padding="5")
        list_frame.pack(fill='both', expand=True, pady=10)

        # Canvas + 滚动条
        self.video_canvas = tk.Canvas(list_frame, highlightthickness=0)
        self.video_canvas.pack(side='left', fill='both', expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.video_canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.video_canvas.configure(yscrollcommand=scrollbar.set)

        # 视频列表内部frame
        self.video_list_frame = ttk.Frame(self.video_canvas)
        self.video_canvas_window = self.video_canvas.create_window((0, 0), window=self.video_list_frame, anchor='nw')

        # 绑定canvas大小变化和滚动事件
        self.video_list_frame.bind('<Configure>', self._on_video_list_configure)
        self.video_canvas.bind('<Configure>', self._on_canvas_configure)
        self.video_canvas.bind_all('<MouseWheel>', self._on_mousewheel)

        # 底部按钮栏
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill='x', pady=5)

        # 左侧 - 全选/取消全选
        select_frame = ttk.Frame(bottom_frame)
        select_frame.pack(side='left')

        ttk.Button(
            select_frame,
            text="✓ 全选",
            command=self._select_all
        ).pack(side='left', padx=2)

        ttk.Button(
            select_frame,
            text="✗ 取消全选",
            command=self._deselect_all
        ).pack(side='left', padx=2)

        # 中间 - 状态标签
        self.status_var = tk.StringVar(value="正在加载稍后再看列表...")
        ttk.Label(
            bottom_frame,
            textvariable=self.status_var,
            font=('Microsoft YaHei', 9)
        ).pack(side='left', padx=20)

        # 右侧 - 下载和取消按钮
        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(side='right')

        self.download_btn = ttk.Button(
            btn_frame,
            text="下载选中",
            command=self._download_selected,
            state='disabled'
        )
        self.download_btn.pack(side='left', padx=5)

        ttk.Button(
            btn_frame,
            text="刷新列表",
            command=lambda: self._run_async(self._load_videos())
        ).pack(side='left', padx=5)

        ttk.Button(
            btn_frame,
            text="取消",
            command=self.close
        ).pack(side='left', padx=5)

    def _on_video_list_configure(self, event=None):
        """视频列表大小变化时更新canvas滚动区域"""
        self.video_canvas.update_idletasks()
        self.video_canvas.configure(scrollregion=self.video_canvas.bbox('all'))

    def _on_canvas_configure(self, event=None):
        """Canvas大小变化时更新内部窗口宽度"""
        self.video_canvas.itemconfig(self.video_canvas_window, width=event.width)

    def _clear_video_list(self):
        """清空视频列表"""
        for widget in self.video_list_frame.winfo_children():
            widget.destroy()
        self.video_checkboxes.clear()

    def _update_video_list(self):
        """初始化视频列表UI（显示前page_size个）"""
        self._clear_video_list()

        if not self.all_videos:
            ttk.Label(
                self.video_list_frame,
                text="稍后再看列表为空",
                font=('Microsoft YaHei', 10),
                foreground='gray'
            ).pack(pady=20)
            return

        # 表头
        header_frame = ttk.Frame(self.video_list_frame)
        header_frame.pack(fill='x', pady=2)

        ttk.Label(header_frame, text="选择", width=6).pack(side='left', padx=2)
        ttk.Label(header_frame, text="标题", width=45).pack(side='left', padx=2)
        ttk.Label(header_frame, text="UP主", width=15).pack(side='left', padx=2)
        ttk.Label(header_frame, text="时长", width=10).pack(side='left', padx=2)

        ttk.Separator(self.video_list_frame, orient='horizontal').pack(fill='x', pady=2)

        # 显示第一批视频
        self._append_video_rows(0, min(self.page_size, len(self.all_videos)))

        # 启用下载按钮
        self.download_btn.configure(state='normal')

        # 更新状态
        self._update_status()

    def _append_video_rows(self, start_idx: int, end_idx: int):
        """追加视频行到列表"""
        for i in range(start_idx, end_idx):
            if i >= len(self.all_videos):
                break

            video = self.all_videos[i]
            var = tk.BooleanVar(value=True)  # 默认全选
            self.video_checkboxes[i] = var

            row_frame = ttk.Frame(self.video_list_frame)
            row_frame.pack(fill='x', pady=1)

            # 复选框
            chk = ttk.Checkbutton(row_frame, variable=var)
            chk.pack(side='left', padx=2)

            # 标题
            title = video.title[:43] + '...' if len(video.title) > 45 else video.title
            ttk.Label(row_frame, text=title, width=45).pack(side='left', padx=2)

            # UP主
            owner_name = video.owner.get('name', '未知') if isinstance(video.owner, dict) else '未知'
            owner_name = owner_name[:13] + '...' if len(owner_name) > 15 else owner_name
            ttk.Label(row_frame, text=owner_name, width=15).pack(side='left', padx=2)

            # 时长
            duration_str = self._format_duration(video.duration)
            ttk.Label(row_frame, text=duration_str, width=10).pack(side='left', padx=2)

        self.displayed_count = end_idx

        # 更新滚动区域
        self.video_canvas.update_idletasks()
        self.video_canvas.configure(scrollregion=self.video_canvas.bbox('all'))

        # 检查是否还有更多
        if self.displayed_count >= len(self.all_videos):
            self.has_more = False

    def _update_status(self):
        """更新状态标签"""
        loaded = len(self.video_checkboxes)
        total = len(self.all_videos)
        more_str = "，向下滚动加载更多" if self.has_more and loaded < total else ""
        self.status_var.set(f"已加载 {loaded}/{total} 个视频{more_str}")

    def _select_all(self):
        """全选"""
        for var in self.video_checkboxes.values():
            var.set(True)

    def _deselect_all(self):
        """取消全选"""
        for var in self.video_checkboxes.values():
            var.set(False)

    def _get_selected_videos(self) -> List[VideoInfo]:
        """获取选中的视频"""
        selected = []
        for i, var in self.video_checkboxes.items():
            if var.get() and 0 <= i < len(self.all_videos):
                selected.append(self.all_videos[i])
        return selected

    async def _load_videos(self):
        """加载稍后再看列表"""
        self.window.after(0, lambda: self.status_var.set("正在加载稍后再看列表..."))

        # 重置状态
        self.displayed_count = 0
        self.has_more = True
        self.is_loading = True
        self.all_videos = []

        try:
            self.all_videos = await self.watch_later_api.get_watch_later_videos()
            self.window.after(0, self._update_video_list)
        except Exception as e:
            logger.error(f"加载稍后再看失败: {e}")
            error_msg = str(e)
            self.window.after(0, lambda msg=error_msg: self.status_var.set(f"加载失败: {msg}"))
            self.window.after(0, lambda: messagebox.showerror("错误", f"无法加载稍后再看: {e}", parent=self.window))
        finally:
            self.is_loading = False

    def _load_more_videos(self):
        """加载更多视频（分批显示）"""
        if self.is_loading or not self.has_more:
            return

        if self.displayed_count >= len(self.all_videos):
            self.has_more = False
            return

        # 计算下一批的结束位置
        end_idx = min(self.displayed_count + self.page_size, len(self.all_videos))

        # 追加显示
        self._append_video_rows(self.displayed_count, end_idx)
        self._update_status()

    def _on_mousewheel(self, event):
        """处理鼠标滚轮事件，执行滚动并检测是否到底部"""
        # 检查canvas是否还存在（对话框可能已关闭）
        try:
            if not self.video_canvas.winfo_exists():
                return "break"
        except tk.TclError:
            return "break"

        # 执行滚动
        self.video_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        # 检测是否滚动到底部（95%位置）
        if self.video_canvas.yview()[1] >= 0.95 and self.has_more and not self.is_loading:
            if self.displayed_count < len(self.all_videos):
                self._load_more_videos()

        return "break"

    def _download_selected(self):
        """下载选中的视频"""
        selected = self._get_selected_videos()

        if not selected:
            messagebox.showwarning("提示", "请先选择要下载的视频", parent=self.window)
            return

        if messagebox.askyesno(
            "确认下载",
            f"确定要下载选中的 {len(selected)} 个视频吗？",
            parent=self.window
        ):
            if self.on_download:
                try:
                    # 传递稍后再看来源信息
                    self.on_download(selected, "稍后再看", "watch_later")
                except Exception as e:
                    logger.error(f"下载回调失败: {e}")

            self.window.after(0, self.close)

    def close(self):
        """关闭对话框"""
        # 解绑鼠标滚轮事件
        try:
            self.video_canvas.unbind_all('<MouseWheel>')
        except:
            pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.window.destroy()
