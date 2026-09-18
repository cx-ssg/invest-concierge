# -*- coding: utf-8 -*-
"""`retrieve_docs`：M1 私域知识层的 Agent 工具实现。

契约：`docs/COVERAGE_DESIGN.md` §3.2 接入层（第 24 个工具）、§3.3 第 4 条
「无引用 = 不算回答：检索为空时明确回『未找到相关公告』，不允许模型凭空补」。

**2026-09-16 判据重构 + 产线 bug 修复**：
- 证据判据改由 `EvidenceJudge`（SAR + V1）给出，**不再用向量 `max_sim`**；
- ⚠️ **修复产线 bug**：`judge` 必须用**全库**构建。旧实现在按 `code` 过滤 matrix **之后**
  才让 `max_sim` 闸门跑在子集上，而阈值是在全库上定的 → 单标的检索池变小会系统性过度弃权、
  接入第二个标的时反向失真（外部评审读码指出，2026-09-16 确认）。

**2026-09-17 补 `chunk_id`**：返回体此前缺块唯一标识 → 前端无法把引用 `[1]` 链回具体段落
（设计 §3.2 的引用渲染要求）。`chunk_id` 因此是引用溯源的必要字段，补入每条结果。

⚠️ **订正（独立审计 P3-1）**：本条曾把「离线评测无法判定 gold 是否召回」也列为理由 ②，
**该理由不成立** —— `scripts/rag_eval.py` 的 `evaluate()` 直接用 `load_index()` 里的
`meta[i]["chunk_id"]`，**从不调用 `retrieve_docs`**，故本改动对离线评测**没有影响**。
（当时"33 条正例全判未召回"是我临时**诊断脚本**缺 id 所致，与产线返回体无关。）

⚠️ `chunk_id` 就是 `chunks.id`（库内自增），**只在同一个 kb.db 构建内稳定** —— 语料重建后会变。
它可用于**同一次构建内的引用回跳与离线评测**，**不可**跨构建持久化（前端若缓存引用，需带构建标识）。

**2026-09-17 二次修复（独立审计实测驱动）**：`none` 档**不再清空结果**。此前 `run_hybrid`
在判 `none` 时直接 `return [], {}`（**检索前硬停**），把**已经检索到的正确证据**一并丢掉 ——
实测 holdout 21 条正例走裸检索 `Recall@5 = 21/21`，其中 `rel-0015` 的 gold 排在 **rank 1**
仍被弃权（`rel-0014` 在 rank 4）→ 报告的 `Recall@5 0.905` 与满分的差距**全部**由这个硬停造成。
现改为「返回候选 + 最强警示语（`NONE_EVIDENCE_NOTE`）」；`evidence_level` 仍照算，评测分档不变。

返回 **JSON 字符串**（与项目既有 23 个工具的统一契约一致）。
"""
import json

from utils.rag import store as rag_store
from utils.rag.evidence import LEVEL_NONE, LEVEL_WEAK, EvidenceJudge
from utils.rag.hybrid import run_hybrid
# ⚠️ 2026-09-18 第六轮审计二 P1-1：措辞抽到 `utils/rag/messages.py`（**单一事实源**）。
# 原委：`NONE_EVIDENCE_NOTE` 改了措辞（「没有」→「未能确认」），但
# `agent_core.AGENT_SYSTEM_PROMPT` 里**权威更高**的同一句仍命令模型
# 「必须明确告诉用户"该数据不可得"」⇒ 假陈述从高权威处重现。两边现已同源。
from utils.rag.messages import NO_HIT_MESSAGE, NONE_EVIDENCE_NOTE, WEAK_EVIDENCE_NOTE
from utils.rag.tokenize import tokenize


def retrieve_docs(query, code=None, top_n=5, db_path=None, query_vec=None):
    """检索私域语料（公告 / 研报 / 财报），返回 JSON 字符串。

    - `code`：限定标的（None = 全库检索）。**只影响返回哪些块，不影响判据**（判据恒用全库）
    - `query_vec`：可注入查询向量（单测 / 批量场景复用，避免重复调用 embedding）
    - 返回体含 `evidence_level`（none / weak / strong）与 `evidence`（sar / v1）
    """
    conn = rag_store.get_conn(db_path)
    try:
        meta_all, matrix = rag_store.load_index(conn)
    finally:
        conn.close()

    if not meta_all or matrix is None:
        return _payload(query, code, [], NO_HIT_MESSAGE, LEVEL_NONE)

    # ① 判据：**全库**构建 —— 不受 code 过滤影响（旧实现 bug 的修复点）
    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta_all])

    # ② 检索池：按 code 过滤，决定「从哪些块里挑」
    pool_meta, pool_matrix = meta_all, matrix
    bm25_index = judge.index          # 池 == 全库时可复用，省一次 O(N·L) 构建
    if code:
        keep = [i for i, m in enumerate(meta_all) if m.get("code") == code]
        if not keep:
            return _payload(query, code, [], NO_HIT_MESSAGE, LEVEL_NONE)
        pool_meta = [meta_all[i] for i in keep]
        pool_matrix = matrix[keep]
        # 池 ≠ 全库 → 排序用的 BM25 必须在**池内**重建，否则分数与 meta 索引错位
        bm25_index = None

    order, rrf, evidence = run_hybrid(
        query, pool_matrix, pool_meta, k=top_n, query_vec=query_vec,
        judge=judge, bm25_index=bm25_index,
    )

    results = []
    for rank, idx in enumerate(order, 1):
        m = pool_meta[idx]
        results.append({
            "rank": rank,
            "score": round(float(rrf[idx]), 6),
            "chunk_id": m.get("chunk_id"),
            "text": m.get("text"),
            "title": m.get("title"),
            "url": m.get("url"),
            "source": m.get("source"),
            "published_at": m.get("published_at"),
            "code": m.get("code"),
            "is_table": bool(m.get("is_table")),
        })

    ev_level = evidence.level if evidence is not None else None
    if ev_level == LEVEL_NONE and results:
        # 2026-09-18（审计 B 的 P2-2）：none 档的候选块**不可引用**。
        # 设计 §3.3 第 4 条「无引用 = 不算回答」原本由「物理清空」执行；清空撤销后需要新的执行者
        # —— 剥掉引用凭据（`url` / `title`）：模型即使想引也无处可引，而候选**正文仍保留**
        # （`rel-0014`/`rel-0015` 那种「判据假阴性、语料确有答案」的情形不能丢证据）。
        for r in results:
            r["url"] = None
            r["title"] = None
    if not results:
        message = NO_HIT_MESSAGE
    elif ev_level == LEVEL_NONE:
        message = NONE_EVIDENCE_NOTE
    elif ev_level == LEVEL_WEAK:
        message = WEAK_EVIDENCE_NOTE
    else:
        message = "命中 {} 条".format(len(results))
    return _payload(query, code, results, message, ev_level, evidence)


def _payload(query, code, results, message, evidence_level=None, evidence=None):
    # ⚠️ 2026-09-18 第六轮审计一 P2：**两个安全字段必须排在 `results` 之前**。
    # `agent_core._truncate`（`max_len=8000`）对超长返回值是**从尾部**截断的，
    # 而 `results` 是体积最大的字段：一旦超限，`message`（警示语）与 `evidence_level`（档位）
    # 会**先被切掉**，且切开后 JSON **不可解析**（模型拿到一段坏 JSON）。
    # 实测：169 条 golden 查询的最大 payload = **5522** 字符，headroom 仅 **1.45×**。
    # 可达性：`chunker.CHUNK_HARD_MAX * 3 = 3600` 允许表格块那么大 ——
    # 接入含 markdown 表格的财报/半年报后，5 个表格块 ≈ 18000 字符即必然触发。
    # 届时表现是「模型既看不到警示语、也看不到档位」—— 正好打掉 A3a 的两个支点，
    # 且**没有任何指标会红**。
    return json.dumps({
        "query": query,
        "code": code,
        "message": message,
        "evidence_level": evidence_level,
        "evidence": ({"sar": round(evidence.sar, 4), "v1": round(evidence.v1, 3)}
                     if evidence is not None else None),
        "results": results,
    }, ensure_ascii=False, default=str)
