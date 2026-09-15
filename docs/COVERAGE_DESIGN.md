# invest-concierge · 能力补全覆盖设计（v0.1 草案）

> 生成：2026-09-14 ｜ 状态：**设计草案，未开工**
> 依据：2026-09-14 现场核对 `D:/work/python1/fund_agent`（HEAD `4d51a15`）
> 上游缺口清单来源：本轮会话对 `services/`、`utils/agent_core.py`、README、CHANGELOG 的现场核查

---

## 0. 目标与非目标

**一句话目标**：在**不重写现有工程**的前提下，用三个增量模块把 invest-concierge 从「工具调用型 Agent」升级为「带私域知识 + 长期记忆 + 多步可恢复编排的 Agent 系统」。

**非目标（明确排除）**：
- ❌ **模型微调 / LoRA**：算力与语料都不成立，收益远低于成本。保留 prompt 层与评测层优化。
- ❌ **全量重写**：现有 23 工具 / FastAPI / React / 桌面壳全部保留，三个模块以增量方式接入。
- ❌ **把实时行情塞进 RAG**：行情、资金流、估值这类**时效性数据继续走现有工具链**（akshare 直连 + 缓存），RAG 只负责**静态语料**（公告、研报、财报、方法论）。这是本设计的核心边界。

---

## 1. 现状与缺口（现场证据）

| 能力 | 现状 | 证据 |
|---|---|---|
| 工具调用 | ✅ 23 个工具，声明式注册表 + 晚绑定 + SSE | `utils/agent_core.py:71` 起，现场数出 23 个 key |
| 诊断引擎 | ✅ 6 tab（基本面/排雷/护城河/估值/三表/AI 辩论） | README §差异化 |
| 私域知识 / RAG | ❌ 0 | 全仓无 embedding / 向量检索 / 切块代码 |
| 长期记忆 | 🟡 **有会话级记忆，缺跨会话结构化记忆**（2026-09-14 核对修正） | `utils/agent_memory.py` 已存在：`agent_sessions` / `agent_messages` 两表持久化 + 每满 8 轮生成一句话摘要（`SUMMARY_TRIGGER_ROUNDS=8`）+ 诊断追问注入**最近 3 条**会话摘要（`MEMORY_CONTEXT_SESSIONS`）。→ **M2 不是从 0 到 1**，且开工前必须先定与它的整合方案，否则会出现**两套记忆并存** |
| 编排 | 🟡 自研单 Agent 工具循环 + **已成型 SSE 事件契约** | `utils/agent_core.py:394 agent_run` 为线性循环，无分支/无断点续跑；SSE 契约 `status → reasoning → tool_start/tool_end → writing → done`（另有 `memory_used`）**已被前端状态机消费，M3 迁移必须保持不变** |
| 工具层 | 🟡 23 个工具，但**契约薄**（2026-09-14 补充） | 统一返回 JSON **字符串** + `_truncate(max_len=8000, list_top_n=20)`（表格类会被截断）；**无参数 schema 校验、无超时、无 per-tool 错误码、无幂等标记**；工具调用为顺序 `for` 循环（`agent_core.py:506`，无并发）→ 见 §11 P0 |
| 模型调优 | ❌ 0（仅 prompt 层） | — |

**已可复用的成熟资产**（本机已有，直接借）：
- `bge-m3` 向量化能力（ollama 已 pull，工作台 P0A 已核验）
- 混合检索方案：中文 bigram 分词 + **BM25** + 向量 + **RRF 融合（k=60）**（知识库重构 A→D-lite 已跑通，含增量重建；⚠️ FTS5 路线已实测排除，见 §3.3 第 3 条）
- 中文文档切块与去噪经验（知识库 7k+ 块实测）

---

## 2. 覆盖方案总览

| 模块 | 补什么缺口 | 对口岗位要求 | 工作量 | 优先级 |
|---|---|---|---|---|
| **M1 私域知识层** | RAG / 向量检索 / 非结构化文档 / 私域知识库与大模型结合 | 智能体岗职责第 3 条（**原文命中**） | 2-3 天 | P1 |
| **M2 长期记忆层** | 记忆模块 | 智能体岗职责第 2 条「规划、决策、**记忆**、工具调用」 | 1-2 天 | P2 |
| **M3 编排层** | 多步 / 分支 / 可恢复编排 | 智能体岗职责第 1、2 条 + LangGraph 要求 | 2-3 天 | P3 |

**三模块互不阻塞**，可单独交付、单独验收。合计 6-8 天（AI 辅助开发节奏）。

---

## 3. M1 · 私域知识层（RAG）

### 3.1 语料来源（全部免费、可自动化）

| 语料 | 来源 | 获取方式 |
|---|---|---|
| 上市公司公告 | 东方财富公告库 | akshare `stock_notice_report` |
| 券商研报摘要 | 东方财富研报 | akshare `stock_research_report_em` |
| 财报原文（PDF） | 巨潮资讯 / 东财 | 下载入 `_DATA_DIR/corpus/pdf/` |
| 方法论（自建） | 项目 docs + 个人笔记 | 手工入 `_DATA_DIR/corpus/notes/` |

### 3.2 数据流

```
[采集层] akshare 拉取 → 按股票代码 + 日期落盘 _DATA_DIR/corpus/{code}/{source}/xxx.md|pdf
   ↓
[解析层] PDF → 文本（pdfplumber）；表格单独抽为结构化块（财报数字必须保留表结构）
   ↓
[切块层] 金融文档专用切块：按「章节标题 + 段落」切，表格整块不切碎；块大小 600 字、硬上限 1200（2026-09-15 对齐知识库实跑参数）；**不设 overlap** —— 知识库实现本就无 overlap（靠标题边界自然分隔），原设计「overlap 80」在复用方案下不适用，此处**显式废弃**而非静默删除
   ↓
[索引层] 每块 → bge-m3 向量（ollama /api/embed，1024 维）+ BM25 内存索引（中文 bigram 分词），持久化到 kb.db
   ↓
[检索层] 查询 → 向量 top-k + BM25 top-k → RRF 融合（k=60）→ 重排（可选）→ top-n
   ↓
[接入层] 暴露为第 24 个 Agent 工具 retrieve_docs(query, code=None, top_n=5)
   ↓
[生成层] 检索结果注入上下文 → 回答必须带引用编号 [1][2] + 来源 URL/文件名 + 日期
```

### 3.3 关键设计决策

1. **为什么用 SQLite 而非专用向量库**：语料量级（单机万级块）下 numpy 向量矩阵 + 暴力检索足够；零部署、可随 exe 分发。若后期超 10 万块再考虑 sqlite-vec / hnswlib。
2. **表格块不切碎**：财报表格被切断会导致数字串错行 —— 表格整体作为一块，并在块元数据标 `is_table=true`。
   - ⚠️ **2026-09-15 修正**：知识库现成的 `chunk_markdown` **只处理 markdown、无表格语义** → 切块器需在其结构上**扩展表格保护**，不能直接照搬。
3. **时效分层 + 检索参数显式固定（2026-09-14 修正）**：
   - **时效**：行情类内容**本就不入语料**（见 §0 非目标），原文写的「超过 N 天的行情类内容不返回」是条**空转条款** —— 已修正。真正的时效风险在**公告 / 研报**：一份 2024 年的研报被召回回答 2026 年的问题。规则改为：按 `published_at` 衰减加权 + 按来源设**有效期上限**（研报 18 个月 / 公告 36 个月，超期仅可召回但不作主依据）+ 回答中显式标注来源日期。
   - **参数（不固定则 A2/A3 不可复现）**：向量 top-20 + **BM25** top-20 → RRF 融合（**k=60**，与知识库 `search_wiki.run_hybrid` 实现逐字一致）→ 取 top-5。
     **相关性判据（2026-09-16 二次修正 —— 绝对阈值方案已被实测证伪）**：
     ① 绝对下限 `MIN_SIM = 0.62`：`max_sim` 低于它直接返回空（**A3 判据**）；
     ② 相对阈值 `MIN_SIM_RATIO = 0.85`：语义路只保留 `sim >= max_sim × 0.85` 的块。
     实测依据（**真实公告语料 814 块**，工具 `scripts/rag_threshold_probe.py`）：

     | 查询组 | 相关查询 `max_sim` | 无关查询 `max_sim` | 结论 |
     |---|---|---|---|
     | TUNING（8 条，参与定值） | 0.6528 ~ 0.8191 | 0.4643 ~ 0.5864 | 可分 → 据此定了 0.62 |
     | **HOLDOUT（8 条，未参与定值）** | 0.6668 ~ 0.8339 | **0.4542 ~ 0.6902** | **不可分（区间倒挂）** |

     🔴 **结论：单纯向量阈值无法实现 A3**。留出组中「怎么写一封求职信」的 `max_sim = 0.6902`
     **高于**相关查询「风险评估报告的结论」的 0.6668 —— bge-m3 在**单公司同质语料**上语义基线过高。
     而 **BM25 分路干净可分**（同批留出查询：REL `bm25_max` ∈ [21.6, 48.4] vs IRR ∈ [0, 7.3]，约 3 倍 gap）。

     → **已拍板方案 A（2026-09-16）**：改为 **BM25 主判据 + 向量负责排序**，门槛
     **由语料分数分布自动校准**（BM25 分数是绝对值、随语料规模漂移，**不能写死常数**）。
     ⚠️ **方案 A 实施前，A3 属「已知不达标」** —— 不得宣称已达成。完整数据与处置见 `docs/M1_INGEST_AUDIT.md`。
   - **⚠️ 2026-09-15 实测修正：FTS5 中文不可用 → 全文索引层改用 Python BM25 + bigram 分词。** 实测（SQLite 3.41.2，**含 ASCII 阳性对照证明探针本身有效**）：`unicode61` / `porter` 对中文查询 **0 命中**（中文整句被当作单个 token）；`trigram` 仅 **3 字**查询命中、**5 字**查询 **0 命中**。→ 复用知识库已验证方案：`search_wiki.tokenize`（中文 bigram + 英文词，纯 `re`，**零依赖**）+ `BM25Index`（k1=1.5, b=0.75）。
   - **索引构建必须后台化**：单机 CPU 上 embed 万级块耗时很长，`ingest` 一律后台跑，带进度、断点续跑与失败重试，**不得阻塞 UI**。
4. **无引用 = 不算回答**：生成层强制携带来源；检索为空时明确回「未找到相关公告」，**不允许模型凭空补**。
5. **增量更新**：幂等键 = **`(code, source, published_at, title)`**。
   （2026-09-16 修正：原写 `(code, source, date)` 缺 `title`，会让**同一天的多份公告互相覆盖** ——
   实测 ingest 25 条只落库 7 篇 = 静默丢数据；见 `utils/rag/store.py` v2、`tests/test_rag_store.py`。）
   每日增量拉取，不重建全库。⚠️ UNIQUE 约束变更无法用 ALTER 完成，**旧库必须删除后重建**。

### 3.4 数据模型

```sql
documents(id, code, source, title, url, published_at, file_path, sha256, created_at)
chunks(id, doc_id, seq, text, is_table, page_no, token_len)
embeddings(chunk_id, model, dim, vec BLOB)   -- bge-m3，dim=1024
meta(key, value)                             -- 索引版本、模型名、tokenizer 版本、构建时间
```

> **2026-09-15 修正**：`chunks_fts`（FTS5 虚表）**已移除** —— 中文检索实测不可用，见 §3.3 第 3 条。
> BM25 索引**不落表**：启动时由 `chunks` 现建（万级块内存可接受），避免双份数据与索引漂移。

### 3.5 验收标准（P1 门禁，逐条可复跑）

| # | 验收命令 | 预期输出 |
|---|---|---|
| A1 | `python scripts/rag_cli.py ingest --code 600519 --days 90` | 落盘 ≥20 份文档，`kb.db` 新增块数打印，退出码 0 |
| A2 | `python scripts/rag_cli.py query "茅台上半年营收"` | 返回 ≥3 条命中，每条带 `source_url` + `published_at` |
| A3 | 同上，检索无关词（如"量子计算"） | 返回 0 条，**不得**返回勉强相关内容 |
| A4 | `pytest tests/test_rag.py -q` | 全过（含切块、RRF 融合、幂等、空结果四类用例） |
| A5 | 端到端：AI 对话问「XX 公司最近一份公告说了什么，附来源」 | 回答含 ≥1 个可点击来源；工具时间线出现 `retrieve_docs` |
| A6 | 断网复跑 A2 | 本地库检索仍然可用（不依赖外部 API） |

---

## 4. M2 · 长期记忆层

### 4.1 三类记忆（分离存储，分离召回策略）

| 类型 | 内容 | 召回方式 |
|---|---|---|
| **偏好记忆** | 风险承受度、投资风格、禁忌（如"不碰杠杆"） | 每次对话**必注入**（小、固定） |
| **事实记忆** | 持仓、成本、长期计划、关注标的 | 按当前问题涉及的标的召回 |
| **经验记忆** | 历史决策 + 事后结果（"8 月加仓 X，回撤 12%"） | 向量召回 top-3 |

### 4.2 写入时机

- **显式**：用户在对话中说"记住…"→ 直接落库
- **隐式**：每轮对话结束后，由轻量 summarizer 抽取候选记忆 → 落 `pending` 表 → **下次会话开始时展示给用户确认**（避免 AI 自行写错记忆）

> 设计原则：**记忆写入必须可审计、可删除**。用户能看全部记忆并一键删除。

### 4.3 验收标准（P2 门禁）

| # | 验收命令 | 预期输出 |
|---|---|---|
| B1 | 会话 A 输入「我风险承受低，不碰杠杆」→ 关掉重开 | `memory` 表新增 1 条偏好记忆 |
| B2 | 会话 B 问「给我个操作建议」 | 回答明确体现该偏好（不推荐高杠杆标的） |
| B3 | `pytest tests/test_memory.py -q` | 全过（写入/召回/去重/删除四类） |
| B4 | 设置页删除该记忆 → 再问同样问题 | 回答不再体现该偏好（**删除必须真生效**） |

---

## 5. M3 · 编排层（LangGraph 渐进接入）

### 5.1 接入策略：**只接一条链路，不重写**

选「股票深度诊断」这一条链路做图化（它本来就是多步 + 可分支 + 需要重试），其余 22 个工具调用保持现有线性循环。

```
[入口] 股票代码
  ↓
[节点 data_fetch] 拉行情/财报/资金流（现有工具，失败可重试）
  ↓
[条件边] 财报数据是否齐？──否──→ [节点 fallback] 降级为「仅行情诊断」
  ↓                是
[节点 analyze] 6 引擎分析（基本面/排雷/护城河/估值/三表/辩论）
  ↓
[节点 retrieve] M1 检索相关公告与研报
  ↓
[节点 synthesize] 汇总成带引用的诊断报告
  ↓
[节点 human_review] 用户确认 ──修改──→ 回到 analyze（受最大轮次限制）
  ↓
[出口] 报告 + 检查点持久化（可断点续跑）
```

### 5.2 关键设计决策

1. **feature flag 并存**：`ORCHESTRATOR=legacy|graph`，出问题一键切回，**不破坏现有可用版本**。
2. **检查点落 SQLite**：断点续跑，长任务不因网络中断重跑。
3. **最大轮次硬限制**：human_review 循环上限 3 轮，防死循环烧 token。
4. **只迁移一条链路**：验证收益后再决定是否推广。

### 5.3 验收标准（P3 门禁）

| # | 验收命令 | 预期输出 |
|---|---|---|
| C1 | `pytest tests/test_graph.py -q` | 全过（节点/条件边/重试/轮次上限/检查点） |
| C2 | `ORCHESTRATOR=graph` 跑一次深度诊断 | 全链路完成，日志显示分支命中情况 |
| C3 | 中途 kill 进程 → 重启续跑 | 从最近检查点继续，不重跑已完成节点 |
| C4 | `ORCHESTRATOR=legacy` 回归 | 现有行为不变（回归测试全绿） |

---

## 6. 风险与回滚

| 风险 | 对策 |
|---|---|
| akshare 接口变动（历史已发生多次） | 采集层 adapter 模式 + 失败降级；索引层与采集层解耦，采集挂了不影响检索 |
| 语料版权（研报） | 仅本地私用，不入仓库、不随 exe 分发（语料落 `_DATA_DIR/corpus/`，天然在仓库外且 `*.db` 已 gitignore） |
| 向量模型在用户机器上不可用（无 ollama） | 降级链：本地 bge-m3 → 远程 API → **纯 BM25 关键词检索**（功能降级但不崩；验收见 `M1_KERNEL_SPEC.md` §6 K3） |
| LangGraph 引入新依赖影响打包 | 桌面壳打 exe 时验证；若体积/兼容性出问题，M3 保持源码可用、不进 exe |
| 15 天工期被高估 | 三模块独立可交付，**任意时点停下都是完整可用的增量** |

**回滚**：每期一个 commit + tag；`ORCHESTRATOR` / `RAG_ENABLED` 两个 flag 可运行时关闭新能力。

---

## 7. 分期与验收门

| 期 | 内容 | 门禁 |
|---|---|---|
| **P1** | M1 采集 + 切块 + 索引 + `retrieve_docs` 工具 + 引用渲染 | A1-A6 全过 |
| **P2** | M2 记忆层 + 设置页记忆管理 UI | B1-B4 全过 |
| **P3** | M3 LangGraph 单链路接入 + flag | C1-C4 全过 |
| **P4（可选）** | 把 P2 行情页 / 回测 / 基金对比补上（原 ROADMAP 缺口） | 与本次设计解耦 |

**规则**：每期结束时，未通过对应门禁 → 不进入下一期（Gate 前置验收）。

---

## 8. 与岗位要求 / 其他主线的对应

| 岗位要求（图1 智能体岗） | 本设计对应 |
|---|---|
| 职责3：RAG、向量检索、非结构化文档处理、私域知识库与大模型结合 | **M1 完全对口**（且是原话） |
| 职责2：规划、决策、**记忆**、工具调用核心模块 | M2（记忆）+ 现有 23 工具（工具调用）+ M3（规划/决策） |
| 职责1：自主规划、决策与执行 | M3（多步图编排 + 条件分支 + 人审） |
| 要求4：掌握 LangChain / LangGraph / LlamaIndex | M3 用 LangGraph 落地，**产出可写"用 LangGraph 重构深度诊断编排"** |
| 职责4：模型训练与调优 | **本设计不做**（明确排除，面试诚实说明） |

**与其他主线的复用**：
- OSPP 2026（DB-GPT 财报课题，核心是 **provenance 来源追踪**）→ M1 的 `documents.url + page_no` 溯源设计**可直接复用**
- 知识库（A→D-lite 已闭环）→ 切块 / RRF / 增量索引经验**直接搬**
- 工作台 P2 流程引擎 → M3 的图编排思路可借鉴

---

## 9. 决策记录

| # | 决策点 | 结论（2026-09-14） |
|---|---|---|
| 1 | 覆盖范围 | ✅ **三个全做（M1 + M2 + M3）** —— 用户拍板 |
| 2 | 向量化方案 | ⏳ 未定。倾向本机 ollama `bge-m3`（免费、离线、可随 exe 分发）；开工前需确认目标机器嵌入模型可用性与降级链 |
| 3 | 开工时机 | ⏳ **开工不急，用户通知后启动**（用户 2026-09-14 明示「开工不急，我会跟你说」） |
| 4 | 与 9/15 发布的关系 | 本设计不阻塞发布；**建议先发 v1.0.0 再开工**（HEAD 仍停在 `4d51a15` @ 2026-09-08，9/13 回归未执行） |
| 5 | **M2 记忆方案** | ✅ **自研 SQLite 记忆表**（用户 2026-09-14 拍板）—— 离线可用、可随 exe 分发、保住「零配置即用」卖点；**不引入 Mem0 / Zep / Letta** 等云依赖 |
| 6 | **M3 编排框架** | ✅ **LangGraph**（详解见 §10）—— SQLite checkpointer 即可、`interrupt()` 直接用 |

**下次开工入口**：用户说「开始 M1」即可 → 先做 P1 采集 + 切块 + 索引 + `retrieve_docs` 工具，门禁 A1-A6 全过才进 P2。

**开工前需先确认的 2 件事**：
1. 向量化走本地 `bge-m3` 还是远程 embedding API
2. 是否先完成 v1.0.0 Release（避免发布线一直挂着）

---

## 10. 附录：智能体框架生态核实与选型结论（2026-09-14 联网核实）

### 10.1 核实方法

对二手材料抽**带时间/版本的硬断言**逐条查：一手来源（官方博客 / arXiv / 官方站）优先，可验证指标（GitHub stars、PyPI 下载量）次之，厂商内容站降权。

### 10.2 已核实事实（附来源等级）

🟢 官方一手 ｜ 🟡 多源一致但未直读官方 ｜ 🔴 厂商营销 / SEO 内容（不作依据）

| 事实 | 结论 | 等级 |
|---|---|---|
| LangChain 1.0 / LangGraph 1.0 GA 日期 | **2025-10-22**（官方博客；1.0 唯一破坏性变更 = `langgraph.prebuilt` 废弃，功能移入 `langchain.agents`） | 🟢 |
| LangChain 1.0 的 `create_agent` 底层 | **跑在 LangGraph 上**（学 LangGraph 即吃下 LangChain 执行语义） | 🟢 |
| LangGraph 1.0 核心能力 | checkpointer（内存/SQLite/Postgres/Redis）、durable execution、`interrupt()` HITL 原语、time-travel 调试、Pregel 超步并发 | 🟢 |
| 微软 Agent Framework 1.0 GA | **2026-04-03**（.NET + Python）；AutoGen 与 Semantic Kernel 同日进 maintenance mode（只修 bug 与安全，SK 关键修复支持至 ≥2027-04） | 🟡 |
| Google ADK 语言与版本 | 五语言 Python / TS / Go / Java / Kotlin；Go 1.0 = 2026-03-31、Java 1.0 = 2026-03-30、Kotlin 0.1.0 beta = 2026-05-21；原生 A2A（`to_a2a()` 生成 Agent Card）；ADK 2.0（2026-04/05）加 graph workflows；2.4.0 = 2026-07-07 | 🟡 |
| 阿里 AgentScope 1.0 | **2025-09-02** 发布（通义实验室）+ arXiv 2508.16279 技术报告；Java v1.0 = 2025-12-10；三层架构 Core / Runtime / Studio | 🟡 |
| Claude Agent SDK | = Claude Code 的 harness 外露（内置 Read/Write/Edit/Bash/Grep/WebSearch + subagent + hooks + permission + 原生 MCP）；2026-05 支持动态工作流最多 1000 并行 subagent | 🟡 |
| OpenAI Agents SDK | 四原语 Agents/Handoffs/Guardrails/Sessions + 内建 tracing；**无 durable execution / checkpoint / HITL 原语**；2026-04 加 sandbox 执行；Assistants API 2026-08-26 日落 | 🟡 |
| 协议层归属 | **MCP 与 A2A 均转入 Linux Foundation 治理**；ACP 并入 A2A（2026 初） | 🟡 |

### 10.3 采用率数据（引用务必谨慎）

| 数据 | 内容 | 可用性 |
|---|---|---|
| LangChain "State of Agent Engineering" | 1,340 受访（2025-11~12）：**57.3% 组织有 agent 在生产**；**88% 的 agent 项目未能上线**；89% 上了可观测 vs 仅 52% 有 eval | 🟡 可用，但**是 LangChain 自己发的调查，有立场** |
| 框架份额 | "LangGraph 34% / CrewAI 28% / AutoGen ~18%" | 🔴 社区调查，来源自评 Grade B，**不可当事实引用** |
| CrewAI 自报 | "60% Fortune 500 采用 / 月 450M workflows / 10 万认证开发者" | 🔴 **厂商自报**，不可引用（GitHub stars ≈50K 可独立验证） |
| ⭐ **arXiv 2512.04123《Measuring Agents in Production》** | 20 个生产团队案例 + 调查：**"生产团队强烈偏好自研 in-house 实现，而非直接套用现成框架"** | 🟢 **与本项目"自研编排"路线不冲突，反而印证其合理性** |

### 10.4 本项目选型结论

| 层 | 结论 | 理由 |
|---|---|---|
| M3 编排 | ✅ **LangGraph** | 需求（状态机 + 条件分支 + 检查点 + 人审中断）与其定义域完全重合；1.0 已稳定近一年；MIT；模型无关 |
| M2 记忆 | ✅ **自研 SQLite** | 见 §9 决策 5 —— 不引 Mem0 / Zep / Letta（云依赖破坏本地优先定位） |
| M1 检索 | ✅ **自研 SQLite + Python BM25 + bge-m3 + RRF** | 已有成熟方案（知识库 A→D-lite）；Haystack / RAGFlow 的切块与 PDF 解析思路可参考，但不引入依赖（2026-09-15 更正：原写 FTS5，实测中文不可用已替换） |
| Trace | ✅ **JSONL 落盘** | Langfuse 自托管需 Docker（本机未装），现阶段不值得引入 |
| 明确排除 | CrewAI / AutoGen / Temporal / Inngest / Dify / Coze / LangSmith | 见下 |

**为什么排除**：

- **CrewAI**：原型最快，但**以生产耐久性换速度**（无节点级错误处理、核心仍 0.x、API 变动频繁；v1.14.x 才补 checkpoint/resume）。多源一致的迁移路径是「CrewAI 原型 → LangGraph 生产」——而 M3 恰需要断点续跑，正是其短板。
- **AutoGen**：2026-04-03 起进入 maintenance mode，**新项目应跳过**。
- **Temporal / Inngest**：通用 durable execution 底座，能力过剩；LangGraph checkpointer 已覆盖本项目需求。
- **Dify / Coze**：低代码平台，与「代码级可审计 + 可随 exe 分发」定位冲突。
- **LangSmith**：商业托管观测；现阶段用 JSONL 自建。

### 10.5 M3 实现要点（可直接作 spec 输入）

1. checkpointer 用 **SQLite**（官方支持，无需 Postgres）
2. 人审节点用 **`interrupt()`** 原语，不手搓审批中断
3. 并发用 Pregel 超步模型（本项目暂不需并行节点，保留可能性）
4. 版本锁定 LangGraph 1.x；注意 `langgraph.prebuilt` 已废弃
5. `ORCHESTRATOR=legacy|graph` flag 保留（见 §5.2）

### 10.6 面试用 30 秒版本

> 框架生态我按用途分四类看：**图编排**（LangGraph）、**角色式多 agent**（CrewAI）、**厂商原生 SDK**（OpenAI Agents SDK / Claude Agent SDK / Google ADK / 微软 MAF）、**低代码平台**（Dify/Coze）。生产长任务我选 LangGraph —— 有检查点、durable execution 和 `interrupt()` 人审原语。微软已用 Agent Framework 合并掉 AutoGen 与 Semantic Kernel，AutoGen 进维护模式，所以新项目不会选它。协议层 MCP 和 A2A 都归 Linux Foundation，那是防框架锁定的那一层。

### 10.7 来源可信度提醒（可迁移）

- **LangGraph 版本日期只引 LangChain 官方博客**（2025-10-22）。大量聚合站（respan、aiproplaybook、callsphere 等）写成「2026 年发布」，是**错的**；同理 AgentScope 1.0 常被误写成 2026。
- 厂商内容站（AgentMarketCap / FutureAGI / LumeValley 等）的数字**互相抄且带营销立场**，只作方向参考，不作数据引用。
- 优先采信顺序：**官方博客 / arXiv / 官方站 > GitHub stars & PyPI 下载量 > 厂商内容站**。

---

## 11. 外部评审处置与增量 backlog（2026-09-14）

> 来源：外部 agent 评审（2026-09-14，**静态读码、未跑代码**）。
> **本节每条代码级断言均由 Reasonix 独立复核**（grep + 定点读码），处置分「采纳 / 驳回 / 待定」。

### 11.1 代码级断言复核结果（10 条：**9 真 1 误**）

| # | 评审断言 | 复核结论 | 证据 |
|---|---|---|---|
| 1 | §1「长期记忆 ❌ 无」不准确 | ✅ **采纳，§1 已修正** | `utils/agent_memory.py`：两表持久化 + `SUMMARY_TRIGGER_ROUNDS=8` + 最近 3 条摘要注入 |
| 2 | 工具层契约薄 | ✅ 采纳（已补进 §1 与 §11.2 P0） | `_truncate(max_len=8000, list_top_n=20)`；无 schema 校验 / 超时 / 错误码 |
| 3 | §5 漏了 SSE 事件契约 | ✅ 采纳（已补进 §1） | `_progress_structured("tool_start" / "tool_end" / "memory_used")` 已在用 |
| 4 | `agent_run` 的 `model=` 默认参数被冻结 | ✅ **真 bug，且影响面比评审说的更大** | `agent_core.py:394` 签名确认；**grep 全部 5 个调用点（`services/agent_service.py` ×2、`report_service.py`、`pages/ai_chat.py`、`pages/stock_diagnosis.py`）都没传 `model`** → **设置页切换模型对 Agent 链路完全无效** |
| 5 | `tool_end.ok` 恒为 True | ✅ **真 bug** | `agent_core.py:520` 判 `startswith("工具执行失败")`，实际错误格式是 `agent_core.py:368` 的 `{"error": "工具执行出错：…"}` → 永不匹配 |
| 6 | 每轮把工具原始输出整条写进 `agent_messages` | ✅ 采纳 | `agent_core.py:533 record_message(session_id, "tool", output)` |
| 7 | 无 token / usage 统计 | ✅ 采纳 | `ai_helper.py:85 max_tokens=2000` 写死；全仓 grep 无 `.usage` |
| 8 | 429 无退避重试 | ✅ 采纳 | `ai_helper.py:138` 仅返回「⏳ 请求太频繁了」中文文本，无退避 |
| 9 | 工具调用顺序执行（无并发） | ✅ 采纳 | `agent_core.py:506 for tc in tool_calls` |
| 10 | **「动态上下文拼进 system prompt 破坏 prefix cache」** | ❌ **驳回** | `agent_core.py:441-461`：`system = AGENT_SYSTEM_PROMPT`（静态）**在前**，动态上下文与持仓快照 **append 在后** —— 这正是 prefix-cache 友好的顺序。真正破坏缓存的是「动态内容插在静态之前/中间」或「工具 schema 顺序不稳定」，本仓当前不属此列 |

### 11.2 P0 · 开工前置项（M1 之前必做）

| # | 项 | 为什么是前置 |
|---|---|---|
| P0-1 | ✅ **已完成（2026-09-15）**：`agent_run` 的 model 冻结 → `model=None` + 调用时解析 | 它是**配置链的断点**；不修则后续所有"换模型再测"都不可信 |
| P0-2 | ✅ **已完成（2026-09-15）**：`tool_end.ok` 判定对齐真实错误契约 + **统一 per-tool 错误码**（`error_code` / `tool` / `retryable`，新增 `make_tool_error()` 与 `tool_output_error()`） | M1 的 `retrieve_docs`、M3 的每个节点都踩在这层；不修则排障被误导 |
| P0-3 | ✅ **已完成（2026-09-15）**：A 段——golden set `tests/golden/cases.py`（26 条 / 覆盖 23 个工具）+ 离线契约 `tests/test_golden_offline.py`；B 段——`scripts/eval_agent.py`（在线评测，默认 dry-run 不花钱）+ `call_llm`/`agent_run` 的 token 记账（`usage` 跨轮累加） | §10.3 引用的调查：**89% 有可观测、仅 52% 有 eval**，我们两项都空 → 当前最便宜的差异化点 |
| P0-4 | ✅ **已完成（2026-09-15）**：必填参数校验（`INVALID_ARGS`）+ 单次调用看门狗超时（`TOOL_TIMEOUT_SECONDS`=30s → `TIMEOUT` 码，且不破坏工具内部超时的 `TOOL_EXCEPTION`）+ 并行执行能力（`parallel_tools`，**默认关**：工具内缓存/SQLite 的线程安全性待实测）。「结构化返回」已在 P0-2 完成 | 同上；并发还是"生产级 Agent"标配 |
| P0-5 | ✅ **已完成（2026-09-15）**：tool 消息落库截断为摘要 + 截断标记（`TOOL_MESSAGE_LIMIT`=600，标记含原始长度）；user/assistant 不受限 | 否则 M2 再往里加事实/经验记忆会彻底失控 |

> **P0-1/P0-2 验收证据（2026-09-15）**：
> ① **P0-1**：先写 RED（`tests/test_p0_agent_fixes.py` 8 条 → 4 failed）→ 修 → GREEN；全量 `pytest` **189 passed**（原 181）；同时修正了 `tests/test_m0_services.py` 里锁定**错误契约**的旧用例（它喂的是真实代码从不产生的「工具执行失败」字符串）。
> ② **P0-2 后半**：先写 RED（`tests/test_tool_error_contract.py` 12 条 → **10 failed**）→ 实现统一错误码 → GREEN；全量 `pytest` **201 passed**（189 + 12）。旧中文文案（"未知工具"/"未找到基金"）**逐字保留**，新字段只增不改。
> ③ **P0-3-A 离线评测（golden set）**：新增 `tests/golden/cases.py`（26 条，覆盖全部 23 个工具）+ `tests/test_golden_offline.py`（32 条）；全量 `pytest` **233 passed**（201 + 32）。**做了阴性对照**（临时把 4 处 `get_stock_diagnosis` 改成 `_TYPO` → 2 条合法性校验立刻 FAIL → 恢复后全绿），证明这套校验不是空测试 —— 落实 §11.7「测试要能被打破」。
> 变更记录见 `CHANGELOG.md`。

### 11.3 模块内小项（并入 M1/M2/M3，不新增一期）

**M1**
- **语料新增一类：用户自己的投资日记 / 交易记录**（`diary_entries` 表已存在）—— "私域"里最私域、且**零版权风险**的部分，直接呼应「越用越懂」定位，建议提为一等公民
- **前端引用渲染要进工作量估算**（`[1][2]` 可点击回溯）；P1 门禁目前只有后端命令
- embedding 降级链（本地 bge-m3 → 远程 → 纯 BM25）**从风险表一句话提升为验收项** —— 它是"零配置即用"卖点的守门员

**M2**
- **与 `agent_memory` 的整合方案（最高优先）**：复用 `agent_messages` 扩展，还是新表 + 摘要合并？不定就是**两套记忆并存**
- 冲突/更新策略：用户改口（"之前说保守，现在想积极点"）需要 supersede / 版本机制
- 容量与衰减：经验记忆需要重要性权重 + 时间衰减
- 隐式抽取的**成本与时机**（同步阻塞？异步？）—— 验收 B1-B4 一条都没覆盖它
- 一键导出 / 一键清空（记忆是明文 SQLite 且随 exe 分发，现仅"能删单条"）

**M3**
- **路由策略缺失（最核心）**：`ORCHESTRATOR=graph` 之后，「今天大盘怎么样」这类非诊断问题走 graph 还是 legacy？**谁分流？** 必须定义
- **`interrupt()` 在 SSE 里怎么表达**：现有事件契约没有「等待用户输入」类型，前端需新增事件 + 交互态
- checkpointer 的 SQLite 放哪（与主库同库？多开桌面壳会不会打架）
- `agent_service` 的 `ThreadPoolExecutor(max_workers=4)` 与 LangGraph 执行模型的兼容（同步线程 vs 异步）
- 打包体积 / 冷启动实测（LangGraph 会拖进 `langchain-core`）—— 开工前先 `pip install` 打一次包量一下

### 11.4 新增项（不改原三期结构）

1. **M0.5 · Agent 质量基线**（= §11.2 的 P0，横切）：后面三个模块的地基，也是岗位对标最缺的一块
2. **MCP 接入**：把 23 个工具暴露为 MCP server（"防框架锁定"那层的具体落点；本机已有 20 个 MCP 插件的实操经验）
3. **Agentic RAG（M1 × M3 的自然融合）**：把 `retrieve_docs` 从「一次检索的平工具」升级为「**可迭代检索子图**」（查询改写 → 检索 → 自评充分性 → 不满意再检索）。这是三个模块**唯一能讲成一个故事的融合点**：面试时"我做了 RAG + 记忆 + 图编排"是清单，而"我把检索做成 Agent 的可迭代动作、用 LangGraph 承载"是一个**设计判断**

### 11.5 建议执行顺序（工期紧时的砍法）

```
P0（P0-1 ~ P0-5） → M1 → M2 → M3 → MCP / Agentic RAG
```

**任意时点停下都是"完整可用的增量"，且停止点永远落在"质量基线已有"之后。**

### 11.6 岗位对标的措辞升级

§8 原文说的是"做了 RAG / 记忆 / 编排"。加上 P0 之后可改写为：

> 在开源金融 Agent 上完成了**检索（RAG）+ 长期记忆 + 图编排 + 评测基线 + token 成本可观测**的完整链路，并接入了 MCP 工具协议。

差别在后半句三个词（**评测 / 可观测 / MCP**）—— 它们是 2026 年智能体岗 JD 的高频词，而我们**目前一个都没覆盖**。
§8 说"职责 4 模型训练与调优不做，面试诚实说明"——诚实是对的，但**用「我做了 eval + 可观测」去补「我没做微调」** 比单纯说"我不做"有力得多：它表明你知道生产 Agent 的瓶颈不在微调，而在**质量闭环**。

### 11.7 本次核实的边界（诚实标注）

- 评审方**未能跑代码**（其 workspace shell 因 9/8 Windows 更新不可用），全部为静态读码
- 本次复核用 **grep + 定点读码确认了 10 条断言中的 9 条**（第 10 条驳回），但**未跑 pytest、未复现 4/5 两个 bug 的运行时表现**
- → 修 P0-1 / P0-2 前**先各写一条失败测试（RED）再修**（符合 TDD 契约，也避免"照着评审直接改"而漏判）

---

## 12. 独立审计记录（2026-09-15 · 子代理审 `4d51a15..HEAD`）

> 审计方：独立**只读**子代理（`tool:read_only_task`）。对象：3 个 commit（`132e802` / `05b5349` / `0d5ac98`），8 个文件，+1115/−14。

### 12.1 总体判定

| 对象 | 判定 |
|---|---|
| **P0-1（config 断点）/ P0-2（ok 判定失真）** | ✅ **达标**：真实缺陷、修复方向正确、旧中文文案与 SSE 契约零破坏；回归测试是**行为锚定**（revert 即 FAIL），**无「输入由被测代码自身生成」的自证陷阱** |
| **P0-3-A golden set** | 🟡 **仅算阶段性半成品**：`test_golden_case_drives_agent_run` 是「mock LLM 按用例吐序列 → agent 再执行同一序列」的**编排冒烟**，**不能称为评测**（已在 `cases.py` docstring 与 CHANGELOG 如实标注） |

### 12.2 findings 处置

| 级别 | 问题（审计要点） | 处置 |
|---|---|---|
| 🟡 | `tool_output_error` 以「顶层 `error` 为真值」判失败，属启发式耦合；未来工具若用顶层 error 传「部分成功/告警」会被误判 | ✅ 把「成功载荷不得含非空顶层 error」写进 `execute_ai_tool_v2` docstring + 2 条契约测试固化（含 `compare_funds_structured` 真实形态 `{"ok":true,"error":""}` 的反面用例） |
| 🟡 | `MIN_TOOL_COVERAGE=20` 形同虚设 | ✅ 提到 **23**（与注册表等值）：新增工具未同步进 golden set 时断言立刻失败 |
| 🟡 | `tool_end` 只转发 `error_code`，丢了 `retryable` / `tool` | ✅ 补 `retryable`（+测试）；`tool` 仍保留在工具返回体内，事件层暂不发（避免冗余） |
| 🟡 | `stock_moat_018` 与多工具用例的期望标定，在线阶段有歧义（推测性） | ✅ `stock_moat_018` 改为直接给代码；`cases.py` 写明在线判定约定：**按工具集合命中、顺序不敏感** |
| ⚪ | `error_code` 当前**无消费方**，CHANGELOG 却写「消费方可按类型分支」（宣称而非落地） | ✅ 措辞已改（CHANGELOG 加「诚实标注」） |
| ⚪ | 三个测试文件均**无自证陷阱**（正面结论） | 记录，无需动作 |

### 12.3 审计的诚实边界（与我们的规则一致）

- 审计方**跑不了 pytest**（只读环境）→ 「RED→GREEN」是它的**静态推演** → **由主代理实测补上**：`test_p0_agent_fixes.py` 8 条 → 4 failed；`test_tool_error_contract.py` 12 条 → 10 failed；各轮全量 189 → 201 → 233 → 236 passed，均为实际运行输出。
- **本次审计自身的经验（可复用）**：`tool:review` 跑满 8 步被暂停；`tool:read_only_task` 传 `effort=high` 报 `UNSUPPORTED_REASONING_EFFORT`（`deepseek-v4.1-flash` 的 supported 列表为空）→ **子代理用默认 effort，别传高推理档**。

---

## 13. 在线评测首跑与「连带发现」实证（2026-09-15）

### 13.1 首跑数据（`scripts/eval_agent.py --run`，全 26 条）

| 指标 | 结果 |
|---|---|
| 工具命中 | **26/26 = 100%**（模型选工具零失误） |
| 事实命中 | 38/45 = 84.4%（宽松子串） |
| token | prompt 204,381 / completion 12,587 / total **216,968**（58 次模型调用） |
| 耗时 | 210.8s（2 条试水时 16.8s / 13,820 token） |

> 显式未做：**未给 `--price`** —— 单价随平台与峰谷变动，宁可只报 token 也不报一个会过时的钱数。

### 13.2 价值实证：在线评测抓出了离线测试**结构上抓不到**的三件事

离线（`test_golden_offline.py`）锁的是「编排契约」——mock 按用例吐工具序列、agent 执行同一序列，
**它不可能发现"工具调对了但数据是空的/假的/拿不到"**。首跑立刻暴露三条：

1. **`_fetch_fund_history` 恒返回空**（按位置取列 → 全行 `ValueError` 被跳过）。离线测试全绿、264 条断言全过，但**基金历史净值实际一条都拿不到**。
2. **`get_fund_info` 净值四字段是硬编码占位** —— 持仓页恒 `--`、**价格预警恒按涨跌 0 判断（永不触发）**，属静默失效。
3. **国内财经域名未绕系统代理** —— 评测里所有股票数据都因 `ProxyError ... RemoteDisconnected` 失败；而本机直连实测 200（0.146s），说明是**代理分流问题而非数据源问题**。

→ 结论：P0-3-B 的投入是**值得的**，它把「离线契约」升级成「能发现静默失效的评测」。
已在 CHANGELOG「在线评测连带发现的缺口修复」条目与 `tests/test_fund_data_fixes.py`（6 条回归锁）中闭环，
全量 `pytest` **264 passed**。

### 13.3 一处环境事实（供后续排障）

`git push` 在本仓库**不能走 HTTPS**：`credential.helper=manager`（GCM）会在无交互的前台挂起
（实测 >2 分钟超时）。已改用 **SSH 443**（`~/.ssh/config` 已配 `Host github.com → ssh.github.com:443`）
推送成功。
