我先说明本轮技能使用：任务核心是「前端重写执行方案」，匹配 `fund-agent-architecture`（需先理解后端架构/目录/技术栈），另按 AGENTS.md Session Gate 需读原文。先读必读文档 + 架构技能 + 探目录。
Shell 报沙箱 runner 错误。重试（换 login 参数）。
Shell runner 故障。改用 codegraph（不同执行通道）探测项目结构与关键符号，先读架构。
重试 shell（显式 workdir），若仍失败再想办法。
Shell 通道彻底故障。改用 node_repl 尝试读取文件系统（若可用则作为只读通道）。
node_repl 可用，作为只读通道。先列 docs 与 pages/data/utils 结构及大小。
结构清楚了：data/16 模块、pages/ 21 个页面模块（含主 app）。现在读必读文档，先看体积再分块读。
现在完整读三份必读文档。
已读 UI_REWRITE_DESIGN。接着读 BACKEND_REVIEW_BRIEF + BACKEND_REVIEW_CODEX 全文。
三份必读已读。现在读后端关键源码：agent_core.py（on_progress）、app.py、web_agent.py、config.py。
核心 agent_run 已明。现在读 app.py / web_agent.py / config.py / ARCHITECTURE.md，弄清现路由与启动方式。
读 sidebar.py（PAGE_META 双轨/live 标志）+ styles.py（现有 token），并抽取各页面 def 清单做映射。
读小页面全文 + 两大页 def/import 结构，弄清页面逻辑归属。
抽取 ai_chat.py / stock_diagnosis.py 的结构：imports、def、数据函数调用、streamlit 使用密度。
映射数据层公共函数签名（全部 data/ 模块 + agent_memory/sentiment/diary_tool），供路由清单用。
查 st 泄漏具体上下文 + agent_memory 会话函数签名。
读 agent_memory / market_sentiment_merged / diary_tool + common 函数签名；并读 ai_chat 关键段（_on_progress / _handle_prompt）确认进度机制。
读 ai_chat.py 关键渲染段（241–512）确认会话/消息/进度现有实现，作为 SSE 契约对照。
读 stock_diagnosis AI 段（596–755）+ 顶部（1–52）+ utils/chart.py + requirements.txt。
读 diagnosis.py（payload 结构）、utils/chart.py、requirements.txt，补齐图表与依赖事实。
补最后事实：tests/ 清单（回归口径）、pytest.ini、有无现成 service/api 目录。
读 ai_helper.call_llm（32–115）确认 reasoning/type 返回结构，作为 SSE 契约依据。
所有事实已核清。整理关键基线后产出 `docs/FRONTEND_PLAN.md`。先尝试写入文件（沙箱只读，若被拒则全文放到回复中）。
写入被沙箱只读策略拦截（`writing is blocked by read-only sandbox`），无法落盘到 `docs/FRONTEND_PLAN.md`。按「方案交付」本职，以下为完整可执行的方案全文，可直接另存为 `docs/FRONTEND_PLAN.md`（UTF-8）。所有事实均来自今日对 `data/`16 模块、`pages/`、`utils/agent_core.py`、`web_agent.py`、`app.py`、`config.py`、三份 docs 的逐行实读。

---

# React 前端重写执行方案（invest-concierge · Reasonix 质感）

> 2026-09-03 · 前端架构师（同日已评审后端）· 状态：**方案（只读，未实施）**
> 前置必读：`docs/UI_REWRITE_DESIGN.md`、`docs/BACKEND_REVIEW_BRIEF.md §3.5`、`docs/BACKEND_REVIEW_CODEX.md §3`
> 一句话目标：用 **React 桌面工作台** 替换 Streamlit 展示层；后端数据/Agent 引擎（16 data 模块 + 93 测试）不动，只加一条 FastAPI 桥。

---

## 0. 事实基线（2026-09-03 代码实读，非估计）

### 0.1 代码地形

| 目录 | 事实 |
|---|---|
| `data/` | 16 个数据模块；统一 `@cached(TTL)` 内存缓存 + 失败缓存 + 多源 fallback；SQLite 已 WAL + busy_timeout |
| `pages/` | 20 个路由页 + `stock_deep_analysis.py` 占位（共 21 模块）。**live 渲染集仅 6 页**（见 0.2） |
| `ui_components/` | sidebar(PAGE_META) / holdings_card / data_table / market_indicator / styles / fund_card |
| `utils/` | `agent_core.py`（agent_run / 11 工具）/ `agent_memory.py`（会话记忆 SQLite）/ `ai_helper.py`（call_llm / multi_agent_stock_analysis）/ `common` / `chart`(matplotlib 渲染) / `diary_tool` |
| `tests/` | 10 个文件（agent_core / ai_tools / clean_number / common / memory / minefield / multi_agent / navigation / tool_registry / valuation），合计 93 用例 |
| `requirements.txt` | streamlit / pandas / numpy<3 / akshare 1.18.x / openai / requests / matplotlib / markdown —— **无 fastapi / uvicorn / pywebview / pystray** |

### 0.2 页面现状与「先做 / 后置」总账（PAGE_META 口径）

live（v1.0 渲染集，**React M2 全部先做**）：

| 页面 | 轨道 | 现页面真实职责 | 抽到 service |
|---|---|---|---|
| `ai_chat` AI 对话 | 通用 | agent_run + 会话记忆 + 工具链折叠 + 无 Key 引导（512 行，真页） | `services/agent_service.py` |
| `stock_diagnosis` 综合诊断 | 股票 | build_diagnosis_payload → 6 tab + AI 辩论/追问（755 行，真页） | `services/diagnosis_service.py` |
| `dashboard` 资产总览 | 基金 | 薄：render_holdings_card | `services/holdings_service.summary` |
| `portfolio` 我的持仓 | 基金 | 薄：render_holdings_card | `services/holdings_service` |
| `diary` 投资日记 | 基金 | 薄：load_diary 列表 | `services/diary_service` |
| `settings` 系统设置 | 通用 | 薄：只读 API 配置展示 | `services/settings_service` |

非 live（**后置**，现 Streamlit 也不渲染 = 占位；React 在 M4 按此序扩）：

| 批次 | 页面 | 复用后端函数（已存在，纯拉 JSON 即可） |
|---|---|---|
| P1 | `market` 行情 / `stock_market_overview` 市场全景 | get_market_index / get_market_sentiment / get_limit_up_review_data / get_market_moneyflow / get_hot_sectors |
| P1 | `stock_search` / `fund_search` 搜索 | search_stock / search_fund |
| P1 | `compare` 基金对比 | compare_funds（**M0 先结构化**） |
| P1 | `dingtou_calc` / `backtest` / `dingtou` | backtest_strategy（纯 dict 已就绪）/ calc_fund_metrics |
| P2 | `watchlist` / `stock_holdings` | database watchlist/stock_holdings CRUD（SQLite 函数已齐） |
| P2 | `stock_deep_analysis` | = 诊断页延伸（build_diagnosis_payload 子集），低 |
| P2 | `alert` / `profile` | load/save_alert_settings（最低） |

> 铁律：React 导航 = PAGE_META live 集逐条对齐；**每把一个页面 live 才进导航**，杜绝「导航有、页面空」。

### 0.3 四个对接硬事实（决定方案走向）

1. **`agent_run` 的 `on_progress(stage, detail)` 中 detail 是字符串**：`reasoning`/`writing` = 整段文本；`tool` = `"name(arg1=v1, arg2=v2)"`（**只有前 2 个参数**），无完成回调、无耗时、无成功态。→ 直接透传 SSE **拿不到时间线要的结构化数据**，M0 需做一次**最小、非破坏**的 agent_core 扩展（§2.2 #4）。
2. **`call_llm` 非流式**：`reasoning_content` 整轮思考结束后一次性返回（每轮一次 `_progress("reasoning", …)`），无逐 token。→ SSE 事件是**分轮分块**推送；「逐字渲染」由前端打字机实现（对齐 UI_REWRITE_DESIGN §2.4）。token 级流式（DeepSeek `stream=True`）列为 M4 可选增强，不阻塞 v1。
3. **st 泄漏 4 模块**（与 BACKEND_REVIEW_CODEX §3 一致）：`market_api.py` 两处 `@st.cache_data`、`database.py:411 load_funds`、`fund_api.py calc_dca`（整段 st 渲染）、`ai_helper.py _is_demo_mode`（读 session_state）。除 calc_dca 外都是「拿 st 当缓存/会话用」的技术债，最小解法见 §2.2。
4. **性能现状**：诊断 `build_diagnosis_payload` 冷启 15–40s（24h 缓存，二次秒回）；东财✗/新浪403/腾讯✅；深诊各引擎另有 7–8 次串行网络。→ 所有慢接口必须有超时 + 明确 loading + 可取消，前端/桥两侧都要防悬挂。

---

## 1. 阶段划分（里程碑 + 天数，单人全栈）

| 里程碑 | 内容 | 预估 | 出口（可验证） |
|---|---|---|---|
| **M0** FastAPI 桥 + 后端去 UI | services/ + server/ 骨架、st 泄漏清零、缓存加锁、compare_funds 结构化、agent_core 结构化进度 hook、SSE 通道、会话/持仓/日记/诊断 API 全通 | **4 天** | `pytest tests/` 93 全绿；`grep -rn "import streamlit" data/ utils/ services/ server/` = 0；curl SSE 见流式事件 |
| **M1** React 骨架 + 设计系统 | Vite+React19+TS、token→Tailwind/CSS、三区壳（TitleBar/Sidebar/StatusBar）、组件基元、Agent 时间线组件初型、路由骨架 = live 6 页 | **4 天** | `npm run build` 通过；浏览器三区布局 + 终端青 token 目视 |
| **M2** 核心页面 | AI Chat（SSE 流 + 工具链时间线 + 会话侧栏）、诊断页 6 tab + AI 辩论/追问、资产总览/持仓/日记/设置、无 Key 降级 | **6 天** | 「诊断 600519 → 6 tab 数据全 + Agent 追问跑通（6 连工具时间线 ✓/●/✕ + 耗时）」；持仓增删写 SQLite；`pytest` 仍绿 |
| **M3** 桌面壳 | pywebview 包 FastAPI+React、自定义标题栏 + 托盘、双击启动脚本 | **3 天** | 双击进原生窗口；托盘/最小化到托盘；浏览器回退可用 |
| **M4** 发布打磨 | 非 live 页面按 §0.2 扩展、PyInstaller exe 冒烟、图标/README/截图、DeepSeek token 流式可选增强 | **4–6 天** | exe 可分发的单机版；v1.0 全量回归 |

**合计**：M0–M3 ≈ **17 天**（关键路径）；M2 末（≈14 天）React 已可浏览器全功能使用。
**并行**：M0 与 M1 无强依赖（后端清理 vs 前端骨架可并行），2 人可压到 ~10 天；M3 需等 M2。
**并存策略**：Streamlit 保留至 M4 前（`streamlit run app.py` 全程可跑、只增不改），确保 93 测试与旧路径永不被破坏；M4 决定退役时间点。

---

## 2. FastAPI 桥

### 2.1 工程布局（新增两个顶层包，沿用全小写单数风格）

```
services/                 # 纯 Python：聚合 data/* 出「JSON 可序列化 dict」，零 FastAPI/st 依赖
  _json.py                # sanitize：DataFrame→records、NaN/inf→None、Timestamp→ISO（全桥共用）
  status_service.py       # /api/health、/api/status（状态栏数据）
  agent_service.py        # agent_run 包装：同步 run / stream（SSE 事件源）/ 会话 CRUD
  diagnosis_service.py    # build_diagnosis_payload + 追问 + AI 辩论 的 JSON 编排
  holdings_service.py     # 持仓 list/add/update/delete + summary（原 render_holdings_summary 逻辑）
  diary_service.py        # 日记 list/add/delete
  settings_service.py     # 只读配置 + demo 开关 + cache 清理
  (P1) market_service.py / fund_service.py / search_service.py
server/                   # FastAPI 薄层（只做参数校验 + 调 services + 返 JSON）
  main.py                 # create_app()：挂 CORS(dev) + StaticFiles(dist) + routers
  routers/ core.py agent.py diagnosis.py holdings.py diary.py settings.py
  (P1) market.py stock.py fund.py
frontend/                 # M1 起（见 §3）
```

约束：模块 ≤500 行；全部 `open(encoding='utf-8')`；服务只 `bind 127.0.0.1`；静态资源用 FastAPI `StaticFiles(html=True)`（桌面壳单端口）。
`requirements.txt` 新增：`fastapi` / `uvicorn[standard]`（桌面壳再加 `pywebview` / `pystray` / `pillow`）。

### 2.2 M0 后端最小改造清单（每项都带测试）

| # | 改动 | 具体 | 测试 |
|---|---|---|---|
| 1 | `market_api.py` 去 st | 删 `import streamlit` + 两处 `@st.cache_data(ttl=300)`（`get_market_index`/`get_hot_sectors`）。内层 `@cached(CACHE_QUOTE/CACHE_SECTOR…)` 保留 = 纯 Python 缓存 | 断言无 st 也能缓存 |
| 2 | `database.py:411 load_funds` | 删 409 行 `import streamlit as st` + `@st.cache_data`；`load_my_funds()` 是权威实现，`load_funds` 保留为无装饰器兼容别名；`_invalidate_funds_cache` 已容错无需改 | 存量 navigation/common 相关 |
| 3 | `MemoryCache` 加锁 | `data/cache.py` 加 `threading.RLock` 包 `get/set/clear_by_prefix`（评审🟡遗留，M0 顺手清） | 双线程写读 |
| 4 | **agent_core 结构化进度 hook（SSE 根基，非破坏）** | `agent_run` 增可选参 `structured_progress=False`。默认走原字符串路径（存量 pages/测试**零改动**）；为 True 时工具调用前发 `("tool_start", {"name","args"})`、`execute_ai_tool_v2` 返回后发 `("tool_end", {"name","ok","elapsed_ms"})`；`reasoning/writing` 不变 | 默认行为不回归 + True 时收到 start/end 对与耗时 |
| 5 | `fund_api.py calc_dca` 拆渲染 | 渲染整段留文件但加 `_UI_ONLY` 注释标记；新增 `dca_result(...)` 纯返回 dict（= 现 `backtest_strategy` DCA 分支）。API 只调纯函数 | dict 返回断言 |
| 6 | `compare_funds` 结构化 | 现有返 Markdown 保留（agent 工具在用勿动签名）；新增 `compare_funds_structured()` 返 JSON（评审 §3 要求） | 结构断言 |
| 7 | `ai_helper._is_demo_mode` | 删 session_state 依赖：模块级 `_demo_flag` + `set_demo_mode(bool)`；无 st 时读 flag（不再拉 st） | demo 开关 |
| 8 | 补 `database.delete_diary_entry(id)` | load→filter→save（现只有整表 save；React 日记页需要删单条） | 删除断言 |
| 9 | `services/_json.py` | `to_jsonable(x)`：DataFrame→records、NaN/±inf→None、Timestamp→ISO | 含 600519 诊断 payload 实测序列化 |

### 2.3 路由清单（url → 后端函数）

> 慢接口列已知耗时口径；全部 GET 走 `ThreadPoolExecutor`（uvicorn 单 worker 下避免阻塞事件循环）。

**系统 / 状态栏**

| 方法 + 路径 | 后端函数 | 说明 | 批次 |
|---|---|---|---|
| GET `/api/health` | status_service.health | `{status, api_key_configured, version, ts}` | M0 |
| GET `/api/status` | status_service.status_bar | 状态栏：引擎状态点 + 数据源健康 + 上次刷新 + Agent 版本（get_market_index + get_market_sentiment 带 8s 超时，弱网降级——对齐现有 `SIDEBAR_FETCH_TIMEOUT` 经验） | M0 |
| GET `/api/nav` | **抽 `PAGE_META` live 集 → `services/nav_service.py`（不 import sidebar.py，防拉 st）** | 返回 `{fund:[…], stock:[…], common:[…]}` 全 live | M0 |

**持仓 / 资产 / 日记（M2 页面）**

| 方法 + 路径 | 后端函数 | 批次 |
|---|---|---|
| GET `/api/holdings/funds` | holdings_service.list（load_my_funds + 逐只 calc_fund_metrics 聚合） | M0 |
| POST `/api/holdings/funds` | holdings_service.add（save_fund_holding + 失效缓存） | M0 |
| PUT `/api/holdings/funds/{code}` | holdings_service.update | M0 |
| DELETE `/api/holdings/funds/{code}` | holdings_service.delete | M0 |
| GET `/api/dashboard/summary` | holdings_service.summary（现 render_holdings_summary 纯 JSON 版） | M0 |
| GET/POST `/api/diary` · DELETE `/api/diary/{id}` | diary_service（list / utils.diary_tool.add_diary / delete_diary_entry） | M0 |

**Agent / 会话 / AI Chat（M2 核心）**

| 方法 + 路径 | 后端函数 | 说明 | 批次 |
|---|---|---|---|
| GET `/api/agent/config` | agent_service.config | `{api_key_configured, chat_model, reasoner_model}` | M0 |
| GET `/api/agent/sessions` | list_agent_sessions + list_recent_agent_sessions | 会话侧栏 | M0 |
| POST `/api/agent/sessions` | create_agent_session | | M0 |
| GET `/api/agent/sessions/{id}/messages` | get_agent_messages → 过滤 user/assistant 文本 | 历史回放 | M0 |
| PATCH `/api/agent/sessions/{id}` | rename / toggle_pin / toggle_archived | | M0 |
| DELETE `/api/agent/sessions/{id}` | delete_agent_session | | M0 |
| POST `/api/agent/chat` | agent_service.run（同步 JSON，非流式 fallback） | 120s 超时 | M0 |
| **POST `/api/agent/chat/stream`** | agent_service.stream（**SSE**，见 §5） | 诊断追问共用（`context` 带诊断数据 + 记忆） | M0 |
| GET `/api/agent/runs/{run_id}/events` | agent_service.poll（SSE 被代理挡时的**轮询降级**，队列暂存 300s） | 可选 | M1 |

**诊断页（M2）**

| 方法 + 路径 | 后端函数 | 说明 | 批次 |
|---|---|---|---|
| GET `/api/stocks/{code}/diagnosis` | diagnosis_service.get → `data.diagnosis.build_diagnosis_payload` | 24h 缓存；冷启 15–40s → 后台线程 + 前端 loading；`_json.to_jsonable` 全量清洗 | M0 |
| POST `/api/stocks/{code}/diagnosis/ask` | diagnosis_service.follow_up（= 现 `_run_diag_follow_up`：诊断 JSON + build_memory_context → agent_run 追问，支持 `?stream=1`） | 原 755 行页追问逻辑迁入 | M2 |
| POST `/api/stocks/{code}/ai-debate` | ai_helper.multi_agent_stock_analysis（30–60s，**P1 接 SSE**，v1 先同步） | AI 辩论 tab | M2 |

**行情 / 个股 / 基金（P1+，复用现 data 函数直接返 JSON）**

| 方法 + 路径 | 后端函数 | 批次 |
|---|---|---|
| GET `/api/market/{indices,sectors,sentiment,moneyflow,limitup}` | get_market_index / get_hot_sectors / utils.market_sentiment_merged.get_market_sentiment / get_market_moneyflow / limit_up_api.get_limit_up_review_data | P1 |
| GET `/api/stocks/search` · `{code}/info` · `{code}/kline?days&ktype` · `{code}/valuation` · `{code}/moneyflow` | search_stock / get_stock_info / get_stock_kline（**K线 JSON：dates+OHLC+vol**）/ data.stock_valuation.get_valuation_data / get_stock_moneyflow | P1 |
| GET `/api/funds/search` · `{code}/info` · `{code}/history` · POST `/api/funds/compare` · POST `/api/funds/backtest/dca` | search_fund / get_fund_info / get_fund_history / compare_funds_structured / backtest_strategy | P1 |
| watchlist / 股票持仓 CRUD | database（watchlist / stock_holdings 组） | P2 |

### 2.4 并发与生命周期

- **请求**：慢 GET 丢 `ThreadPoolExecutor(max_workers=8)`；每条带超时（诊断 120s、行情 10s、LLM 120s）；前端 AbortController 取消 → FastAPI `ClientDisconnect` → 释放 worker。
- **写序列化**：funds/diary 是「整表读-改-写」，FastAPI 多线程下必须加**进程内 per-file `threading.Lock`**（services 层持有），防双写覆盖（评审丢首笔 bug 的根治）。
- **缓存**：进程内存级；FastAPI 与 Streamlit 并存期两者独立（可接受，M4 退役 st 后归一）；缓存键含失败缓存，慢源挂时秒回「数据不可得」。
- **JSON 化**：一切响应过 `_json.to_jsonable`；禁止裸 DataFrame 返前端（NaN/±inf 会变非法 JSON）。

---

## 3. React 技术栈

### 3.1 选型清单（库名 + 理由 + 备选）

| 角色 | 选用 | 理由 | 备选 |
|---|---|---|---|
| 构建 | **Vite**（React 模板） | 秒级 HMR，桌面壳静态产物干净 | — |
| 框架 | **React 19 + TypeScript(strict)** | 对齐 UI_REWRITE_DESIGN §1「React 19」 | — |
| 路由 | **react-router（HashRouter）** | pywebview/file 场景无服务端 path，hash 最稳 | MemoryRouter |
| 状态 | **zustand** + **@tanstack/react-query** | zustand 管引擎/会话/SSE 流缓冲；react-query 管服务端数据缓存与 mutation 后失效 | Redux Toolkit（过重） |
| 样式 | **Tailwind CSS v4**（`@tailwindcss/vite`，CSS-first）+ tokens.css | 原子类提速；**token 以 CSS 变量为唯一权威**（见 §4） | 纯 CSS Modules |
| 图标 | **lucide-react**（16px 线条） | Reasonix 同款 | — |
| Markdown | **react-markdown + remark-gfm** | Agent 回复渲染 | — |
| 图表 | **ECharts 5（echarts + 自写浅封装）** | 一套主题绑定 token 覆盖 **K线/估值折线(分位)/财报柱状/持仓饼**，免多库主题割裂 | lightweight-charts（K线手感优先可换，二选一） |
| 打字机 | 自写 `<Typewriter>` | 克制动效，不引动画库 | — |

版本纪律：以开工日 npm 最新稳定为准（写死主版本防漂移）：react@19、zustand@^5、@tanstack/react-query@^5、react-router-dom@^7、tailwindcss@^4、echarts@^5.6+；Node ≥ 20 LTS。

### 3.2 状态架构

```ts
stores/engine.ts    // 状态栏：引擎点(ready/thinking/off) + 数据源健康 + 上次刷新 + agent 版本
stores/nav.ts       // track(fund/stock) + currentPage（与 /api/nav 对齐）
stores/agent.ts     // 消息流、活跃流、会话列表、toolTimeline[]（SSE 落点，见 §5）
stores/holdings.ts  // 轻量镜像 + 刷新触发器（真数据归 react-query）
queries/            // useDiagnosis(code) / useHoldings() / useDiary() / useSessions() …
```

### 3.3 `frontend/` 目录

```
frontend/src/
  styles/tokens.css + global.css
  app/Shell.tsx (三区骨架) · layout/TitleBar.tsx Sidebar.tsx StatusBar.tsx Panel.tsx
  components/ui/     Card Badge StatusDot Tabs Kicker Hairline DataTable Number
  components/engine/ ToolTimeline.tsx ThinkingFlow.tsx Typewriter.tsx AgentBubble.tsx
  features/agent/    stream.ts(SSE reader) ChatPage/ ChatSessionBar/ Timeline/
  features/diagnosis/ DiagnosisPage/ tabs/(6) charts/
  pages/  chat diagnosis dashboard holdings diary settings
  lib/api.ts  charts/echartsTheme.ts  stores/  types/
```

### 3.4 开工前拍板（M0 入口对一次，避免瞎猜）

1. 图表 K线：ECharts 单库（推荐）还是 lightweight-charts？
2. 自定义标题栏 + 托盘：M3 直接试 frameless（WebView2 支持 `app-region: drag`，带回退开关）还是先原生标题栏？
3. React 包放**仓库内 `frontend/`**（推荐，与 93 测试同仓 CI）还是独立仓库？
4. 品牌名 invest-concierge + `#4C8BF5` 终端青（按 UI_REWRITE_DESIGN 已定）。

---

## 4. Reasonix 质感落地（token → CSS 变量 → Tailwind）

### 4.1 设计 token（唯一权威 = UI_REWRITE_DESIGN §2.1）落成 `styles/tokens.css`

```css
:root {
  --bg:#1A1B1E; --bg-sidebar:#141517;
  --surface:#232428; --surface-2:#2C2E33;
  --accent:#4C8BF5; --accent-soft:rgba(76,139,245,.12);   /* 唯一彩色强调 */
  --text-1:#E6E6E6; --text-2:#9A9DA3; --text-3:#6B6E73;
  --line:rgba(255,255,255,.07); --line-strong:rgba(255,255,255,.14);
  --up:#F04438; --down:#2DBE64;                              /* A股红涨绿跌，仅有的语义色 */
  --radius:8px; --radius-sm:4px;
  --font-mono:Consolas,"Cascadia Mono",monospace;
  --ease:cubic-bezier(.2,.6,.3,1); --dur:160ms;
}
```

Tailwind v4 桥接（CSS-first，`@theme` 只做别名引用，色值不复制）：

```css
@theme {
  --color-bg: var(--bg); --color-bg-sidebar: var(--bg-sidebar);
  --color-surface: var(--surface); --color-surface-2: var(--surface-2);
  --color-accent: var(--accent);   /* bg-accent / text-accent / border-accent 直接可用 */
  --color-ink: var(--text-1); --color-ink-2: var(--text-2); --color-ink-3: var(--text-3);
  --color-hairline: var(--line); --color-hairline-strong: var(--line-strong);
  --color-rise: var(--up); --color-fall: var(--down);
}
```

纪律（进组件评审 checklist）：彩色只剩 `--accent` + 涨跌；圆角 ≤ `rounded-lg(8px)`；边框一律 `border-hairline`；数字容器加 `tabular-nums`，工具调用/参数/日志用 `font-mono`；零 box-shadow、零渐变。旧 styles.py「夜航蓝×香槟金」仅 Streamlit 并存期保留。

### 4.2 三区布局组件树（App.tsx 骨架）

```tsx
// grid: 行 = [titlebar 38px | main 1fr | statusbar 26px]；列 = [sidebar 200px | content 1fr]
<EngineShell>                      // app/Shell.tsx：撑满窗口，bg-bg text-ink
  <TitleBar/>                      // 品牌「私 invest-concierge」左 + 引擎状态 chip / 设置 右（drag region）
  <div className="grid grid-cols-[200px_1fr] overflow-hidden">
    <Sidebar/>                     // 轨道(基金/股票) + live 导航 + 持仓迷你 + 大盘 3 行（8s 超时弱网降级）
    <main className="overflow-y-auto"><Routes>{live 6 页}</Routes></main>
  </div>
  <StatusBar/>                     // ● 引擎就绪 | 数据源 AkShare | 上次刷新 | Agent v1.x  ← Reasonix 标志
</EngineShell>
```

`TitleBar`：`app-region: drag`（WebView2 Chromium 支持；按钮区 `no-drag`），发丝线下边。
`StatusBar`：26px 单行等宽小字；左 `StatusDot`（就绪=accent 常亮 / 思考中=accent 呼吸 `@keyframes pulse 1.6s` / off=ink-3），右数据源·刷新·版本；由 `stores/engine.ts` 每 30s 轮询 `/api/status` 驱动。
`Sidebar` 对 ai_chat 特殊：Reasonix 式「会话在左」，其它页走标准左导航（对齐现 sidebar.py 行为差异）。

### 4.3 引擎工作台组件基元

- `StatusDot`（6px 圆点 + 呼吸/常亮/off）—— 状态栏 + 工具行共用。
- `ToolTimeline` 行状态机：`pending(灰●)→running(accent 脉冲)→done(✓/✕ 语义色)`；行 = 图标(lucide 16px) + `name(args)`(mono 截断) + 右 `elapsed_ms`(tabular)；发丝线分隔，零装饰。见 §5。
- `ThinkingFlow`：reasoning 块等宽小字 `text-ink-2`，末尾光标脉冲（打字机「流式输出」）。
- `DataTable`：数字右对齐 tabular、涨跌只取语义色、发丝线行分隔、无斑马纹。
- `Kicker/panel-label`：11px 字距 1.5px `text-ink-3`（对应旧 .panel-label）。

### 4.4 ECharts 主题绑定 token

`charts/echartsTheme.ts` 用 `getComputedStyle(document.documentElement)` 读 var 缓存进 `ECharts.registerTheme('engine', …)`（bg transparent、axis `--line`、text `--ink-2`、series 只用 `--accent`；涨跌蜡烛 `--up/--down`、分位带 `--accent-soft`）。所有 chart 组件同一主题 → 一套 token 全局一致。

---

## 5. 工具链时间线组件（agent_run on_progress → SSE → React 流式渲染）

### 5.1 SSE 事件协议（`POST /api/agent/chat/stream`）

`Content-Type: text/event-stream`；每条 `data: <json>\n\n`；服务端每 15s 发 `: ping` 注释行防代理掐断。

| type | payload | 前端行为 |
|---|---|---|
| `status` | `{state:"running"\|"done"\|"error"}` | 状态栏点呼吸 |
| `reasoning` | `{text}`（整轮思考块，多轮多次） | ThinkingFlow 打字机追加 |
| `tool_start` | `{name, args}` | 时间线**插入行**：图标+参数摘要，置 running（accent 脉冲） |
| `tool_end` | `{name, ok, elapsed_ms}` | 该行定态 ✓/✕ + 耗时（elapsed_ms 由 M0 hook 计时） |
| `writing` | `{text}` | 状态栏「组织最终回答」 |
| `done` | `{session_id, content, tool_trace}` | agent 气泡打字机渲染 content；tool_trace 归档可展开回放 |
| `error` | `{message}` | 时间线中断态 + 错误提示（不崩） |

### 5.2 后端 SSE 通道实现要点

```python
# services/agent_service.stream(): agent_run 后台线程，队列桥到 asyncio
q = queue.Queue(); sentinel = object()
def worker():
    def cb(stage, detail):           # structured_progress=True → detail 为 dict
        q.put({"type": stage, **detail})
    try:
        res = agent_run(task, memory=True, session_id=sid,
                        continue_question=True, structured_progress=True,
                        on_progress=cb)
        q.put({"type": "done", **pick(res)})
    except Exception as e:
        q.put({"type": "error", "message": str(e)})
    finally:
        q.put(sentinel)
pool.submit(worker)
async def gen():
    while True:
        item = await asyncio.to_thread(q.get)
        if item is sentinel: break
        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
return StreamingResponse(gen(), media_type="text/event-stream",
                         headers={"Cache-Control": "no-cache"})
```

- 有限并发：全局 `ThreadPoolExecutor(max_workers=4)`；超限 503「引擎忙」。
- 断开：`ClientDisconnect` 中断 generator → 关队列；agent_run 取消点 P1。
- **降级**：SSE 被网络层吞时同参数落 `run_id` 队列，前端轮询 `GET /api/agent/runs/{run_id}/events`（§2.3）。
- 慢源（东财✗）：单工具超时返 error —— 该行 15s 后提示「数据不可得」而非无限转圈（复用 agent 防幻觉文案）。

### 5.3 前端流式渲染（fetch 读流，不用 EventSource——GET-only，需要 POST body）

```ts
// features/agent/stream.ts
export async function* readSSE(body: ChatBody, signal: AbortSignal) {
  const res = await fetch('/api/agent/chat/stream', {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body), signal});
  const reader = res.body!.getReader(); let buf = '';
  const dec = new TextDecoder();
  for (;;) {
    const {done, value} = await reader.read(); if (done) break;
    buf += dec.decode(value, {stream:true});
    let idx; while ((idx = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, idx); buf = buf.slice(idx+2);
      const line = chunk.split('\n').find(l => l.startsWith('data: '));
      if (line) yield JSON.parse(line.slice(6));
    }
  }
}
```

`stores/agent.ts` 消费：`tool_start/tool_end` 维护 `toolTimeline[]`（zustand immutable push），`ToolTimeline.tsx` 订阅渲染（状态机 §4.3）；`reasoning/done.content` 交给 `Typewriter` 逐字追加（16ms/字 + 光标脉冲），最终以 `done.tool_trace` 校正并支持展开 JSON 回放。

### 5.4 取消与错误

AbortController → fetch abort → 服务端 ClientDisconnect；UI：当前 tool 行置 ✕「已取消」，已落库会话保留；重发同 session_id = 追问链续写。

---

## 6. 桌面壳（pywebview 包 FastAPI + React）

1. **构建前端**：`npm run build` → `frontend/dist`。
2. **FastAPI 托管静态**：`app.mount("/", StaticFiles(directory="frontend/dist", html=True))`；HashRouter 无 history fallback 问题。
3. **依赖**：requirements 加 `pywebview`/`pystray`/`pillow`；pywebview Windows 后端 = WebView2/EdgeChromium（Win10/11 自带）。
4. **`desktop/launcher.py`**：
   ```python
   uvicorn.run(app, host="127.0.0.1", port=0)   # 随机端口，后台线程起
   window = webview.create_window("invest-concierge",
       url=f"http://127.0.0.1:{port}/",
       width=1440, height=900, min_size=(1100,700),
       frameless=CFG.frameless, js_api=DesktopBridge(), background_color="#1A1B1E")
   webview.start()   # 主线程
   ```
5. **DesktopBridge（js_api）**：`minimize()/toggle_maximize()/close_to_tray()/quit()`；关闭事件不退出只隐藏 → 托盘（pystray + Pillow 生成 ICO/菜单：显示/退出）；托盘双击恢复。
6. **自定义标题栏**：`frameless=True` + TitleBar `app-region: drag` + 窗口按钮调 DesktopBridge；`CFG.frameless=False` 原生标题栏保底。
7. **启动脚本**：`start.bat` → `python desktop/launcher.py`；开发期 vite.config `server.proxy['/api'] → 127.0.0.1:8000`。
8. **打包 exe（M4）**：PyInstaller `--onedir --windowed --name invest-concierge --add-data frontend/dist:frontend --collect-all pywebview …`；akshare 动态 import 面广 → **先冒烟**（exe 起、进窗口、拉一条行情）再谈 CI。
9. **保底**：桌面壳任何环节失败 → 浏览器开 `http://127.0.0.1:8000` 功能零损失。

---

## 7. 验收标准（每阶段 · 可执行验证）

**M0**
- `pytest tests/ -q` → `93 passed`（存量 + 新增 M0 用例）。
- `Get-ChildItem data,utils,services,server -Recurse -Filter *.py | Select-String 'import streamlit'` → 无命中。
- `uvicorn server.main:app --port 8765` 启动；`Invoke-RestMethod …/api/health` 返 `{status:"ok",…}`。
- `curl -N -X POST …/api/agent/chat/stream -d '{"task":"查一下600519"}'` 依次见 `status/reasoning/tool_start/tool_end…/done`。

**M1**
- `npm run build && npm run lint` 通过。
- 目视 checklist：三区成立；全界面除涨跌色外**只有终端青一个彩色**；数字等宽；发丝线；零渐变/阴影/圆角≤8px；状态栏实时字段有真实数据。

**M2**
- **核心验收**：诊断 600519 → 6 tab 数据齐全；「AI 追问：最大风险点是什么」→ 工具时间线 ≥6 连工具（✓/●/✕ 实时 + 每步耗时）→ 有数据支撑回答；无 Key 引导卡不崩。
- 持仓增/删/改 → 重启仍在（SQLite）；日记增删回读一致。
- 会话侧栏：新建/续写（追问链带记忆）/重命名/归档。
- 回归：`pytest tests/` 93 绿；`streamlit run app.py` 旧界面仍可开。

**M3**
- 双击入口 → 原生窗口（自定义标题栏可拖动、窗口按钮可用）；关闭 → 托盘驻留；恢复/退出；`netstat` 验证仅 127.0.0.1 监听。

**M4**
- exe 冒烟：双击 → 窗口 → 拉行情 + 一次 Agent 追问不崩；非 live 页按 §0.2 P1/P2 扩展完成；README/截图更新。

---

## 8. 风险（≤5，按威胁排序 = 影响 × 概率）

| # | 风险 | 应对 |
|---|---|---|
| 1 | **后端并发/一致性**：MemoryCache 无锁 + funds/diary「整表读改写」+ 无 st 后缓存失效钩子缺位 → FastAPI 多线程把「丢首笔 bug」放大为偶发数据丢失，最隐蔽伤信任 | M0 先做：缓存 RLock + services 写锁 + 失效统一 + 并发测试 |
| 2 | **冷启慢接口 + 数据源墙**（东财✗/诊断 15–40s）进长请求 → 白等/SSE 悬挂/uvicorn 无超时语义；回归测试 mock 网络掩盖真态 | 线程池 + 每请求超时 + 失败缓存秒回 + 前端 loading/取消 + 不依赖 mock 的 smoke 脚本 |
| 3 | **agent_run 契约被改破 93 测试/现页面**（直接透传 on_progress 会丢结构化而有人去改 agent_core） | hook 必须**纯增量**（默认 `structured_progress=False` 不变），每步全量 pytest；SSE 协议有后端契约测试 |
| 4 | **双 UI 并存期漂移**：React 只覆盖 live 6 页，导航/能力口径不一致 + 前端无 e2e → 「导航有页面空」空洞 | 导航 = PAGE_META live 集逐条对齐；新增后端契约测试；前端 vitest 冒烟核心 store |
| 5 | **桌面壳/打包链**：WebView2 环境、PyInstaller × akshare 动态 import × 线程 uvicorn、frameless 拖动/托盘 | 冒烟先行；`frameless=False` 回退；浏览器回退零损失 |

---

## 9. 明确不做（边界）

- ❌ 不重写 A 股引擎 / Agent 逻辑（93 测试保底）；M4 前不退役 Streamlit。
- ❌ 不引 Electron（pywebview 已定）；不引 Redux/SSR/过度懒加载设计。
- ❌ 不做登录/多用户/云同步（本地单机）。
- ❌ 不用动画库（Reasonix 克制：160ms 过渡 + 呼吸脉冲，CSS 足够）。
- ❌ 不并行维护两套图表库（K线/估值/财报统一 ECharts 单主题）。
- ❌ DeepSeek token 级流式不阻塞 v1（M4 可选增强）。

---

## 10. 建议开工顺序（周粒度）

- **W1**：M0 #1–#3（st 去泄漏 + 缓存锁）与 M1 脚手架并行 → 周末 `/api/health` + React 空壳三区可用。
- **W2**：M0 #4–#9 + 路由全表（agent/diagnosis/holdings/diary）+ SSE 契约测试；M1 token 系统 + 组件基元。
- **W3–W4**：M2 六页（先 AI Chat 时间线 → 诊断 6 tab → 持仓/资产/日记 → 设置/降级）→ W4 末浏览器全功能版验收。
- **W5**：M3 桌面壳 + 托盘 + 自定义标题栏 → 双击原生窗口验收。
- **W6+**：M4 非 live 页扩展 + 打包 + 打磨发布。

---

**说明**：方案已在桌面沙箱中尝试写入 `docs/FRONTEND_PLAN.md`，但当前会话是只读沙箱（写操作被系统拦截）。请将上述全文另存为 `D:\work\python1\fund_agent\docs\FRONTEND_PLAN.md`（UTF-8）即可作为开工文档；方案未改动任何代码。开工前请先对 §3.4 的 4 个拍板项给一次结论，其中「图表库 ECharts vs lightweight-charts」与「自定义标题栏 v1 是否上 frameless」最影响 M1/M3 排期。