"""下载管理面板"""
import asyncio
import logging
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

from ..models.download import DownloadTask
from ..models.enums import TaskStatus
from ..core.state_manager import get_state_manager, AppState
from ..core.event_bus import get_event_bus
from ..services.download_service import DownloadService
from ..utils.format_utils import format_size, format_speed

logger = logging.getLogger(__name__)


class DownloadManager:
    """下载管理面板 - 显示和控制下载任务"""

    def __init__(self, parent: tk.Widget, download_service: DownloadService,
                 async_runner: Optional[Callable] = None):
        self.parent = parent
        self.download_service = download_service
        self.async_runner = async_runner  # 异步任务运行器
        self.state_manager = get_state_manager()
        self.event_bus = get_event_bus()

        # UI组件
        self.tree: Optional[ttk.Treeview] = None
        self.scrollbar: Optional[ttk.Scrollbar] = None

        # 状态标签变量
        self.stats_var = tk.StringVar(value="总任务: 0 | 下载中: 0 | 已完成: 0")

        # 任务ID到tree item的映射
        self._task_items: dict[str, str] = {}

        # 自动刷新
        self._refresh_after_id: Optional[str] = None

        self._build_ui()
        self._setup_event_handlers()
        self._start_refresh_loop()

    def _build_ui(self):
        """构建UI"""
        # 标题栏
        header_frame = ttk.Frame(self.parent)
        header_frame.pack(fill='x', padx=10, pady=5)

        ttk.Label(
            header_frame,
            text="下载任务",
            font=('Microsoft YaHei', 12, 'bold')
        ).pack(side='left')

        # 统计信息
        ttk.Label(
            header_frame,
            textvariable=self.stats_var,
            font=('Microsoft YaHei', 9)
        ).pack(side='right')

        # 按钮栏
        btn_frame = ttk.Frame(self.parent)
        btn_frame.pack(fill='x', padx=10, pady=5)

        ttk.Button(
            btn_frame,
            text="开始",
            command=self._start_selected
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="暂停",
            command=self._pause_selected
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="删除",
            command=self._remove_selected
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="清空已完成",
            command=self._clear_completed
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="清理失效",
            command=self._clear_missing
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="刷新",
            command=self._refresh_list
        ).pack(side='right', padx=2)

        # 任务列表
        list_frame = ttk.Frame(self.parent)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)

        # Treeview
        columns = ('status', 'source', 'progress', 'speed', 'size', 'path')
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show='headings',
            selectmode='extended'
        )

        # 定义列
        self.tree.heading('#0', text='视频标题')
        self.tree.column('#0', width=250, anchor='w')

        self.tree.heading('status', text='状态')
        self.tree.column('status', width=70, anchor='center')

        self.tree.heading('source', text='来源')
        self.tree.column('source', width=80, anchor='center')

        self.tree.heading('progress', text='进度')
        self.tree.column('progress', width=70, anchor='center')

        self.tree.heading('speed', text='速度')
        self.tree.column('speed', width=80, anchor='center')

        self.tree.heading('size', text='大小')
        self.tree.column('size', width=80, anchor='center')

        self.tree.heading('path', text='保存路径')
        self.tree.column('path', width=180, anchor='w')

        # 滚动条
        self.scrollbar = ttk.Scrollbar(
            list_frame,
            orient='vertical',
            command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.scrollbar.set)

        self.tree.pack(side='left', fill='both', expand=True)
        self.scrollbar.pack(side='right', fill='y')

        # 右键菜单
        self._setup_context_menu()

    def _setup_context_menu(self):
        """设置右键菜单"""
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="开始", command=self._start_selected)
        self.context_menu.add_command(label="暂停", command=self._pause_selected)
        self.context_menu.add_command(label="重新下载", command=self._restart_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="打开文件", command=self._open_file)
        self.context_menu.add_command(label="打开目录", command=self._open_directory)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="删除", command=self._remove_selected)

        self.tree.bind('<Button-3>', self._show_context_menu)
        self.tree.bind('<Double-1>', self._on_double_click)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _on_double_click(self, event):
        """双击打开目录"""
        self._open_directory()

    def _setup_event_handlers(self):
        """设置事件处理器"""
        self.event_bus.subscribe('download.created', self._on_download_created)
        self.event_bus.subscribe('download.completed', self._on_download_completed)
        self.event_bus.subscribe('download.error', self._on_download_error)

    def _on_download_created(self, data):
        """下载创建事件"""
        self._refresh_list()

    def _on_download_completed(self, data):
        """下载完成事件"""
        task_id = data.get('task_id')
        if task_id in self._task_items:
            # 使用after确保在主线程更新
            self.parent.after(0, lambda: self._update_item_status(task_id, "已完成"))

    def _on_download_error(self, data):
        """下载错误事件"""
        task_id = data.get('task_id')
        error = data.get('error', '未知错误')
        if task_id in self._task_items:
            self.parent.after(0, lambda: self._update_item_status(task_id, f"失败: {error}"))

    def _update_item_status(self, task_id: str, status: str):
        """更新项目状态"""
        if task_id in self._task_items:
            item = self._task_items[task_id]
            self.tree.set(item, 'status', status)

    def _refresh_list(self):
        """刷新任务列表 - 增量更新，保留选中状态"""
        state = self.state_manager.get_state()

        # 保存当前选中项
        selected_items = set(self.tree.selection())
        selected_task_ids = {
            task_id for task_id, item in self._task_items.items()
            if item in selected_items
        }

        # 获取当前显示的任务ID
        current_task_ids = set(self._task_items.keys())
        new_task_ids = {task.task_id for task in state.download_tasks}

        # 删除已不存在的任务
        for task_id in current_task_ids - new_task_ids:
            if task_id in self._task_items:
                self.tree.delete(self._task_items[task_id])
                del self._task_items[task_id]

        # 统计
        total = len(state.download_tasks)
        downloading = 0
        completed = 0

        # 添加新任务或更新现有任务
        for task in state.download_tasks:
            if task.status == TaskStatus.DOWNLOADING:
                downloading += 1
            elif task.status == TaskStatus.COMPLETED:
                completed += 1

            if task.task_id in self._task_items:
                # 更新现有任务
                self._update_task_in_tree(task)
            else:
                # 添加新任务
                item = self._add_task_to_tree(task)
                self._task_items[task.task_id] = item

        # 恢复选中状态
        for task_id in selected_task_ids:
            if task_id in self._task_items:
                self.tree.selection_add(self._task_items[task_id])

        # 更新统计
        self.stats_var.set(f"总任务: {total} | 下载中: {downloading} | 已完成: {completed}")

    def _format_file_size(self, file_path: str) -> str:
        """格式化文件大小，根据大小智能选择单位"""
        import os
        if not os.path.exists(file_path):
            return "--"

        try:
            size = os.path.getsize(file_path)
            if size >= 1024 ** 3:  # GB
                return f"{size / (1024 ** 3):.2f} GB"
            elif size >= 1024 ** 2:  # MB
                return f"{size / (1024 ** 2):.1f} MB"
            elif size >= 1024:  # KB
                return f"{size / 1024:.1f} KB"
            else:
                return f"{size} B"
        except Exception:
            return "--"

    def _get_source_text(self, task: DownloadTask) -> str:
        """获取来源显示文本"""
        source_map = {
            'url': '直接',
            'favorite': '收藏夹',
            'watch_later': '稍后再看',
            'cheese': '课程',
        }
        return source_map.get(task.source, task.source)

    def _add_task_to_tree(self, task: DownloadTask) -> str:
        """添加任务到Treeview"""
        progress_text = f"{task.progress:.1f}%"
        speed_text = format_speed(task.download_speed)

        # 检查文件是否存在（仅对已完成任务）
        file_exists = True
        if task.status == TaskStatus.COMPLETED:
            import os
            file_exists = os.path.exists(task.download_path)

        # 获取文件大小（实际文件大小优先）
        if file_exists:
            size_text = self._format_file_size(task.download_path)
        else:
            # 使用task记录的file_size
            size_text = format_size(task.file_size)

        # 如果文件不存在，在状态后添加标记
        status_text = task.status_text
        if task.status == TaskStatus.COMPLETED and not file_exists:
            status_text = "文件已删"

        # 获取来源文本
        source_text = self._get_source_text(task)

        path_display = task.download_path
        if len(path_display) > 35:
            path_display = path_display[:35] + '...'

        item = self.tree.insert(
            '',
            'end',
            text=task.video.title[:45] + ('...' if len(task.video.title) > 45 else ''),
            values=(
                status_text,
                source_text,
                progress_text,
                speed_text,
                size_text,
                path_display
            )
        )

        # 根据状态设置颜色
        if task.status == TaskStatus.COMPLETED:
            if file_exists:
                self.tree.tag_configure('completed', foreground='green')
                self.tree.item(item, tags=('completed',))
            else:
                # 文件不存在的已完成任务标红
                self.tree.tag_configure('missing', foreground='red')
                self.tree.item(item, tags=('missing',))
        elif task.status == TaskStatus.FAILED:
            self.tree.tag_configure('failed', foreground='red')
            self.tree.item(item, tags=('failed',))
        elif task.status == TaskStatus.DOWNLOADING:
            self.tree.tag_configure('downloading', foreground='blue')
            self.tree.item(item, tags=('downloading',))

        return item

    def _update_task_in_tree(self, task: DownloadTask) -> None:
        """更新树中的现有任务"""
        if task.task_id not in self._task_items:
            return

        item = self._task_items[task.task_id]
        progress_text = f"{task.progress:.1f}%"
        speed_text = format_speed(task.download_speed)

        # 检查文件是否存在（仅对已完成任务）
        file_exists = True
        if task.status == TaskStatus.COMPLETED:
            import os
            file_exists = os.path.exists(task.download_path)

        # 获取文件大小
        if file_exists:
            size_text = self._format_file_size(task.download_path)
        else:
            size_text = format_size(task.file_size)

        # 状态文本
        status_text = task.status_text
        if task.status == TaskStatus.COMPLETED and not file_exists:
            status_text = "文件已删"

        # 来源文本
        source_text = self._get_source_text(task)

        # 更新值
        self.tree.item(item, values=(
            status_text,
            source_text,
            progress_text,
            speed_text,
            size_text,
            task.download_path[:35] + '...' if len(task.download_path) > 35 else task.download_path
        ))

        # 更新颜色标签
        if task.status == TaskStatus.COMPLETED:
            if file_exists:
                self.tree.item(item, tags=('completed',))
            else:
                self.tree.item(item, tags=('missing',))
        elif task.status == TaskStatus.FAILED:
            self.tree.item(item, tags=('failed',))
        elif task.status == TaskStatus.DOWNLOADING:
            self.tree.item(item, tags=('downloading',))
        else:
            self.tree.item(item, tags=())

    def _start_refresh_loop(self):
        """启动自动刷新循环"""
        self._refresh_list()
        self._refresh_after_id = self.parent.after(1000, self._start_refresh_loop)

    def stop_refresh(self):
        """停止自动刷新"""
        if self._refresh_after_id:
            self.parent.after_cancel(self._refresh_after_id)
            self._refresh_after_id = None

    def _get_selected_task_ids(self) -> list[str]:
        """获取选中的任务ID"""
        selected = self.tree.selection()
        task_ids = []
        for task_id, item in self._task_items.items():
            if item in selected:
                task_ids.append(task_id)
        return task_ids

    def _start_selected(self):
        """开始选中的任务"""
        for task_id in self._get_selected_task_ids():
            if self.async_runner:
                self.async_runner(self.download_service.start_download(task_id))
            else:
                # 如果没有提供runner，尝试直接调用（同步模式）
                logger.warning("异步运行器未设置，无法启动下载")

    def _pause_selected(self):
        """暂停选中的任务"""
        for task_id in self._get_selected_task_ids():
            self.download_service.pause_download(task_id)

    def _restart_selected(self):
        """重新下载选中的任务"""
        for task_id in self._get_selected_task_ids():
            self.download_service.resume_download(task_id)

    def _remove_selected(self):
        """删除选中的任务（支持批量删除任何状态的任务）"""
        task_ids = self._get_selected_task_ids()
        if not task_ids:
            messagebox.showinfo("提示", "请先选择要删除的任务")
            return

        count = len(task_ids)
        if not messagebox.askyesno("确认", f"确定要删除选中的 {count} 个任务吗？"):
            return

        for task_id in task_ids:
            # 如果是正在下载的任务，先取消
            state = self.state_manager.get_state()
            for task in state.download_tasks:
                if task.task_id == task_id and task.status == TaskStatus.DOWNLOADING:
                    self.download_service.cancel_download(task_id)
                    break
            # 直接从状态中移除
            self.state_manager.update(lambda s, tid=task_id: s.remove_task(tid))

        self._refresh_list()
        messagebox.showinfo("完成", f"已删除 {count} 个任务")

    def _clear_completed(self):
        """清空已完成的任务"""
        state = self.state_manager.get_state()
        completed_ids = [
            t.task_id for t in state.download_tasks
            if t.status == TaskStatus.COMPLETED
        ]

        for task_id in completed_ids:
            self.state_manager.update(lambda s, tid=task_id: s.remove_task(tid))

        self._refresh_list()

    def _clear_missing(self):
        """清理文件已不存在的记录"""
        import os

        state = self.state_manager.get_state()
        missing_ids = []

        for task in state.download_tasks:
            # 只检查已完成的任务
            if task.status == TaskStatus.COMPLETED:
                if not os.path.exists(task.download_path):
                    missing_ids.append(task.task_id)

        if not missing_ids:
            messagebox.showinfo("提示", "没有失效的记录")
            return

        if messagebox.askyesno("确认", f"确定要删除 {len(missing_ids)} 条失效记录吗？"):
            for task_id in missing_ids:
                self.state_manager.update(lambda s, tid=task_id: s.remove_task(tid))

            self._refresh_list()
            messagebox.showinfo("完成", f"已清理 {len(missing_ids)} 条失效记录")

    def _open_file(self):
        """打开文件"""
        import os
        import subprocess

        for task_id in self._get_selected_task_ids():
            state = self.state_manager.get_state()
            for task in state.download_tasks:
                if task.task_id == task_id and os.path.exists(task.download_path):
                    try:
                        os.startfile(task.download_path)
                    except Exception as e:
                        logger.error(f"打开文件失败: {e}")
                    break

    def _open_directory(self):
        """打开所在目录"""
        import os
        import subprocess

        for task_id in self._get_selected_task_ids():
            state = self.state_manager.get_state()
            for task in state.download_tasks:
                if task.task_id == task_id:
                    dir_path = os.path.dirname(task.download_path)
                    if os.path.exists(dir_path):
                        try:
                            os.startfile(dir_path)
                        except Exception as e:
                            logger.error(f"打开目录失败: {e}")
                    break
