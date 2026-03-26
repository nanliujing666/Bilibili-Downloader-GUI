"""下载历史记录对话框"""
import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable
from datetime import datetime

from ..models.download_history import get_download_history, DownloadHistoryItem


class HistoryDialog:
    """下载历史记录查看对话框"""

    def __init__(self, parent: tk.Tk, on_open_video: Optional[Callable[[str], None]] = None):
        self.parent = parent
        self.on_open_video = on_open_video

        # 创建窗口
        self.window = tk.Toplevel(parent)
        self.window.title("下载历史")
        self.window.geometry("900x600")
        self.window.resizable(True, True)
        self.window.transient(parent)
        self.window.grab_set()

        self._center_window()

        # 历史记录管理器
        self.history = get_download_history()

        self._build_ui()
        self._load_history()

    def _center_window(self):
        """居中窗口"""
        self.window.update_idletasks()
        width = 900
        height = 600
        x = (self.window.winfo_screenwidth() // 2) - (width // 2)
        y = (self.window.winfo_screenheight() // 2) - (height // 2)
        self.window.geometry(f'{width}x{height}+{x}+{y}')

    def _format_datetime(self, dt: datetime) -> str:
        """格式化日期时间"""
        return dt.strftime("%Y-%m-%d %H:%M")

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        if size >= 1024 * 1024 * 1024:
            return f"{size / 1024 / 1024 / 1024:.2f} GB"
        elif size >= 1024 * 1024:
            return f"{size / 1024 / 1024:.2f} MB"
        elif size >= 1024:
            return f"{size / 1024:.2f} KB"
        else:
            return f"{size} B"

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

    def _get_source_display(self, item: DownloadHistoryItem) -> str:
        """获取来源显示文本"""
        source_map = {
            'url': '直接下载',
            'favorite': f'收藏夹: {item.source_name or ""}',
            'watch_later': '稍后再看',
            'cheese': f'课程: {item.source_name or ""}',
        }
        return source_map.get(item.source, item.source)

    def _build_ui(self):
        """构建UI"""
        # 主容器
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill='both', expand=True)

        # 标题
        ttk.Label(
            main_frame,
            text="📜 下载历史",
            font=('Microsoft YaHei', 14, 'bold')
        ).pack(pady=5)

        # 说明
        ttk.Label(
            main_frame,
            text="双击记录可打开视频文件",
            font=('Microsoft YaHei', 9),
            foreground='gray'
        ).pack(pady=2)

        # 历史记录列表
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill='both', expand=True, pady=10)

        # Treeview
        columns = ('title', 'owner', 'source', 'size', 'date')
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show='headings',
            selectmode='browse'
        )

        # 设置列
        self.tree.heading('title', text='标题')
        self.tree.column('title', width=300, anchor='w')

        self.tree.heading('owner', text='UP主')
        self.tree.column('owner', width=100, anchor='w')

        self.tree.heading('source', text='来源')
        self.tree.column('source', width=150, anchor='w')

        self.tree.heading('size', text='大小')
        self.tree.column('size', width=80, anchor='center')

        self.tree.heading('date', text='下载时间')
        self.tree.column('date', width=120, anchor='center')

        # 滚动条
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 绑定双击事件
        self.tree.bind('<Double-1>', self._on_double_click)

        # 底部按钮栏
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill='x', pady=5)

        # 左侧 - 统计信息
        self.status_var = tk.StringVar(value="加载中...")
        ttk.Label(
            bottom_frame,
            textvariable=self.status_var,
            font=('Microsoft YaHei', 9)
        ).pack(side='left')

        # 右侧 - 操作按钮
        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(side='right')

        ttk.Button(
            btn_frame,
            text="打开文件",
            command=self._open_selected
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="打开所在文件夹",
            command=self._open_folder
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="刷新",
            command=self._load_history
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="清空历史",
            command=self._clear_history
        ).pack(side='left', padx=2)

        ttk.Button(
            btn_frame,
            text="关闭",
            command=self.close
        ).pack(side='left', padx=2)

    def _load_history(self):
        """加载历史记录"""
        # 清空现有数据
        for item in self.tree.get_children():
            self.tree.delete(item)

        # 获取历史记录
        items = self.history.get_all()

        # 添加到列表
        for item in items:
            self.tree.insert('', 'end', values=(
                item.title,
                item.owner_name,
                self._get_source_display(item),
                self._format_size(item.file_size),
                self._format_datetime(item.downloaded_at)
            ), tags=(item.download_path,))

        # 更新状态
        self.status_var.set(f"共 {len(items)} 条记录")

    def _get_selected_path(self) -> Optional[str]:
        """获取选中项的文件路径"""
        selection = self.tree.selection()
        if not selection:
            return None

        item = selection[0]
        tags = self.tree.item(item, 'tags')
        if tags:
            return tags[0]
        return None

    def _on_double_click(self, event):
        """双击打开文件"""
        self._open_selected()

    def _open_selected(self):
        """打开选中的视频文件"""
        path = self._get_selected_path()
        if not path:
            messagebox.showinfo("提示", "请先选择一条记录", parent=self.window)
            return

        if not os.path.exists(path):
            messagebox.showerror("错误", f"文件不存在: {path}", parent=self.window)
            return

        try:
            if os.path.isdir(path):
                # 如果是目录，打开目录
                os.startfile(path)
            else:
                # 如果是文件，打开文件
                os.startfile(path)
        except Exception as e:
            messagebox.showerror("错误", f"打开文件失败: {e}", parent=self.window)

    def _open_folder(self):
        """打开选中文件所在的文件夹"""
        path = self._get_selected_path()
        if not path:
            messagebox.showinfo("提示", "请先选择一条记录", parent=self.window)
            return

        folder = path if os.path.isdir(path) else os.path.dirname(path)

        if not os.path.exists(folder):
            messagebox.showerror("错误", f"文件夹不存在: {folder}", parent=self.window)
            return

        try:
            # 打开文件夹并选中文件
            if os.path.isfile(path):
                subprocess.run(['explorer', '/select,', os.path.normpath(path)])
            else:
                os.startfile(folder)
        except Exception as e:
            messagebox.showerror("错误", f"打开文件夹失败: {e}", parent=self.window)

    def _clear_history(self):
        """清空历史记录"""
        if messagebox.askyesno(
            "确认",
            "确定要清空所有下载历史记录吗？\n(不会删除已下载的文件)",
            parent=self.window
        ):
            self.history.clear()
            self._load_history()

    def close(self):
        """关闭对话框"""
        self.window.destroy()
