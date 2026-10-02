# -*- coding: utf-8 -*-
"""A-R1 **F2** 回归锁：多次检索时「正文 `[n]` ↔ 来源卡编号」的**全局**对应关系。

## 缺陷（两路审计独立复现，`report-A2` 的 F1/F2 同源）

- 生成侧 `CITATION_NOTE` 按**每次调用**声明编号口径（「第 1 条 = `[1]`」，
  `utils/rag/messages.py`），`retrieve_docs` 每次调用都从 `[1]` 起；
- 渲染侧 `frontend/src/features/agent/useAgentRun.ts::mergeSources` 把一次运行里
  **多次** `retrieve_docs` 的 sources 按 `chunk_id` 去重、**首次出现顺序全局合并**，
  卡片编号 = 合并后下标 +1（`ChatArea.tsx` 的 `SourceList`）。

⇒ 一次运行发生第二次检索时，模型的 `[1]` 指到**第二次**检索的第一条，而卡片 `[1]`
是**第一次**的第一条 —— 引用归属错误（金融场景里"点开引用看到的是另一份公告"）。

## 本文件的断言纪律

1. **真链路**：走真 `agent_run` + 真 `execute_ai_tool_v2` + 真 `retrieve_docs`（真 kb.db、
   真判据），只桩 LLM —— 与本仓「单测全绿 ≠ 生产路径可达」的教训对齐。
2. **双/三次 `tool_end`**：桩三次检索（第 3 次与第 1 次返回同一批 chunk ⇒ 去重分支）。
3. **对应关系自证**：把 `tool_end.sources` 按**前端的合并规则**（去重、首次出现顺序）
   重算一遍卡片编号，要求注入侧文案里的每一个编号都等于该 chunk 的**卡片编号**。
"""
import json

import pytest

from utils.rag import store as rag_store
from utils.rag import messages as rag_messages
from utils.rag.citation_scope import CitationScope
from utils.rag.messages import citation_note
from utils.rag.retrieve import retrieve_docs

VEC_A = [1.0, 0.0]
VEC_B = [0.0, 1.0]
QUERY_A = "贵州茅台上半年营业收入同比增长"
QUERY_B = "平安银行三季度营业收入同比增长"


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _double_decode(output):
    """解开 `execute_ai_tool_v2` 对字符串工具的再编码（生产实况）。"""
    data = json.loads(output)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _merge_like_frontend(ends):
    """复刻 `useAgentRun.mergeSources`：按 chunk_id 去重、首次出现顺序合并。

    返回 `card_no`：`{chunk_id: 卡片编号(= 合并后下标+1)}`。
    """
    seen, card_no, next_no = set(), {}, 1
    for e in ends:
        for s in (e.get("sources") or []):
            cid = s.get("chunk_id")
            if cid is not None:
                if cid in seen:
                    continue
                seen.add(cid)
            card_no[cid] = next_no
            next_no += 1
    return card_no


@pytest.fixture()
def kb2(tmp_path):
    """两份标的、三段语料（含真向量）：`QUERY_A` 打 A 标的两块，`QUERY_B` 打 B 标的一块。"""
    db = str(tmp_path / "kb2.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_a = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台2026年半年度报告",
        "url": "https://example.com/maotai-h1", "published_at": "2026-08-15",
    })
    ids_a = rag_store.insert_chunks(conn, doc_a, [
        {"seq": 0, "text": "贵州茅台上半年营业收入同比增长百分之十五，净利润增速略低于营收增速。",
         "is_table": False},
        {"seq": 1, "text": "贵州茅台表示直销渠道占比继续提升，i茅台平台贡献显著。", "is_table": False},
    ])
    doc_b = rag_store.upsert_document(conn, {
        "code": "000001", "source": "notice", "title": "平安银行2026年半年度报告",
        "url": "https://example.com/pay-h1", "published_at": "2026-08-20",
    })
    ids_b = rag_store.insert_chunks(conn, doc_b, [
        {"seq": 0, "text": "平安银行三季度营业收入同比增长百分之九，净利润增速高于营收增速。",
         "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids_a + ids_b, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    conn.close()
    return db


# ==================== ① 作用域本身（纯函数层） ====================


def test_scope_assigns_globally_increasing_numbers_with_dedup():
    """`CitationScope` 必须与前端 `mergeSources` **同构**：去重、首次出现顺序、全局递增。"""
    scope = CitationScope()
    assert scope.assign([11, 12]) == (0, [1, 2]), "第 1 次检索：前面 0 条，编号 1、2"
    assert scope.assign([12, 13]) == (2, [2, 3]), "12 是重复 ⇒ 沿用 [2]；13 取全局下一个 [3]"
    assert scope.assign([14]) == (3, [4]), "前面已返回 3 条 ⇒ 本次从 [4] 起"
    assert scope.count == 4


def test_citation_note_declares_global_start():
    """第 k 次检索的规范文案必须声明「本次编号从 [N+1] 起（前面已返回 N 条）」。"""
    note = citation_note(2, [3])
    assert "本次编号从 [3] 起" in note, "必须声明全局起点（N=2 ⇒ [3]）"
    assert "前面已返回 2 条" in note, "必须声明前面已返回的条数"
    assert "第1条=[3]" in note, "必须给出本次逐条编号，模型才能对上卡片"

    first = citation_note(0, [1, 2])
    assert first == rag_messages.CITATION_NOTE, \
        "第 1 次检索必须逐字沿用 A2-R1 的既有文案（零回归、单一事实源）"


def test_citation_note_maps_duplicates_to_original_numbers():
    """重复 chunk 必须沿用**原编号**（前端去重后卡片编号不会变），不能重新从 1 排。"""
    note = citation_note(2, [1, 3])
    assert "第1条=[1]" in note and "第2条=[3]" in note, "重复项要指回原编号 [1]，新项 [3]"


# ==================== ② 真链路：三次检索 / 三次 tool_end ====================


def _run_three_retrievals(monkeypatch, kb):
    """跑一次真 `agent_run`：三次 `retrieve_docs`（A / B / A 重复），返回 (events, result)。"""
    from utils import agent_core, ai_helper

    calls = [QUERY_A, QUERY_B, QUERY_A]
    seen = {"round": 0}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen["round"] += 1
        if seen["round"] <= len(calls):
            q = calls[seen["round"] - 1]
            return _tool_call("retrieve_docs", {"query": q}, call_id="c%d" % seen["round"])
        return {"type": "text", "content": "（打桩终稿）", "usage": None}

    real_retrieve = retrieve_docs

    def tool_with_vec(query, code=None, top_n=5):
        vec = VEC_B if "平安银行" in query else VEC_A
        return real_retrieve(query, code=code, top_n=top_n, db_path=kb, query_vec=vec)

    monkeypatch.setattr(ai_helper, "call_llm", fake_llm)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", tool_with_vec)

    events = []
    res = agent_core.agent_run(
        "把茅台和平安的公告都看一遍", memory=False, model="fake-model",
        on_progress=lambda s, d: events.append((s, d)), structured_progress=True,
    )
    return events, res


def test_two_retrievals_number_against_merged_source_cards(monkeypatch, kb2):
    """**核心锁**：后端注入的编号必须等于前端合并去重后的卡片编号（`[n]` ↔ 卡片）。"""
    events, res = _run_three_retrievals(monkeypatch, kb2)
    ends = [d for s, d in events if s == "tool_end"]
    assert len(ends) == 3, "必须恰好三次 tool_end（三次检索）"
    trace = res.get("tool_trace") or []
    assert len(trace) == 3, "tool_trace 必须逐次留档"

    card_no = _merge_like_frontend(ends)
    assert len(card_no) >= 3, "两次不同标的的检索必须带来 ≥3 条去重后的来源卡"

    for i, end in enumerate(ends):
        sources = end.get("sources") or []
        assert sources, "第 {} 次检索必须有 sources".format(i + 1)
        payload = _double_decode(trace[i]["output"])
        message = payload["message"]
        nums = [card_no[s["chunk_id"]] for s in sources]
        if i == 0:
            assert rag_messages.CITATION_NOTE in message, "第 1 次检索文案不得变（零回归）"
            assert nums == list(range(1, len(nums) + 1)), "第 1 次检索必然从 [1] 起"
        else:
            assert "第1条=[{}]".format(nums[0]) in message, \
                "第 {} 次检索的编号必须接续全局编号（实得文案：{}）".format(i + 1, message[-160:])
            for j, n in enumerate(nums):
                assert "第{}条=[{}]".format(j + 1, n) in message, \
                    "第 {} 次第 {} 条的全局编号应为 [{}]".format(i + 1, j + 1, n)

    # 第二次检索的第一条**不得**是 [1]（那正是缺陷形态：模型以为可以重新从 1 起）
    second = _double_decode(trace[1]["output"])["message"]
    assert "第1条=[1]" not in second, \
        "第二次检索若仍从 [1] 编号 ⇒ 正文 [1] 会跳到第一次检索的来源卡（F2 缺陷）"

    # 第三次检索与第一次同批 chunk ⇒ 必须沿用原编号（去重后卡片不变）
    third = _double_decode(trace[2]["output"])["message"]
    nums3 = [card_no[s["chunk_id"]] for s in ends[2]["sources"]]
    for j, n in enumerate(nums3):
        assert "第{}条=[{}]".format(j + 1, n) in third, "重复来源必须沿用原编号"


def test_scope_resets_after_agent_run(monkeypatch, kb2):
    """作用域必须随运行结束复位（try/finally）—— 否则后续**直接**调用会被错误偏移。"""
    _run_three_retrievals(monkeypatch, kb2)
    out = json.loads(retrieve_docs(QUERY_A, db_path=kb2, query_vec=VEC_A))
    assert rag_messages.CITATION_NOTE in out["message"], \
        "agent_run 结束后作用域未复位 ⇒ 直接调 retrieve_docs 的文案被错误偏移"
    assert "本次编号从" not in out["message"], "复位后应回到 A2-R1 的原始文案"


def test_scope_parity_with_frontend_merge_order():
    """作用域与前端 `mergeSources` 的同构性：同一序列两种实现必须给出同一张编号表。"""
    scope = CitationScope()
    calls = [[11, 12], [12, 13], [13, 11, 14]]
    got = {cid: n for call in calls for cid, n in zip(call, scope.assign(call)[1])}
    expected = {}
    for call in calls:
        for cid in call:
            if cid not in expected:
                expected[cid] = len(expected) + 1
    assert got == expected, "注入侧编号必须与前端合并去重后的卡片编号逐条一致"
