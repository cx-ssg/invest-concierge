# -*- coding: utf-8 -*-
"""`retrieve_docs`：M1 私域知识层的 Agent 工具实现。

契约：`docs/COVERAGE_DESIGN.md` §3.2 接入层（第 24 个工具）
      §3.3 第 4 条「无引用 = 不算回答：检索为空时明确回『未找到相关公告』，
      不允许模型凭空补」。

返回 **JSON 字符串**（与项目既有 23 个工具的统一契约一致，见 `utils/agent_core.py`
`execute_ai_tool`：工具层一律返回 JSON 字符串）。
"""
import json

from utils.rag import store as rag_store
from utils.rag.hybrid import run_hybrid

# 无命中时的显式提示：把「没有」这件事说清楚，模型才不会拿训练数据硬编
NO_HIT_MESSAGE = "未找到相关公告或研报 —— 请如实告知用户知识库中没有相关内容，不要凭记忆编造"


def retrieve_docs(query, code=None, top_n=5, db_path=None, query_vec=None):
    """检索私域语料（公告 / 研报 / 财报），返回 JSON 字符串。

    - `code`：限定标的（None = 全库检索）
    - `query_vec`：可注入查询向量（单测与批量场景复用，避免重复调用 embedding）
    - 无命中 / 空库 / code 不匹配 → `results=[]` + 明确 `message`
    """
    conn = rag_store.get_conn(db_path)
    try:
        meta, matrix = rag_store.load_index(conn)
    finally:
        conn.close()

    if code:
        keep = [i for i, m in enumerate(meta) if m.get("code") == code]
        if not keep:
            return _payload(query, code, [], NO_HIT_MESSAGE)
        meta = [meta[i] for i in keep]
        matrix = matrix[keep] if matrix is not None else None

    if not meta or matrix is None:
        return _payload(query, code, [], NO_HIT_MESSAGE)

    order, rrf = run_hybrid(query, matrix, meta, k=top_n, query_vec=query_vec)
    results = []
    for rank, idx in enumerate(order, 1):
        m = meta[idx]
        results.append({
            "rank": rank,
            "score": round(float(rrf[idx]), 6),
            "text": m.get("text"),
            "title": m.get("title"),
            "url": m.get("url"),
            "source": m.get("source"),
            "published_at": m.get("published_at"),
            "code": m.get("code"),
            "is_table": bool(m.get("is_table")),
        })
    message = "命中 {} 条".format(len(results)) if results else NO_HIT_MESSAGE
    return _payload(query, code, results, message)


def _payload(query, code, results, message):
    return json.dumps(
        {"query": query, "code": code, "results": results, "message": message},
        ensure_ascii=False, default=str,
    )
