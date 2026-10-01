# M2 · 长期记忆层 —— 实现计划（施工版）

> 设计依据：`docs/COVERAGE_DESIGN.md` **§4**（§4.1 三类记忆 / §4.2 写入时机 / §4.3 P2 门禁 B1–B4）
> 状态：2026-10-02 定稿（M3 收官后开工）；用户拍板「下一步开工 M2」。
> ⚠️ 沿用 M3 阶段的硬教训：**契约测试必须喂真实形态、且必须走真实可达路径**（本阶段三次同族翻车）。

---

## 0. 与现有实现的边界（先划清，避免重复造）

| 现状 | 是什么 | 与 M2 的关系 |
|---|---|---|
| `utils/agent_memory.py`（134 行） | **短期会话记忆**：8 轮触发摘要、最近 3 条摘要注入、`agent_messages` 落库 | ⚠️ **不是**长期记忆；M2 建在它之上，不合并 |
| `agent_sessions` / `agent_messages` | 会话与消息 | 复用（M2 记录 `session_id` 溯源） |
| `tests/test_memory.py` / `test_memory_visibility.py` | 会话记忆 + **持仓记忆显性化**（v1.1 三件套 C） | 保留；M2 另开 `tests/test_m2_memory.py` |
| `kb.db` + `utils/rag/` | **文档语料**检索（公告/研报） | ⚠️ **不复用**：经验记忆是"用户自己的决策史"，与文档语料混库会污染两边 |

## 1. 数据模型（`data/database.py` 新增两表，含旧库兼容）

```sql
CREATE TABLE IF NOT EXISTS memories (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,                  -- 'preference' | 'fact' | 'experience'
  key         TEXT NOT NULL DEFAULT '',        -- 去重键（偏好/事实用；经验为空）
  content     TEXT NOT NULL,                  -- 记忆正文（自然语言，用于注入）
  meta        TEXT NOT NULL DEFAULT '{}',      -- JSON：标的代码 / 时间 / 数值等结构化信息
  source      TEXT NOT NULL DEFAULT 'explicit',-- explicit | implicit | seed
  session_id  INTEGER,                        -- 溯源：哪次会话写入的
  embedding   BLOB,                           -- 仅 experience 用（bge-m3 向量，独立于 kb.db）
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, key)                           -- 同 kind+key ⇒ 覆盖更新（**去重**）
);
CREATE TABLE IF NOT EXISTS memories_pending ( -- 隐式候选，等用户确认（§4.2）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, key TEXT NOT NULL DEFAULT '',
  content TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
  session_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending | accepted | rejected
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> ⚠️ `UNIQUE(kind, key)` 是**去重的单一事实源**：同一条偏好被说两遍只留一条（`ON CONFLICT DO UPDATE`）。
> `key` 为空的场景（经验记忆）不参与唯一约束冲突（SQLite 允许多个 NULL，但 `''` 会冲突）⇒
> 经验记忆的 `key` 统一写 `''` **但用 `kind` 区分**，因此**经验记忆的去重靠内容指纹**（见 §2）。

## 2. 模块 `utils/long_memory.py`（新）

| 函数 | 语义 | 召回策略依据 |
|---|---|---|
| `add(kind, content, key="", meta=None, source="explicit", session_id=None)` | 写入/覆盖（去重） | §4.2 显式：用户说「记住…」 |
| `propose(kind, content, ...)` | 写 **pending** 候选 | §4.2 隐式：会话结束由 summarizer 抽取 |
| `list_all(kind=None)` / `get(mid)` | 列出（用户可审计，§4 原则） | — |
| `delete(mid)` / `delete_by_key(kind, key)` | 删除（**必须真生效**，B4） | — |
| `recall_preferences()` | 偏好**全量**（小、固定） | §4.1「每次对话必注入」 |
| `recall_facts(stock_codes)` | 事实：按**问题涉及的标的**过滤 | §4.1「按标的召回」 |
| `recall_experiences(query, top_k=3)` | 经验：**向量召回 top-3** | §4.1「向量召回 top-3」 |
| `build_recall_block(question, stock_codes=None)` | 汇总为一个可注入文本块 + **来源标记** | 供 `agent_run` 注入 |
| `accept_pending(pid)` / `reject_pending(pid)` | 用户确认候选 | §4.2 |
| `summarize_to_candidates(session_id, llm_fn=None)` | 轻量抽取隐式候选（**无 LLM 时可用规则兜底**） | §4.2 隐式 |

**去重规则（明确，便于测）**：
- `preference` / `fact`：按 `(kind, key)` 覆盖更新，`key` 由调用方给（如 `risk_tolerance` / `stock:600519`）
- `experience`：`key` 存**内容指纹**（归一化去空白 + 截断 200 字的 sha1 前 16 位）
  ⇒ 这样 `UNIQUE(kind, key)` **天然完成去重**；重复写入返回既有 id，不新增
  （⚠️ 不要给 experience 写 `key=''`：多条 `''` 会互相冲突，这是定稿时发现的设计漏洞）

**向量召回**：复用 `utils/rag/embed.py`（bge-m3，本地 Ollama）做编码，但**向量存 `memories.embedding`**（独立于 `kb.db`）。
⚠️ 无 Ollama 时**必须优雅降级**（按 `created_at` 倒序取 top-k，并标注"未向量化"），不得抛异常。

## 3. 注入点（`utils/agent_core.py::agent_run`）

- 在现有 `## 已知用户上下文`（持仓快照）之后追加 `## 长期记忆` 段，结构：
  ```
  ## 长期记忆
  ### 偏好（必注入）
  - 风险承受低，不碰杠杆
  ### 相关事实
  - 600519：2026-08 买入，成本 1450
  ### 相关经验（top-3）
  - 8 月加仓 600519，回撤 12%（2026-08-20）
  ```
- **沿用 v1.1 三件套 C 的惯例**：`memory_used` 事件带上 `sources`（`preferences`/`facts`/`experiences`）；
  ⚠️ **无命中则不发该段、不发事件**（不暗示"我记得"）
- ⚠️ **隐私开关复用**：设置页已有「允许 AI 读取我的持仓」；M2 新增「允许 AI 使用长期记忆」默认**开**，关闭时**不注入、不发事件**

## 4. API（`server/routers/memory.py`，新）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/memory` | 列出全部（按 kind 分组），供"可审计" |
| DELETE | `/api/memory/{id}` | 删除单条（B4：删完再问**不得**再体现） |
| GET | `/api/memory/pending` | 待确认候选 |
| POST | `/api/memory/pending/{id}` | body `{action: accept\|reject}` |
| POST | `/api/memory` | 手动新增（等价显式「记住…」） |

## 5. 验收（§4.3 P2 门禁）+ 单测

| # | 验收方式 | 预期 |
|---|---|---|
| **B1** | 会话 A 说「我风险承受低，不碰杠杆」→ 关掉重开 | `memories` 新增 1 条 `preference` |
| **B2** | 会话 B 问「给我个操作建议」 | 回答明确体现该偏好（不推荐高杠杆标的） |
| **B3** | `pytest tests/test_m2_memory.py -q` | 全过（**写入 / 召回 / 去重 / 删除**四类） |
| **B4** | 删除该记忆 → 再问同样问题 | **不再体现**（删除真生效） |
| 附加 | 无 Ollama / 无 LLM 时的降级 | 不抛异常，且**如实标注**降级 |

## 6. 任务拆解（每步带验收命令 —— Gate 前置）

| Step | 内容 | 验收 |
|---|---|---|
| 1 | 两表 + 旧库兼容 | `python -c "import data.database"` OK；`pytest -q` **393 passed** 不破 |
| 2 | **TDD RED**：`tests/test_m2_memory.py`（写入/去重/三类召回/删除真生效/降级） | `pytest tests/test_m2_memory.py -q` **先全红**且红因是功能缺失 |
| 3 | 实现 `utils/long_memory.py` | 上述测试转绿 |
| 4 | 注入 `agent_run` + `memory_used` 扩展 | 注入文本可测（构造记忆 ⇒ prompt 含该段）；无命中时不注入 |
| 5 | API（列出/删除/pending/手动新增） | `TestClient` 走通 + 非法入参 422 |
| 6 | **B1/B2/B4 端到端**（真实会话链路，不只单测） | 三步演示 + 证据（沿用 M3 的 E2E 纪律） |
| 7 | 外部审计 + 文档 + 提交 | 审计报告落盘 + findings 处置 + CHANGELOG/§4 状态回写 |

## 7. 明确不做（防范围蔓延）

- 不做**自动抽取决策史**的复杂 LLM 管线（隐式候选先用轻量规则 + 可选 LLM summarizer）
- 不把 M2 接进 M3 的图（两者独立；图内是否用长期记忆留给后续评估）
- 不做记忆的**向量库迁移**（规模小：个人级记忆量级，`BLOB` + 线性扫描足够；先不上 sqlite-vec）
