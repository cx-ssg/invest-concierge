# Roadmap · invest-concierge

> 状态标识：✅ 已上线 · 🔜 规划中 · 💡 待评估
> 原则：**只写真实可用的功能**（README 同），占位功能点亮后才标记。
> 维护约定：本文件与 [README.md](../README.md) 口径一致；**指标必须带口径限定**（见 §5），
> 行末给出实查证据（文件/命令），不写印象。
>
> 最近一次核对：**2026-10-04（H5）**——逐条核对见 §2。

## 1. 已上线

### v1.0 · 基座

- ✅ 基金持仓管理 / 资产总览 / 投资日记
- ✅ AI 对话（DeepSeek + 工具调用，**31 个工具**；SSE 流式 + 思考流 + 工具时间线）
- ✅ 股票综合诊断（5 引擎体检 + AI 多角色辩论；`ORCHESTRATOR=graph` 时走图编排人审链路）
- ✅ 双轨导航（基金 / 股票）
- ✅ CI / 测试 / 安全加固（CI 双矩阵 Python 3.9 / 3.11 + gitleaks；pytest **674 passed**，2026-10-04 H6 实测）
- ✅ **Agent 对话直达 12 项数据能力（P1 已交付）**：基金/股票搜索、基金量化指标（收益/回撤/夏普）、历史净值、定投回测、个股行情/K 线/资金流向、大盘资金（含北向）、热门板块、涨停复盘、指数估值分位——在 AI 对话里直接提问即可。验收实录见 [AGENT_TOOLS_PLAN.md](AGENT_TOOLS_PLAN.md) §P1。

### v1.1.0 · 粘性三件套 + 私域知识层（M1）

- ✅ **价格预警（A）**——规则 CRUD + 触发事件时间线 + 交易时段低频轮询调度器（10 分钟/标的、三重限频）+ 桌面壳托盘气泡 + 应用内角标；基金按盘中估值判定并在通知里标注「按盘中估值、非最终净值」。见 [RELEASE_NOTES_v1.1.0.md](RELEASE_NOTES_v1.1.0.md)。
- ✅ **持仓周报（B）**——一键生成（纯数据层 + AI 点评；无 Key 降级为纯数据卡，同周幂等覆盖写），入口在资产总览页。
- ✅ **记忆显性化（C）**——持仓快照注入 + SSE `memory_used` + 前端动态 chip + 设置页隐私开关。
- ✅ **私域知识层（M1）**——公告/研报/财报 PDF 切块入库，BM25 + `bge-m3` 向量 RRF 混合检索，返回原文片段 + 来源 + 日期；证据不足**主动弃权**（宁可说「没找到」，也不编）。

### v1.2.0 · 图编排（M3，可选）

- ✅ **M3 图编排**——`ORCHESTRATOR=graph` 才生效（**默认 `legacy`、行为不变**），只图化「股票深度诊断」一条链路：条件边分流 + 检查点续跑 + 人工确认（上限 3 轮）；人审入口 `POST /api/stocks/{code}/diagnosis/review` + 诊断页人审面板。见 [RELEASE_NOTES_v1.2.0.md](RELEASE_NOTES_v1.2.0.md)。

### v1.3.0 → v1.3.2 · 引用与判官 + 长期记忆（M2）+ 语料扩标

- ✅ **A3b LLM 判官**——**异步**（`done` 先到、`evidence_judged` 后到）· **仅 `weak` 档触发**（`none` 档已弃权 ⇒ 零调用）· 引文需**逐字**命中原文块（确定性校验）· **失败一律降级** `uncertain`；来源卡按判官结论标注。
- ✅ **引用回跳**——回答正文 `[n]` 为**可点上标** + 来源卡（标题 / 日期 / 链接）；H1 起 DOM id 改为 `src-{scope}-{n}`，历史多条消息同页不再互撞。
- ✅ **历史回放显示来源**——切回**旧会话**同样显示来源卡与判官标注（v1.3.1 起，落 `agent_messages.meta`）；**老会话不回填**（`meta = NULL` ⇒ 不显示来源区）。
- ✅ **M2 长期记忆层 + 前端记忆管理 UI**——三类记忆**分离存储、分离召回**（偏好每次必注入 / 事实按标的 / 经验向量 top-3）；写入走「候选 → 用户确认」（**AI 不自行写记忆**）；设置页可列表 / 删除 / 确认候选 / **召回预览**（审计入口）。
- ✅ **多标的语料 + 查询侧标的识别**——语料 **65 docs / 2111 chunks**（茅台 + 五粮液 + 泸州老窖 + 山西汾酒）；`query_scope` 识别到标的才收窄检索池，抑制跨标的污染（口径见 §5）。
- ✅ 采集层大报告不再硬截断（`MAX_PAGES` 60 → 160；重采此前被截断的 2 篇）。

### H5（2026-10-04）· P2 + P3 页面补线

- ✅ **市场行情页（P2）**——`/fund/market`，按 [AGENT_TOOLS_PLAN.md](AGENT_TOOLS_PLAN.md) §P2 的既有设计做**指数 / 板块 / 情绪 / 资金 / 估值 五 tab**（行情是「开着看」的）；后端 `/api/market/*` 复用 P1 已工具化的同一批引擎，**只展示真实数据**，取不到就显示「数据不可得」。
- ✅ **自选股页（P3）**——`/stock/watchlist`，**自选股 / 持仓股票**两个 tab；复用库层既有 `watchlist` / `stock_holdings` CRUD 与 `get_stock_info` 行情，不另起数据通路。

### H6（2026-10-04）· P4 龙虎/打板工具族工具化（Agent 工具，**不做页面**）

- ✅ **`dragon_api` 12 个业务函数 → 7 个 Agent 工具**（24 → 31 个工具）：`get_limit_up_pool` / `get_limit_up_detail` / `get_lhb_stats` / `get_dragon_stocks` / `get_board_list` / `get_board_members` / `get_stock_boards`。合并依据与工具映射见 `data/dragon_api.py` 的「Agent 工具适配层」段与 `report-H6.md` §V2。
- ✅ **边界（按设计原意）**：这族「游资向」功能**只由用户在 AI 对话里主动问**——不加页面、不进导航、UI 不主动推荐；每个返回值带 `source` + `risk_note`（**不构成投资建议**）。
- ✅ **golden set** 覆盖与注册表等值（31 个工具，`MIN_TOOL_COVERAGE = 31`），离线契约同步。
- ✅ GUI 禁令遵守：本轮未起 Streamlit / 桌面壳，仅 `server/main.py`。

## 2. 逐条核对表（2026-10-04 · H5，实查代码非印象）

| 功能 | 本文件原状态 | 实查证据 | 核对结论 |
|---|---|---|---|
| 🔔 预警设置 | 🔜 P3「数据层已就绪」 | `frontend/src/pages/AlertsPage.tsx`（规则 CRUD + 触发时间线）、`server/routers/alert.py`、`services/alert_service.py`（交易时段调度器）、`desktop/tray.py`（托盘气泡） | **更正为 ✅ 已上线（v1.1）** |
| 📉 市场行情页 | 🔜 P2「发布后第一周更新」 | H5 新增 `frontend/src/pages/MarketPage.tsx`（五 tab）+ `server/routers/market.py` + `services/market_service.py`；真实 API 输出见 `report-H5.md` §V3 | **更正为 ✅ 已上线（H5）** |
| 🔎 自选股 | 🔜 P3「引擎与 Agent 工具已就绪，页面待接线」 | 库层 `data/database.py`（watchlist / stock_holdings CRUD）早已存在，但**无 HTTP 层、无 React 页**；H5 补 `server/routers/watchlist.py` + `services/watchlist_service.py` + `frontend/src/pages/WatchlistPage.tsx` | **更正为 ✅ 已上线（H5）** |
| 📊 多基金对比页 | 💡「Agent 对话已可用」 | `compare_funds` 工具在册（TOOL_REGISTRY 31 个之一）；`frontend/src` grep `compare_funds` **0 命中** | 💡 维持（工具可用、页面未做） |
| 🧰 投资工具箱 | 💡「待设计」 | H6 实查：`utils/agent_core.py` grep `dragon_api` **8 命中**（7 个工具晚绑定 + 1 处设计意图注释）；`pages/` 与 `frontend/src` grep 龙虎工具名 **0 命中** | **更正为 ✅ Agent 工具已交付（H6）**，仍不做页面 |
| 👤 个人中心 | 💡「v1 已裁剪，暂缓」 | `services/nav_service.py` 中 `profile` 仍 `live: false`，`pages/profile.py` 为旧 Streamlit 占位 | 💡 维持（不做） |

> 另：v1.1 的**持仓周报**此前未进本文件，本次补入 §1（实查 `frontend/src/pages/DashboardPage.tsx` 的周报面板 + `server/routers/reports.py`）。

## 3. 🔜 规划中

P1（对话能力）/ P2（市场行情）/ P3（自选股 + 预警）**均已交付**，当前没有已排期未开工的页面级功能；
剩余候选一律留在 §4「💡 待评估」，避免把「想做」写成「在做」。

## 4. 💡 待评估

| 功能 | 说明 | 状态 |
|---|---|---|
| 📊 多基金对比页 | 表格化对比 UI | 💡 Agent 对话已可用（`compare_funds` 工具），页面看需求热度 |
| 🧰 投资工具箱 | 聚合工具集 | 💡 待设计（多数能力已并入 Agent 对话） |
| 🐉 龙虎/打板工具族 | `dragon_api` 12 个函数工具化（P4） | ✅ **已交付（H6）**：12 函数 → 7 个 Agent 工具，按原设计**只做工具不做页面**，见 §1 H6 |
| 👤 个人中心 | 画像聚合 | 💡 待设计（v1 已裁剪，暂缓） |
| 收盘自动扫描 + 飞书推送 | 对齐 Sequoia-X | 💡 待评估 |
| 每日复盘自动生成 | 对齐 Vibe-Research | 💡 待评估 |
| 回测/定投玩法深化 | 现有 `backtest_dca` 工具的深化 | 💡 待评估 |

## 5. 指标口径（引用时必须带限定，与 README 同源）

| 指标 | 值 | **必带限定** |
|---|---|---|
| 检索 `Recall@5` / `MRR@10`（`prod` 口径） | 0.926 / 0.781 | ⚠️ **`prod` 是 oracle 上界、不是产线实测**（query 含由金标派生的标的）；`full` 口径 0.593 / 0.386 代表「无法识别标的时」的真实能力，**刻意保留** |
| `query_scope` 真实净贡献 | `Recall@5` 0.889 → 0.926 | ⚠️ 不是 0.593 → 0.926（后者含「查询形态效应」） |
| A3a（域外主动弃权） | 0.863 | ⚠️ **经 holdout 二次标定的拟合值，不能称「holdout 验收」**；敏感性区间 [0.863, 0.902]（更高值需第三次动用 holdout ⇒ 主动不取） |
| A3b 判官 `judge_fp` / `judge_fn` / `span_valid` | 0.097 / 0.192 / 0.955 | 门槛 ≤0.10 / **≤0.20（v1.3.2 订正）** / ≥0.95；`judge_fn` 中「判官自身」份额是**上界口径** |
| 语料 | 65 docs / 2111 chunks | 语料与向量**不随仓库分发**，需自行构建 |
| 测试 | pytest **674 passed**（2026-10-04 H6） | 前端 `npx tsc -b` exit 0；`npm run build` 成功 |

完整依据与自查边界见 [README.md](../README.md) 评测小节、[M1_EVAL_REPORT.md](M1_EVAL_REPORT.md)、
[COVERAGE_DESIGN.md](COVERAGE_DESIGN.md) 与各版 RELEASE_NOTES。

---

> 想优先看到哪个功能点亮？欢迎提 [issue](https://github.com/cx-ssg/invest-concierge/issues)。
