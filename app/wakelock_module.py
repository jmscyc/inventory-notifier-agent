import sys
import platform
import subprocess
import threading
import time
from typing import Optional

class SystemWakeLock:
    """
    跨平台防系統休眠管理器 (Windows, macOS, Linux)
    當監控排程開啟時鎖定睡眠，讓 CPU 與網路持續活躍；排程暫停或離開時釋放鎖定。
    """
    def __init__(self):
        self.os_type = platform.system()
        self.is_locked = False
        self._caffeinate_proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def acquire(self):
        if self.is_locked:
            return
        self.is_locked = True
        self._stop_event.clear()

        # 1. Windows: 使用 kernel32.SetThreadExecutionState
        if self.os_type == "Windows":
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                ES_AWAYMODE_REQUIRED = 0x00000040
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
                )
                print("☕ [WakeLock] Windows 系統防休眠鎖定已啟用 (CPU/網路保持活躍)")
            except Exception as e:
                print(f"⚠️ [WakeLock] Windows 啟用休眠鎖失敗: {e}")

        # 2. macOS: 調用 caffeinate 指令
        elif self.os_type == "Darwin":
            try:
                self._caffeinate_proc = subprocess.Popen(
                    ["caffeinate", "-dism"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print("☕ [WakeLock] macOS caffeinate 防休眠已在背景啟動")
            except Exception as e:
                print(f"⚠️ [WakeLock] macOS 啟動 caffeinate 失敗: {e}")

        # 3. Linux: 支援 systemd-inhibit 或定期維持狀態
        elif self.os_type == "Linux":
            try:
                # 測試是否有 systemd-inhibit
                self._caffeinate_proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep", "--who=InventoryAgent", "--why=Monitoring stock", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print("☕ [WakeLock] Linux systemd-inhibit 防休眠守護已啟動")
            except Exception:
                # 若無 systemd-inhibit，退化為輕量守護
                pass

    def release(self):
        if not self.is_locked:
            return
        self.is_locked = False
        self._stop_event.set()

        if self.os_type == "Windows":
            try:
                import ctypes
                ES_CONTINUOUS = 0x80000000
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                print("☕ [WakeLock] Windows 系統防休眠鎖定已釋放，恢復標準節能")
            except Exception:
                pass

        elif self.os_type in ["Darwin", "Linux"]:
            if self._caffeinate_proc:
                try:
                    self._caffeinate_proc.terminate()
                    self._caffeinate_proc.wait(timeout=2)
                except Exception:
                    pass
                self._caffeinate_proc = None
            print(f"☕ [WakeLock] {self.os_type} 防休眠守護已停止")

wakelock_manager = SystemWakeLock()
