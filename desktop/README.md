# desktop/ — M3 桌面壳（pywebview 包 FastAPI + React）

用 pywebview(WebView2) 把后端 FastAPI（services/server/main.py）+ 前端（frontend/）
包成 Windows 原生桌面应用：双击进桌面版，关窗最小化到托盘。

## 启动

```bat
:: 双击桌面版（生产模式：FastAPI 托管 frontend/dist）
desktop\start.bat

:: 等价命令（一条命令进桌面版）
python desktop\launcher.py

:: 开发模式：窗口指向 Vite 5173（需先 cd frontend && npm run dev）
python desktop\launcher.py --mode dev

:: 只起后端 + 自动开浏览器（桌面壳不可用时的回退，功能零损失）
python desktop\launcher.py --browser

:: 其他参数
python desktop\launcher.py --port 8123   :: 强制后端端口
python desktop\launcher.py --no-tray     :: 关窗直接退出（调试）
python desktop\launcher.py --debug       :: pywebview 开发者工具
```

## 生产 / 开发模式怎么切

| 模式 | 命令 | 前端来源 | 后端 |
|---|---|---|---|
| 生产（默认） | `python desktop\launcher.py` | FastAPI 托管 `frontend/dist`（同源） | 内嵌 uvicorn @ 127.0.0.1 |
| 开发 | `python desktop\launcher.py --mode dev` | Vite dev server `http://localhost:5173` | 同左（vite proxy / CORS 打到后端） |

端口策略（backend.py）：优先 8000；被占用时若已是本应用实例（/api/health ok）则复用；
否则自动挑空闲端口。前端 dist 已按**相对 /api** 构建（`VITE_API_BASE=` 空值重建），
因此任意端口都同源可用，无 8000 硬编码。

## 实现要点

- `backend.py` — BackendServer：守护线程跑 uvicorn（`server.main:app`），就绪探测 /api/health，优雅停止。
- `tray.py` — pystray 托盘（Pillow 画图标）：菜单「显示主窗口 / 退出」，双击/单击恢复。
- `launcher.py` — 主入口：模式切换、端口决策、pywebview 原生窗口（v1 不做 frameless）、
  关窗 → `window.hide()` + 取消关闭（回到托盘）、托盘「退出」→ 销毁窗口停后端。
  GUI 任何环节失败 → 提示 + 自动浏览器回退。
- `smoke_test.py` — 自测（`desktop\.venv\Scripts\python.exe desktop\smoke_test.py`）：
  依赖/dist 相对 base/端口策略/内嵌后端/关窗决策/开发模式/GUI 冒烟(子进程)/托盘冒烟。

## 依赖

`requirements.txt` 已追加 `pywebview` / `pystray` / `pillow`（pywebview Windows 后端 = WebView2，
Win10/11 自带 EdgeChromium Runtime，无需额外安装）。

注意：本目录 `.venv/` 是沙箱自测用虚拟环境（--system-site-packages），已 gitignore；
真机直接 `pip install -r requirements.txt` 后 `python desktop\launcher.py` 即可。