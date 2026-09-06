# -*- coding: utf-8 -*-
"""v1.1 记忆显性化（粘性三件套 C）单测。

覆盖：
1. 持仓注入：memory=True + 开关开 + 有持仓 → system 含持仓快照 + memory_used 事件
   带 holdings 来源（load_funds_snapshot patch 掉，零网络零真实库读）；
2. 历史来源：continue_question 会话有历史 → sources 含 history，追问重放行为不变；
3. 隐私开关关：不注入持仓、sources 无 holdings（服务层 patch，不触真实库）；
4. 演示模式：不注入持仓；
5. 空持仓：无注入无事件（不撒谎）；
6. memory=False（诊断页追问路径）：完全无注入无事件；
7. holdings_context_brief 格式：无持仓返回空串、有持仓输出含代码/金额。

全部 mock 模块属性，零网络；app_settings 读写走 patch，不触真实 fund_agent.db。
"""
import json
from unittest.mock import patch

from utils import agent_core, ai_helper


def _fake_call_llm(result_type="text", content="回答"):
    """call_llm 桩：捕获 messages，直接返回非工具回答结束循环。"""

    def _call(messages, tools=None, model=None, temperature=None, **kwargs):
        _call.captured = messages
        return {"type": result_type, "content": content}

    _call.captured = []
    return _call


def _run_agent(task="你好", memory=True, session_id=None, continue_question=False,
               structured=True):
    """跑一次 agent_run，返回 (结果, 结构化事件列表, call_llm 桩)。"""
    events = []

    def _on_progress(stage, detail):
        events.append((stage, detail))

    stub = _fake_call_llm()
    with patch.object(ai_helper, "call_llm", stub):
        res = agent_core.agent_run(
            task, memory=memory, session_id=session_id,
            continue_question=continue_question,
            structured_progress=structured, on_progress=_on_progress,
        )
    return res, events, stub


# ---------- 持仓注入 + memory_used 事件 ----------

@patch("utils.ai_helper._is_demo_mode", return_value=False)
@patch("services.settings_service.get_ai_read_holdings", return_value=True)
@patch("utils.ai_helper.load_funds_snapshot")
def test_memory_holdings_injected_with_event(_snap, _priv, _demo):
    _snap.return_value = {
        "count": 1,
        "funds": [{"code": "110022", "name": "易方达消费行业",
                   "amount": 10000.0, "cost_nav": 3.5, "hold_shares": 2000.0}],
    }
    res, events, llm = _run_agent(memory=True)
    system = llm.captured[0]["content"]
    assert "110022" in system and "易方达消费行业" in system
    assert "已知用户上下文" in system
    used = [d for s, d in events if s == "memory_used"]
    assert used and used[0]["sources"] == ["holdings"]


@patch("utils.ai_helper._is_demo_mode", return_value=False)
@patch("services.settings_service.get_ai_read_holdings", return_value=True)
@patch("utils.ai_helper.load_funds_snapshot")
def test_history_source_and_replay_unchanged(_snap, _priv, _demo):
    _snap.return_value = {"count": 0, "funds": []}
    history = [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {"role": "tool", "content": "工具输出不应重放"},
    ]
    with patch("utils.agent_memory.get_agent_messages", return_value=history):
        res, events, llm = _run_agent(memory=True, session_id=7, continue_question=True)
    msgs = llm.captured
    # 重放行为不变：user/assistant 在 user 消息之前，tool 不重放
    assert {"role": "user", "content": "上一问"} in msgs
    assert {"role": "assistant", "content": "上一答"} in msgs
    assert all(m.get("role") != "tool" or m is msgs[-1] for m in msgs)
    used = [d for s, d in events if s == "memory_used"]
    # 有历史、无持仓（空快照）→ 仅 history 来源
    assert used and used[0]["sources"] == ["history"]


# ---------- 隐私开关 / 演示模式 / 空持仓 / memory=False ----------

@patch("utils.ai_helper._is_demo_mode", return_value=False)
@patch("services.settings_service.get_ai_read_holdings", return_value=False)
@patch("utils.ai_helper.load_funds_snapshot")
def test_privacy_switch_off_no_holdings(_snap, _priv, _demo):
    _snap.return_value = {"count": 1, "funds": [{"code": "110022", "name": "x"}]}
    res, events, llm = _run_agent(memory=True)
    assert "110022" not in llm.captured[0]["content"]
    used = [d for s, d in events if s == "memory_used"]
    assert not used  # 无任何注入 → 不发事件


@patch("utils.ai_helper._is_demo_mode", return_value=True)
@patch("services.settings_service.get_ai_read_holdings", return_value=True)
@patch("utils.ai_helper.load_funds_snapshot")
def test_demo_mode_no_holdings_injection(_snap, _priv, _demo):
    _snap.return_value = {"count": 1, "funds": [{"code": "110022", "name": "x"}]}
    res, events, llm = _run_agent(memory=True)
    assert "110022" not in llm.captured[0]["content"]
    assert not [d for s, d in events if s == "memory_used"]


@patch("utils.ai_helper._is_demo_mode", return_value=False)
@patch("services.settings_service.get_ai_read_holdings", return_value=True)
@patch("utils.ai_helper.load_funds_snapshot")
def test_empty_holdings_no_event_no_lie(_snap, _priv, _demo):
    _snap.return_value = {"count": 0, "funds": []}
    res, events, llm = _run_agent(memory=True)
    assert "已知用户上下文" not in llm.captured[0]["content"]
    assert not [d for s, d in events if s == "memory_used"]


def test_memory_false_no_injection_no_event():
    with patch("utils.ai_helper.load_funds_snapshot") as _snap, \
         patch("utils.ai_helper._is_demo_mode", return_value=False), \
         patch("services.settings_service.get_ai_read_holdings", return_value=True):
        _snap.return_value = {"count": 1, "funds": [{"code": "110022", "name": "x"}]}
        res, events, llm = _run_agent(memory=False)
    assert "110022" not in llm.captured[0]["content"]
    assert not [d for s, d in events if s == "memory_used"]


# ---------- holdings_context_brief 纯函数 ----------

def test_brief_empty_on_no_holdings():
    with patch("utils.ai_helper.load_funds_snapshot", return_value={"count": 0, "funds": []}):
        assert agent_core.holdings_context_brief() == ""


def test_brief_empty_on_engine_failure():
    with patch("utils.ai_helper.load_funds_snapshot", side_effect=RuntimeError("net")):
        assert agent_core.holdings_context_brief() == ""


def test_brief_contains_code_amount_and_metrics():
    snap = {"count": 1, "funds": [{
        "code": "110022", "name": "易方达消费行业", "amount": 10000.0,
        "cost_nav": 3.5, "hold_shares": 2000.0,
        "metrics": {"returns_1m": 0.0512, "max_drawdown": -0.12, "dates": [1, 2]},
    }]}
    with patch("utils.ai_helper.load_funds_snapshot", return_value=snap):
        brief = agent_core.holdings_context_brief()
    assert "110022" in brief and "金额=10000" in brief and "成本净值=3.5" in brief
    assert "returns_1m" in brief
    assert "dates" not in brief  # 非数值字段剔除


# ---------- settings 服务：隐私开关存取（patch 底层 get/set_setting，不触真实库） ----------

def test_privacy_switch_roundtrip():
    from services import settings_service
    store = {}
    with patch("data.database.set_setting", side_effect=lambda k, v: store.__setitem__(k, v) or True), \
         patch("data.database.get_setting", side_effect=lambda k, d="": store.get(k, d)):
        assert settings_service.get_ai_read_holdings() is True  # 默认开
        r = settings_service.set_ai_read_holdings(False)
        assert r == {"ok": True, "ai_read_holdings": False}
        assert settings_service.get_ai_read_holdings() is False


def test_privacy_switch_default_when_db_unavailable():
    from services import settings_service
    with patch("data.database.get_setting", side_effect=RuntimeError("no db")):
        assert settings_service.get_ai_read_holdings() is True
