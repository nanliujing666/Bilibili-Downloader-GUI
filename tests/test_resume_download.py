"""恢复下载逻辑单元测试

覆盖：Tk 主线程直接调用 resume_download 时没有运行中的事件循环，
不能抛 ``RuntimeError: no running event loop``。
"""
import asyncio
import os
import sys
import threading
import unittest
from types import SimpleNamespace

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from src.models.download import TaskStatus  # noqa: E402
from src.services.download_service import DownloadService  # noqa: E402


class _FakeTask:
    def __init__(self, task_id: str, status: TaskStatus):
        self.task_id = task_id
        self.status = status


class _FakeState:
    def __init__(self, task):
        self.download_tasks = [task]

    def update_task(self, task_id, **kwargs):
        return SimpleNamespace(task_id=task_id, **kwargs)


class _FakeStateManager:
    def __init__(self, task):
        self.state = _FakeState(task)
        self.updates = []

    def get_state(self):
        return self.state

    def update(self, fn):
        result = fn(self.state)
        self.updates.append(result)
        return result


class TestResumeDownload(unittest.TestCase):
    def _make_service(self, status: TaskStatus):
        service = DownloadService(auth_service=None)
        task = _FakeTask("task-1", status)
        service.state_manager = _FakeStateManager(task)
        return service, task

    def test_sync_call_without_running_loop_does_not_raise(self):
        """GUI 主线程（无事件循环）调用不应抛异常"""
        service, _ = self._make_service(TaskStatus.FAILED)

        errors = []

        def run():
            try:
                service.resume_download("task-1")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(errors, [])
        # 没有事件循环时不应修改任务状态
        self.assertEqual(service.state_manager.updates, [])

    def test_async_resume_resets_state_and_starts_download(self):
        """异步恢复会重置状态并重新启动下载"""
        service, _ = self._make_service(TaskStatus.FAILED)

        started = []

        async def fake_start_download(task_id):
            started.append(task_id)

        service.start_download = fake_start_download  # type: ignore[assignment]

        asyncio.run(service.resume_download_async("task-1"))

        self.assertEqual(started, ["task-1"])
        self.assertTrue(service._active_downloads["task-1"])
        self.assertEqual(len(service.state_manager.updates), 1)
        update = service.state_manager.updates[0]
        self.assertEqual(update.status, TaskStatus.PENDING)
        self.assertEqual(update.progress, 0.0)
        self.assertIsNone(update.error_message)

    def test_async_resume_ignores_completed_task(self):
        """已完成任务不会被重新启动"""
        service, _ = self._make_service(TaskStatus.COMPLETED)

        started = []

        async def fake_start_download(task_id):
            started.append(task_id)

        service.start_download = fake_start_download  # type: ignore[assignment]

        asyncio.run(service.resume_download_async("task-1"))

        self.assertEqual(started, [])
        self.assertEqual(service.state_manager.updates, [])


if __name__ == "__main__":
    unittest.main()
