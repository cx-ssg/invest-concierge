# -*- coding: utf-8 -*-
"""A2 引用回跳渲染 —— 后端契约锁（任务书 `task-A2.md` §3/§5）。

链条：`retrieve_docs` 返回 `results`（含 chunk_id/title/url/published_at）
    → `agent_core.agent_run` 的 `_progress_structured("tool_end", ...)` 附 `sources` / `evidence_level`
    → `services.agent_service.stream_events` 透传（SSE）
    → 前端 `MarkdownContent` 把正文 `[n]` 渲染为可点上标。

⚠️ **本仓最痛的历史教训：「测试全绿」≠「生产路径可达」**（连续三次同族）。
因此本文件的断言**全部经过 `agent_run` 真实的 `_progress_structured` 路径**，
而不是只测 `extract_sources` 这个纯函数本身（纯函数测试在 5 条契约锁里只占 2 条）。

设计依据：`docs/COVERAGE_DESIGN.md` §3.2 生成层「回答必须带引用编号 [1][2] + 来源 URL + 日期」；
§3.3 第 4 条「无引用 = 不算回答」。
"""
import json
from unittest.mock import patch

import pytest

from services import agent_service
from utils import agent_core, ai_helper
from utils.rag import store as rag_store
from utils.rag.retrieve import retrieve_docs
from utils.rag.sources import extract_sources

# 事件体只允许出现的字段（⚠️ 刻意不含 `text`：正文已在 tool_trace 里，事件体必须小）
EXPECTED_FIELDS = {"rank", "chunk_id", "title", "url", "source", "published_at", "code", "is_table"}

# 弱档查询（实测 tmp 语料判 weak，url/title 不被剥）+ 强向量
WEAK_QUERY = "贵州茅台上半年营业收入同比增长"
WEAK_VEC = [1.0, 0.0]
# 弃权档查询（实测判 none，url/title 被 retrieve.py 剥掉）+ 向量
NONE_QUERY = "茅台明天的股价是多少"
NONE_VEC = [0.6, 0.8]


@pytest.fixture()
def kb(tmp_path):
    """真实形态的临时知识库：1 篇公告 / 3 个块（含真向量）。

    语料与 `scripts/rag_ingest.py` 落库后的真实块形态一致（公告标题 + 正文 + 真 URL + 日期）。
    """
    db = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "贵州茅台2026年半年度报告",
        "url": "https://example.com/maotai-h1", "published_at": "2026-08-15",
    })
    ids = rag_store.insert_chunks(conn, doc_id, [
        {"seq": 0, "text": "贵州茅台上半年营业收入同比增长百分之十五，净利润增速略低于营收增速。",
         "is_table": False},
        {"seq": 1, "text": "公司表示直销渠道占比继续提升，i茅台平台贡献显著。", "is_table": False},
        {"seq": 2, "text": "比亚迪新能源汽车七月销量创新高，海外出口同比增长明显。", "is_table": False},
    ])
    rag_store.save_embeddings(conn, ids, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    conn.close()
    return db


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _tool_json(output):
    """解开 `execute_ai_tool_v2` 的**双层编码**。

    ⚠️ 生产实况（2026-10-02 实测）：`execute_ai_tool_v2` 对**返回字符串的工具**会再
    `json.dumps`（`_truncate` 后统一序列化）→ `agent_run` 里 `output` 是
    `"\"{\\\"query\\\": ...}\""` 这种双层字符串。`retrieve_docs` 正是这一类 ⇒
    `extract_sources` 必须能处理（否则事件里永远没有 sources —— 这正是本仓
    「单测全绿 ≠ 生产路径可达」的最新一例）。
    """
    data = json.loads(output)
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _drive_agent_run(monkeypatch, tool_name, args, tool_impl, task="贵州茅台最近公告说了什么"):
    """跑一次**真实** agent_run（structured_progress=True），返回 (events, result, tool_output)。

    - LLM 打桩：第一轮返回一次工具调用，第二轮返回终稿文本（`call_llm` 由 agent_run 晚绑定调用）
    - `tool_impl`：被测工具的**真实实现包装**（`utils.rag.retrieve.retrieve_docs` 等），
      经 `execute_ai_tool_v2` 的 resolve → _truncate → tool_output_error 全链路
    """
    seen = {"round": 0}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen["round"] += 1
        if seen["round"] == 1:
            assert tools, "agent_run 必须把工具 schema 传给模型"
            return _tool_call(tool_name, args)
        return {"type": "text", "content": "（打桩终稿）", "usage": None}

    monkeypatch.setattr(ai_helper, "call_llm", fake_llm)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", tool_impl)

    events = []
    res = agent_core.agent_run(
        task, memory=False, model="fake-model",
        on_progress=lambda s, d: events.append((s, d)),
        structured_progress=True,
    )
    trace = res.get("tool_trace") or []
    assert trace, "agent_run 必须记录 tool_trace（正文来源）"
    return events, res, trace[0]["output"]


def _tool_ends(events):
    return [d for s, d in events if s == "tool_end"]


# ==================== 契约锁 1：真实形态（纯函数层） ====================


def test_extract_sources_from_real_retrieve_output(kb):
    """真实 `retrieve_docs` 返回 → `extract_sources` 只搬运「引用所需最小字段集」。

    真实形态不 monkeypatch 被测对象：这里直接调**真工具**（真 kb.db、真判据、真 JSON）。
    """
    raw = retrieve_docs(WEAK_QUERY, db_path=kb, query_vec=WEAK_VEC)
    parsed = json.loads(raw)
    assert parsed["results"], "前置：该查询必须命中（语料里确有答案）"
    assert parsed["evidence_level"] == "weak", "前置：该查询判弱档（url/title 不被剥）"

    srcs, level = extract_sources(raw)

    assert level == parsed["evidence_level"]
    assert len(srcs) == len(parsed["results"])
    for got, exp in zip(srcs, parsed["results"]):
        assert set(got) == EXPECTED_FIELDS, "事件体字段集必须精确（多带 text 会撑大事件）"
        assert "text" not in got, "正文不得进事件体（已在 tool_trace 里）"
        assert got["chunk_id"] == exp["chunk_id"]
        assert got["title"] == exp["title"]
        assert got["url"] == exp["url"]
        assert got["published_at"] == exp["published_at"]
        assert got["source"] == exp["source"]
        assert got["code"] == exp["code"]
    assert srcs[0]["url"] == "https://example.com/maotai-h1", "弱档必须带得出真实外链"
    assert srcs[0]["chunk_id"] is not None and srcs[0]["published_at"] == "2026-08-15"


# ==================== 契约锁 2：none 档脱敏（不得在事件层补回来） ====================


def test_extract_sources_keeps_none_level_masked_credentials(kb):
    """`none` 档的 `url`/`title` 已被 `retrieve.py:101-108` 剥掉 ⇒ 事件里**也不得**出现非空值。

    事件层只是搬运工：**不许**在这里把凭据"补回来"，否则设计 §3.3 第 4 条
    「无引用 = 不算回答」的机器强制会在事件层被悄悄撤销。
    """
    raw = retrieve_docs(NONE_QUERY, db_path=kb, query_vec=NONE_VEC)
    parsed = json.loads(raw)
    assert parsed["evidence_level"] == "none", "前置：该查询判弃权档"
    assert parsed["results"], "前置：none 档仍返回候选（2026-09-17 起不再物理清空）"
    assert all(r["url"] is None and r["title"] is None for r in parsed["results"]), \
        "前置：工具层已剥凭据"

    srcs, level = extract_sources(raw)
    assert level == "none"
    assert srcs, "候选块仍要进事件（正文在 tool_trace，来源卡显示未确认）"
    assert all(s["url"] is None and s["title"] is None for s in srcs), \
        "事件层不得把 none 档的引用凭据补回来"


def test_extract_sources_handles_double_encoded_production_shape(kb):
    """生产实况：`agent_run` 里的 `output` 是**双层编码**的 JSON 字符串。

    `execute_ai_tool_v2` 对「返回字符串的工具」会再 `json.dumps` 一次
    （`_truncate` 之后统一序列化）—— `retrieve_docs` 正是这一类。
    若 `extract_sources` 只解一层，拿到的是 `str` → 判为「无 results」
    → **事件里永远不会有 sources**，而纯函数单测（喂单层 JSON）却全绿。
    这条锁专门防这个陷阱：它喂的是**真工具返回 + 真再编码**。
    """
    raw = retrieve_docs(WEAK_QUERY, db_path=kb, query_vec=WEAK_VEC)
    double = json.dumps(raw, ensure_ascii=False)      # 复刻 execute_ai_tool_v2 的再编码
    assert isinstance(json.loads(double), str), "前置：确实是双层编码"

    srcs, level = extract_sources(double)
    assert srcs, "双层编码必须能解开（否则 agent_run 事件里永远没有 sources）"
    assert level == "weak"
    assert len(srcs) == len(json.loads(raw)["results"])
    assert srcs[0]["url"] == "https://example.com/maotai-h1"


# ==================== 契约锁 3：畸形输入（绝不抛异常） ====================


@pytest.mark.parametrize("bad", [
    "",
    "{not json",
    '{"results": null}',
    '{"results": []}',
    '{"results": "abc"}',
    '{"results": [null, 3]}',
    '{"results": [{"chunk_id": 1}',          # 被 _truncate 从尾部截断的坏 JSON
    "null",
    "[]",
    '"just a string"',
])
def test_extract_sources_malformed_never_raises(bad):
    """畸形 / 空串 / 无 results ⇒ `([], None)`，**绝不抛异常**（事件必须照发）。"""
    assert extract_sources(bad) == ([], None)


def test_extract_sources_non_string_input_never_raises():
    """非 str 输入（工具异常路径可能给出 None/dict）也不得炸。"""
    assert extract_sources(None) == ([], None)
    assert extract_sources({"results": [{"chunk_id": 1}]}) == ([], None)


# ==================== 契约锁 4：可达性（真实 _progress_structured 路径） ====================


def test_agent_run_tool_end_carries_real_sources(monkeypatch, kb):
    """**可达性锁**：`tool_end` 事件必须真的带上 `sources`（不是只测纯函数）。

    这是本仓历史上反复踩的坑：单测全绿但生产事件里没有 `sources` 键。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    events, _res, output = _drive_agent_run(
        monkeypatch, "retrieve_docs", {"query": WEAK_QUERY, "code": "600519"}, real_tool)

    ends = _tool_ends(events)
    assert len(ends) == 1, "必须恰好一次 tool_end"
    payload = ends[0]
    assert payload["name"] == "retrieve_docs"
    assert payload["ok"] is True
    assert "elapsed_ms" in payload
    assert "sources" in payload, "❌ tool_end 没有 sources 键 ⇒ 前端永远看不到来源"
    assert payload["evidence_level"] == "weak"

    tool_json = _tool_json(output)
    assert len(payload["sources"]) == len(tool_json["results"])
    top = payload["sources"][0]
    assert top["chunk_id"] == tool_json["results"][0]["chunk_id"]
    assert top["url"] == "https://example.com/maotai-h1"
    assert top["title"] == "贵州茅台2026年半年度报告"
    assert top["published_at"] == "2026-08-15"
    assert set(top) == EXPECTED_FIELDS


def test_agent_run_tool_end_none_level_no_credential_leak(monkeypatch, kb):
    """none 档走完整 agent_run 链路时，事件 sources 里 url/title 必须仍为空。"""
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=NONE_VEC)

    events, _res, output = _drive_agent_run(
        monkeypatch, "retrieve_docs", {"query": NONE_QUERY}, real_tool)

    payload = _tool_ends(events)[0]
    assert payload["evidence_level"] == "none"
    assert payload["sources"], "候选仍要进事件（供前端显示「未确认凭据」）"
    assert all(s["url"] is None and s["title"] is None for s in payload["sources"]), \
        "事件层补回凭据 = 撤销 none 档的机器强制"
    assert _tool_json(output)["evidence_level"] == "none"


def test_agent_run_malformed_tool_output_still_emits_tool_end(monkeypatch):
    """工具返回畸形串（"" / "{not json"）时：事件照发、ok 仍判成功、**不出现 sources 键**。"""
    for bad in ("", "{not json"):
        def broken(query, code=None, top_n=5, _bad=bad):
            return _bad

        events, _res, _out = _drive_agent_run(
            monkeypatch, "retrieve_docs", {"query": "x"}, broken)
        payload = _tool_ends(events)[0]
        assert payload["name"] == "retrieve_docs"
        assert payload["ok"] is True, "坏 JSON 不属于「工具执行失败」（execute 层已判过）"
        assert "sources" not in payload, "无来源 ⇒ 不得出现 sources 键"
        assert payload.get("evidence_level") is None


# ==================== 契约锁 5：协议纯净（非 retrieve_docs 不得带 sources） ====================


def test_non_retrieve_tool_has_no_sources_key(monkeypatch):
    """`get_stock_info`（非检索工具）的 tool_end **没有** `sources` / `evidence_level` 键。"""
    def fake_stock_info(stock_code):
        return json.dumps({"code": stock_code, "name": "贵州茅台", "price": 1500.0},
                          ensure_ascii=False)

    monkeypatch.setattr("data.stock_api.get_stock_info", fake_stock_info)
    events, _res, output = _drive_agent_run(
        monkeypatch, "get_stock_info", {"stock_code": "600519"}, fake_stock_info)

    payload = _tool_ends(events)[0]
    assert payload["name"] == "get_stock_info"
    assert payload["ok"] is True
    assert "sources" not in payload, "非检索工具污染协议：不得出现 sources 键"
    assert "evidence_level" not in payload
    assert _tool_json(output)["code"] == "600519"


# ==================== 契约锁 6：SSE 桥必须把 sources 透传到前端 ====================


def test_sse_stream_events_forwards_sources_to_frontend(monkeypatch, kb):
    """`services.agent_service.stream_events` 是前端**唯一**的消费入口 —— 它必须透传 sources。

    ⚠️ 事件字典由 `_on_progress` 用 `{"type": stage, **detail}` 拼装，
    `tool_end` 的 detail 就是 `_payload` —— 若 payload 里没有 sources，前端拿到的就一定没有。
    """
    def real_tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    seen = {"round": 0}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen["round"] += 1
        if seen["round"] == 1:
            return _tool_call("retrieve_docs", {"query": WEAK_QUERY, "code": "600519"})
        return {"type": "text", "content": "（打桩终稿）", "usage": None}

    from utils import agent_memory

    monkeypatch.setattr(ai_helper, "call_llm", fake_llm)
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", real_tool)
    # 测试隔离：不落库、不注入持仓/长期记忆（它们会打网络；且污染真实 agent_sessions）
    monkeypatch.setattr(agent_memory, "ensure_session", lambda *a, **k: 1)
    monkeypatch.setattr(agent_memory, "record_message", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "maybe_summarize_session", lambda *a, **k: None)
    monkeypatch.setattr(agent_memory, "get_agent_messages", lambda *a, **k: [])
    monkeypatch.setattr(agent_core, "_ai_read_holdings_enabled", lambda: False)
    monkeypatch.setattr(agent_core, "_demo_mode_on", lambda: True)

    events = list(agent_service.stream_events("贵州茅台最近公告说了什么"))

    types = [e.get("type") for e in events]
    assert types[0] == "status" and types[-1] == "done", "SSE 契约首尾不变（零回归）"
    ends = [e for e in events if e.get("type") == "tool_end"]
    assert len(ends) == 1
    assert "sources" in ends[0], "SSE 桥丢了 sources ⇒ 前端引用回跳整条链不可达"
    assert ends[0]["sources"][0]["url"] == "https://example.com/maotai-h1"
    assert ends[0]["sources"][0]["chunk_id"] is not None
    assert ends[0]["evidence_level"] == "weak"
    # 事件体必须小：正文不放事件（前端另有 tool_trace 通道）
    assert all("text" not in s for s in ends[0]["sources"])
