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
    p.add_argument("--server", action="store_true",
                   help="无头模式（SHELL_UPGRADE ③ Electron 壳配合）：只起后端不起 GUI/托盘，"
                        "stdin 关闭即退出——Electron 壳 spawn 后杀进程树用")
    p.add_argument("--frameless", action="store_true",
                   help="无边框自绘标题栏（SHELL_UPGRADE ②）：窗口无系统边框，拖动/最小化/最大化/关闭"
                        "由 React TitleBar 承担（.pywebview-drag-region 类）；默认关=v1.0 原生边框")
    p.add_argument("--print-port", action="store_true",
                   help="后端就绪后打印一行 PORT=<port> 到 stdout（Electron 壳 spawn 解析用，SHELL_UPGRADE ③）")
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


def keep_alive_server(base_url):
    """无头驻留（--server，SHELL_UPGRADE ③ Electron 壳配合项）：
    后端就绪 + PORT= 上报后阻塞，直到 stdin 关闭或 KeyboardInterrupt——
    Electron 壳 spawn 本进程（--print-port --server）解析端口后 loadURL，
    退出时杀进程树；stdin 关闭检测让 Ctrl+C/管道关闭也能自然停。"""
    print(f"[desktop] 无头模式：后端 {base_url} 驻留中（stdin 关闭或 Ctrl+C 退出）。")
    try:
        while True:
            # stdin 活着就驻留（Electron 杀进程树时管道关闭，readline 立即 EOF）
            if sys.stdin is None or sys.stdin.closed:
                print("[desktop] stdin 已关闭，退出无头模式。")
                return
            line = sys.stdin.readline()
            if line == "":
                # EOF：管道方（Electron 壳）已死或 Ctrl+Z——EOF 不区分 tty/管道，一律退出
                print("[desktop] stdin 已关闭，退出无头模式。")
                return
            time.sleep(1)
    except KeyboardInterrupt:
        print("[desktop] Ctrl+C，正在退出…")


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


def make_window_api(window_holder):
    """frameless 窗口控制桥（SHELL_UPGRADE ②）。

    pywebview 的 window.pywebview.api.<method> 只暴露 create_window(js_api=...)
    传入对象上的公开方法（util.get_functions 按 dir() 收集、下划线开头跳过）；
    不传 js_api 时 api 为空对象——TitleBar 三枚自绘按钮将静默无效。
    这里把 Window 的 minimize/maximize/destroy 包成 js_api 类：
    - destroy() 走 gui.destroy_window → Form.Close() → FormClosing → closing 事件
      → decide_close（有托盘=hide 取消关闭、无托盘/--no-tray=真退出），
      与系统标题栏点 X 的链路完全一致，托盘语义不丢；
    - window 引用经 holder 延迟解析（js_api 对象在 create_window 之前构造，
      而方法调用发生在窗口创建之后，时序天然安全）。
    """
    class WindowApi:
        def minimize(self):
            window_holder["window"].minimize()

        def maximize(self):
            window_holder["window"].maximize()

        def restore(self):
            window_holder["window"].restore()

        def destroy(self):
            window_holder["window"].destroy()

    return WindowApi()


def run_desktop(url, backend=None, external=False, args=None):
    """创建 pywebview 窗口 + 托盘；阻塞直到退出。返回退出码。

    参数序对齐 main() 的位置调用 run_desktop(url, backend, external, args)——
    d5c14b1 定义侧漏了后三参（TypeError 必炸回退浏览器），2026-09-05 安装器
    实装实测抓到；首轮修复曾按 (url, args, ...) 排序导致 backend 错位到 args
    （AttributeError no_tray），本版按调用点顺序对齐。

    --frameless（SHELL_UPGRADE ②）：无边框 + easy_drag 由 .pywebview-drag-region
    承担拖动（pywebview 内置 JS 机制），DRAG_REGION_DIRECT_TARGET_ONLY=True 让
    命中判定只看直接目标（拖动区域内点按钮不会带着窗口跑）。
    """
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

    window_kwargs = dict(
        width=1440,
        height=900,
        min_size=(1100, 700),
        background_color=BG_COLOR,
    )
    window_holder = {"window": None}
    if getattr(args, "frameless", False):
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
        window_kwargs["frameless"] = True
        window_kwargs["easy_drag"] = True
        window_kwargs["js_api"] = make_window_api(window_holder)
        # 前端凭 ?shell=frameless 渲染三枚窗口按钮（默认模式下 window.pywebview
        # 同样存在，若只探测 pywebview 会把死按钮画到原生标题栏旁边）
        url = url + ("&" if "?" in url else "?") + "shell=frameless"
        print("[desktop] frameless 模式：拖动区=TitleBar（.pywebview-drag-region），"
              "窗口控制=右上三枚自绘按钮（js_api 桥）。")

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        **window_kwargs,
    )
    window_holder["window"] = window
    window.events.closing += on_closing

    if not args.no_tray:
        tray = TrayIcon(on_show=lambda: window.show(), on_quit=on_quit_from_tray)
        tray.start()
        tray_holder["tray"] = tray
        # v1.1 预警通知桥：调度器触发时走托盘气泡（浏览器/无头模式只落库+应用内角标）
        try:
            from services import alert_service
            alert_service.register_notifier(tray.notify)
        except Exception as exc:
            print(f"[desktop] 预警通知桥注册失败（不影响预警落库）：{exc}")
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

        # ---------- 2.5 端口上报（Electron 壳 spawn 解析用，SHELL_UPGRADE ③）----------
        if args.print_port:
            print(f"PORT={port}", flush=True)

        # ---------- 3. GUI / 无头 / 回退 ----------
        if args.server:
            keep_alive_server(base_url)
            return 0
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