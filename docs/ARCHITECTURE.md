# 架构详解 · invest-concierge

> 生成：2026-09-08（M4 发布打磨同步为当前 FastAPI + React + 桌面壳架构；数据层/AI 引擎部分沿用原文档）

## 一、分层结构

```
desktop/launcher.py          桌面壳入口（pywebview 原生窗口 + pystray 托盘）
 ├─ desktop/backend.py       内嵌 uvicorn（127.0.0.1，优先 8000，被占自动换空闲端口/复用）
 ├─ desktop/tray.py          系统托盘（关窗最小化，托盘「退出」结束）
 └─ server/main.py           FastAPI 应用（REST + SSE + frontend/dist 静态托管）
     ├─ server/routers/      core / holdings / diary / agent / diagnosis / settings
     ├─ services/            业务服务层（diagnosis_service / agent_service / settings_service …）
     ├─ utils/               ai_helper（LLM 入口）/ agent_core（工具注册表）/ agent_memory（会话记忆）
     └─ data/                数据层（14 模块：行情/财报/估值/资金/情绪/排雷/护城河）
         └─ SQLite (database.py) 持久化：持仓/自选/日记/预警

frontend/                    React 19 单页前端（Vite + TypeScript + Tailwind v4）
 ├─ src/pages/               6 个 live 页（ai_chat / settings / fund:dashboard,portfolio,diary / stock:stock_diagnosis）
 ├─ src/features/            agent（SSE 状态机）/ diagnosis（6 tab）/ holdings
 └─ dist/                    构建产物，由 server/main.py 挂载为静态根
```

## 二、请求流（前端 → 后端 → 数据）

```
React 页面 → REST/SSE(/api/*) → services/ → data/ 模块 → (AkShare/新浪/腾讯/东财 免费接口) → DataFrame
                                  ↓ 缓存                        ↓ fallback
                           data/cache.py TTL             弱网自动切备用源，失败降级显示「--」
                           SQLite 持久化
```

- FastAPI 只 bind 127.0.0.1（本机桌面壳/浏览器，不暴露局域网）；CORS 放行 Vite dev server 5173。
- 前端按相对 `/api` 构建（VITE_API_BASE 默认空），FastAPI 同源托管，任意端口可用。
- AI 对话走 SSE 流式事件契约：`status → reasoning×N → tool_start/tool_end → done`。

## 三、AI 引擎（utils/ai_helper.py + agent_core.py）

- `call_llm(prompt, tools, model)`：统一 LLM 入口，OpenAI SDK 兼容 DeepSeek API；支持工具调用；错误分级返回（timeout/connection/401/429）。
- `agent_run(...)`：**规划循环**（默认最多 8 轮），Agent 通过 11 个工具（行情/财报/持仓/日记等）自主取数；每轮 error 回填须明说「数据不可得」。
- `agent_memory`：会话消息持久化 + 满 8 轮摘要注入，最近 3 条摘要随对话带入。
- 工具注册表晚绑定（importlib 按「模块.函数名」解析），存量数据函数无需改造即可被 Agent 调用。
- 无 Key → 页面显示引导卡，不调用（避免空转）。

## 四、双轨导航

- `utils`/`ui_components` 内的 `PAGE_META`：21 页轨道归属（基金轨 10 / 股票轨 7 / 通用 4）——与渲染解耦，是 Roadmap 生成源。
- 新前端 6 页按 `/fund/*`、`/stock/*`、`/settings` 组织；旧 Streamlit 双轨导航（`ui_components/sidebar.py`）保留于 `pages/`（不参与新 UI）。
- 占位页 live=false 不进导航（只隐藏不删除）。

## 五、桌面壳（desktop/）

- 端口策略：优先 8000（空闲则自起）→ 8000 上是本应用且已托管 dist 则复用 → 否则挑空闲端口。
- 任何 GUI 环节不可用（缺 pywebview / 无 WebView2 / 窗口起不来）→ 提示 + 自动浏览器回退，后端继续跑，功能零损失。
- 双击入口：`desktop/start.bat`（依赖自检 + 兼容 local_env.bat / .env 读 Key）。

## 六、测试与质量

- `pytest tests/`：110 用例（工具注册表 / Agent 规划 / 记忆 / M0 桥 / 排雷 / 估值 / 导航），全绿。
- 前端：`cd frontend && npm run build`（tsc 类型检查 + vite 构建）。
- 桌面壳：`python desktop/smoke_test.py`（依赖 / dist 产物相对化 / 端口策略 / 内嵌后端 / GUI·托盘冒烟）。
- CI：GitHub Actions 双矩阵（Python 3.9 / 3.11）pytest + gitleaks 密钥扫描。
- 发布前 `gitleaks detect` 防 key 泄露（`.env` / `local_env.bat` 已 gitignore；`.env.example` 为无密钥模板）。

## 七、数据源说明

| 数据源 | 用途 | 性质 |
|---|---|---|
| AkShare | A 股行情/财报/资金/情绪 | 免费公开 |
| 天天基金 | 基金净值/估值 | 免费公开 |
| 新浪/腾讯/百度 | 股票行情 fallback | 免费公开 |
| DeepSeek API | AI 对话/辩论（可选） | 需 Key |

*备注：akshare 接口可能随时间变动（如 v1.18.64 移除部分旧接口），页面已做优雅降级显示「数据缺失」，详见 docs/verification.md。*