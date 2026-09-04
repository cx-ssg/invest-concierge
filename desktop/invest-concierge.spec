# -*- mode: python ; coding: utf-8 -*-
"""invest-concierge 桌面版打包 spec（M4b）

难点处理：
1. uvicorn 动态收集：--collect-all uvicorn（它运行时按字符串 import app/loop）
2. dist 资源：frontend/dist 打包为 datas，运行时靠 sys._MEIPASS 重定位
3. pythonnet/WebView2：pywebview 依赖 pythonnet（CLR），需 collect 其 bootstrap
"""
import sys
import os

# project root：spec 在 desktop/ 下，根 = spec 所在目录的上上级
# （PyInstaller 执行 spec 时 SPECPATH=spec 所在目录，__file__ 不可靠）
_SPEC_DIR = os.path.abspath(SPECPATH if "SPECPATH" in dir() else os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(_SPEC_DIR)

# frontend/dist 静态资源（M3 已 build 产出，缺失时打包会失败——先检查）
DIST_DIR = os.path.join(ROOT, "frontend", "dist")
if not os.path.isdir(DIST_DIR):
    raise SystemExit(f"[spec] frontend/dist 不存在：{DIST_DIR}（先 cd frontend && npm run build）")

datas = [
    (DIST_DIR, "frontend/dist"),
    # akshare 自带数据文件（file_fold/calendar.json 等）——.py 之外的资源，
    # hiddenimports 抓不到，漏收会让 exe 里行情/情绪全挂（M4b 实测踩坑）
    (os.path.join(os.path.dirname(__import__("akshare").__file__), "file_fold"), "akshare/file_fold"),
    # 注意：不要把 ROOT/desktop 整目录打进去——含 .venv/pyinstaller_env 数十 MB
    # 会拖垮 analysis（曾致挂起）。desktop 下 py 脚本由 Analysis hiddenimports 收集。
]

hiddenimports = [
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    # FastAPI / starlette
    "fastapi",
    "starlette",
    "starlette.applications",
    "starlette.routing",
    "starlette.staticfiles",
    # server 与 services（项目自身）
    "server.main",
    "services",
    "services.agent_service",
    "services.holdings_service",
    "services.diagnosis_service",
    "services.diary_service",
    "services.nav_service",
    "services.status_service",
]

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[ROOT, os.path.join(ROOT, "desktop")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["streamlit", "pytest", "matplotlib", "IPython"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="invest-concierge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 桌面壳先带 console（uvicorn 日志可见），后续 M4c 可改 False 隐藏
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)