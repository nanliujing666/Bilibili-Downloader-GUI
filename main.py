#!/usr/bin/env python3
"""
Bilibili 视频下载器 GUI

功能特点：
- 支持普通视频、番剧、课程下载
- 二维码登录
- 收藏夹批量下载
- 多线程下载
- FFmpeg音视频合并
- GPU加速支持（NVIDIA）

使用方法：
    python main.py

依赖安装：
    pip install -r requirements.txt
"""

import sys
import os

# 添加src到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.gui.main_window import main

if __name__ == '__main__':
    main()
