"""课程下载对话框"""
import asyncio
import logging
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable, List

try:
    from bilibili_api import cheese
except ImportError:
    cheese = None

from ..api.auth_service import AuthService
from ..models.video import VideoInfo, VideoPage, VideoType

logger = logging.getLogger(__name__)


class CheeseDialog:
    """课程下载对话框 - 输入课程ID下载"""

    def __init__(
        self,
        parent: tk.Tk,
        auth_service: AuthService,
        on_download: Optional[Callable[[List[VideoInfo], str], None]] = None
    ):
        self.parent = parent
        self.auth_service = auth_service
        self.on_download = on_download

        # 创建窗口
        self.window = tk.Toplevel(parent)
        self.window.title("课程号下载")
        self.window.geometry("700x600")
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.grab_set()

        self._center_window()

        # 数据
        self.episodes: List[VideoInfo] = []
        self.course_title: str = ""

        # 创建事件循环
        self._loop = asyncio.new_event_loop()
        self._loop_thread: Optional[threading.Thread] = None
        self._start_loop()

        self._build_ui()

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
        width = 700
        height = 600
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _build_ui(self):
        """构建UI"""
        # 主容器
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill='both', expand=True)

        # 标题
        ttk.Label(
            main_frame,
            text="📚 课程号下载",
            font=('Microsoft YaHei', 14, 'bold')
        ).pack(pady=10)

        # 说明
        ttk.Label(
            main_frame,
            text="请输入课程号（如: ss360 或 ep1234）",
            font=('Microsoft YaHei', 10)
        ).pack(pady=5)

        ttk.Label(
            main_frame,
            text="课程地址格式: https://www.bilibili.com/cheese/play/ss360",
            font=('Microsoft YaHei', 9),
            foreground='gray'
        ).pack(pady=2)

        # 输入框
        input_frame = ttk.Frame(main_frame)
        input_frame.pack(fill='x', pady=10)

        ttk.Label(input_frame, text="课程号:").pack(side='left', padx=5)
        self.cheese_id_var = tk.StringVar()
        self.id_entry = ttk.Entry(input_frame, textvariable=self.cheese_id_var, width=30)
        self.id_entry.pack(side='left', padx=5)
        self.id_entry.focus()

        ttk.Button(
            input_frame,
            text="获取课程",
            command=self._fetch_course
        ).pack(side='left', padx=5)

        # 课程信息 - 紧凑布局
        info_frame = ttk.Frame(main_frame)
        info_frame.pack(fill='x', pady=5)

        self.course_title_var = tk.StringVar(value="未获取")
        ttk.Label(info_frame, text="课程:").pack(side='left', padx=5)
        ttk.Label(info_frame, textvariable=self.course_title_var, font=('Microsoft YaHei', 9, 'bold')).pack(side='left', padx=5)

        self.episode_count_var = tk.StringVar(value="0")
        ttk.Label(info_frame, text="集数:").pack(side='left', padx=(20, 5))
        ttk.Label(info_frame, textvariable=self.episode_count_var).pack(side='left', padx=5)

        # 集数列表 - 不扩展，只填充x方向
        list_frame = ttk.LabelFrame(main_frame, text="课程列表", padding="5", height=200)
        list_frame.pack(fill='x', pady=5)
        list_frame.pack_propagate(False)

        # Treeview
        columns = ('index', 'title')
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show='headings',
            selectmode='extended'
        )

        self.tree.heading('#0', text='序号')
        self.tree.column('#0', width=50, anchor='center')

        self.tree.heading('index', text='索引')
        self.tree.column('index', width=50, anchor='center')

        self.tree.heading('title', text='标题')
        self.tree.column('title', width=300, anchor='w')

        # 滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 状态标签（先pack，会在底部按钮上方）
        self.status_var = tk.StringVar(value="请输入课程号并点击获取")
        ttk.Label(
            main_frame,
            textvariable=self.status_var,
            font=('Microsoft YaHei', 9)
        ).pack(pady=5, side='bottom')

        # 按钮栏（后pack，会在最底部）
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill='x', pady=10, side='bottom')

        self.download_all_btn = ttk.Button(
            btn_frame,
            text="下载全部",
            command=self._download_all,
            state='disabled'
        )
        self.download_all_btn.pack(side='left', padx=5)

        self.download_selected_btn = ttk.Button(
            btn_frame,
            text="下载选中",
            command=self._download_selected,
            state='disabled'
        )
        self.download_selected_btn.pack(side='left', padx=5)

        ttk.Button(
            btn_frame,
            text="取消",
            command=self.close
        ).pack(side='right', padx=5)

    async def _fetch_course_async(self):
        """异步获取课程信息"""
        cheese_id = self.cheese_id_var.get().strip()
        if not cheese_id:
            self.window.after(0, lambda: messagebox.showwarning("提示", "请输入课程号", parent=self.window))
            return

        self.window.after(0, lambda: self.status_var.set("正在获取课程信息..."))

        try:
            credential = self.auth_service.get_credential() if self.auth_service else None

            # 解析ID
            season_id = None
            if cheese_id.startswith('ss'):
                season_id = int(cheese_id[2:])
                cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)
            elif cheese_id.startswith('ep'):
                ep_id = int(cheese_id[2:])
                cheese_list = cheese.CheeseList(ep_id=ep_id, credential=credential)
            else:
                # 尝试直接作为season_id
                season_id = int(cheese_id)
                cheese_list = cheese.CheeseList(season_id=season_id, credential=credential)

            # 获取课程信息
            meta = await cheese_list.get_meta()
            self.course_title = meta.get('title', '未知课程')

            # 获取season_id（如果从ep_id创建的，需要从meta中获取）
            if season_id is None:
                season_id = meta.get('season_id')

            # 获取课程列表
            episodes = await cheese_list.get_list()

            # 转换为VideoInfo列表
            self.episodes = []
            for i, ep in enumerate(episodes):
                # 获取每一集的详细信息
                ep_meta = await ep.get_meta()
                ep_title = ep_meta.get('title', f'第{i+1}节')

                video_info = VideoInfo(
                    bvid=f"cheese{season_id}",
                    cid=ep.get_epid(),
                    aid=0,
                    title=f"{i+1}. {ep_title}",
                    description=meta.get('summary', ''),
                    duration=0,
                    owner={'mid': 0, 'name': meta.get('up_name', '哔哩哔哩课堂'), 'face': ''},
                    pages=[VideoPage(cid=ep.get_epid(), page=1, title=ep_title, duration=0)],
                    video_type=VideoType.CHEESE,
                    cover_url=meta.get('cover', ''),
                    pub_date=None,
                    stat={},
                    is_charge=True,
                    qualities=[]
                )
                self.episodes.append(video_info)

            # 更新UI
            self.window.after(0, self._update_course_info)

        except Exception as err:
            error_msg = str(err)
            logger.error(f"获取课程失败: {error_msg}")
            self.window.after(0, lambda msg=error_msg: self.status_var.set(f"获取失败: {msg}"))
            self.window.after(0, lambda msg=error_msg: messagebox.showerror("错误", f"无法获取课程: {msg}", parent=self.window))

    def _update_course_info(self):
        """更新课程信息到UI"""
        self.course_title_var.set(self.course_title)
        self.episode_count_var.set(str(len(self.episodes)))
        self.status_var.set(f"获取成功: {self.course_title}")

        # 清空列表
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 添加集数
        for i, ep in enumerate(self.episodes):
            self.tree.insert(
                '',
                'end',
                text=str(i + 1),
                values=(i + 1, ep.title),
                tags=(str(i),)
            )

        # 启用下载按钮
        if self.episodes:
            self.download_all_btn.configure(state='normal')
            self.download_selected_btn.configure(state='normal')

    def _get_selected_indices(self) -> List[int]:
        """获取选中的索引"""
        selected = self.tree.selection()
        indices = []
        for item in selected:
            tags = self.tree.item(item, 'tags')
            if tags:
                indices.append(int(tags[0]))
        return indices

    def _download_all(self):
        """下载全部"""
        if not self.episodes:
            messagebox.showwarning("提示", "请先获取课程信息", parent=self.window)
            return

        if messagebox.askyesno("确认", f"确定要下载全部 {len(self.episodes)} 个视频吗？", parent=self.window):
            self._start_download(self.episodes)

    def _download_selected(self):
        """下载选中"""
        indices = self._get_selected_indices()
        if not indices:
            messagebox.showwarning("提示", "请先选择要下载的视频", parent=self.window)
            return

        selected_episodes = [self.episodes[i] for i in indices if 0 <= i < len(self.episodes)]

        if messagebox.askyesno("确认", f"确定要下载选中的 {len(selected_episodes)} 个视频吗？", parent=self.window):
            self._start_download(selected_episodes)

    def _start_download(self, episodes: List[VideoInfo]):
        """开始下载"""
        if self.on_download:
            try:
                self.on_download(episodes, self.course_title)
            except Exception as e:
                logger.error(f"下载回调失败: {e}")

        self.window.after(0, self.close)

    def _fetch_course(self):
        """获取课程（UI调用入口）"""
        self._run_async(self._fetch_course_async())

    def close(self):
        """关闭对话框"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self.window.destroy()
