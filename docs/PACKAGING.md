# M4b 打包实录：PyInstaller → invest-concierge.exe

> 2026-09-04 本地产判。记录真实踩坑与最终可复现路径，供 CI/他人复刻。
> 结论先行：**必须在干净 venv 里打包**，宿主 Anaconda 环境有两大杀手（见 §1）。
> **最近一次复跑（2026-10-04 · v1.5.0）**：干净 `desktop\build_env`（PyInstaller 6.22.2）打包 → exit 0，
> 产物 `dist_v150\invest-concierge.exe` **87.11 MiB**（= 91.35 MB 十进制；耗时 ≈98 s）；
> `scripts\build_installer.bat` → exit 0，`dist_m4\invest-concierge-setup-v1.5.0.exe` **87.81 MiB**（= 92.08 MB）。
> ⚠️ 体积随依赖变化：补齐 `langgraph` 全家（F1 连带修复）后，v1.5.0 由 83.19 → **87.11 MiB**。
> ⚠️ 口径：本文用 **MiB**（`size / 1MB`，同 Windows 资源管理器）；十进制 MB 见括号。

## 1. 环境杀手（宿主 Anaconda 的问题，不是代码问题）

| # | 杀手 | 症状 | 处置 |
|---|---|---|---|
| 1 | **`pathlib` 旧背包**（pip 里装了与标准库同名的旧 pathlib 包） | PyInstaller 直接拒绝启动，强制要求删除 | `pip uninstall pathlib`（标准库自带，删无副作用） |
| 2 | **Hermes editable 安装**（`__editable__.hermes_agent-*.finder` 混进 sys.path） | analysis 阶段挂起：build/ 目录 9 分钟只生成 1 个 `qt.conf`，无 Analysis/TOC 产物 | 不在宿主环境打包 → **新建干净 venv**（见 §2） |

判别信号：`build/` 只有 qt.conf + 零增长 = analysis 早期挂死，不是慢。

## 2. 可复现的干净打包路径

```bat
:: 1. 干净 venv（关键：--system-site-packages 关掉，Hermes/pathlib 全隔离）
python -m venv desktop\build_env
desktop\build_env\Scripts\pip install -r requirements.txt pyinstaller pywebview pystray pillow python-dotenv

:: 2. 前端产物在位（没有就先构建）
cd frontend && npm run build && cd ..

:: 3. 打包（spec 已处理三个难点，见 §3）
desktop\build_env\Scripts\pyinstaller desktop\invest-concierge.spec --noconfirm

:: 4. 产物：dist\invest-concierge.exe（**onefile 单文件**；输出目录可用 --distpath 自定，如 --distpath dist_v150）

:: 5. 【必做】构建后校验（两条，退出码非 0 时**不要发布**）
::    5a. 动态导入面完整性（扫磁盘源码 vs 包内清单）—— 源码态测试**覆盖不到**这类缺失
::        （2026-10-04 F1 实证：v1.5.0 的 exe 缺 3 个 data 模块 ⇒ 9 个工具在安装版不可用，
::         而 674 条 pytest 全绿）
::    5b. exe 端到端自证（解开 exe 直接读：模块数/TOC 同源、工具依赖模块、**版本常量**、
::        exe 比源码新）—— 回答「exe 里到底是哪份代码」，不靠时间戳推断
python scripts\verify_bundle.py
python scripts\verify_exe.py
```

## 3. spec 三个核心难点（desktop/invest-concierge.spec 已处理）

### 难点 1：uvicorn 动态导入
uvicorn 运行时按字符串 `import uvicorn.loops.auto` 等装配——静态分析抓不到 →
`hiddenimports` 全量枚举（loops/protocols.http/websockets/lifespan 全家）。

### 难点 2：frontend/dist 资源定位
- 打包：`datas = [(frontend/dist, "frontend/dist")]` —— **只收 dist 本体**
- 运行：`server/main.py` 已加 `sys._MEIPASS` 感知（开发走仓库路径，exe 走解包目录）
- **血坑**：datas 曾写成 `(ROOT/desktop, "desktop")` 把 desktop 整目录（含 26MB 构建环境）递归拷进 analysis → PyInstaller 挂起。datas 必须精确到文件/叶子目录。

### 难点 3：spec 内定位项目根
PyInstaller 执行 spec 时 `__file__` 不可靠 → 用 `SPECPATH`（spec 所在目录）反推。

## 4. 打包安全适配（Mimosa 红线）

- exe 内嵌 uvicorn 服务的自检请求（`desktop/backend.py http_get`）加了**回环白名单**：
  仅允许 `127.0.0.1` / `localhost` / `::1`，任何外网目标直接拒绝（防 SSRF）。
- 冒烟脚本的 vite 日志改写 `tempfile`（不往仓库路径写文件）。

## 5. exe 冒烟清单（每次出包必跑）

> ⚠️ **2026-10-04 交付线未执行本节**：项目有 **GUI 禁令**（禁起桌面壳 / 打包 exe，
> 避免强杀 GUI 进程触发 Windows `0x80000003`），**双击冒烟必须人工完成**。
> 本轮替代验证 = 源码态 `/api/health`（实测 `version=1.5.0`）+ 构建后 `scripts/verify_bundle.py`
> （动态导入面完整性 —— F1 正是靠它抓出来的）。**双击冒烟仍待补**（见交付说明）。

1. 双击 exe → 原生窗口（pywebview/WebView2）
2. 窗口内 React 加载、`/api/health` 200（backend.py 自检会先跑）
3. 侧栏大盘指数出现真实数据（AkShare/腾讯 fallback）
4. AI 对话发一条真实追问 → SSE 工具时间线滚动 → 回答落库
5. `netstat` 确认仅 127.0.0.1 监听
6. 关窗 → 托盘驻留 → 双击恢复 → 菜单退出干净

## 6. 已知边界

- akshare 动态 import 面广，冷启首问 15-40s（与开发态一致，非打包引入）
- WebView2 依赖系统自带（Win10/11 默认有）；无 WebView2 → launcher 自动回退浏览器模式
- exe 体积实测 **≈87 MiB**（pandas/akshare/langgraph 全家桶 + 前端 dist；2026-10-04 v1.5.0 实测 **87.11 MiB** = 91.35 MB 十进制）；
  产物是 **onefile 单文件** —— spec 只有 `EXE(...)`、**没有 `COLLECT`**（.iss 的 `[Files]` 也按单文件搬运安装）。
  ⚠️ 本节旧文曾写「onedir 而非 onefile」，与代码不符，2026-10-04 实查订正。
