# M4b 打包实录：PyInstaller → invest-concierge.exe

> 2026-09-04 本地产判。记录真实踩坑与最终可复现路径，供 CI/他人复刻。
> 结论先行：**必须在干净 venv 里打包**，宿主 Anaconda 环境有两大杀手（见 §1）。

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

:: 4. 产物：dist\invest-concierge\invest-concierge.exe（onedir）
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

1. 双击 exe → 原生窗口（pywebview/WebView2）
2. 窗口内 React 加载、`/api/health` 200（backend.py 自检会先跑）
3. 侧栏大盘指数出现真实数据（AkShare/腾讯 fallback）
4. AI 对话发一条真实追问 → SSE 工具时间线滚动 → 回答落库
5. `netstat` 确认仅 127.0.0.1 监听
6. 关窗 → 托盘驻留 → 双击恢复 → 菜单退出干净

## 6. 已知边界

- akshare 动态 import 面广，冷启首问 15-40s（与开发态一致，非打包引入）
- WebView2 依赖系统自带（Win10/11 默认有）；无 WebView2 → launcher 自动回退浏览器模式
- exe 体积 ~200-300MB（pandas/akshare 全家桶），onedir 而非 onefile（onedir 启动快、杀毒误报低）
