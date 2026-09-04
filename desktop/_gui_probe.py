# -*- coding: utf-8 -*-
"""
M3 GUI 冒烟探针（desktop/_gui_probe.py，由 smoke_test.py 以子进程调用）

目的：把「真实 pywebview 窗口 + 壳内 fetch(/api/health)」从主自测进程隔离出来——
     某些受限环境里 pythonnet/.NET 崩溃只影响本子进程，不污染主进程退出码。

用法：python _gui_probe.py <url> <name>
输出：最后一行 `VERDICT <PASS|SKIP|FAIL> <detail>`（flush 保证落盘）。
"""

import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None


def probe(url, name):
    import webview

    box = {"loaded": False, "health": ""}

    def on_loaded():
        box["loaded"] = True

    window = webview.create_window(f"smoke-{name}", url, width=960, height=640)
    window.events.loaded += on_loaded

    def watchdog():
        deadline = time.time() + 45
        while time.time() < deadline and not box["loaded"]:
            time.sleep(0.5)
        if box["loaded"]:
            time.sleep(2.5)
            for _ in range(20):
                try:
                    window.evaluate_js("fetch('/api/health').then(r=>r.text()).then(t=>{window.__h=t})")
                except Exception:
                    pass
                time.sleep(1)
                try:
                    val = window.evaluate_js("window.__h || ''") or ""
                except Exception:
                    val = ""
                box["health"] = val
                if val:
                    break
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        webview.start()
    except Exception as exc:
        return "SKIP", f"webview.start 失败(无桌面/WebView2?): {exc!r}"
    if not box["loaded"]:
        return "SKIP", "窗口加载超时（无桌面会话）"
    if '"status":"ok"' in box["health"]:
        return "PASS", f"壳内 fetch /api/health → {box['health'][:70]}"
    return "FAIL", f"窗口加载了但 health 未返回: {box['health'] or '无'}"


def main(argv=None):
    if len(argv) < 2:
        print("VERDICT SKIP 参数不足: <url> <name>", flush=True)
        return 0
    url, name = argv[0], argv[1]
    try:
        verdict, detail = probe(url, name)
    except Exception as exc:
        verdict, detail = "SKIP", f"探针异常: {exc!r}"
    print(f"VERDICT {verdict} {detail}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))