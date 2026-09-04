# -*- coding: utf-8 -*-
"""
M3 桌面壳 - 内嵌 FastAPI 后端管理（desktop/backend.py）

职责：
    1. 端口策略：优先 8000；被占用时探测是否为「本应用实例」，是则复用；
       否则自动挑空闲端口（前端已按相对 /api 构建，任意端口同源可用）。
    2. 在守护线程内启动 uvicorn（主线程留给 pywebview GUI 循环）。
    3. 就绪探测（/api/health）+ 优雅停止。

用法（由 launcher.py 调用）：
    port, external, reused = select_port(preferred=8000, require_index=True)
    if not external:
        srv = BackendServer(port); srv.start()
    ...
    srv.stop()
"""

import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 把仓库根目录放进 sys.path，保证 server/services/data 可导入（双击/任何 cwd 启动都成立）
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _is_loopback_url(url):
    """仅允许访问本机回环地址（127.0.0.1/localhost/[::1]）——本函数只用于
    探测本应用自起的后端/Vite，任何非回环目标直接拒绝（防 SSRF）。"""
    try:
        parsed = urllib.parse.urlparse(str(url))
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return False
        if host in ("localhost", "::1"):
            return True
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, AttributeError):
        return False


def http_get(url, timeout=2.0):
    """GET 一个 URL，返回 (status, text)；失败返回 (None, '')。

    仅接受回环地址（本应用后端/Vite 自检）；非回环拒绝请求。
    """
    if not _is_loopback_url(url):
        return None, ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "invest-concierge-selfcheck"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(4096).decode("utf-8", "replace")
    except Exception:
        return None, ""


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port():
    """让内核挑一个空闲端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_our_backend(port):
    """端口上跑的是不是本应用后端（/api/health 返回 status=ok 视为本应用）。"""
    status, body = http_get(f"http://127.0.0.1:{port}/api/health")
    return status == 200 and '"status"' in body and '"ok"' in body


def _serves_index(port):
    """该端口是否已托管前端（GET / 返回含 #root 的 HTML）。"""
    status, body = http_get(f"http://127.0.0.1:{port}/")
    return status == 200 and '<div id="root"' in body


def select_port(preferred=8000, require_index=False):
    """挑选后端端口。

    返回 (port, external, reused)：
        port     实际端口
        external 是否复用已在跑的实例（True 时不需自己起 uvicorn）
        reused   是否实际发生了复用（external=True 时信息性置 True）

    策略：
        - preferred 空闲          → 用它（自己起）
        - preferred 上有本应用    → 若 require_index 且已托管前端则复用；
                                   否则（如旧实例未挂 dist）换空闲端口自己起
        - preferred 上是别的程序  → 换空闲端口自己起
    """
    if _port_free(preferred):
        return preferred, False, False
    if _is_our_backend(preferred):
        if (not require_index) or _serves_index(preferred):
            return preferred, True, True
    return find_free_port(), False, False


class BackendServer:
    """内嵌 uvicorn：后台线程运行 server.main:app，bind 127.0.0.1。"""

    def __init__(self, port):
        self.port = port
        self._server_obj = None
        self._thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self, wait_seconds=15):
        """启动并等待 /api/health 就绪；成功返回 True。"""
        if self.port <= 0:
            return False
        import uvicorn
        from server.main import app  # 顶层 init_db() 会幂等执行

        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="info")
        self._server_obj = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server_obj.run, daemon=True, name="uvicorn-backend")
        self._thread.start()

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            status, _ = http_get(f"{self.url}/api/health", timeout=1.5)
            if status == 200:
                return True
            time.sleep(0.3)
        return False

    def stop(self):
        """请求 uvicorn 优雅退出（should_exit=True），等线程收尾。"""
        if self._server_obj is not None:
            try:
                self._server_obj.should_exit = True
            except Exception:
                pass
            self._server_obj = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            self._thread = None