# Electron 并行壳（SHELL_UPGRADE ③）

pywebview 壳（`desktop/launcher.py`）之外的**并行**桌面壳实验线——同一前端、同一后端 exe，
换 Electron 容器。失败不影响 pywebview 主线发布。

## 架构

```
desktop-electron/
├── main.cjs        # 主进程：spawn 主 exe → 解析 PORT= → BrowserWindow(frameless) → Tray
├── preload.cjs     # contextBridge 暴露 window.desktopAPI（IPC 白名单桥）
├── package.json    # npm run electron:dev / electron:build
└── electron-builder.yml  # nsis + portable 双产物，extraResource 打包主 exe
```

```
Electron main.cjs
  └─ spawn dist_m4/invest-concierge.exe --print-port --server   （无头后端，不起 pywebview 窗）
       └─ stdout 逐行匹配 ^PORT=(\d+)$                          （只认精确行，横幅/INFO 不误匹配）
  └─ BrowserWindow(frameless) loadURL http://127.0.0.1:{port}/?shell=frameless
  └─ Tray：显示主界面 / 退出
       └─ 退出 = taskkill /PID <spawn> /T /F（按 pid 树杀，不用 /IM——portable 版
          壳主进程也叫 invest-concierge.exe，/IM 会自杀）→ app.quit()
  └─ 后端自杀兜底：launcher --server 检测 stdin EOF（壳崩溃/被强杀时管道关闭即退出，无孤儿进程）
```

## IPC 设计（preload 白名单）

| 渲染进程调用 | 主进程行为 | 语义 |
|---|---|---|
| `desktopAPI.minimize()` | `win:minimize` | 最小化 |
| `desktopAPI.maximize()` | `win:maximize` 切换并返回新态 | true=已最大化 / false=已还原（React 图标 Square↔Copy 同步） |
| `desktopAPI.restore()` | `win:restore` | 还原 |
| `desktopAPI.close()` | `win:close` → close 事件被拦 | 隐藏到托盘（与 pywebview 壳托盘语义一致） |
| `desktopAPI.isMaximized()` | 查询 | 最大化态同步 |

前端 TitleBar 探测分支见 `frontend/src/app/layout/TitleBar.tsx`：`window.desktopAPI` 存在（Electron，
contextBridge 注入先于页面脚本）或 `window.pywebview` 存在（pywebview，加载后注入）+ URL `?shell=frameless`
门控，三按钮渲染缺一不可。

## 使用

```bash
# 开发（需先构建 dist_m4/invest-concierge.exe，见 docs/PACKAGING.md）
cd desktop-electron
npm install    # ELECTRON_MIRROR 走 npmmirror（国内）
npm run electron:dev

# 打包双产物（nsis 安装包 + portable 单文件，输出 release/）
npm run electron:build
```

## 打包坑位（实测沉淀）

- **winCodeSign 解压卡死**：electron-builder 下载 winCodeSign-2.6.0.7z 后 7z 解压对
  darwin/ 里的符号链接报"客户端没有所需的特权"（rc=2）无限重试。解法：手动下载
  npmmirror 同名 7z，用 builder 自带 7za 解压到 `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0\`
  （builder 缓存命中即跳过；darwin symlink 缺失不影响 win 打包）。
- **winCodeSign 下载卡死**：github 直连（release 资产）在 TUN 下 reset——用
  `ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/`。
- **portable 进程名**：electron-builder 把壳 exe 命名为 `invest-concierge.exe`（productName），
  与后端主 exe 同名——排查进程/杀树时按命令行特征区分（`--print-port --server`=后端；
  `--type=renderer`=Electron 子进程），不能用 `taskkill /IM invest-concierge.exe`（自杀）。
