"""收藏夹选择对话框"""
import asyncio
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable, List

from ..api.favorite_api import FavoriteApiClient
from ..api.auth_service import AuthService
from ..models.video import VideoInfo

logger = logging.getLogger(__name__)


class FavoriteDialog:
    """收藏夹选择对话框 - 左侧收藏夹，右侧视频列表"""

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
        self.favorite_api = FavoriteApiClient(credential)

        # 创建窗口
        self.window = tk.Toplevel(parent)
        self.window.title("收藏夹下载")
        self.window.geometry("900x600")
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.grab_set()

        self._center_window()

        # 数据
        self.favorites: List[dict] = []
        self.videos: List[VideoInfo] = []
        self.selected_favorite_id: Optional[str] = None
        self.video_checkboxes: dict = {}  # 存储视频勾选状态 {index: BooleanVar}
        self.current_page: int = 1
        self.page_size: int = 20
        self.is_loading: bool = False
        self.has_more: bool = True

        # 创建事件循环
        self._loop = asyncio.new_event_loop()
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

        self._build_ui()
        self._run_async(self._load_favorites())

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
        width = 900
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
        """构建UI - 双栏布局"""
        # 主容器
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill='both', expand=True)

        # 标题
        ttk.Label(
            main_frame,
            text="📁 收藏夹下载",
            font=('Microsoft YaHei', 14, 'bold')
        ).pack(pady=5)

        # 内容区域 - 左右分栏
        content_frame = ttk.PanedWindow(main_frame, orient='horizontal')
        content_frame.pack(fill='both', expand=True, pady=10)

        # 左侧面板 - 收藏夹列表
        left_frame = ttk.LabelFrame(content_frame, text="我的收藏夹", padding="5")
        content_frame.add(left_frame, weight=1)

        # 收藏夹Treeview
        self.fav_tree = ttk.Treeview(
            left_frame,
            columns=('count',),
            show='tree headings',
            selectmode='browse'
        )
        self.fav_tree.heading('#0', text='收藏夹名称')
        self.fav_tree.column('#0', width=150, anchor='w')
        self.fav_tree.heading('count', text='视频数')
        self.fav_tree.column('count', width=60, anchor='center')

        fav_scrollbar = ttk.Scrollbar(left_frame, orient='vertical', command=self.fav_tree.yview)
        self.fav_tree.configure(yscrollcommand=fav_scrollbar.set)

        self.fav_tree.pack(side='left', fill='both', expand=True)
        fav_scrollbar.pack(side='right', fill='y')

        # 绑定收藏夹选择事件
        self.fav_tree.bind('<<TreeviewSelect>>', self._on_favorite_select)

        # 右侧面板 - 视频列表
        right_frame = ttk.LabelFrame(content_frame, text="视频列表", padding="5")
        content_frame.add(right_frame, weight=2)

        # 视频列表容器（带复选框）
        self.video_canvas = tk.Canvas(right_frame, highlightthickness=0)
        self.video_canvas.pack(side='left', fill='both', expand=True)

        video_scrollbar = ttk.Scrollbar(right_frame, orient='vertical', command=self.video_canvas.yview)
        video_scrollbar.pack(side='right', fill='y')
        self.video_canvas.configure(yscrollcommand=video_scrollbar.set)

        # 视频列表内部frame
        self.video_list_frame = ttk.Frame(self.video_canvas)
        self.video_canvas_window = self.video_canvas.create_window((0, 0), window=self.video_list_frame, anchor='nw')

        # 绑定canvas大小变化和滚动事件
        self.video_list_frame.bind('<Configure>', self._on_video_list_configure)
        self.video_canvas.bind('<Configure>', self._on_canvas_configure)
        self.video_canvas.bind_all('<MouseWheel>', self._on_mousewheel)

        # 加载更多按钮（初始隐藏）
        self.load_more_btn = ttk.Button(self.video_list_frame, text="加载更多...", command=self._load_more_videos)
        self.loading_var = tk.StringVar(value="")

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
        self.status_var = tk.StringVar(value="正在加载收藏夹...")
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
            text="取消",
            command=self.close
        ).pack(side='left', padx=5)

    def _on_video_list_configure(self, event=None):
        """视频列表大小变化时更新canvas滚动区域"""
        # 更新滚动区域以包含所有内容
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
        """更新视频列表UI"""
        self._clear_video_list()

        if not self.videos:
            ttk.Label(
                self.video_list_frame,
                text="该收藏夹为空",
                font=('Microsoft YaHei', 10),
                foreground='gray'
            ).pack(pady=20)
            return

        # 表头
        header_frame = ttk.Frame(self.video_list_frame)
        header_frame.pack(fill='x', pady=2)

        ttk.Label(header_frame, text="选择", width=6).pack(side='left', padx=2)
        ttk.Label(header_frame, text="标题", width=40).pack(side='left', padx=2)
        ttk.Label(header_frame, text="UP主", width=15).pack(side='left', padx=2)
        ttk.Label(header_frame, text="时长", width=10).pack(side='left', padx=2)

        ttk.Separator(self.video_list_frame, orient='horizontal').pack(fill='x', pady=2)

        # 视频列表
        for i, video in enumerate(self.videos):
            var = tk.BooleanVar(value=True)  # 默认全选
            self.video_checkboxes[i] = var

            row_frame = ttk.Frame(self.video_list_frame)
            row_frame.pack(fill='x', pady=1)

            # 复选框
            chk = ttk.Checkbutton(row_frame, variable=var)
            chk.pack(side='left', padx=2)

            # 标题
            title = video.title[:38] + '...' if len(video.title) > 40 else video.title
            ttk.Label(row_frame, text=title, width=40).pack(side='left', padx=2)

            # UP主
            owner_name = video.owner.get('name', '未知') if isinstance(video.owner, dict) else '未知'
            owner_name = owner_name[:13] + '...' if len(owner_name) > 15 else owner_name
            ttk.Label(row_frame, text=owner_name, width=15).pack(side='left', padx=2)

            # 时长
            duration_str = self._format_duration(video.duration)
            ttk.Label(row_frame, text=duration_str, width=10).pack(side='left', padx=2)

        # 启用下载按钮
        self.download_btn.configure(state='normal')

        # 更新状态
        loaded = len(self.video_checkboxes)
        more_str = "，向下滚动加载更多" if self.has_more else ""
        self.status_var.set(f"已加载 {loaded} 个视频{more_str}")

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
            if var.get() and 0 <= i < len(self.videos):
                selected.append(self.videos[i])
        return selected

    async def _load_favorites(self):
        """加载收藏夹列表"""
        self.window.after(0, lambda: self.status_var.set("正在加载收藏夹..."))

        try:
            favorites = await self.favorite_api.get_user_folders()
            self.favorites = [
                {'id': f.fid, 'title': f.title, 'media_count': f.media_count}
                for f in favorites
            ]

            self.window.after(0, self._update_favorites_list)

        except Exception as e:
            logger.error(f"加载收藏夹失败: {e}")
            self.window.after(0, lambda: self.status_var.set(f"加载失败: {e}"))
            self.window.after(0, lambda: messagebox.showerror("错误", f"无法加载收藏夹: {e}", parent=self.window))

    def _update_favorites_list(self):
        """在主线程更新收藏夹列表"""
        for item in self.fav_tree.get_children():
            self.fav_tree.delete(item)

        for fav in self.favorites:
            self.fav_tree.insert(
                '',
                'end',
                text=fav.get('title', '未命名'),
                values=(fav.get('media_count', 0),),
                tags=(fav.get('id'),)
            )

        self.status_var.set(f"共 {len(self.favorites)} 个收藏夹，点击选择")

    def _on_favorite_select(self, event):
        """收藏夹选择事件"""
        selection = self.fav_tree.selection()
        if selection:
            item = selection[0]
            tags = self.fav_tree.item(item, 'tags')
            if tags:
                self.selected_favorite_id = tags[0]
                favorite_title = self.fav_tree.item(item, 'text')
                self.status_var.set(f"正在加载「{favorite_title}」...")
                self._run_async(self._load_videos(self.selected_favorite_id))

    async def _load_videos(self, favorite_id: str):
        """加载收藏夹视频 - 分页加载，先加载前50个"""
        # 重置分页状态
        self.current_page = 1
        self.has_more = True
        self.is_loading = True
        self.videos = []
        self.video_checkboxes.clear()

        try:
            # 先加载第一页
            page_videos = await self.favorite_api.get_favorite_videos(
                favorite_id,
                page=1,
                page_size=self.page_size
            )

            self.videos.extend(page_videos)

            # 检查是否还有更多
            if len(page_videos) < self.page_size:
                self.has_more = False

            self.window.after(0, self._update_video_list)
        except Exception as e:
            logger.error(f"加载视频列表失败: {e}")
            error_msg = str(e)
            self.window.after(0, lambda msg=error_msg: self.status_var.set(f"加载失败: {msg}"))
        finally:
            self.is_loading = False

    def _download_selected(self):
        """下载选中的视频"""
        selected = self._get_selected_videos()

        if not selected:
            messagebox.showwarning("提示", "请先选择要下载的视频", parent=self.window)
            return

        # 获取收藏夹名称
        favorite_title = ""
        for item in self.fav_tree.selection():
            favorite_title = self.fav_tree.item(item, 'text')

        if messagebox.askyesno(
            "确认下载",
            f"确定要下载选中的 {len(selected)} 个视频吗？",
            parent=self.window
        ):
            if self.on_download:
                try:
                    # 获取收藏夹信息
                    favorite_title = ""
                    for item in self.fav_tree.selection():
                        favorite_title = self.fav_tree.item(item, 'text')

                    # 回调传递视频列表、收藏夹名称和ID
                    self.on_download(selected, favorite_title, self.selected_favorite_id)
                except Exception as e:
                    logger.error(f"下载回调失败: {e}")

            self.window.after(0, self.close)

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

        # 检测是否滚动到底部
        if self.video_canvas.yview()[1] >= 0.95 and self.has_more and not self.is_loading:
            if len(self.video_checkboxes) >= self.current_page * self.page_size:
                self._run_async(self._load_more_videos_async())
        return "break"

    def _load_more_videos(self):
        """UI调用加载更多视频"""
        if not self.is_loading and self.has_more:
            self._run_async(self._load_more_videos_async())

    async def _load_more_videos_async(self):
        """异步加载更多视频"""
        if self.is_loading or not self.has_more:
            return

        self.is_loading = True
        self.window.after(0, lambda: self.status_var.set(f"正在加载第 {self.current_page + 1} 页..."))

        try:
            # 加载下一页
            page_videos = await self.favorite_api.get_favorite_videos(
                self.selected_favorite_id,
                page=self.current_page + 1,
                page_size=self.page_size
            )

            if page_videos:
                self.current_page += 1
                start_idx = len(self.videos)
                self.videos.extend(page_videos)
                self.window.after(0, lambda: self._append_video_rows(start_idx, page_videos))

                if len(page_videos) < self.page_size:
                    self.has_more = False
            else:
                self.has_more = False

        except Exception as e:
            logger.error(f"加载更多视频失败: {e}")
        finally:
            self.is_loading = False
            self.window.after(0, self._update_status)

    def _append_video_rows(self, start_idx: int, videos: List[VideoInfo]):
        """追加视频行到列表"""
        for i, video in enumerate(videos):
            idx = start_idx + i
            var = tk.BooleanVar(value=True)
            self.video_checkboxes[idx] = var

            row_frame = ttk.Frame(self.video_list_frame)
            row_frame.pack(fill='x', pady=1)

            chk = ttk.Checkbutton(row_frame, variable=var)
            chk.pack(side='left', padx=2)

            title = video.title[:38] + '...' if len(video.title) > 40 else video.title
            ttk.Label(row_frame, text=title, width=40).pack(side='left', padx=2)

            owner_name = video.owner.get('name', '未知') if isinstance(video.owner, dict) else '未知'
            owner_name = owner_name[:13] + '...' if len(owner_name) > 15 else owner_name
            ttk.Label(row_frame, text=owner_name, width=15).pack(side='left', padx=2)

            duration_str = self._format_duration(video.duration)
            ttk.Label(row_frame, text=duration_str, width=10).pack(side='left', padx=2)

        # 更新滚动区域
        self.video_canvas.update_idletasks()
        self.video_canvas.configure(scrollregion=self.video_canvas.bbox('all'))

    def _update_status(self):
        """更新状态标签"""
        loaded = len(self.video_checkboxes)
        total_str = f"+" if self.has_more else ""
        self.status_var.set(f"已加载 {loaded} 个视频{total_str}")

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
