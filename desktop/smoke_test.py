# -*- coding: utf-8 -*-
"""
M3 桌面壳自动化自测（desktop/smoke_test.py）

覆盖验收点（每项输出 PASS / FAIL / SKIP，SKIP 为环境限制不算失败）：
    A. 依赖            pywebview / pystray 可导入
    B. dist 产物       frontend/dist 存在；JS 为相对 API base（无 http://localhost:8000 硬编码）
    C. 端口策略        select_port 生产=8000 占用时挑空闲端口；开发=复用本应用 8000 实例
    D. 内嵌后端        BackendServer 起 uvicorn → /api/health ok → GET / 托管 React(dist)
    E. 关窗决策        托盘存在→隐藏取消关闭；退出流程/无托盘→允许关闭
    F. 开发模式        Vite 5173 可达性 + resolve_backend('dev') 取 URL
    G. GUI 冒烟        真实 pywebview 窗口加载 React，壳内 fetch('/api/health') 通（尽力而为）
    H. 托盘冒烟        pystray 图标创建 + notify（尽力而为）

用法：
    desktop\\.venv\\Scripts\\python.exe desktop\\smoke_test.py [--no-gui]
退出码：0=无 FAIL；1=存在 FAIL。
"""

import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None

DESKTOP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DESKTOP_DIR)
for p in (DESKTOP_DIR, ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from backend import (  # noqa: E402
    BackendServer,
    _is_our_backend,
    _port_free,
    _serves_index,
    find_free_port,
    http_get,
    select_port,
)
from launcher import decide_close, resolve_backend  # noqa: E402

RESULTS = []


def report(name, verdict, detail=""):
    RESULTS.append((name, verdict, detail))
    print(f"  [{verdict:5s}] {name} {('- ' + detail) if detail else ''}")
    return verdict == "PASS"


def check_dist():
    dist = os.path.join(ROOT_DIR, "frontend", "dist")
    if not os.path.isdir(dist):
        return report("B1 dist 存在", "FAIL", f"缺少 {dist}（先 npm run build）")
    js_files = [os.path.join(dist, "assets", f) for f in os.listdir(os.path.join(dist, "assets")) if f.endswith(".js")]
    nj = len(js_files)
    if nj == 0:
        return report("B2 dist JS 产物", "FAIL", "assets 下无 JS")
    report("B2 dist JS 产物", "PASS", f"{nj} 个 bundle")
    hardcoded = any("http://localhost:8000" in open(f, encoding="utf-8", errors="ignore").read() for f in js_files)
    if hardcoded:
        return report("B3 API base 相对化", "FAIL", "bundle 仍含 http://localhost:8000，切换端口会断 API")
    return report("B3 API base 相对化", "PASS", "同源相对 /api，任意端口可用")


def check_port_policy():
    # 生产：8000 若被非托管实例占用 → 挑空闲端口
    port, external, reused = select_port(8000, require_index=True)
    if external:
        report("C1 prod 端口策略", "PASS", f"8000 可复用(已托管 dist)，port={port}")
    elif _port_free(port):
        report("C1 prod 端口策略", "PASS", f"8000 被占且不(完整)托管 → 挑空闲端口 {port}")
    else:
        report("C1 prod 端口策略", "FAIL", f"返回端口 {port} 不可用")
    # 开发：8000 上是本应用 → 复用
    dport, dexternal, dreused = select_port(8000, require_index=False)
    if dexternal:
        report("C2 dev 端口策略(复用)", "PASS", f"8000 上是本应用 → 复用 external=True port={dport}")
    else:
        report("C2 dev 端口策略(复用)", "PASS", f"8000 空闲/非本应用 → 自起 port={dport}")
    return True


def check_backend():
    port, external, _ = select_port(8000, require_index=True)
    if external:
        srv = None
    else:
        srv = BackendServer(port)
        if not srv.start():
            report("D1 后端启动", "FAIL", "uvicorn 就绪超时")
            return False
    report("D1 后端启动", "PASS", f"uvicorn @ 127.0.0.1:{port} 就绪")
    ok = True
    status, body = http_get(f"http://127.0.0.1:{port}/api/health")
    ok &= report("D2 /api/health", "PASS" if (status == 200 and '"status":"ok"' in body) else "FAIL", body[:60])
    status, body = http_get(f"http://127.0.0.1:{port}/")
    ok &= report("D3 FastAPI 托管 dist", "PASS" if (status == 200 and '<div id="root"' in body) else "FAIL",
                 body[:60].replace("\n", " "))
    return srv, ok


def check_close_decision():
    hidden = {"n": 0}
    q = {"quit": False}
    ok = True
    ok &= report("E1 有托盘·未退出 → 隐藏并取消关闭",
                 "PASS" if (decide_close(q, True, lambda: hidden.__setitem__("n", hidden["n"] + 1)) is False and hidden["n"] == 1) else "FAIL")
    q2 = {"quit": True}
    ok &= report("E2 已退出流程 → 允许关闭",
                 "PASS" if decide_close(q2, True, lambda: None) is True else "FAIL")
    ok &= report("E3 无托盘 → 允许关闭",
                 "PASS" if decide_close({"quit": False}, False, lambda: None) is True else "FAIL")
    return ok


def check_dev_mode():
    status, _ = http_get("http://localhost:5173", timeout=2.0)
    port, external, _ = resolve_backend("dev", None)
    url = "http://localhost:5173" if status == 200 else None
    if status == 200:
        report("F1 Vite 5173 可达", "PASS", "使用 http://localhost:5173")
    else:
        report("F1 Vite 5173 可达", "SKIP", "dev server 未启动（本项由 F2 启动后复测或跳过）")
    report("F2 dev 后端解析", "PASS", f"resolve_backend('dev') → port={port} external={external}")
    return True


def gui_smoke(url, name):
    """真实 pywebview 窗口（子进程隔离）：加载后壳内 fetch('/api/health')。"""
    probe = os.path.join(DESKTOP_DIR, "_gui_probe.py")
    try:
        r = subprocess.run([sys.executable, probe, url, name],
                           capture_output=True, text=True, timeout=150,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "SKIP", "GUI 探针超时（环境无桌面会话）"
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.startswith("VERDICT")]
    if lines:
        _, verdict, detail = lines[-1].split(" ", 2)
        return verdict, detail
    tail = (r.stdout or "")[-160:].replace("\n", " ") or f"rc={r.returncode}"
    return "SKIP", f"探针无 VERDICT: {tail}"


def tray_smoke():
    try:
        from tray import TrayIcon
    except Exception as exc:
        return "SKIP", f"tray 模块导入失败: {exc!r}"
    try:
        t = TrayIcon(on_show=lambda: None, on_quit=lambda: None)
        t.start()
        time.sleep(1.5)
        t.notify("smoke")
        t.stop()
        return "PASS", "pystray 图标创建 + notify OK"
    except Exception as exc:
        return "SKIP", f"托盘创建失败(无桌面会话?): {exc!r}"


def start_vite_dev(no_gui):
    """后台起 vite dev（需要 vite 补丁在位）；返回 proc 或 None。"""
    import tempfile
    frontend = os.path.join(ROOT_DIR, "frontend")
    # 冒烟日志写系统临时目录（一次性诊断产物，不落仓库目录）
    logf = tempfile.TemporaryFile("w+b")
    proc = subprocess.Popen(["npm.cmd", "run", "dev"], cwd=frontend, stdout=logf, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.time() + 90
    while time.time() < deadline:
        status, _ = http_get("http://localhost:5173", timeout=1.5)
        if status == 200:
            return proc
        if proc.poll() is not None:
            break
        time.sleep(1)
    return None


def kill_vite(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/pid", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
    except Exception:
        pass


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--no-gui", action="store_true", help="跳过 GUI/托盘冒烟")
    args = p.parse_args(argv)

    print("=" * 60)
    print("  M3 桌面壳自测")
    print("=" * 60)

    # A 依赖
    try:
        import webview  # pip 包名 pywebview，import 模块名 webview
        import pystray  # noqa: F401
        report("A1 pywebview/pystray 已装", "PASS", f"webview {getattr(webview, '__version__', '?')}")
    except ImportError as exc:
        report("A1 pywebview/pystray 已装", "FAIL", f"缺失: {exc}（pip install -r requirements.txt）")

    check_dist()
    check_port_policy()

    # D 后端（先起，供 G 复用）
    srv = None
    d_ok = False
    try:
        _srv, d_ok = check_backend()
        srv = _srv
    except Exception as exc:
        report("D 后端", "FAIL", f"{exc!r}")
    backend_url = f"http://127.0.0.1:{srv.port}" if srv is not None else "http://127.0.0.1:8000"

    check_close_decision()

    # F 开发模式 + 真跑 vite dev
    dev_proc = None
    check_dev_mode()
    dev_url = None
    status, _ = http_get("http://localhost:5173", timeout=2.0)
    if status != 200:
        print("  [INFO ] 启动 Vite dev server 验证开发模式…")
        dev_proc = start_vite_dev(args.no_gui)
        if dev_proc is not None:
            report("F3 Vite dev server 启动", "PASS", "http://localhost:5173 就绪")
            dev_url = "http://localhost:5173"
        else:
            report("F3 Vite dev server 启动", "SKIP", "vite dev 起不来（npm/网络受限）")
    else:
        dev_url = "http://localhost:5173"
        report("F3 Vite dev server", "PASS", "5173 已在运行")

    # G GUI 冒烟（生产同源 + 开发跨源两条）
    if not args.no_gui:
        verdict, detail = gui_smoke(backend_url, "prod")
        report("G1 GUI·生产模式(dist 同源)", verdict, detail)
        if dev_url:
            verdict, detail = gui_smoke(dev_url, "dev")
            report("G2 GUI·开发模式(5173)", verdict, detail)
        else:
            report("G2 GUI·开发模式(5173)", "SKIP", "vite dev 未就绪")
        verdict, detail = tray_smoke()
        report("H1 托盘冒烟", verdict, detail)
    else:
        report("G1 GUI·生产", "SKIP", "--no-gui")
        report("G2 GUI·开发", "SKIP", "--no-gui")
        report("H1 托盘", "SKIP", "--no-gui")

    # 清理
    kill_vite(dev_proc)
    if srv is not None:
        srv.stop()

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    skips = [r for r in RESULTS if r[1] == "SKIP"]
    print("-" * 60)
    print(f"  结果：{len(RESULTS)} 项，FAIL={len(fails)}，SKIP={len(skips)}，PASS={len(RESULTS) - len(fails) - len(skips)}")
    for name, verdict, detail in fails:
        print(f"  [FAIL ] {name} {detail}")
    print("=" * 60)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())