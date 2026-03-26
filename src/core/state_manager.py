"""状态管理器 - 集中式不可变状态管理"""
import threading
from dataclasses import dataclass, replace, field
from datetime import datetime
from typing import Callable, List, Dict, Any, Optional
from enum import Enum, auto

# 延迟导入避免循环依赖
def _get_task_persistence():
    from ..services.task_persistence import get_task_persistence
    return get_task_persistence()


import logging
logger = logging.getLogger(__name__)


class AppStatus(Enum):
    """应用状态"""
    IDLE = auto()
    INITIALIZING = auto()
    READY = auto()
    DOWNLOADING = auto()
    ERROR = auto()


@dataclass(frozen=True)
class AppState:
    """
    应用状态 - 不可变
    
    所有状态变更都必须通过StateManager.update()创建新状态
    """
    # 应用状态
    status: AppStatus = AppStatus.IDLE
    status_message: str = "就绪"
    
    # 登录状态
    is_logged_in: bool = False
    user_info: Optional[Dict[str, Any]] = None
    
    # 下载任务
    download_tasks: tuple = field(default_factory=tuple)
    active_downloads: int = 0
    completed_downloads: int = 0
    failed_downloads: int = 0
    
    # 配置
    download_path: str = "./downloads"
    default_quality: int = 80  # 默认1080P
    max_concurrent: int = 3
    
    # 全局错误信息
    error_message: Optional[str] = None
    error_timestamp: Optional[datetime] = None
    
    def with_task(self, task: Any) -> 'AppState':
        """添加新任务"""
        new_tasks = self.download_tasks + (task,)
        return replace(self, download_tasks=new_tasks)
    
    def update_task(self, task_id: str, **kwargs) -> 'AppState':
        """更新任务状态"""
        new_tasks = tuple(
            replace(task, **kwargs) if getattr(task, 'task_id', None) == task_id else task
            for task in self.download_tasks
        )
        return replace(self, download_tasks=new_tasks)
    
    def remove_task(self, task_id: str) -> 'AppState':
        """移除任务"""
        new_tasks = tuple(
            task for task in self.download_tasks
            if getattr(task, 'task_id', None) != task_id
        )
        return replace(self, download_tasks=new_tasks)
    
    def with_error(self, message: str) -> 'AppState':
        """设置错误信息"""
        return replace(
            self,
            status=AppStatus.ERROR,
            error_message=message,
            error_timestamp=datetime.now()
        )
    
    def clear_error(self) -> 'AppState':
        """清除错误信息"""
        return replace(
            self,
            status=AppStatus.READY if self.is_logged_in else AppStatus.IDLE,
            error_message=None,
            error_timestamp=None
        )


# 状态变更监听器类型
StateListener = Callable[[AppState, AppState], None]


class StateManager:
    """
    状态管理器 - 集中管理应用状态
    
    使用不可变状态模式，所有变更都创建新状态对象
    支持状态监听，便于UI自动更新
    """
    
    _instance: 'StateManager' = None
    _instance_lock = threading.Lock()
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._state = AppState()
        self._listeners: List[StateListener] = []
        self._listeners_lock = threading.RLock()
        self._state_lock = threading.RLock()
    
    def get_state(self) -> AppState:
        """
        获取当前状态（只读快照）
        
        Returns:
            当前状态的不可变副本
        """
        with self._state_lock:
            return self._state
    
    def set_state(self, new_state: AppState) -> None:
        """
        设置新状态，通知所有监听器
        
        Args:
            new_state: 新状态
        """
        with self._state_lock:
            old_state = self._state
            self._state = new_state
        
        # 通知监听器（不在锁内执行）
        self._notify_listeners(new_state, old_state)
    
    def update(self, updater: Callable[[AppState], AppState], save: bool = True) -> AppState:
        """
        使用更新函数修改状态

        Args:
            updater: 接收当前状态，返回新状态的函数
            save: 是否保存到文件

        Returns:
            新状态
        """
        with self._state_lock:
            old_state = self._state
            new_state = updater(old_state)
            self._state = new_state

        self._notify_listeners(new_state, old_state)

        # 保存任务到文件（只在任务数量变化时记录日志）
        if save:
            try:
                persistence = _get_task_persistence()
                old_count = len(old_state.download_tasks)
                new_count = len(new_state.download_tasks)
                persistence.save_tasks(list(new_state.download_tasks))
                # 只在任务数量变化时记录日志
                if old_count != new_count:
                    logger.info(f"任务列表已更新: {old_count} -> {new_count}")
            except Exception:
                pass

        return new_state

    def load_tasks(self) -> None:
        """从文件加载任务"""
        try:
            persistence = _get_task_persistence()
            tasks = persistence.load_tasks()
            if tasks:
                self.update(lambda s: replace(s, download_tasks=tuple(tasks)), save=False)
                logger.info(f"从文件加载了 {len(tasks)} 个任务")
        except Exception as e:
            logger.error(f"加载任务失败: {e}")
    
    def subscribe(self, listener: StateListener) -> None:
        """
        订阅状态变更
        
        Args:
            listener: 状态变更监听器，接收(new_state, old_state)两个参数
        """
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)
    
    def unsubscribe(self, listener: StateListener) -> bool:
        """
        取消订阅
        
        Args:
            listener: 要移除的监听器
            
        Returns:
            是否成功移除
        """
        with self._listeners_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
                return True
        return False
    
    def _notify_listeners(self, new_state: AppState, old_state: AppState) -> None:
        """通知所有监听器"""
        with self._listeners_lock:
            listeners = self._listeners.copy()
        
        for listener in listeners:
            try:
                listener(new_state, old_state)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("State listener error")
    
    def clear_listeners(self) -> None:
        """清除所有监听器（谨慎使用）"""
        with self._listeners_lock:
            self._listeners.clear()


def get_state_manager() -> StateManager:
    """获取状态管理器单例"""
    return StateManager()
