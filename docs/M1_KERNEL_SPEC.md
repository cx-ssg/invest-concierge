# M1 内核切片 · 实现级 Spec（KERNEL SPEC）

> 状态：**待用户审阅 → 通过后按 TDD 开工**
> 上游设计：`docs/COVERAGE_DESIGN.md` §3（M1 私域知识层）
> 生成：2026-09-15 ｜ 依据：现场读码 + 实测探针（见 §7 证据）

---

## 1. 本切片范围

**做**：RAG 内核 —— 切块、分词、BM25、向量、RRF 融合、持久化、单测。
**不做（YAGNI，留后续切片）**：PDF 解析、公告正文抓取、前端引用渲染、时效加权。

> ✅ **2026-09-15 范围补正**：本节初版曾把「`retrieve_docs` 工具注册」与「语料试水」列为"不做"，
> 但用户拍板的方案 A 原文包含二者（「内核 + **retrieve_docs 工具** + 语料先用少量真实公告试水」）。
> 已补做：第 24 个 Agent 工具 + `scripts/rag_ingest.py`（akshare 公告采集）+ K5 端到端验收。

**理由**：`COVERAGE_DESIGN.md` §6 风险表第一条即「akshare 接口变动」，采集层最易被外部打掉；内核稳定且可独立测试。

---

## 2. 复用清单（不重写，来源已核对）

| 组件 | 源文件 | 复用方式 |
|---|---|---|
| 中文 bigram 分词 | `<ws>/co-planning/scripts/search_wiki.py:374 tokenize()` | 复制实现（纯 `re`，零依赖） |
| BM25 | 同上 `:383 class BM25Index`（k1=1.5, b=0.75） | 复制实现 |
| RRF 融合 | 同上 `:503 run_hybrid()`（`1.0/(60+rank+1)`） | 复制并**精简**（去掉 domain/pool 维度，YAGNI） |
| embedding 客户端 | `<ws>/co-planning/scripts/embed_client.py` | 复制（stdlib-only urllib → ollama `/api/embed`，DIM=1024，timeout=300） |
| 切块结构参考 | 同上 `search_wiki.py:45 chunk_markdown()`（CHUNK_TARGET=600 / CHUNK_MAX=1200） | **参考结构 + 扩展表格保护**（原实现无表格语义） |

> `<ws>` = `C:/Users/cx101/AppData/Roaming/reasonix/global-workspace`

---

## 3. 模块与接口签名

```
utils/rag/
  __init__.py
  embed.py      embed_texts(texts, model="bge-m3", timeout=300) -> list[list[float]]
                DIM = 1024
                # ollama /api/embed；失败抛 RuntimeError 并给出「ollama pull bge-m3」指引（K3）
  tokenize.py   tokenize(text) -> list[str]
                # 中文连续片段 bigram + [A-Za-z0-9_]{2,} 小写英文词
  bm25.py       class BM25Index:
                    __init__(docs: list[list[str]])
                    score(query_toks: list[str], k1=1.5, b=0.75) -> list[float]
  hybrid.py     run_hybrid(query: str, matrix, meta: list[dict], k: int = 5,
                           pool: int | None = None, query_vec=None,
                           min_sim: float = 0.45, min_sim_ratio: float = 0.85
                           ) -> tuple[list[int], dict[int, float]]
                # 向量 top-pool + BM25 top-pool → RRF(k=60) → 取 k
                # pool 默认 max(k*20, 50)
                # query_vec：可注入查询向量（单测/批量复用，避免重复调 embedding）
                # 相关性双判据（2026-09-15 实测修正，原写「阈值 0.35」不成立）：
                #   ① max_sim < min_sim → 整查询判为无关，返回 ([], {})（A3）
                #   ② 语义路保留 sim > max_sim * min_sim_ratio；BM25 路保留 分 > 0
                #   实测依据：无关查询 max_sim 0.30~0.38、相关块 0.72~0.80
  chunker.py    chunk_document(text: str, target: int = 600, hard_max: int = 1200) -> list[dict]
                # 返回 [{"seq": int, "text": str, "is_table": bool}]
  store.py      ensure_schema(conn)                              # SCHEMA_VERSION = 2
                upsert_document(conn, doc: dict) -> int
                # 幂等键 **(code, source, published_at, title)** —— v1 少了 title，
                # 会让同一天的多份公告互相覆盖（实测 25 条只落库 7 篇，见 test_rag_store.py）
                insert_chunks(conn, doc_id: int, chunks: list[dict]) -> list[int]
                save_embeddings(conn, chunk_ids: list[int], vecs: list[list[float]], model: str)
                load_index(conn) -> tuple[list[dict], "np.ndarray"]   # (meta, matrix)
  retrieve.py   retrieve_docs(query, code=None, top_n=5, db_path=None, query_vec=None) -> str
                # 第 24 个 Agent 工具（返回 JSON 字符串，与既有 23 工具同契约）
                # 无命中 → results=[] + 明确 message（设计 §3.3 第 4 条「无引用 = 不算回答」）
```

**依赖**：仅 `numpy`（项目已有）+ stdlib。**不新增第三方依赖**（不需要 jieba / pdfplumber / rank_bm25）。

---

## 4. 切块器规格（表格保护是扩展点）

1. 输入为纯文本（markdown 或已解析的 PDF 文本）。
2. 识别**表格行**：以 `|` 分隔且行内含 ≥2 个 `|` 的行 → 连续表格行合并为一个**表格块**（`is_table=True`），**不切碎**，整块可超 `target` 但不超过 `hard_max` 的 3 倍（超长表格按行切并**重复表头**）。
3. 非表格段落：按空行/标题聚合到 `target`≈600 字；单块 > `hard_max` 时硬拗断。
4. 标题行（`#` 开头）作为**块前缀**保留，保证块自解释。
5. `seq` 从 0 递增，同一文档内唯一。

---

## 5. 数据模型与位置

`kb.db` 位置：与主库并列，`config._DATA_DIR/kb.db`（现 `DB_FILE = _DATA_DIR/fund_agent.db`，`config.py:101`）。`.gitignore` 已含 `*.db`，自动不入库。

```sql
documents(id, code, source, title, url, published_at, file_path, sha256, created_at)
chunks(id, doc_id, seq, text, is_table, page_no, token_len)
embeddings(chunk_id, model, dim, vec BLOB)   -- bge-m3, dim=1024, float32
meta(key, value)                             -- schema_version / model / tokenizer / built_at
```
**无 FTS5 表**（中文实测不可用，见 `COVERAGE_DESIGN.md` §3.3 第 3 条）。BM25 索引启动时由 `chunks` 现建。
documents 的 **UNIQUE 约束 = (code, source, published_at, title)**（v2；v1 少了 title → 同日公告互相覆盖）。

> ⚠️ **schema v1→v2 必须重建库**：UNIQUE 约束无法用 ALTER 修改。删 `kb.db` 后重新 ingest。
> `ensure_schema()` 会检出旧版本并打印提示，**不自动删库** —— 静默清空用户数据比报错更糟。

---

## 6. 验收（Gate 前置，逐条可复跑）

| # | 命令 | 预期输出 |
|---|---|---|
| **K1** | `pytest tests/test_rag_core.py tests/test_rag_retrieve.py tests/test_rag_store.py -p no:warnings` | **21 passed**（12 内核 + 4 工具 + 5 持久化）；含：中文 bigram / BM25 排序 / **RRF 精确融合值（能区分纯向量·纯 BM25·真融合）** / 无关块排除 / 空查询 / 表格不切碎 / 超长表格拆分并重复表头 / embedding 失败给指引 / retrieve_docs 引用字段齐备 / **幂等键（同日多公告不互相覆盖）** |
| **K2** | `python scripts/rag_probe.py`（UTF-8 脚本文件） | 中文长查询（≥5 字）**#1 命中正确块**（贵州茅台段）；⚠️ 已知行为：与查询共享通用词（如"增长"）的弱相关块仍会经 **BM25 路**进入结果，见 §9 |
| **K3** | `python scripts/rag_probe.py --no-embed` 与 `python scripts/rag_probe.py --embed-url http://127.0.0.1:1/api/embed` | 两条降级路径均 **exit 0 不崩溃**，且给出 `ollama pull bge-m3` 等可操作指引；BM25 单路仍能返回结果 |
| **K4** | `python scripts/rag_probe.py --query "量子计算最新进展"` | **返回 0 条（`RESULT: NO_HIT`）** —— A3「无关查询返回 0 条」的内核级验收 |
| **K5** | `python scripts/rag_ingest.py --code 600519 --limit 25`，再用 `retrieve_docs` 检索 | **documents / chunks / embedded = 25 / 25 / 25**（schema v2）；「董事会决议公告」「利润分配方案」命中对应公告且带 `url` + `published_at`；无关查询返回 0 条 |

> ⚠️ **K2 必须走脚本文件**：bash 内联 `python -c` 传中文会按 cp936 破坏源码字符串（本会话已实测踩到，导致首轮探针假阴性）。

---

## 7. 证据（本 spec 的实测依据）

| 事实 | 证据 |
|---|---|
| bge-m3 可用、1024 维 | `POST http://127.0.0.1:11434/api/embed {"model":"bge-m3","input":"..."}` → `embeddings[0]` 长度 1024 |
| `ollama list` 含 bge-m3 | `bge-m3:latest 790764642607 1.2 GB` |
| FTS5 中文不可用 | 探针 `%TEMP%/fts5_cn_probe.py`：trigram 3 字→hits=1、5 字→hits=0；unicode61/porter→0；**ASCII 对照 hits=1** |
| akshare 接口在位（后续切片用） | akshare 1.18.64，`stock_notice_report` / `stock_individual_notice_report` / `stock_research_report_em` 均存在 |
| 项目内 RAG 代码为 0 | 全仓 grep `embedding/bge/faiss/sqlite_vec/retrieve_docs` → 源码 0 命中 |

---

## 8. TDD 步骤顺序（RED → Verify RED → GREEN）

1. **RED**：写 `tests/test_rag_core.py`，覆盖
   - `test_tokenize_chinese_bigram`（"贵州茅台" → ["贵州","州茅","茅台"]）
   - `test_bm25_ranks_relevant_first`
   - `test_rrf_fusion_order`（构造只有向量命中 / 只有 BM25 命中的两路，验证融合后顺序）
   - `test_chunker_table_not_split`（表格 8 行 → 1 块且 is_table=True）
   - `test_chunker_long_paragraph_hard_max`
   - `test_empty_query_returns_empty`（K/空结果）
   - `test_embed_dim_mismatch_raises`
   → 运行，确认 **失败原因是功能缺失**（非 import/语法错），记录 N failed。
2. **GREEN**：实现 `utils/rag/*` 最少代码使全过。
3. **回归**：`pytest -p no:warnings` 全绿（基线 **274 → 276 passed**，新增 2 条内核加固用例）。
4. **K2/K3 实跑**：`scripts/rag_probe.py`（UTF-8 文件）。

---

## 9. 已知风险与对策

| 风险 | 对策 |
|---|---|
| 用户机器无 ollama | 降级链：embed 失败 → 仅 BM25 单路（`rag_probe.py --no-embed` 即此路径的验收） |
| 万级块 embedding 耗时（实测 1.47 块/秒） | 后台化 + 断点续跑（后续采集切片实现，本切片只保证 `embed_texts` 超时 300s） |
| 表格识别基于 `\|`，非 markdown 表格（如 PDF 抽取）可能误判 | 本切片仅支持 markdown 表格；PDF 表格识别留 PDF 切片 |
| **BM25 路无相对阈值** → 共享通用词（如"增长"）的弱相关块会进入结果 | **已知行为，刻意保留召回**（K2 实测 #2 即此例：比亚迪块仅共享"增长"）。待真实语料接入后用标注数据决定是否给 BM25 加相对阈值 —— **不臆造阈值** |
| **阈值样本量小**（bge-m3 实测仅 5 篇样例） | `MIN_SIM=0.45` / `MIN_SIM_RATIO=0.85` 是**初值**，接入公告/研报后必须重测回填（已同步进 `COVERAGE_DESIGN.md` §3.3） |
| `min_sim` / `min_sim_ratio` 可在降级路径被显式归零 | 仅 `rag_probe.py` 的降级分支如此使用。⚠️ 曾因**无条件归零**导致无关查询返回 3 条（2026-09-15 实测发现并修复，probe 内已留注释） |
