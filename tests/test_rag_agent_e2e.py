# -*- coding: utf-8 -*-
"""`retrieve_docs` × `agent_run` 的**端到端**契约（2026-09-18 新增）。

## 为什么需要这一层

审计 B（2026-09-18）指出：仓库里**没有任何 agent_run 级测试**覆盖「域外查询必须拒答」——
`tests/test_rag_retrieve.py` 只测到「工具返回了什么」，而 `tests/test_agent_core.py` 的三条
mock 测试都不涉及 `retrieve_docs`。于是**「模型拿到警示语后是否真的拒答」零覆盖**。

真实的「模型是否拒答」需要 LLM（属在线评测，不能进单测）。本文件锁住**其中可确定的部分**：
**工具返回的 JSON 到底有没有把警示语带到模型面前**，以及 none 档的候选块是否已剥掉引用凭据。
这是「防幻觉链」上唯一可离线验证的一环 —— 如果警示语根本没进上下文，模型再听话也没用。
"""
import json
from unittest.mock import patch

from services import llm_config
from utils import agent_core, ai_helper
from utils.rag import store as rag_store
from utils.rag.retrieve import retrieve_docs


def _kb_with_one_chunk(tmp_path):
    db = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台公告",
        "url": "https://example.com/a", "published_at": "2026-08-30",
    })
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": "贵州茅台上半年营业收入同比增长百分之十五。", "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids, [[1.0, 0.0]])
    conn.close()
    return db


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def test_agent_run_carries_none_evidence_warning_into_model_context(tmp_path):
    """`none` 档的警示语必须**真的出现在喂给模型的上下文里**（agent_run 级）。

    链条：`retrieve_docs` 返回 `evidence_level=none` + `NONE_EVIDENCE_NOTE`
         → `agent_run` 把该 JSON 作为 tool 结果回填进 messages
         → 模型据此拒答（这一跳需要 LLM，属在线评测）。

    本测试锁住**前三跳**（可离线验证的部分）：若警示语在中途丢了，模型再听话也无从遵守。
    同时验证 none 档候选块**已剥掉 `url`/`title`**（设计 §3.3「无引用 = 不算回答」的新执行者）。
    """
    db = _kb_with_one_chunk(tmp_path)

    # 前提：该查询有字面交集（不被分层硬停清空）但判据不通过 → none + 候选 + 强警示
    payload = json.loads(retrieve_docs("茅台明天的股价是多少", db_path=db, query_vec=[0.6, 0.8]))
    assert payload["evidence_level"] == "none", "前提：该查询应判证据不足档"
    assert payload["results"], "前提：有字面交集 → 不被分层硬停清空"
    assert all(r["url"] is None and r["title"] is None for r in payload["results"]), \
        "前提：none 档候选必须已剥掉引用凭据"

    captured = {}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        captured.setdefault("first", messages)
        captured["last"] = messages
        if not captured.get("called"):
            captured["called"] = True
            return _tool_call("retrieve_docs", {"query": "茅台明天的股价是多少"})
        return {"type": "text", "content": "done"}

    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_llm), \
         patch.object(agent_core, "execute_ai_tool_v2",
                      side_effect=lambda name, args: json.dumps(payload, ensure_ascii=False)):
        agent_core.agent_run("茅台明天的股价是多少")

    blob = json.dumps(captured["last"], ensure_ascii=False)
    assert "未能确认" in blob, \
        "`NONE_EVIDENCE_NOTE` 必须进到模型上下文 —— 否则防幻觉链在工具层就断了"
    assert "\"evidence_level\": \"none\"" in blob or "evidence_level" in blob, \
        "`evidence_level` 必须随结果回填，模型才能据此分档（此前全仓无任何消费者）"
    # 候选正文可以进（真证据不能丢），但引用凭据必须已被剥掉
    assert "https://example.com/a" not in blob, \
        "none 档的 url 不得进入模型上下文（否则模型仍可产出带引用的回答）"
