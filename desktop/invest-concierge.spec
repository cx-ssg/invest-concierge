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
    # M3 编排层（LangGraph 单链路）：这些是**函数内 import**，PyInstaller 一般能自动
    # 分析到，但显式声明是防御 —— 漏收会让 `ORCHESTRATOR=graph` 在 exe 里直接 ImportError
    # （见 docs/COVERAGE_DESIGN.md §6「LangGraph 依赖影响打包」）
    "utils.orchestrator",
    "utils.orchestrator.flags",
    "utils.orchestrator.state",
    "utils.orchestrator.graph",
    "langgraph.graph",
    "langgraph.types",
    "langgraph.checkpoint",
    "langgraph.checkpoint.sqlite",
]

# 2026-10-04 交付线（critic 审计 F1 **实测**，非推测）——工具注册表用**字符串晚绑定**
# （`utils/agent_core.py` 写 `module="data.dragon_api"`，真正 import 在其
# `importlib.import_module(mod_name)`，参数是**变量**）⇒ PyInstaller 静态分析收不到，
# 且**不报错、不 warn**。v1.5.0 的 exe 实测缺 data.dragon_api / data.moneyflow_api /
# data.limit_up_api ⇒ **9 个工具（含全部 7 个龙虎工具）在安装版 ModuleNotFoundError**，
# 而源码态 pytest 全绿（模块就在磁盘上）。
#
# ⚠️ 两个坑（本轮实测踩到，写下来防复发）：
#   ① `collect_submodules` 需要**能 import 目标包** ⇒ 必须先把 ROOT 注入 sys.path，
#      否则它**静默返回 []**（本轮第一次"改完仍缺"的根因之一）
#   ② PyInstaller 会**复用已存在的 `PYZ-00.pyz`**（只重算 Analysis/PKG/EXE 的 .toc）
#      ⇒ 改完本 spec 必须**删掉 workpath**（如 `rm -rf build_v150`）再构建，
#      否则新 hiddenimports 不生效（本轮第一次"修完仍红"的第二个根因）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

_data_mods = sorted(set(collect_submodules("data"))
                    | {"data.dragon_api", "data.moneyflow_api", "data.limit_up_api"})
if not _data_mods:
    raise SystemExit("[spec] data 包一个模块都没收到 ⇒ 检查 ROOT/sys.path（禁止静默通过）")
hiddenimports += _data_mods
print("[spec] hiddenimports += data 包 %d 个模块：%s" % (len(_data_mods), _data_mods))

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