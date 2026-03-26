"""事件总线 - 线程安全的事件发布/订阅系统"""
import logging
import threading
from typing import Dict, List, Callable, Any

logger = logging.getLogger(__name__)

__all__ = ['EventBus', 'get_event_bus']


class EventBus:
    """
    线程安全的事件总线，用于解耦模块间通信
    
    使用示例:
        bus = EventBus()
        
        # 订阅事件
        bus.subscribe('download.progress', handler)
        
        # 发布事件
        bus.publish('download.progress', {'task_id': 'xxx', 'progress': 50})
        
        # 取消订阅
        bus.unsubscribe('download.progress', handler)
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()
    
    def subscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """
        订阅事件
        
        Args:
            event_type: 事件类型
            handler: 事件处理器函数，接收一个参数(事件数据)
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            # 避免重复订阅
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)
    
    def unsubscribe(self, event_type: str, handler: Callable[[Any], None]) -> bool:
        """
        取消订阅
        
        Args:
            event_type: 事件类型
            handler: 要移除的处理器
            
        Returns:
            是否成功移除
        """
        with self._lock:
            if event_type in self._subscribers:
                if handler in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(handler)
                    # 如果没有订阅者了，删除该事件类型
                    if not self._subscribers[event_type]:
                        del self._subscribers[event_type]
                    return True
        return False
    
    def publish(self, event_type: str, data: Any = None) -> None:
        """
        发布事件（同步，线程安全）
        
        处理器在同一个线程中被调用
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        handlers = []
        
        # 复制处理器列表，避免在遍历时被修改
        with self._lock:
            handlers = self._subscribers.get(event_type, []).copy()
        
        # 调用所有处理器（不在锁内执行，避免阻塞）
        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                # 单个处理器失败不应影响其他处理器
                logger.exception(f"Event handler error for {event_type}")
    
    def publish_async(self, event_type: str, data: Any = None) -> None:
        """
        发布事件（异步）
        
        注意：实际的后台线程执行需要在应用层实现
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        # 这里只是提供一个接口，真正的异步执行需要配合线程池
        # 在GUI应用中，通常使用root.after来实现异步
        self.publish(event_type, data)
    
    def get_subscriber_count(self, event_type: str) -> int:
        """获取某事件的订阅者数量"""
        with self._lock:
            return len(self._subscribers.get(event_type, []))
    
    def clear(self) -> None:
        """清空所有订阅"""
        with self._lock:
            self._subscribers.clear()


# 全局事件总线实例（单例）
_event_bus: EventBus = None
_event_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """
    获取全局事件总线实例（线程安全单例）
    """
    global _event_bus
    if _event_bus is None:
        with _event_bus_lock:
            if _event_bus is None:
                _event_bus = EventBus()
    return _event_bus
