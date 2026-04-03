"""主窗口"""
import asyncio
import logging
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Optional

from ..api.auth_service import AuthService
from ..models.enums import TaskStatus
from ..services.download_service import DownloadService
from ..core.state_manager import get_state_manager
from ..core.event_bus import get_event_bus
from ..config.settings import get_settings
from .login_dialog import LoginDialog
from .download_manager import DownloadManager
from .favorite_dialog import FavoriteDialog
from .cheese_dialog import CheeseDialog
from .watch_later_dialog import WatchLaterDialog
from .history_dialog import HistoryDialog

logger = logging.getLogger(__name__)


class AsyncTkApp:
    """支持异步的Tk应用基类"""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

    def _start_loop(self):
        """在后台线程启动事件循环"""
        def run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()

    def run_async(self, coro, callback=None):
        """在事件循环中运行异步协程

        Args:
            coro: 协程对象
            callback: 完成后的回调函数(可选)
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        if callback:
            future.add_done_callback(lambda f: callback(f.result()) if f.exception() is None else None)

        return future

    def stop(self):
        """停止事件循环"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)


class MainWindow(AsyncTkApp):
    """应用主窗口"""

    def __init__(self):
        super().__init__()

        self.root = tk.Tk()
        self.root.title("Bilibili 视频下载器")
        self.root.geometry("1000x700")
        self.root.minsize(800, 600)

        # 初始化服务
        self.auth_service = AuthService()
        self.download_service = DownloadService(self.auth_service)
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()

        # 加载设置并应用最大并发数
        settings = get_settings()
        self._pending_max_concurrent = settings.max_concurrent

        # 首次启动检查下载路径
        self._check_download_path_on_startup()

        # 登录状态
        self.is_logged_in = False
        self.user_info: Optional[dict] = None

        # 状态变量
        self.status_var = tk.StringVar(value="就绪")
        self.login_status_var = tk.StringVar(value="未登录")

        self._build_ui()
        self._setup_event_handlers()
        self._check_saved_login()
        self._load_saved_tasks()

        # 应用保存的最大并发数设置
        self.run_async(self.download_service.set_max_concurrent(self._pending_max_concurrent))

    def _check_download_path_on_startup(self):
        """首次启动时检查下载路径，路径不存在则提示用户选择"""
        from ..config.settings import get_settings, save_settings
        import os
        from tkinter import messagebox, filedialog

        settings = get_settings()

        # 检查路径是否有效（存在且可写）
        path_valid = False
        if settings.download_path:
            # 处理相对路径
            if settings.download_path.startswith('./'):
                abs_path = os.path.join(os.getcwd(), settings.download_path[2:])
            else:
                abs_path = settings.download_path

            # 检查路径是否存在且是目录
            if os.path.exists(abs_path) and os.path.isdir(abs_path):
                # 检查是否可写
                try:
                    test_file = os.path.join(abs_path, '.write_test')
                    with open(test_file, 'w') as f:
                        f.write('test')
                    os.remove(test_file)
                    path_valid = True
                except:
                    path_valid = False

        if not path_valid:
            # 路径无效，提示用户选择
            messagebox.showinfo(
                "设置下载路径",
                f"下载路径无效或不存在:\n{settings.download_path}\n\n"
                "请选择有效的下载文件夹。"
            )

            path = filedialog.askdirectory(
                title="选择下载目录",
                mustexist=True
            )

            if path:
                settings.download_path = path
                save_settings(settings)
                logger.info(f"用户设置下载路径: {path}")
            else:
                # 用户取消，使用程序目录下的 downloads 文件夹
                default_path = os.path.join(os.getcwd(), "downloads")
                settings.download_path = default_path
                save_settings(settings)
                os.makedirs(default_path, exist_ok=True)
                logger.info(f"使用默认下载路径: {default_path}")

    def _build_ui(self):
        """构建UI"""
        # 菜单栏
        self._build_menu()

        # 顶部工具栏
        self._build_toolbar()

        # 主内容区
        content_frame = ttk.PanedWindow(self.root, orient='horizontal')
        content_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # 左侧面板 - 下载管理
        left_frame = ttk.LabelFrame(content_frame, text="下载管理", padding="5")
        content_frame.add(left_frame, weight=3)

        self.download_manager = DownloadManager(left_frame, self.download_service, async_runner=self.run_async)

        # 右侧面板 - 下载设置
        right_frame = ttk.LabelFrame(content_frame, text="下载设置", padding="10")
        content_frame.add(right_frame, weight=1)

        # 设置面板放入带滚动条的容器
        self._build_settings_panel_with_scroll(right_frame)

        # 底部状态栏
        self._build_statusbar()

    def _build_menu(self):
        """构建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # 文件菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="设置下载路径", command=self._set_download_path)
        file_menu.add_command(label="下载历史", command=self._show_history)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)

        # 下载菜单
        download_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="下载", menu=download_menu)
        download_menu.add_command(label="新建下载", command=self._show_new_download)
        download_menu.add_command(label="从收藏夹下载", command=self._show_favorite_download)
        download_menu.add_command(label="从稍后再看下载", command=self._show_watch_later_download)
        download_menu.add_command(label="课程号下载", command=self._show_cheese_download)
        download_menu.add_separator()
        download_menu.add_command(label="开始全部", command=self._start_all)
        download_menu.add_command(label="暂停全部", command=self._pause_all)

        # 账户菜单
        self.account_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="账户", menu=self.account_menu)
        self.account_menu.add_command(label="登录", command=self._show_login)
        self.account_menu.add_command(label="注销", command=self._logout)
        self.account_menu.add_separator()
        self.account_menu.add_command(label="刷新登录状态", command=self._check_login_status)

        # 帮助菜单
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="帮助", menu=help_menu)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)

    def _build_toolbar(self):
        """构建工具栏"""
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill='x', padx=10, pady=5)

        # 下载按钮
        ttk.Button(
            toolbar,
            text="🎬 新建下载",
            command=self._show_new_download
        ).pack(side='left', padx=2)

        ttk.Button(
            toolbar,
            text="📁 收藏夹下载",
            command=self._show_favorite_download
        ).pack(side='left', padx=2)

        ttk.Button(
            toolbar,
            text="📚 课程下载",
            command=self._show_cheese_download
        ).pack(side='left', padx=2)

        ttk.Button(
            toolbar,
            text="⏱ 稍后再看",
            command=self._show_watch_later_download
        ).pack(side='left', padx=2)

        ttk.Separator(toolbar, orient='vertical').pack(side='left', fill='y', padx=5)

        # 控制按钮
        ttk.Button(
            toolbar,
            text="▶ 开始全部",
            command=self._start_all
        ).pack(side='left', padx=2)

        ttk.Button(
            toolbar,
            text="⏸ 暂停全部",
            command=self._pause_all
        ).pack(side='left', padx=2)

        # 右侧登录信息
        ttk.Separator(toolbar, orient='vertical').pack(side='right', fill='y', padx=5)

        self.login_label = ttk.Label(
            toolbar,
            textvariable=self.login_status_var,
            font=('Microsoft YaHei', 9)
        )
        self.login_label.pack(side='right', padx=5)

        ttk.Button(
            toolbar,
            text="🔑 登录",
            command=self._show_login
        ).pack(side='right', padx=2)

    def _build_settings_panel_with_scroll(self, parent: ttk.Widget):
        """构建设置面板（带滚动条）"""
        # 创建Canvas和滚动条
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)

        # 创建内容框架
        content_frame = ttk.Frame(canvas, padding="5")
        canvas_window = canvas.create_window((0, 0), window=content_frame, anchor='nw')

        # 绑定大小变化
        def on_configure(event):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.itemconfig(canvas_window, width=event.width)

        content_frame.bind('<Configure>', on_configure)
        canvas.bind('<Configure>', lambda e: canvas.itemconfig(canvas_window, width=e.width))

        # 构建实际内容
        self._build_settings_content(content_frame)

    def _build_settings_content(self, parent: ttk.Widget):
        """构建设置面板内容"""
        settings = get_settings()

        # 快速操作 - 放在最上面
        ttk.Label(parent, text="快速操作:", font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', pady=(0, 5))

        ttk.Button(
            parent,
            text="打开下载目录",
            command=self._open_download_dir
        ).pack(fill='x', pady=2)

        ttk.Button(
            parent,
            text="检查FFmpeg",
            command=self._check_ffmpeg
        ).pack(fill='x', pady=2)

        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=10)

        # 下载路径
        ttk.Label(parent, text="下载路径:", font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', pady=(0, 5))

        path_frame = ttk.Frame(parent)
        path_frame.pack(fill='x', pady=(0, 10))

        self.path_var = tk.StringVar(value=settings.download_path)
        ttk.Entry(path_frame, textvariable=self.path_var, state='readonly').pack(side='left', fill='x', expand=True)
        ttk.Button(path_frame, text="浏览", command=self._set_download_path).pack(side='left', padx=2)

        # 视频质量
        ttk.Label(parent, text="视频质量:", font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', pady=(10, 5))

        # 自动选择最佳质量
        self.auto_quality_var = tk.BooleanVar(value=settings.auto_quality)
        ttk.Checkbutton(
            parent,
            text="自动选择最佳质量",
            variable=self.auto_quality_var,
            command=self._on_auto_quality_changed
        ).pack(anchor='w', pady=(5, 0))

        ttk.Label(
            parent,
            text="手动选择质量 (关闭自动时生效):",
            font=('Microsoft YaHei', 9),
            foreground='gray'
        ).pack(anchor='w', pady=(10, 5))

        self.quality_var = tk.IntVar(value=settings.default_quality)
        quality_options = [
            (116, "1080P60"),
            (112, "1080P+"),
            (80, "1080P"),
            (64, "720P"),
            (32, "480P"),
            (16, "360P"),
        ]

        self.quality_radios = []
        for value, text in quality_options:
            radio = ttk.Radiobutton(
                parent,
                text=text,
                variable=self.quality_var,
                value=value
            )
            radio.pack(anchor='w', pady=2)
            self.quality_radios.append(radio)

        # 根据自动设置禁用/启用手动选择
        self._update_quality_radio_state()

        # 并发下载设置
        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=15)
        ttk.Label(parent, text="下载设置:", font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', pady=(10, 5))

        ttk.Label(parent, text="同时下载视频数 (1-10):", font=('Microsoft YaHei', 9)).pack(anchor='w', pady=(5, 0))

        concurrent_frame = ttk.Frame(parent)
        concurrent_frame.pack(fill='x', pady=5)

        self.concurrent_var = tk.IntVar(value=settings.max_concurrent)
        self.concurrent_spinbox = tk.Spinbox(
            concurrent_frame,
            from_=1,
            to=10,
            textvariable=self.concurrent_var,
            width=5,
            command=self._on_concurrent_changed
        )
        self.concurrent_spinbox.pack(side='left')

        ttk.Label(concurrent_frame, text="个", font=('Microsoft YaHei', 9)).pack(side='left', padx=5)

        ttk.Label(
            parent,
            text="注: 每个进程先完成当前任务再开始下一个",
            font=('Microsoft YaHei', 8),
            foreground='gray'
        ).pack(anchor='w', pady=(0, 5))

        # 每次加载视频数设置
        ttk.Label(parent, text="每次加载视频数 (10-50):", font=('Microsoft YaHei', 9)).pack(anchor='w', pady=(10, 0))

        page_size_frame = ttk.Frame(parent)
        page_size_frame.pack(fill='x', pady=5)

        self.page_size_var = tk.IntVar(value=settings.page_size)
        self.page_size_spinbox = tk.Spinbox(
            page_size_frame,
            from_=10,
            to=50,
            textvariable=self.page_size_var,
            width=5,
            command=self._on_page_size_changed
        )
        self.page_size_spinbox.pack(side='left')

        ttk.Label(page_size_frame, text="个", font=('Microsoft YaHei', 9)).pack(side='left', padx=5)

        ttk.Label(
            parent,
            text="注: 控制稍后再看列表每次显示的视频数量（滚动加载更多）",
            font=('Microsoft YaHei', 8),
            foreground='gray'
        ).pack(anchor='w', pady=(0, 5))

        # 窗口行为设置
        ttk.Separator(parent, orient='horizontal').pack(fill='x', pady=15)
        ttk.Label(parent, text="窗口设置:", font=('Microsoft YaHei', 9, 'bold')).pack(anchor='w', pady=(10, 5))

        self.minimize_to_tray_var = tk.BooleanVar(value=settings.minimize_to_tray)
        ttk.Checkbutton(
            parent,
            text="点击×最小化到托盘",
            variable=self.minimize_to_tray_var,
            command=self._on_minimize_to_tray_changed
        ).pack(anchor='w', pady=5)

        ttk.Label(
            parent,
            text="开启后点击关闭按钮会最小化到系统托盘",
            font=('Microsoft YaHei', 8),
            foreground='gray'
        ).pack(anchor='w')

        # 保存按钮
        ttk.Button(
            parent,
            text="保存设置",
            command=self._save_settings
        ).pack(fill='x', pady=20)

    def _build_statusbar(self):
        """构建状态栏"""
        statusbar = ttk.Frame(self.root, relief='sunken', padding="5")
        statusbar.pack(fill='x', side='bottom')

        ttk.Label(
            statusbar,
            textvariable=self.status_var,
            font=('Microsoft YaHei', 9)
        ).pack(side='left')

        ttk.Separator(statusbar, orient='vertical').pack(side='right', fill='y', padx=10)

        ttk.Label(
            statusbar,
            text="Bilibili Downloader v1.0",
            font=('Microsoft YaHei', 8)
        ).pack(side='right')

    def _setup_event_handlers(self):
        """设置事件处理器"""
        self.event_bus.subscribe('download.created', self._on_status_update)
        self.event_bus.subscribe('download.completed', self._on_status_update)
        self.event_bus.subscribe('download.error', self._on_download_error)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_status_update(self, data):
        """状态更新"""
        task_id = data.get('task_id', '')
        self.root.after(0, lambda: self.status_var.set(f"任务 {task_id[:8]}... 更新"))

    def _on_download_error(self, data):
        """下载错误"""
        error = data.get('error', '未知错误')
        self.root.after(0, lambda: messagebox.showerror("下载错误", f"任务失败: {error}"))

    def _check_saved_login(self):
        """检查保存的登录状态"""
        if self.auth_service.load_cookies():
            self.run_async(self._do_check_login())

    def _load_saved_tasks(self):
        """加载保存的下载任务"""
        try:
            from ..services.task_persistence import get_task_persistence
            persistence = get_task_persistence()
            tasks = persistence.load_tasks()

            if tasks:
                # 更新状态管理器但不触发保存（避免循环）
                from dataclasses import replace
                self.state_manager.update(
                    lambda s: replace(s, download_tasks=tuple(tasks)),
                    save=False
                )
                logger.info(f"已加载 {len(tasks)} 个保存的任务")
                self.status_var.set(f"已恢复 {len(tasks)} 个下载任务")
        except Exception as e:
            logger.error(f"加载保存的任务失败: {e}")

    async def _do_check_login(self):
        """异步检查登录状态"""
        try:
            status = await self.auth_service.check_login_status()
            if status.is_logged_in:
                self.is_logged_in = True
                self.user_info = status.user_info
                username = status.user_info.name if status.user_info else '未知用户'
                self.root.after(0, lambda: self.login_status_var.set(f"已登录: {username}"))
                self.root.after(0, lambda: self.status_var.set(f"欢迎回来，{username}！"))
            else:
                self.is_logged_in = False
                self.root.after(0, lambda: self.login_status_var.set("未登录"))
                if status.error_message:
                    self.root.after(0, lambda: self.status_var.set(f"登录已过期: {status.error_message}"))
                else:
                    self.root.after(0, lambda: self.status_var.set("请登录"))
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            self.root.after(0, lambda: self.login_status_var.set("未登录"))

    def _check_login_status(self):
        """刷新登录状态（供UI调用）"""
        self.run_async(self._do_check_login())

    def _show_login(self):
        """显示登录对话框"""
        LoginDialog(
            self.root,
            self.auth_service,
            on_login_success=lambda: self.run_async(self._do_check_login())
        )

    def _logout(self):
        """注销登录"""
        if messagebox.askyesno("确认", "确定要注销当前账户吗？"):
            self.auth_service.clear_cookies()
            self.is_logged_in = False
            self.user_info = None
            self.login_status_var.set("未登录")
            self.status_var.set("已注销")

    def _show_new_download(self):
        """显示新建下载对话框"""
        # 创建对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("新建下载")
        dialog.geometry("500x200")
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (250)
        y = (dialog.winfo_screenheight() // 2) - (100)
        dialog.geometry(f'+{x}+{y}')

        # URL输入
        ttk.Label(dialog, text="视频地址:", font=('Microsoft YaHei', 10)).pack(pady=10)

        url_var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=url_var, width=50)
        entry.pack(pady=5, padx=20)
        entry.focus()

        def start_download():
            url = url_var.get().strip()
            if not url:
                messagebox.showwarning("提示", "请输入视频地址", parent=dialog)
                return

            # 创建下载任务
            self.run_async(self._create_download_task(url, source="url"))
            dialog.destroy()

        # 按钮
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=20)

        ttk.Button(btn_frame, text="开始下载", command=start_download).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side='left', padx=5)

        # 回车确认
        dialog.bind('<Return>', lambda e: start_download())

    def _show_favorite_download(self):
        """显示收藏夹下载对话框"""
        if not self.is_logged_in:
            if messagebox.askyesno("提示", "需要先登录才能访问收藏夹，是否立即登录？"):
                self._show_login()
            return

        FavoriteDialog(
            self.root,
            self.auth_service,
            on_download=self._on_favorite_selected
        )

    def _show_cheese_download(self):
        """显示课程下载对话框"""
        CheeseDialog(
            self.root,
            self.auth_service,
            on_download=self._on_cheese_selected
        )

    def _show_watch_later_download(self):
        """显示稍后再看下载对话框"""
        if not self.is_logged_in:
            if messagebox.askyesno("提示", "需要先登录才能访问稍后再看，是否立即登录？"):
                self._show_login()
            return

        WatchLaterDialog(
            self.root,
            self.auth_service,
            on_download=self._on_watch_later_selected
        )

    def _on_watch_later_selected(self, videos, source_name: str, source_id: str):
        """稍后再看选择回调"""
        count = len(videos)
        if count == 0:
            messagebox.showinfo("提示", "列表为空")
            return

        # 批量创建任务（轻量级，不卡顿）
        self.run_async(self._batch_create_tasks(
            videos, "watch_later", source_name, source_id
        ))

    def _on_favorite_selected(self, videos, favorite_name: str, favorite_id: str):
        """收藏夹选择回调"""
        count = len(videos)
        if count == 0:
            messagebox.showinfo("提示", "收藏夹为空")
            return

        if messagebox.askyesno("确认", f"收藏夹共有 {count} 个视频，是否开始下载？"):
            # 批量创建任务（轻量级，不卡顿）
            self.run_async(self._batch_create_tasks(
                videos, "favorite", favorite_name, favorite_id
            ))

    async def _batch_create_tasks(self, videos, source: str, source_name: str, source_id: str):
        """批量创建任务（轻量级，不解析视频信息，批量添加避免卡顿）

        创建任务时只保存URL，视频解析延迟到下载时进行，自动跳过重复任务
        """
        from ..models.download import DownloadTask, TaskStatus
        from ..utils.path_utils import sanitize_filename
        import uuid
        import os

        quality = self.quality_var.get()
        settings = get_settings()
        state_manager = get_state_manager()
        total = len(videos)
        created = 0
        skipped = 0
        failed = 0

        self.status_var.set(f"正在创建 {source_name} 的任务 (0/{total})...")

        # 先收集所有任务
        tasks_to_add = []
        task_ids = []

        for video in videos:
            try:
                if hasattr(video, 'bvid') and video.bvid:
                    url = f"https://www.bilibili.com/video/{video.bvid}"
                    video_title = getattr(video, 'title', video.bvid)
                elif hasattr(video, 'aid') and video.aid:
                    url = f"https://www.bilibili.com/video/av{video.aid}"
                    video_title = getattr(video, 'title', str(video.aid))
                else:
                    failed += 1
                    continue

                # 检查重复任务
                existing_task = self.download_service._is_duplicate_task(url, source, source_name)
                if existing_task:
                    skipped += 1
                    task_ids.append(existing_task.task_id)  # 使用已有任务
                    continue

                # 生成下载路径
                safe_title = sanitize_filename(video_title)[:50]
                output_path = os.path.join(settings.download_path, f"{safe_title}.mp4")

                # 检查文件是否已存在
                counter = 1
                original_output_path = output_path
                while os.path.exists(output_path):
                    base, ext = os.path.splitext(original_output_path)
                    output_path = f"{base}_{counter}{ext}"
                    counter += 1

                # 创建任务（延迟解析，video=None）
                task_id = str(uuid.uuid4())[:8]
                task = DownloadTask(
                    task_id=task_id,
                    video=None,  # 延迟解析
                    status=TaskStatus.PENDING,
                    progress=0.0,
                    download_path=output_path,
                    quality=quality,
                    source=source,
                    source_name=source_name,
                    source_id=source_id,
                    url=url,  # 保存URL用于延迟解析
                )

                tasks_to_add.append(task)
                task_ids.append(task_id)
                created += 1

                # 更新进度
                if created % 50 == 0:
                    self.root.after(0, lambda c=created, t=total, sn=source_name:
                        self.status_var.set(f"正在准备 {sn} 的任务 ({c}/{t})..."))

            except Exception as e:
                failed += 1
                logger.error(f"创建任务失败: {e}")

        # 一次性批量添加所有任务（不通知监听器）
        if tasks_to_add:
            state_manager.bulk_update(
                lambda s: s.with_tasks(tasks_to_add),
                save=True,
                notify=False
            )

        # 触发一次UI刷新
        self.root.after(0, lambda: state_manager.notify_listeners())

        # 发布批量创建事件
        self.event_bus.publish('download.batch_created', {
            'count': len(task_ids),
            'source': source,
            'source_name': source_name
        })

        # 完成提示
        status_text = f"已创建 {created} 个任务"
        if skipped > 0:
            status_text += f"，跳过 {skipped} 个重复"
        if failed > 0:
            status_text += f"，{failed} 个失败"
        self.status_var.set(status_text)

        # 批量开始下载（带延迟）
        for i, task_id in enumerate(task_ids):
            self.run_async(self.download_service.start_download(task_id))
            if (i + 1) % 10 == 0:
                await asyncio.sleep(0.1)

    async def _batch_create_cheese_tasks_async(self, episodes, course_title: str):
        """异步批量创建课程任务，批量添加避免UI卡顿，自动跳过重复"""
        from ..config.settings import get_settings
        from ..models.download import DownloadTask, TaskStatus
        from ..core.state_manager import get_state_manager
        from ..utils.path_utils import sanitize_filename
        import uuid

        quality = self.quality_var.get()
        settings = get_settings()
        state_manager = get_state_manager()
        total = len(episodes)
        created = 0
        skipped = 0
        failed = 0

        self.root.after(0, lambda: self.status_var.set(f"正在创建课程「{course_title}」的任务 (0/{total})..."))

        # 构建下载路径 - 使用课程名称作为子文件夹
        safe_course = sanitize_filename(course_title)
        course_path = os.path.join(settings.download_path, safe_course)
        os.makedirs(course_path, exist_ok=True)

        # 先收集所有任务，避免频繁UI更新
        tasks_to_add = []
        task_ids = []

        for video in episodes:
            try:
                # 构建视频URL用于重复检测
                if hasattr(video, 'bvid') and video.bvid:
                    url = f"https://www.bilibili.com/video/{video.bvid}"
                else:
                    url = ""

                # 检查重复任务（课程使用cheese source + course_title）
                existing_task = self.download_service._is_duplicate_task(url, "cheese", course_title)
                if existing_task:
                    skipped += 1
                    task_ids.append(existing_task.task_id)  # 使用已有任务
                    continue

                # 生成文件名
                safe_title = sanitize_filename(video.title)
                output_path = os.path.join(course_path, f"{safe_title}.mp4")

                # 检查文件是否已存在
                counter = 1
                original_output_path = output_path
                while os.path.exists(output_path):
                    base, ext = os.path.splitext(original_output_path)
                    output_path = f"{base}_{counter}{ext}"
                    counter += 1

                # 创建任务（不立即添加到state_manager）
                task_id = str(uuid.uuid4())[:8]
                task = DownloadTask(
                    task_id=task_id,
                    video=video,
                    status=TaskStatus.PENDING,
                    progress=0.0,
                    download_path=output_path,
                    quality=quality,
                    source="cheese",
                    source_name=course_title,
                    source_id=None,
                    url=url,  # 保存URL用于重复检测
                )

                tasks_to_add.append(task)
                task_ids.append(task_id)
                created += 1

            except Exception as e:
                failed += 1
                logger.error(f"创建课程任务失败: {e}")

            # 更新进度（不刷新UI，只更新状态栏）
            if (created + skipped) % 50 == 0:
                self.root.after(0, lambda c=created+skipped, t=total, ct=course_title:
                    self.status_var.set(f"正在准备课程「{ct}」的任务 ({c}/{t})..."))

        # 一次性批量添加所有任务到state_manager（不通知监听器）
        if tasks_to_add:
            state_manager.bulk_update(
                lambda s: s.with_tasks(tasks_to_add),
                save=True,
                notify=False
            )

        # 触发一次UI刷新
        self.root.after(0, lambda: state_manager.notify_listeners())

        # 发布批量创建事件
        self.event_bus.publish('download.batch_created', {
            'count': len(task_ids),
            'source': 'cheese',
            'source_name': course_title
        })

        status_text = f"已创建课程「{course_title}」{created} 个任务"
        if skipped > 0:
            status_text += f"，跳过 {skipped} 个重复"
        if failed > 0:
            status_text += f"，{failed} 个失败"
        self.root.after(0, lambda: self.status_var.set(status_text))

        # 批量开始下载（使用延迟启动，避免同时开始太多）
        for i, task_id in enumerate(task_ids):
            self.run_async(self.download_service.start_download(task_id))
            # 每10个任务稍微延迟一下，避免瞬间发起太多请求
            if (i + 1) % 10 == 0:
                await asyncio.sleep(0.1)

    def _batch_create_cheese_tasks(self, episodes, course_title: str):
        """批量创建课程任务（入口方法）"""
        self.run_async(self._batch_create_cheese_tasks_async(episodes, course_title))

    def _on_cheese_selected(self, episodes, course_title):
        """课程选择回调"""
        count = len(episodes)
        if count == 0:
            messagebox.showinfo("提示", "课程为空")
            return

        if messagebox.askyesno("确认", f"课程「{course_title}」共有 {count} 个视频，是否开始下载？"):
            # 记录课程下载历史（集合记录）
            try:
                from ..models.download_history import get_download_history
                from ..config.settings import get_settings
                from ..utils.path_utils import sanitize_filename

                settings = get_settings()
                history = get_download_history()

                # 构建课程下载目录路径
                safe_course = sanitize_filename(course_title)
                course_path = os.path.join(settings.download_path, safe_course)

                # 添加课程集合记录
                history.add_course_record(
                    course_title=course_title,
                    episodes_count=count,
                    download_dir=course_path,
                    quality=self.quality_var.get()
                )
            except Exception as e:
                logger.error(f"记录课程历史失败: {e}")

            # 批量创建课程任务（轻量级，不卡顿）
            self._batch_create_cheese_tasks(episodes, course_title)

    async def _create_cheese_download_task(self, video, course_title: str, quality: Optional[int] = None):
        """创建课程视频下载任务"""
        try:
            from ..config.settings import get_settings
            from ..models.download import DownloadTask, TaskStatus
            from ..core.state_manager import get_state_manager
            from ..utils.path_utils import sanitize_filename
            import uuid

            settings = get_settings()
            state_manager = get_state_manager()

            if quality is None:
                quality = self.quality_var.get()

            # 构建下载路径 - 使用课程名称作为子文件夹
            safe_course = sanitize_filename(course_title)
            course_path = os.path.join(settings.download_path, safe_course)
            os.makedirs(course_path, exist_ok=True)

            # 生成文件名
            safe_title = sanitize_filename(video.title)
            output_path = os.path.join(course_path, f"{safe_title}.mp4")

            # 检查文件是否已存在
            counter = 1
            original_output_path = output_path
            while os.path.exists(output_path):
                base, ext = os.path.splitext(original_output_path)
                output_path = f"{base}_{counter}{ext}"
                counter += 1

            # 创建任务，添加课程来源信息（只创建，不自动开始）
            task_id = str(uuid.uuid4())[:8]
            task = DownloadTask(
                task_id=task_id,
                video=video,
                status=TaskStatus.PENDING,
                progress=0.0,
                download_path=output_path,
                quality=quality,
                source="cheese",
                source_name=course_title,
                source_id=None,
            )

            state_manager.update(lambda s: s.with_task(task))
            self.event_bus.publish('download.created', {'task_id': task_id})

            self.root.after(0, lambda: self.status_var.set(f"课程任务创建成功: {task_id}"))

            # 不自动开始下载，让用户手动点击开始按钮
            # await self.download_service.start_download(task_id)

        except Exception as e:
            logger.error(f"创建课程下载任务失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("错误", f"创建课程任务失败: {e}"))

    async def _create_download_task(self, url: str, quality: Optional[int] = None,
                                   source: str = "url",
                                   source_name: Optional[str] = None,
                                   source_id: Optional[str] = None):
        """创建下载任务"""
        try:
            if quality is None:
                quality = self.quality_var.get()

            logger.info(f"[主窗口] 创建任务: quality={quality}, auto_quality={self.auto_quality_var.get()}")
            self.root.after(0, lambda: self.status_var.set(f"正在解析: {url[:50]}..."))
            task_id = await self.download_service.create_download_task(
                url, quality,
                source=source,
                source_name=source_name,
                source_id=source_id
            )

            self.root.after(0, lambda: self.status_var.set(f"任务创建成功: {task_id}"))

            # 自动开始下载
            await self.download_service.start_download(task_id)

        except Exception as e:
            logger.error(f"创建下载任务失败: {e}")
            self.root.after(0, lambda: messagebox.showerror("错误", f"创建任务失败: {e}"))
            self.root.after(0, lambda: self.status_var.set("创建任务失败"))

    def _start_all(self):
        """开始所有待下载任务"""
        state = self.state_manager.get_state()
        for task in state.download_tasks:
            if task.status in [TaskStatus.PENDING, TaskStatus.PAUSED, TaskStatus.FAILED]:
                self.run_async(self.download_service.start_download(task.task_id))

        self.status_var.set("已开始所有任务")

    def _pause_all(self):
        """暂停所有下载中或等待中的任务"""
        state = self.state_manager.get_state()
        paused_count = 0
        for task in state.download_tasks:
            # 暂停下载中或等待中的任务
            if task.status in [TaskStatus.DOWNLOADING, TaskStatus.PENDING]:
                self.download_service.pause_download(task.task_id)
                paused_count += 1

        self.status_var.set(f"已暂停 {paused_count} 个任务")

    def _set_download_path(self):
        """设置下载路径"""
        path = filedialog.askdirectory(title="选择下载目录")
        if path:
            self.path_var.set(path)
            self._save_settings()

    def _on_auto_quality_changed(self):
        """自动质量选项改变"""
        self._update_quality_radio_state()
        self._save_settings()

    def _on_concurrent_changed(self):
        """并发数改变"""
        try:
            value = int(self.concurrent_var.get())
            if 1 <= value <= 10:
                self.run_async(self.download_service.set_max_concurrent(value))
                self._save_settings()
                self.status_var.set(f"并发下载数已设置为 {value}")
        except ValueError:
            pass

    def _on_page_size_changed(self):
        """每次加载视频数改变"""
        try:
            value = int(self.page_size_var.get())
            if 10 <= value <= 50:
                self._save_settings()
                self.status_var.set(f"每次加载视频数已设置为 {value}")
        except ValueError:
            pass

    def _update_quality_radio_state(self):
        """更新质量单选按钮状态"""
        auto = self.auto_quality_var.get()
        for radio in self.quality_radios:
            radio.configure(state='disabled' if auto else 'normal')

    def _save_settings(self):
        """保存设置"""
        settings = get_settings()
        settings.download_path = self.path_var.get()
        settings.default_quality = self.quality_var.get()
        settings.auto_quality = self.auto_quality_var.get()
        settings.minimize_to_tray = self.minimize_to_tray_var.get()
        settings.max_concurrent = self.concurrent_var.get()
        settings.page_size = self.page_size_var.get()
        settings.save(settings.get_default_path())
        self.status_var.set("设置已保存")

    def _on_minimize_to_tray_changed(self):
        """最小化到托盘选项改变"""
        self._save_settings()

    def _open_download_dir(self):
        """打开下载目录"""
        path = self.path_var.get()
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showwarning("提示", "目录不存在")

    def _show_history(self):
        """显示下载历史对话框"""
        HistoryDialog(self.root)

    def _check_ffmpeg(self):
        """检查FFmpeg"""
        from ..utils.ffmpeg_utils import check_ffmpeg, check_nvidia_gpu

        if check_ffmpeg():
            gpu_support = check_nvidia_gpu()
            gpu_status = "支持" if gpu_support else "不支持"
            messagebox.showinfo(
                "FFmpeg检查",
                f"✅ FFmpeg已安装\n"
                f"🎮 NVIDIA GPU加速: {gpu_status}"
            )
        else:
            messagebox.showwarning(
                "FFmpeg检查",
                "❌ FFmpeg未安装或未添加到PATH\n"
                "请先安装FFmpeg才能正常下载视频"
            )

    def _show_help(self):
        """显示帮助"""
        help_text = """
使用方法:

1. 登录
   - 点击"登录"按钮，使用B站APP扫码登录
   - 登录后可以访问收藏夹、稍后再看等内容

2. 下载视频
   - 点击"新建下载"，输入视频地址
   - 或从"收藏夹下载"批量下载
   - 或从"稍后再看"下载之前收藏的视频

3. 管理下载
   - 在下载管理面板查看进度
   - 支持暂停、继续、删除任务

4. 设置
   - 在右侧面板设置下载路径
   - 选择默认视频质量

注意:
- 下载需要安装FFmpeg
- 部分视频需要登录才能下载
- 稍后再看功能需要登录后使用
        """
        messagebox.showinfo("使用说明", help_text)

    def _show_about(self):
        """显示关于"""
        messagebox.showinfo(
            "关于",
            "Bilibili Downloader v1.0\n\n"
            "一个简洁的B站视频下载工具\n"
            "支持普通视频、番剧、课程下载\n\n"
            "Made with ❤️ by Claude"
        )

    def _on_close(self):
        """关闭窗口"""
        settings = get_settings()
        if settings.minimize_to_tray:
            # 最小化到托盘
            self._minimize_to_tray()
        else:
            # 检查是否有活跃的下载任务
            state = self.state_manager.get_state()
            active_tasks = [
                t for t in state.download_tasks
                if t.status in [TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED]
            ]

            # 只有在有活跃任务时才提示
            if active_tasks:
                if messagebox.askyesno("确认", f"确定要退出吗？当前有 {len(active_tasks)} 个下载任务将被暂停。"):
                    self._do_exit()
            else:
                # 没有活跃任务，直接退出
                self._do_exit()

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        try:
            # 尝试导入 pystray，如果没有则提示用户
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            messagebox.showwarning(
                "提示",
                "最小化到托盘需要安装 pystray 和 pillow\n"
                "请运行: pip install pystray pillow\n\n"
                "将直接退出程序。"
            )
            self._do_exit()
            return

        # 隐藏主窗口
        self.root.withdraw()

        # 创建托盘图标
        def create_icon():
            # 创建一个简单的图标
            width = 64
            height = 64
            image = Image.new('RGB', (width, height), 'white')
            dc = ImageDraw.Draw(image)
            dc.rectangle([0, 0, width, height], fill='#00a1d6')
            dc.text((20, 20), 'B', fill='white')
            return image

        def on_show(icon, item):
            """显示主窗口"""
            self.root.after(0, self.root.deiconify)
            icon.stop()
            self.tray_icon = None

        def on_exit(icon, item):
            """退出程序"""
            icon.stop()
            self._do_exit()

        # 创建托盘菜单
        menu = pystray.Menu(
            pystray.MenuItem("显示", on_show),
            pystray.MenuItem("退出", on_exit)
        )

        # 创建托盘图标
        self.tray_icon = pystray.Icon("bilibili_downloader", create_icon(), "Bilibili下载器", menu)

        # 在后台线程运行托盘图标
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _do_exit(self):
        """真正退出程序"""
        # 停止下载管理器的刷新
        if hasattr(self, 'download_manager'):
            self.download_manager.stop_refresh()

        # 停止事件循环
        self.stop()

        # 保存设置
        self._save_settings()

        self.root.destroy()

    def run(self):
        """运行应用"""
        self.root.mainloop()


def main():
    """主入口"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 创建并运行主窗口
    app = MainWindow()
    app.run()


if __name__ == '__main__':
    main()
