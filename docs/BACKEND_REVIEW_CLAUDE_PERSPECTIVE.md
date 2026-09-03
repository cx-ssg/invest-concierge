# 后端评审（Claude Code 视角 · ds pro max 执行）

> 2026-09-03 · 第二视角独立评审（Go 额度 429，用官方 API 替代）

## 1. Bug / 隐患（按严重度）
- **P0** `data/market_api.py:get_market_index`：df 正常时 `fallback` 未定义，`if not fallback` 抛 `UnboundLocalError`，被 except 捕获后直接返回腾讯源；且 `return fallback` 在 `result` 解析前，东财数据实际永不返回。应先 `fallback=None`，仅在 df 空时取 fallback，并将解析循环放在 return 之前。
- **P0** `data/cache.py:cached`：用 `if result is not None` 区分不了“缓存 miss”和“缓存值为 None”，导致 `cache_failures=True` 完全失效；`MemoryCache` 也无锁，多线程会竞态。改为 sentinel/key 存在判断 + `threading.RLock`。
- **P1** `data/database.py:get_conn`：连接无 `busy_timeout` / `WAL`，Streamlit rerun 并发易报 `database is locked`，且连接未统一关闭。加 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000`，用 contextmanager。
- **P1** `data/market_api.py:get_market_index` 的 `@st.cache_data(ttl=300)` 与 `@cached(CACHE_QUOTE)` 叠加，TTL 不一致，去 Streamlit 后会失效。

## 2. 性能优化（按收益）
- **高** 去掉 `@st.cache_data` + `@cached` 双缓存，修复失败缓存 None 判断，避免东财/腾讯重复拉取。
- **高** `data/cache.py:MemoryCache` 增加 LRU/maxsize，防后端常驻内存无限增长。
- **中** `utils/agent_core.py:execute_ai_tool_v2` 每次分派若 `importlib.import_module`，加 `@lru_cache`。
- **低** SQLite 加索引：`watchlist(code)`、`stock_transactions(created_time)`、`diary_entries(date)`。

## 3. React 前端对接（FastAPI 桥）
- **必须去 UI 依赖**：
  - `data/market_api.py:7` 删除 `import streamlit`、去掉 `@st.cache_data`，改为 `@cached`。
  - `data/database.py:406` 函数内 `import streamlit` 改为 `@cached`。
  - `data/fund_api.py:288` 去掉 st，缓存统一走 `data/cache.py`。
  - `utils/ai_helper.py:8` 若读 `st.session_state`，改为显式传参 / `app.state` / 内存态。
- **新增 FastAPI 路由**：`GET /api/market/index` → `data/market_api.get_market_index`；`GET /api/funds/{code}` → `data/fund_api.get_fund_info`；`GET /api/watchlist` → `data/database` 对应函数；`POST /api/agent/chat` → `utils/agent_core.agent_run`。
- **抽 service**：`web_agent.py` 里各 tab 的数据组织逻辑移入 `service/*.py`，返回 plain dict/list；React 只消费 JSON。
- **固化 schema**：为 market/fund/holding 返回字段定义 Pydantic 或 TS 类型，避免 `change_percent` 与前端 `changePercent` 漂移。

## 4. 后续维护
- **CI 硬约束**：data/ utils/ 禁止 `import streamlit`（grep 检查），避免 UI 依赖回潮。
- **缓存统一**：所有 st.cache_data、局部缓存收口到 `data/cache.py` 一个装饰器，支持 TTL、失败缓存、锁、容量。
- **日志替换 print**：数据模块大量 `print`，改成 logging，并标记数据源（东财/腾讯 fallback）便于排障。
- **补测试**：覆盖失败缓存 None、SQLite WAL/锁、agent 工具晚绑定解析；93 个用例未覆盖这些关键点。