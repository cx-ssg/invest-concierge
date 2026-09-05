# 桌面壳升级设计 v1 —— 安装包化 / frameless / Electron 评估

> 2026-09-05 · 用户拍板「三个都做，可回退使劲干」（decision dec-826070cddf8243b9）
> 前提：9/15 发布候选 ec9a1d2 不被破坏——每步独立提交，随时可回退单步
> 风险排序：①安装包化（低）→ ②frameless（中）→ ③Electron（高）；每步完成即独立可发布

## 0. 现状基线

- 壳：pywebview 6.2.1 + WebView2，原生标题栏，托盘（pystray），窗口 1180×720 默认
- 分发：dist_m4/invest-concierge.exe（59MB onefile，`--browser/--port/--no-tray` 参数）
- 启动链：start.bat → launcher.py → backend.py(uvicorn) + webview 窗口 + tray.py
- 用户痛点（推测+实测）：①免装体验差（要先装 Python+pip）②标题栏原生样式与自研 UI 割裂（黑色原生栏 vs 应用双主题）③无安装/卸载/版本管理

## 1. ① 安装包化（Inno Setup）——低风险，先做

### 1.1 交付物
`desktop/installer/invest-concierge-setup.iss` + 构建脚本 → 产出
`dist_m4/invest-concierge-setup-v1.0.0.exe`（安装器，~60-70MB）

### 1.2 安装器行为设计
- **安装内容**：主 exe（onefile 已含 Python/依赖/dist，安装器只是"搬运+注册"层，不重复打包 Python）
- **安装目录**：`{autopf}\invest-concierge`（默认 C:\Program Files\invest-concierge，用户可改）
- **开始菜单**：快捷方式组「投资私人管家」（主程序 + 卸载 + 可选"浏览器模式"快捷方式）
- **桌面图标**：可勾选（默认勾）
- **卸载器**：标准 Inno uninstaller；**额外清理**：`%LOCALAPPDATA%\invest-concierge`（SQLite 数据库/日记/会话——**卸载时询问"保留我的持仓与日记数据吗"，默认保留**）
- **版本信息**：AppVersion=1.0.0，Publisher=cx-ssg，卸载信息面板可显示
- **中文界面**：`Default.isl` 用 ChineseSimplified.isl（Inno 自带）
- **代码签名**：无证书（开源免费版，SmartScreen 提示"仍要运行"可过，release notes 里说明）

### 1.3 构建链
```
scripts/build_installer.(sh|bat)
  1. （可选）python -m PyInstaller 重打主 exe（--distpath dist_m4）
  2. "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" desktop/installer/invest-concierge-setup.iss
  3. 产物校验：存在 + 体积>50MB + 文件版本信息可读
```
⚠️ ISCC 路径：winget 已装 Inno Setup 6，实施时用 `where ISCC` 或 find 确认实际路径（候选：`C:\Program Files (x86)\Inno Setup 6\ISCC.exe`）

### 1.4 验收
- 安装器双击 → 安装成功 → 开始菜单启动 → 桌面版打开（功能全）
- 卸载 → 提问保留数据 → 数据目录保留/删除按选择生效
- 升级安装（装 v1.0.0 再装同版）→ 覆盖成功不报错

## 2. ② frameless 自绘标题栏——中风险

### 2.1 交付物
desktop/launcher.py 加 `--frameless` 开关（默认关，v1.0 保持原生；v1.1 默认开）+ frontend TitleBar 改造

### 2.2 技术方案（pywebview frameless API）
- `webview.create_window(frameless=True)` + `easy_drag=False`（自管拖动）
- **拖动区**：React TitleBar 空白区监听 mousedown → `window.pywebview.api.start_move()`（Python 端 `webview.windows[0].move()`）
  - pywebview 6.x 提供 `move()`；若版本不支持则降级用 Win32 SendMessage(WM_NCLBUTTONDOWN)——实施时实测
- **窗口按钮**：最小化/最大化/关闭三枚自绘（SVG，双主题适配：light 用 --text-2、hover --surface-2）
  - `minimize()` / `toggle_fullscreen()`（替代 maximize）/ `destroy()`→走现有 quit 链（托盘最小化逻辑复用 decide_close()）
- **双击标题栏**：最大化/还原切换
- **窗口边缘 resize**：frameless 模式 `easy_drag=True` 只保拖动不保 resize → 接受 v1.1 固定尺寸（或 6.x 的 `on_resized`+Win32 方案，风险高，后置）
- **托盘交互不变**：关窗→托盘逻辑照旧（decide_close 判断 quit 标志）

### 2.3 前端配合（frontend/）
- TitleBar.tsx 加 12px 拖动区 + 右上三按钮（仅 `window.pywebview` 存在时渲染，浏览器模式自动隐藏）
- 探测：`const isDesktop = 'pywebview' in window`（pywebview 注入桥）
- 标题栏高度 44px 不变（§UI_POLISH_PLAN 规格）

### 2.4 验收
- `--frameless` 启动：拖动区可拖窗、三按钮工作、双击最大化、关窗进托盘
- 浏览器模式（npm run dev）：TitleBar 无按钮无拖动区（不退化）
- 双主题下按钮图标对比度可读

## 3. ③ Electron 换壳评估——高风险，先评估后实施

### 3.1 评估结论（先给）
**建议：做，但定位为 v1.1.x 的并行产物，不替换 pywebview 主线**
- pywebview 版保留（轻量/60MB/系统 WebView2）作为"快速试用"分发
- Electron 版新增（体积 ~150MB）作为"完整桌面体验"分发（安装包化+frameless 全齐+未来托盘多态/自动更新）
- 理由：两者共用 100% 前端与后端，壳层隔离干净（launcher/backend/tray ≈ 300 行），维护成本可控；Electron 解决 WebView2 版本碎片（Win10 老机）+ 原生菜单/自动更新（electron-updater）——这是 v1.1 真正的升级收益

### 3.2 结构（新增不替换）
```
desktop-electron/
├── main.cjs            # 主进程：起 uvicorn(child_process) + BrowserWindow + 托盘 + 自绘标题栏(IPC)
├── preload.cjs         # contextBridge: window.desktopAPI {minimize/maximize/close/startMove}
├── package.json        # electron + electron-builder 配置（win nsis 目标）
└── builder.yml         # electron-builder: nsis 安装包 + portable 双产物
```

### 3.3 主进程要点
- 后端：`spawn(sys.executable 的打包版 or pythonw, ['-m','uvicorn',...])`？——**不**。Electron 版复用同一 exe 逻辑太重；改为：electron-builder 打包时把 `dist_m4 exe` 作为 extraResource，主进程 spawn 它 `--no-tray --port 0`（端口自适应→stdout 解析实际端口）→ BrowserWindow loadURL(`http://127.0.0.1:{port}`)
- 窗口：frameless 自绘（同②设计，IPC 版）、宽高 1180×720、backgroundColor #1A1B1E
- 托盘：Electron Tray + Menu（恢复/退出）
- 退出链：窗口 close → 隐藏到托盘；托盘退出 → kill 后端进程树 → app.quit()

### 3.4 风险与对策
| 风险 | 对策 |
|---|---|
| spawn exe 端口解析不稳 | exe 加 `--print-port` 参数（launcher.py 顺手加，打印 `PORT=xxxx` 行）|
| electron 下载慢/被墙 | `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/` |
| 双壳功能漂移 | ②的 TitleBar 改造做成双栈兼容（pywebview 桥 vs desktopAPI 桥，探测哪个用哪个）|
| 发布日临近 | ③ 完全独立目录，失败不阻塞 ①②；v1.0 照发 pywebview 版 |

### 3.5 验收
- `npm run electron:dev`：窗口起 + 后端 spawn 成功 + 端口自适应 + UI 完整
- electron-builder 产 nsis 安装包 + portable exe 双产物
- 托盘/自绘标题栏/双主题全功能

## 4. 实施顺序与回退点

| 步骤 | 产出 | 提交点（回退锚） | 预计 |
|---|---|---|---|
| ① | setup.exe 安装器 | `feat(installer): Inno Setup 安装包化` | 0.5 天 |
| ② | --frameless 壳 | `feat(desktop): frameless 自绘标题栏（--frameless 开关）` | 1 天 |
| ③ | desktop-electron/ | `feat(electron): 并行壳 v1（dev 跑通）` | 1-2 天 |
| ④ | 终态回归 | pytest 130 + build/lint + 两壳冒烟 + 安装器实装实测 | 0.5 天 |

- 每步独立 commit，单步 revert 不影响其他步
- ③ 失败 → 回退到 ②终态，v1.0 照常发布（①② 均为 pywebview 线增强）
- 9/15 发布前如果只完成 ①：也值得发（安装器是纯增益）

## 5. 执行分工（沿用现有协作链）

- zcode：①② 实施（今天上下文最热，壳代码都是它 M3/M4b 写的）
- DSH：③ Electron 骨架（新目录新栈，适合冷启动探索）
- Reasonix：每步核验 + 冲突协调 + 终态验收
