# -*- coding: utf-8 -*-
"""G1-2 回归锁：历史回放带「引用来源 + 判官结论」（`agent_messages.meta`）。

红断言 `scripts/verify_g1_replay_sources.py` 只断言**最外层**（历史接口带 `sources` 键），
本文件把整条链路的每一环都钉死（GBK 教训：单测全绿 ≠ 生产路径可达）：

1. 迁移：老库（无 `meta` 列）升级后列存在、**幂等**、老行 `meta` 仍为 NULL；
2. 写入点①/②：`agent_run` 把跨轮检索来源**合并**后挂到助手消息上（正常收口 + 轮数超限收口）；
3. 写入点③：判官结论（`done` 之后才到）**合并**进同一条消息的 meta，且不抹掉 sources；
4. 接口：`session_messages` 透传 sources/judge，**老行不出现这两个键**（不回填、不显示占位）；
5. 红线：`meta` 坏 JSON / 非对象 / 空 ⇒ 一律按「无元数据」处理，绝不抛。
"""
import json
import sqlite3

import pytest

from data import database
from services import agent_service, judge_service
from utils import agent_core, ai_helper
from utils.rag import store as rag_store
from utils.rag.sources import merge_sources

# 与 tests/test_rag_sources.py 同口径：该查询实测判 weak（url/title 不被剥）
WEAK_QUERY = "贵州茅台上半年营业收入同比增长"
WEAK_VEC = [1.0, 0.0]


# ==================== 夹具 ====================


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """隔离的 agent 库（不碰真实 fund_agent.db）。"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "g1.db"))
    database.init_db()
    return database


@pytest.fixture()
def kb(tmp_path):
    """真实形态的临时知识库（1 篇公告 / 3 个块，含真向量）—— 与 test_rag_sources.py 一致。"""
    path = str(tmp_path / "kb.db")
    conn = rag_store.get_conn(path)
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
    return path


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _stub_llm(seen=None):
    """第一轮一次 `retrieve_docs` 工具调用，第二轮终稿。"""
    state = seen if seen is not None else {"round": 0}

    def fake_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        state["round"] += 1
        if state["round"] == 1:
            return _tool_call("retrieve_docs", {"query": WEAK_QUERY, "code": "600519"})
        return {"type": "text", "content": "（打桩终稿）", "usage": None}

    return fake_llm


def _real_retrieve(kb):
    from utils.rag.retrieve import retrieve_docs

    def tool(query, code=None, top_n=5):
        return retrieve_docs(query, code=code, top_n=top_n, db_path=kb, query_vec=WEAK_VEC)

    return tool


def _offline(monkeypatch, kb):
    """离线化：不打真 LLM、不注入持仓/长期记忆（避免网络与真实库污染）。"""
    monkeypatch.setattr(ai_helper, "call_llm", _stub_llm())
    monkeypatch.setattr("utils.rag.retrieve.retrieve_docs", _real_retrieve(kb))
    monkeypatch.setattr(agent_core, "_ai_read_holdings_enabled", lambda: False)
    monkeypatch.setattr(agent_core, "_demo_mode_on", lambda: True)


# ==================== 1. 迁移（幂等 + 老行 NULL） ====================


def _make_legacy_db(path):
    """G1 现场实测的老结构：agent_messages 只有 5 列。"""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("INSERT INTO agent_messages (session_id, role, content) "
                 "VALUES (1, 'user', '老问题')")
    conn.execute("INSERT INTO agent_messages (session_id, role, content) "
                 "VALUES (1, 'assistant', '老回答 [1]')")
    conn.commit()
    conn.close()


def test_migration_adds_meta_column_and_is_idempotent(tmp_path, monkeypatch):
    """老库升级：补 `meta` 列；**跑两次不报错**；老行 meta 仍为 NULL（不回填）。"""
    path = str(tmp_path / "legacy.db")
    _make_legacy_db(path)
    monkeypatch.setattr(database, "DB_FILE", path)

    database.init_db()
    database.init_db()  # 幂等：第二次 PRAGMA 已含该列 ⇒ 空操作

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_messages)")}
        assert "meta" in cols, "迁移必须给老库补上 meta 列"
        raw = [r["meta"] for r in conn.execute(
            "SELECT meta FROM agent_messages ORDER BY id")]
    finally:
        conn.close()
    assert raw == [None, None], "产品决策：老会话不回填 ⇒ 老行 meta 必须保持 NULL"

    msgs = database.get_agent_messages(1)
    assert [m["meta"] for m in msgs] == [{}, {}], "NULL ⇒ 读取侧解析为 {}（不补 sources）"


def test_meta_helpers_never_raise_on_garbage():
    """坏 JSON / 非对象 / 空 ⇒ 一律「无元数据」，绝不抛（旁路不得打断对话）。"""
    assert database.parse_message_meta(None) == {}
    assert database.parse_message_meta("not-json") == {}
    assert database.parse_message_meta("[1,2]") == {}
    assert database.parse_message_meta(123) == {}
    assert database.parse_message_meta('{"sources": []}') == {"sources": []}
    for bad in (None, {}, "", "  ", "not-json", object()):
        assert database._meta_to_json(bad) is None


# ==================== 2. DB 读写 + meta 合并 ====================


def test_message_meta_roundtrip(db):
    sid = database.create_agent_session(title="G1")
    mid = database.add_agent_message(sid, "assistant", "回答 [1]",
                                     meta={"sources": [{"chunk_id": 7, "title": "T"}]})
    assert mid > 0, "写入必须返回新消息 id（判官回填要靠它）"
    plain = database.add_agent_message(sid, "user", "老式写入（无 meta）")

    msgs = database.get_agent_messages(sid)
    assert msgs[0]["meta"]["sources"][0]["chunk_id"] == 7
    assert msgs[1]["meta"] == {}, "无 meta ⇒ {}（与老行同形）"
    assert plain > mid, "id 单调递增"


def test_merge_agent_message_meta_preserves_sources(db):
    """判官回填是 read-modify-write：**不得**把已落库的 sources 抹掉。"""
    sid = database.create_agent_session(title="G1")
    mid = database.add_agent_message(sid, "assistant", "回答 [1]",
                                     meta={"sources": [{"chunk_id": 7}]})

    assert database.merge_agent_message_meta(mid, {"judge": {"7": {"verdict": "relevant"}}})
    meta = database.get_agent_messages(sid)[0]["meta"]
    assert meta["sources"] == [{"chunk_id": 7}], "回填判官不得覆盖 sources"
    assert meta["judge"]["7"]["verdict"] == "relevant"

    # 幂等/可重复：再次合并同键 = 覆盖，不产生第二份
    assert database.merge_agent_message_meta(mid, {"judge": {"7": {"verdict": "uncertain"}}})
    meta = database.get_agent_messages(sid)[0]["meta"]
    assert meta["judge"] == {"7": {"verdict": "uncertain"}}
    assert len(meta["judge"]) == 1


# ==================== 3. 接口：透传 + 老行不出现键 ====================


def test_session_messages_legacy_rows_keep_old_shape(db):
    """V1 兼容红线：老会话（meta NULL）回放体**仍是** `{role, content}` —— 不显示来源区。"""
    sid = database.create_agent_session(title="老会话")
    database.add_agent_message(sid, "user", "老问题")
    database.add_agent_message(sid, "assistant", "老回答，正文带 [1] 引用编号")

    out = agent_service.session_messages(sid)
    assert out == [{"role": "user", "content": "老问题"},
                   {"role": "assistant", "content": "老回答，正文带 [1] 引用编号"}]
    assert not any("sources" in m or "judge" in m for m in out), \
        "无来源时不得出现 sources/judge 键（前端据此不渲染来源区，而非渲染占位）"


def test_session_messages_passes_through_sources_and_judge(db):
    sid = database.create_agent_session(title="新会话")
    src = {"rank": 1, "chunk_id": 24, "title": "公告", "url": "https://example.com/a",
           "source": "notice", "published_at": "2026-07-18", "code": "600519",
           "is_table": False}
    database.add_agent_message(sid, "user", "贵州茅台最近公告说了什么")
    database.add_agent_message(
        sid, "assistant", "回答 [1]",
        meta={"sources": [src], "judge": {"24": {"chunk_id": 24, "verdict": "relevant",
                                                 "quote": "q", "quote_rejected": False}}})

    out = agent_service.session_messages(sid)
    assistant = out[-1]
    assert assistant["sources"] == [src], "历史回放必须逐字透传来源（前端引用编号靠它）"
    assert assistant["judge"]["24"]["verdict"] == "relevant"
    assert "text" not in assistant["sources"][0], "事件体契约不变：来源不含正文"


def test_session_messages_drops_empty_meta_keys(db):
    """`meta` 有键但值为空 ⇒ 不输出该键（协议纪律：没有就不出现）。"""
    sid = database.create_agent_session(title="空 meta")
    database.add_agent_message(sid, "assistant", "回答", meta={"sources": [], "judge": {}})
    out = agent_service.session_messages(sid)
    assert list(out[0].keys()) == ["role", "content"]


# ==================== 4. 写入点①/②：agent_run 合并来源并落库 ====================


def test_agent_run_persists_merged_sources_to_assistant_message(kb, db, monkeypatch):
    """真实 `agent_run`（真 retrieve_docs → execute_ai_tool_v2 全链路）落 sources。"""
    _offline(monkeypatch, kb)

    res = agent_core.agent_run(WEAK_QUERY, memory=True, model="fake-model")

    sid = res["session_id"]
    msgs = database.get_agent_messages(sid)
    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert assistants, "必须落库助手消息"
    meta = assistants[-1]["meta"]
    assert meta.get("sources"), "助手消息 meta 必须带检索来源（G1 缺陷的根因修复点）"
    assert meta["sources"][0]["url"] == "https://example.com/maotai-h1"
    assert res["assistant_message_id"] == assistants[-1]["id"], \
        "返回的 id 必须指向**那条**助手消息（判官回填的唯一句柄）"

    out = agent_service.session_messages(sid)
    assert "sources" in out[-1], "历史接口必须能看到它"


def test_merge_sources_dedupes_by_chunk_id_like_frontend():
    """跨轮合并口径必须与前端 `useAgentRun.mergeSources` 一致（否则 [n] 序号错位）。"""
    a = [{"chunk_id": 1, "title": "A"}, {"chunk_id": 2, "title": "B"}]
    b = [{"chunk_id": 2, "title": "B2"}, {"chunk_id": 3, "title": "C"}]
    assert [s["chunk_id"] for s in merge_sources(a, b)] == [1, 2, 3]
    assert merge_sources(a, b)[1]["title"] == "B", "去重保留**首次**出现"
    assert merge_sources(a, []) is not a
    assert merge_sources(None, b) == b, "畸形输入不抛"
    assert merge_sources(a, "boom") == a


# ==================== 5. 写入点③：判官结论（done 之后）回填同一 meta ====================


def test_stream_events_persists_judge_into_same_message_meta(kb, db, monkeypatch):
    """SSE 真链路：`done` 之后的判官结论必须落进**同一条**助手消息的 meta。"""
    _offline(monkeypatch, kb)

    def fake_judge_candidates(question, candidates, *, llm_fn=None,
                              per_chunk_chars=None, timeout_s=None):
        return {
            "level": "uncertain",
            "items": [{"chunk_id": candidates[0]["chunk_id"], "verdict": "relevant",
                       "quote": "贵州茅台上半年营业收入同比增长百分之十五",
                       "quote_rejected": False}],
            "checked": True, "latency_ms": 1, "reason": "",
        }

    monkeypatch.setattr(judge_service, "judge_candidates", fake_judge_candidates)

    events = list(agent_service.stream_events(WEAK_QUERY))
    types = [e.get("type") for e in events]
    assert "done" in types and "evidence_judged" in types, \
        "前置：weak 档必须触发判官（types=%s）" % types
    judged = [e for e in events if e.get("type") == "evidence_judged"]
    assert judged[0]["checked"] and judged[0]["items"], "前置：判官必须给出结论"
    cid = str(judged[0]["items"][0]["chunk_id"])

    done = [e for e in events if e.get("type") == "done"][0]
    sid = done["session_id"]
    msgs = database.get_agent_messages(sid)
    assistant = [m for m in msgs if m["role"] == "assistant"][-1]
    meta = assistant["meta"]
    assert meta["judge"][cid]["verdict"] == "relevant", "判官结论必须落库（G1-2 决策②）"
    assert meta.get("sources"), "回填判官**不得**抹掉同一列的 sources"

    out = agent_service.session_messages(sid)
    assert "judge" in out[-1] and "sources" in out[-1], "历史接口必须两者都带"


def test_judge_items_map_skips_unchecked_and_missing_chunk_id():
    """`checked=false`（超时/无 LLM）⇒ 不落任何键（沉默好过编造"未确认"）。"""
    assert judge_service.judge_items_map([]) == {}
    assert judge_service.judge_items_map([{"items": []}]) == {}
    assert judge_service.judge_items_map([{"items": [{"verdict": "relevant"}]}]) == {}
    got = judge_service.judge_items_map([
        {"items": [{"chunk_id": 3, "verdict": "irrelevant", "quote": ""}]},
        {"items": [{"chunk_id": 3, "verdict": "uncertain", "quote_rejected": True}]},
    ])
    assert got == {"3": {"chunk_id": 3, "verdict": "uncertain", "quote": "",
                         "quote_rejected": True}}, "同 chunk 取其后者；字段集固定"
    assert judge_service.judge_items_map(None) == {}
