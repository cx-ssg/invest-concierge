# -*- coding: utf-8 -*-
"""
M3 桌面壳主入口（desktop/launcher.py）

一条命令进桌面版：
    python desktop/launcher.py                    # 生产模式：FastAPI 托管 frontend/dist（默认）
    python desktop/launcher.py --mode dev         # 开发模式：窗口指向 Vite 5173（需先 npm run dev）
    python desktop/launcher.py --browser          # 只起后端 + 自动开浏览器（桌面壳不可用时的回退）
    python desktop/launcher.py --port 8123        # 强制后端端口
    python desktop/launcher.py --no-tray          # 关窗即退出（调试用，不驻留托盘）

行为：
    1. 内嵌 uvicorn（127.0.0.1，优先 8000，占用时自动找空闲端口或复用本应用实例）；
    2. pywebview 开原生窗口（v1 拍板：原生标题栏，不做 frameless），
       关窗 → 最小化到托盘（pystray），托盘双击/单恢复、菜单「退出」完整退出；
    3. 桌面壳任何环节不可用（缺 pywebview / 无 WebView2 / GUI 起不来）→
       提示 + 自动用浏览器打开，后端继续跑（Ctrl+C 停止），功能零损失。
"""

import argparse
import os
import sys
import time
import webbrowser

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
sys.stderr.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stderr, "reconfigure") else None
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from backend import BackendServer, _is_our_backend, _port_free, _serves_index, http_get, select_port  # noqa: E402
from tray import APP_NAME, TrayIcon  # noqa: E402

DEFAULT_DEV_URL = "http://localhost:5173"
WINDOW_TITLE = "invest-concierge · A股投研工作台"
BG_COLOR = "#1A1B1E"  # tokens.css --bg


def parse_args(argv):
    p = argparse.ArgumentParser(description="invest-concierge 桌面壳（pywebview + FastAPI）")
    p.add_argument("--mode", choices=["prod", "dev"], default="prod",
                   help="prod=FastAPI 托管 frontend/dist；dev=指向 Vite 5173（需先 npm run dev）")
    p.add_argument("--port", type=int, default=None, help="强制后端端口（默认优先 8000，被占用自动找空闲）")
    p.add_argument("--browser", action="store_true", help="不起 GUI，只起后端并打开浏览器")
    p.add_argument("--no-tray", action="store_true", help="不驻留托盘：关窗直接退出（调试用）")
    p.add_argument("--debug", action="store_true", help="pywebview debug 模式（开发者工具可见）")
    return p.parse_args(argv)


def resolve_backend(mode, port_arg):
    """决策后端：返回 (port, external, reused)；external=True 表示复用已有实例。"""
    preferred = port_arg or 8000
    if port_arg:
        if _port_free(port_arg):
            return port_arg, False, False
        if _is_our_backend(port_arg) and (mode == "dev" or _serves_index(port_arg)):
            return port_arg, True, True
        print(f"[错误] 指定端口 {port_arg} 已被占用且不是本应用后端，无法启动。"
              f"请换 --port 或用浏览器模式。")
        sys.exit(1)
    return select_port(preferred, require_index=(mode == "prod"))


def keep_alive_browser_fallback(base_url, note=""):
    """浏览器回退：打印提示 + 打开浏览器 + 后端驻留到 Ctrl+C。"""
    if note:
        print(f"[提示] {note}")
    print(f"[桌面壳] 后端已在运行：{base_url}")
    print(f"[桌面壳] 请用浏览器打开 {base_url}（或手动打开）。按 Ctrl+C 停止后端。")
    try:
        opened = webbrowser.open(base_url)
        if not opened:
            print(f"[提示] 浏览器自动打开失败，请手动访问 {base_url}")
    except Exception:
        print(f"[提示] 浏览器自动打开失败，请手动访问 {base_url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[desktop] Ctrl+C，正在退出…")


def decide_close(quit_flag, has_tray, hide):
    """关窗决策：退出流程中或无托盘 → 允许关闭(True)；否则隐藏窗口取消关闭(False)。"""
    if quit_flag["quit"] or not has_tray:
        return True
    hide()
    return False


def run_desktop(url, args):
    """创建 pywebview 窗口 + 托盘；阻塞直到退出。返回退出码。"""
    import webview

    quit_flag = {"quit": False}
    tray_holder = {"tray": None}

    def on_closing():
        return decide_close(quit_flag, tray_holder["tray"] is not None,
                            lambda: window.hide())

    def on_quit_from_tray():
        quit_flag["quit"] = True
        tray = tray_holder["tray"]
        if tray is not None:
            tray.stop()
            tray_holder["tray"] = None
        try:
            window.destroy()
        except Exception as exc:
            print(f"[desktop] 退出时销毁窗口异常：{exc}")

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=900,
        min_size=(1100, 700),
        background_color=BG_COLOR,
    )
    window.events.closing += on_closing

    if not args.no_tray:
        tray = TrayIcon(on_show=lambda: window.show(), on_quit=on_quit_from_tray)
        tray.start()
        tray_holder["tray"] = tray
        print("[desktop] 托盘已就绪：关窗最小化到托盘，双击托盘图标恢复。")
    else:
        print("[desktop] --no-tray：关窗直接退出。")

    print(f"[desktop] 窗口已创建：{url}")
    try:
        webview.start(debug=args.debug)
    finally:
        tray = tray_holder["tray"]
        if tray is not None:
            tray.stop()
        # 等待 unvicorn 收尾在 main() 统一处理
    return 0


def main(argv=None):
    args = parse_args(argv)
    mode = args.mode

    print("=" * 58)
    print(f"  invest-concierge 桌面壳  ·  模式: {'开发(Vite 5173)' if mode == 'dev' else '生产(dist 托管)'}")
    print("=" * 58)

    rc = 0
    backend = None
    try:
        # ---------- 1. 后端 ----------
        port, external, _reused = resolve_backend(mode, args.port)
        if not external:
            backend = BackendServer(port)
            print(f"[desktop] 正在启动内嵌后端 uvicorn @ 127.0.0.1:{port} …")
            ok = backend.start()
        else:
            ok = True
            print(f"[desktop] 检测到已在运行的本应用实例 @ 127.0.0.1:{port}，直接复用。")
        if not ok:
            print(f"[错误] 后端就绪失败（127.0.0.1:{port} /api/health 不通）。"
                  f"请检查端口/依赖后重试，或用浏览器模式。")
            return 1
        base_url = f"http://127.0.0.1:{port}"

        # ---------- 2. 前端地址 ----------
        if mode == "dev":
            status, _ = http_get(DEFAULT_DEV_URL, timeout=2.0)
            if status is None:
                print("[错误] 开发模式需要 Vite dev server：请在 frontend/ 下先执行 npm run dev，再启动本壳。")
                keep_alive_browser_fallback(base_url, note="桌面壳回退为浏览器模式（如果后端没有托管 dist，请用 npm run dev 后访问 http://localhost:5173）")
                return 0
            url = DEFAULT_DEV_URL
            print(f"[desktop] 前端指向 Vite dev server：{url}")
        else:
            url = base_url
            print(f"[desktop] 前端由 FastAPI 托管（同源相对 /api，可用任意端口）：{url}")

        # ---------- 3. GUI / 回退 ----------
        if args.browser:
            keep_alive_browser_fallback(base_url)
            return 0

        try:
            rc = run_desktop(url, backend, external, args)
        except ImportError as exc:
            print(f"[错误] 桌面壳依赖缺失（{exc}）。请安装: pip install -r requirements.txt")
            keep_alive_browser_fallback(base_url)
        except Exception as exc:
            print(f"[错误] 桌面壳不可用：{exc!r}")
            print("[提示] 请确认系统已装 Edge WebView2 Runtime（Win10/11 一般自带）。")
            keep_alive_browser_fallback(base_url)
    finally:
        # 所有出口统一收尾：停托盘由 run_desktop 负责，这里停自己起的后端
        if backend is not None:
            backend.stop()

    print("[desktop] 已退出。")
    return rc


if __name__ == "__main__":
    sys.exit(main())