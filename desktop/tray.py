# -*- coding: utf-8 -*-
"""
M3 桌面壳 - 系统托盘（desktop/tray.py，pystray + Pillow）

托盘菜单：
    - 显示主窗口（双击/单击默认动作）
    - 退出（完整退出：关窗口 + 停后端）

调用方注入 on_show / on_quit 回调；本模块不直接依赖 pywebview，
方便在无桌面环境时单独测试或降级。
"""

import threading

APP_NAME = "invest-concierge"


def make_icon_image():
    """Pillow 画 64x64 托盘图标：终端青圆角方块 + 白色直角 K 线（品牌 invest-concierge）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    accent = (76, 139, 245, 255)  # --accent #4C8BF5
    white = (255, 255, 255, 255)

    d.rounded_rectangle([3, 3, 61, 61], radius=14, fill=accent)
    # 迷你 K 线：三段折线
    pts = [(16, 44), (24, 24), (32, 38), (40, 18), (48, 30)]
    d.line(pts, fill=white, width=5, joint="curve")
    # 收盘点小方块
    x, y = pts[-1]
    d.rectangle([x - 5, y - 5, x + 5, y + 5], fill=white)
    return img


class TrayIcon:
    """pystray 托盘图标（run_detached 后台线程运行，不阻塞 pywebview 主循环）。"""

    def __init__(self, on_show=None, on_quit=None):
        self._icon = None
        self._on_show = on_show
        self._on_quit = on_quit
        self._lock = threading.Lock()

    def start(self):
        """创建并显示托盘图标；失败抛异常由调用方降级（无托盘时关窗=退出）。"""
        import pystray
        from pystray import Menu, MenuItem

        menu = Menu(
            MenuItem("显示主窗口", self._show, default=True),
            MenuItem("退出", self._quit),
        )
        icon = pystray.Icon(APP_NAME, make_icon_image(), "invest-concierge · A股投研工作台", menu)
        icon.run_detached()
        with self._lock:
            self._icon = icon

    def _show(self, icon, item):
        if self._on_show:
            self._on_show()

    def _quit(self, icon, item):
        if self._on_quit:
            self._on_quit()

    def notify(self, message):
        """托盘气泡提示（尽力而为，失败静默）。"""
        try:
            icon = self._icon
            if icon is not None:
                icon.notify(message, APP_NAME)
        except Exception:
            pass

    def stop(self):
        with self._lock:
            icon = self._icon
            self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass