# 后端评审（Codex 视角 · 2026-09-03）

> 第一视角独立评审（Codex CLI 0.142.3，deepseek-v4-flash）
> 通读任务书 + data/ 16 文件 + utils/ + app.py + web_agent.py，只读未改代码
> tokens used: 111,261

## 1) Bug（按严重度）

- **高** `data/market_api.py:get_market_index`：EM 成功时 `fallback` 未赋值→NameError 落 except，恒切腾讯且 change/change_percent=0；`return` 后 EM 解析为死代码→大盘涨跌恒 0。
- **高** `data/database.py:load_funds`(@st.cache_data 60s) + `utils/fund_utils.py:add_fund`：读缓存、save 不失效，60s 内连加两笔整表覆盖丢首笔。
- **中** `data/moneyflow_api.py:get_north_moneyflow_details`：持股/增持同参重复请求，无变动列时 top_increase 退化为持股榜。
- **中** `data/cache.py:cached`：无锁无上限(多线程竞态/键膨胀)；`utils/common.py:fetch_with_timeout` 超时后台线程仍写同 key。
- **中** `data/fund_api.py:_get_fund_list` 永久缓存不刷新；`get_fund_info` 每查全表 astype。
- **低** `data/stock_api.py:_get_market_name`：8/9 判错(实北交所)，688 误归沪主板。

## 2) 性能（按收益）

- `utils/ai_helper.py:build_system_prompt` 串行 EM+腾讯两跳 + 蛋卷 4 指数串行，弱网 40s+→预取缓存成 prompt 片段。
- 深诊链 minefield/moat/fundamentals/report 各 7-8 次 EM/THS 顺序调用、三表被 4 模块重复拉→公共 fetcher+24h 落盘(首查 15-40s→<3s)。
- `data/sentiment_api.py` 三函数未 cache_failures，EM 挂时每次 rerun 重等 15s×N→补失败缓存。
- `utils/common.py:fetch_with_timeout` 每次新建线程池、孤儿线程泄漏→全局单例池。
- `utils/ai_helper.py:multi_agent_stock_analysis` 5 次 LLM 串行→专家并行。

## 3) React 对接（st 泄漏最小解）

- 仅 2 处真技术债：`market_api.py:33/302`、`database.py:406` 的 @st.cache_data 只是拿 st 当缓存层，内层已叠纯 `@cached`→删装饰器+import 即解；MemoryCache 加 threading.Lock 即 FastAPI 兼容。
- `ai_helper.py:8` st 仅在 `_is_demo_mode` 读 session_state→改 use_demo_funds 入参并删 import；`common.py:safe_rerun` 迁 UI。
- `fund_api.py:calc_dca`、`utils/chart.py`、`fund_utils.get_portfolio_chart` 为渲染→迁 pages/，data 留 `backtest_strategy` 返 dict。
- 按 pages/20 页抽"聚合返 JSON"供 FastAPI 直调；`compare_funds` 返 Markdown 的改结构化。
- `agent_core.py:agent_run` 的 on_progress 直接接 SSE；会话记忆 SQLite 可复用。

## 4) 后续维护

- 巨模块(moat 926/database 799/minefield 837 行)拆抓取/评分；四财报模块重复拉三表集中一 fetcher。
- `config.py` 相对路径(my_funds.json/DB_FILE)依赖 cwd，服务化必炸→pathlib 绝对化。
- 多数 fallback 仍打 push2.eastmoney(同死源)，东财✗ 时 dragon/moneyflow/limit_up/sentiment 全空→集中源健康探针。
- `data/fund_api.py` 与 `utils/fund_utils.py` 双份同名函数字段不一致→收敛单一权威。
- 清 print[DEBUG]、双缓存常量(config.CACHE_TTL vs data/cache.py)、死代码。

---

## 修复状态（Reasonix 2026-09-03 追记）

- 🔴 高①②两条（fallback NameError / load_funds 丢首笔）：**已修复**（commit c1dd35b）
- 🟡 中 `cached` 无锁：已加 contains() 但**加锁未做**（MemoryCache threading.Lock 留待 React 重构时一并）
- 🟡 moneyflow 重复请求 / _get_fund_list 永久缓存：**未修**（非紧急，随重构）
- 🟡 性能类（build_system_prompt 串行 / 深诊链重复拉 / fetch_with_timeout 线程池）：**未修**（重构优化项，进 v1.2 清单）
- 🟢 React 对接（st 泄漏）：**设计已清**（见 BACKEND_REVIEW_BRIEF.md §3.5），执行随前端重写
- 🟢 维护类（print→logging / config 路径 / fallback 探针）：进后续迭代