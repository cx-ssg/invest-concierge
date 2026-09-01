# -*- coding: utf-8 -*-
"""Memory 单测：SQLite 写读 / 满 8 轮摘要 / 无 key 降级兜底 / 跨 session 不串 / 最近 3 条注入规则"""
from unittest.mock import patch

from data import database
from utils import agent_memory, ai_helper

from utils.agent_memory import (
    SUMMARY_TRIGGER_ROUNDS,
    MEMORY_CONTEXT_SESSIONS,
    summarize_session,
    maybe_summarize_session,
    build_memory_context,
)


# ==================== 落库写读 ====================


def test_init_db_creates_agent_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t1.db"))
    database.init_db()
    conn = database.get_conn()
    try:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"agent_sessions", "agent_messages"} <= tables


def test_session_and_message_write_read(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t2.db"))
    database.init_db()
    sid = database.create_agent_session(title="我的持仓怎么样？")
    assert sid > 0
    assert database.add_agent_message(sid, "user", "我的持仓怎么样？")
    assert database.add_agent_message(sid, "assistant", "当前持仓 3 只基金。")

    session = database.get_agent_session(sid)
    assert session["title"] == "我的持仓怎么样？"
    assert session["summary"] == ""

    msgs = database.get_agent_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "我的持仓怎么样？"
    assert database.count_agent_messages(sid) == 2
    assert database.count_agent_messages(sid, role="user") == 1


def test_cross_session_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t3.db"))
    database.init_db()
    a = database.create_agent_session(title="A")
    b = database.create_agent_session(title="B")
    database.add_agent_message(a, "user", "A 的问题")
    database.add_agent_message(b, "user", "B 的问题")

    msgs_a = database.get_agent_messages(a)
    msgs_b = database.get_agent_messages(b)
    assert len(msgs_a) == 1 and msgs_a[0]["content"] == "A 的问题"
    assert len(msgs_b) == 1 and msgs_b[0]["content"] == "B 的问题"


# ==================== 摘要机制 ====================


def _seed_rounds(sid, n):
    """写入 n 轮对话（每轮 user+assistant，模拟 agent_run 落库形态）"""
    for i in range(n):
        database.add_agent_message(sid, "user", "第{}个问题".format(i + 1))
        database.add_agent_message(sid, "assistant", "第{}个回答".format(i + 1))


def test_summary_trigger_at_8_rounds_no_key_fallback(tmp_path, monkeypatch):
    """会话满 8 轮生成摘要；无 key 降级为最近消息末尾截取"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t4.db"))
    database.init_db()
    monkeypatch.setattr(agent_memory, "API_KEY", "")

    sid = database.create_agent_session(title="持仓咨询")
    _seed_rounds(sid, 7)
    assert maybe_summarize_session(sid) == 7
    assert database.get_agent_session(sid)["summary"] == ""       # 未满 8 轮不生成

    # 第 8 轮（内容独立，避免与第 1 轮标签相同）
    database.add_agent_message(sid, "user", "第8个问题")
    database.add_agent_message(sid, "assistant", "第8个回答")
    assert maybe_summarize_session(sid) == 8
    summary = database.get_agent_session(sid)["summary"]
    assert summary != ""
    assert "第8个回答" in summary                                 # 末尾截取兜底


def test_summary_uses_llm_when_key_present(tmp_path, monkeypatch):
    """有 key：摘要由 LLM 生成（mock call_llm，确定性断言）"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t5.db"))
    database.init_db()
    monkeypatch.setattr(agent_memory, "API_KEY", "sk-test")

    def fake_call_llm(messages, **kwargs):
        return {"type": "text", "content": "用户偏好长期持有白酒板块"}

    sid = database.create_agent_session(title="持仓咨询")
    _seed_rounds(sid, 8)
    with patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        summary = summarize_session(sid)

    assert summary == "用户偏好长期持有白酒板块"
    assert database.get_agent_session(sid)["summary"] == "用户偏好长期持有白酒板块"


def test_summary_fallback_when_llm_fails(tmp_path, monkeypatch):
    """LLM 失败（返回非 text）→ 兜底末尾截取，不崩"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t6.db"))
    database.init_db()
    monkeypatch.setattr(agent_memory, "API_KEY", "sk-test")

    def fake_call_llm(messages, **kwargs):
        return {"type": "tool_call", "content": None}             # 异常形态

    sid = database.create_agent_session(title="x")
    _seed_rounds(sid, 8)
    with patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        summary = summarize_session(sid)
    assert summary != "" and "第8个回答" in summary


# ==================== 注入规则：最近 3 条摘要 ====================


def test_build_memory_context_recent_3_by_update_desc(tmp_path, monkeypatch):
    """注入规则定死：取最近 3 条已有摘要的会话，按更新倒序（同秒按 id 倒序稳定）"""
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t7.db"))
    database.init_db()
    sids = [database.create_agent_session(title="会话{}".format(i)) for i in range(1, 5)]
    for i, sid in enumerate(sids):
        database.update_agent_session_summary(sid, "摘要{}".format(i + 1))

    ctx = build_memory_context()
    # S4 最新在前，S1 最老被挤出（limit 3）
    assert ctx.index("摘要4") < ctx.index("摘要3") < ctx.index("摘要2")
    assert "摘要1" not in ctx
    assert "最近会话记忆" in ctx
    # limit 参数可调
    assert "摘要1" in build_memory_context(limit=4)


def test_build_memory_context_empty_when_no_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t8.db"))
    database.init_db()
    sid = database.create_agent_session(title="无摘要会话")
    _seed_rounds(sid, 5)                                          # 不触发摘要
    assert build_memory_context() == ""


def test_constants_match_design():
    """设计定值：满 8 轮触发；注入最近 3 条"""
    assert SUMMARY_TRIGGER_ROUNDS == 8
    assert MEMORY_CONTEXT_SESSIONS == 3