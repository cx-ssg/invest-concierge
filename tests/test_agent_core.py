# -*- coding: utf-8 -*-
"""agent_run 规划循环单测：多轮顺序 / error 回填明说数据不可得 / 无 key 降级 / 记忆续写（离线 mock）"""
import json
from unittest.mock import patch

from utils import agent_core, ai_helper
from utils.agent_core import agent_run


# ==================== 工具：确定性驱动 ====================


def _tool_call(name, args=None, call_id="call_1"):
    return {"type": "tool_call", "content": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


# ==================== 规划循环：多轮顺序 ====================


def test_agent_run_plan_loop_tool_trace_ge_3_in_order():
    """验收：单任务触发 tool_trace ≥3 步，且步骤顺序正确（诊断→估值→排雷→文本）"""
    calls = []
    order = ["get_stock_diagnosis", "get_stock_valuation", "get_stock_minefield"]

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        calls.append(messages)
        if len(calls) <= len(order):
            name = order[len(calls) - 1]
            return _tool_call(name, {"stock_code": "600519"}, call_id="call_{}".format(len(calls)))
        return {"type": "text", "content": "综合诊断完成：财务稳健、估值合理。"}

    def fake_execute(name, arguments):
        return json.dumps({"name": name, "ok": True}, ensure_ascii=False)

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        r = agent_run("诊断一下 600519", max_tool_rounds=8)

    assert r["type"] == "text"
    assert r["content"] == "综合诊断完成：财务稳健、估值合理。"
    assert len(r["tool_trace"]) >= 3
    assert [t["name"] for t in r["tool_trace"]] == order           # 步骤顺序正确
    assert all(t["output"] for t in r["tool_trace"])               # 每步结果回填
    # 规划提示词：先计划 + 防幻觉守则已进 system prompt
    assert "执行计划" in calls[0][0]["content"]
    assert "数据不可得" in calls[0][0]["content"]
    # 回填结构：第二轮起 history 含 assistant(tool_calls) 与 role=tool 消息
    assert any(m.get("role") == "tool" for m in calls[1])
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in calls[1])


def test_agent_run_error_fill_says_data_unavailable():
    """验收：mock 全部工具返回 error → 回填 error 后最终明说"数据不可得"，不编造"""
    calls = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        calls.append(messages)
        if len(calls) == 1:
            return _tool_call("get_stock_diagnosis", {"stock_code": "600519"})
        return {"type": "text", "content": "该股票数据不可得，无法给出分析，请稍后重试。"}

    def fake_execute(name, arguments):
        return json.dumps({"error": "工具执行出错：网络不可达"}, ensure_ascii=False)

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        r = agent_run("分析一下 600519 的风险")

    assert "数据不可得" in r["content"]                            # 明说数据不可得
    assert len(r["tool_trace"]) == 1
    assert "error" in json.loads(r["tool_trace"][0]["output"])      # 工具确实返回了 error
    # error 输出被回填为 role=tool 消息传给第二轮
    tool_msgs = [m for m in calls[1] if m.get("role") == "tool"]
    assert tool_msgs and "error" in tool_msgs[0]["content"]


def test_agent_run_no_key_returns_guide():
    """无 key 全链路降级：返回引导文案，tool_trace 为空，不崩"""
    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        return {"type": "text", "content": "⚠️ 请先配置 DeepSeek API Key 才能使用 AI 功能哦~"}

    with patch.object(ai_helper, "API_KEY", ""), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        r = agent_run("今天大盘怎么样？")
    assert r["type"] == "text"
    assert "API Key" in r["content"]
    assert r["tool_trace"] == []


def test_agent_run_max_rounds_cap():
    """模型一直请求工具 → max_tool_rounds(8) 封顶，返回超限提示"""
    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        return _tool_call("get_market_index", {}, call_id="call_x")

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(agent_core, "execute_ai_tool_v2", return_value="[]"):
        r = agent_run("hi", max_tool_rounds=8)

    assert r["type"] == "text"
    assert "超限" in r["content"]
    assert len(r["tool_trace"]) == 9  # 0..8 共 9 轮调用后封顶


# ==================== 上下文注入 + 记忆 ====================


def test_agent_run_injects_context_into_system_prompt():
    """context（含诊断数据 + 记忆摘要）拼进 system prompt（诊断追问注入规则的落点）"""
    captured = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        captured.append(messages)
        return {"type": "text", "content": "好的"}

    context = [
        "当前股票：贵州茅台（600519）",
        "当前诊断数据：{...}",
        "最近会话记忆：\n- 【持仓偏好】会话#1：用户偏好白酒赛道",
    ]
    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        agent_run("这只股票风险大吗？", context=context)

    sys_content = captured[0][0]["content"]
    assert "当前股票：贵州茅台（600519）" in sys_content
    assert "最近会话记忆" in sys_content
    assert "用户偏好白酒赛道" in sys_content


def test_agent_run_memory_records_and_reuses_session(tmp_path, monkeypatch):
    """记忆落库：agent_run(memory=True) 新建会话落 user/tool/assistant；
    continue_question 复用同一会话最近消息进入下一轮"""
    from data import database
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "agent.db"))
    database.init_db()

    calls = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        calls.append(messages)
        if len(calls) == 1:
            return _tool_call("get_market_index", {}, call_id="call_1")
        if len(calls) == 2:
            return {"type": "text", "content": "大盘平稳。"}
        # 追问轮：能看到上一轮 user 消息被带入上下文
        return {"type": "text", "content": "追问完毕。"}

    def fake_execute(name, arguments):
        return json.dumps([{"name": "上证指数", "price": 3200.0}], ensure_ascii=False)

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        r1 = agent_run("今天大盘怎么样？", memory=True, continue_question=True)
        session_id = r1["session_id"]

    assert session_id is not None and session_id > 0
    assert r1["tool_trace"][0]["name"] == "get_market_index"
    # 落库：user + tool + assistant 共 3 条
    msgs = database.get_agent_messages(session_id)
    assert [m["role"] for m in msgs] == ["user", "tool", "assistant"]
    # 追问：续写同一会话，上轮内容进入本轮上下文
    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        r2 = agent_run("再讲讲风险？", memory=True, session_id=session_id, continue_question=True)
    assert r2["session_id"] == session_id
    assert "今天大盘怎么样？" in str(calls[2])                    # 追问链带入上轮问题
    # 落库累计：run1(user+tool+assistant=3) + run2(本轮无工具调用: user+assistant=2) = 5
    assert database.count_agent_messages(session_id) == 5


def test_agent_run_memory_no_key_still_records(tmp_path, monkeypatch):
    """无 key 降级 + 记忆仍记录：user + assistant 引导文案落库"""
    from data import database
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "agent2.db"))
    database.init_db()

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        return {"type": "text", "content": "⚠️ 请先配置 DeepSeek API Key 才能使用 AI 功能哦~"}

    with patch.object(ai_helper, "API_KEY", ""), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        r = agent_run("我的持仓怎么样？", memory=True)

    assert "API Key" in r["content"]
    assert r["session_id"]
    assert database.count_agent_messages(r["session_id"]) == 2  # user + assistant 引导