# -*- coding: utf-8 -*-
"""P0-5 回归锁：`agent_messages` 瘦身 —— tool 消息只存摘要，不落全文

问题：`record_message(session_id, "tool", output)` 把工具返回的**完整 JSON** 落库。
行情/K 线/财报类返回可达数十 KB，而它对「越用越懂」（记住用户偏好/关注点）毫无价值，
却会让 `agent_messages` 无界膨胀，并污染 `summarize_session` 的 transcript。

约定（P0-5）：
- `role="tool"` → 超过 `TOOL_MESSAGE_LIMIT` 只存**前 N 字符 + 截断标记（含原始长度）**
- `user` / `assistant` 消息**不受影响**（对话内容本身是记忆的原料）
"""
import json

import pytest

from data import database
from utils import agent_memory


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "p05.db"))
    database.init_db()
    return database


def _messages(sid):
    return database.get_agent_messages(sid)


def _last(sid):
    return _messages(sid)[-1]


def test_tool_message_is_truncated(tmp_db):
    sid = agent_memory.ensure_session(None, title="t")
    big = json.dumps({"klines": ["x" * 50] * 200}, ensure_ascii=False)
    assert len(big) > 10000, "构造的载荷要足够大才有意义（实际 %d）" % len(big)

    agent_memory.record_message(sid, "tool", big)
    content = _last(sid)["content"]

    assert len(content) <= agent_memory.TOOL_MESSAGE_LIMIT + 80, \
        "tool 消息必须被截断（实际 %d 字符）" % len(content)
    assert "截断" in content, "必须留下截断标记，否则排障时会被误导"
    assert str(len(big)) in content, "标记里要带原始长度"


def test_short_tool_message_kept_intact(tmp_db):
    sid = agent_memory.ensure_session(None, title="t")
    small = json.dumps({"ok": True, "value": 1}, ensure_ascii=False)
    agent_memory.record_message(sid, "tool", small)
    assert _last(sid)["content"] == small


def test_user_message_not_truncated(tmp_db):
    """用户消息是记忆原料，不能截断"""
    sid = agent_memory.ensure_session(None, title="t")
    long_q = "我" * 5000
    agent_memory.record_message(sid, "user", long_q)
    assert _last(sid)["content"] == long_q


def test_assistant_message_not_truncated(tmp_db):
    sid = agent_memory.ensure_session(None, title="t")
    long_a = "好" * 5000
    agent_memory.record_message(sid, "assistant", long_a)
    assert _last(sid)["content"] == long_a


def test_limit_is_reasonable():
    """上限要小到能防膨胀，又要大到能留下可读摘要"""
    assert 200 <= agent_memory.TOOL_MESSAGE_LIMIT <= 2000


def test_non_str_content_is_serialized_not_crashed(tmp_db):
    """非字符串入参（dict）→ 序列化后按同一规则处理，不抛异常"""
    sid = agent_memory.ensure_session(None, title="t")
    agent_memory.record_message(sid, "tool", {"big": "y" * 5000})
    content = _last(sid)["content"]
    assert len(content) <= agent_memory.TOOL_MESSAGE_LIMIT + 80
