# -*- coding: utf-8 -*-
"""工具层错误契约回归锁（P0-2 后半；见 docs/COVERAGE_DESIGN.md §11.2）

问题：`execute_ai_tool_v2` 的三种错误返回**只有中文文案**，没有机器可判的字段 →
调用方（agent_run / SSE 消费方 / 未来的 M1 检索与 M3 图节点）无法按错误类型分支：
重试？降级？上报？

目标契约（在**不破坏**既有中文文案的前提下新增字段）：
    {"error": "<原中文文案>", "error_code": "...", "tool": "...", "retryable": bool}

error_code 取值：
    UNKNOWN_TOOL   —— 注册表里没有这个工具（不可重试）
    NOT_FOUND      —— 工具返回 None 且声明了 none_error 模板（不可重试）
    TOOL_EXCEPTION —— 执行抛异常（网络/超时类可重试，其余不可重试）
"""
import json
from unittest.mock import patch

from services import llm_config

from utils import agent_core, ai_helper
from utils.agent_core import agent_run, execute_ai_tool_v2


def _err(output):
    return json.loads(output)


# ==================== 一、三类错误的 error_code ====================


def test_unknown_tool_error_code():
    """未知工具：新字段齐全，且旧中文文案保持不变（存量测试断言过它）"""
    data = _err(execute_ai_tool_v2("no_such_tool_xyz", {}))
    assert data["error"].startswith("未知工具"), "旧文案必须保留"
    assert data.get("error_code") == "UNKNOWN_TOOL"
    assert data.get("tool") == "no_such_tool_xyz"
    assert data.get("retryable") is False


def test_not_found_error_code():
    """none_error 模板（工具返回 None）：error_code=NOT_FOUND，不可重试"""
    with patch.object(agent_core, "resolve", return_value=(lambda **kw: None)):
        data = _err(execute_ai_tool_v2("get_fund_info", {"fund_code": "999999"}))
    assert "未找到基金" in data["error"], "旧文案必须保留"
    assert data.get("error_code") == "NOT_FOUND"
    assert data.get("tool") == "get_fund_info"
    assert data.get("retryable") is False


def test_tool_exception_error_code():
    """执行抛普通异常：error_code=TOOL_EXCEPTION，不可重试"""
    def boom(**kw):
        raise RuntimeError("boom")

    with patch.object(agent_core, "resolve", return_value=boom):
        data = _err(execute_ai_tool_v2("get_fund_info", {"fund_code": "000001"}))
    assert data["error"].startswith("工具执行出错"), "旧文案必须保留"
    assert data.get("error_code") == "TOOL_EXCEPTION"
    assert data.get("tool") == "get_fund_info"
    assert data.get("retryable") is False


def test_tool_exception_network_is_retryable():
    """执行抛网络/超时类异常 → retryable=True（调用方可据此重试）"""
    def boom(**kw):
        raise TimeoutError("timed out")

    with patch.object(agent_core, "resolve", return_value=boom):
        data = _err(execute_ai_tool_v2("get_fund_info", {"fund_code": "000001"}))
    assert data.get("error_code") == "TOOL_EXCEPTION"
    assert data.get("retryable") is True


def test_connection_error_is_retryable():
    """ConnectionError（含 requests 的 IOError 系）同样可重试"""
    def boom(**kw):
        raise ConnectionError("connection reset")

    with patch.object(agent_core, "resolve", return_value=boom):
        data = _err(execute_ai_tool_v2("get_market_index", {}))
    assert data.get("retryable") is True


# ==================== 二、成功路径不受影响 ====================


def test_success_payload_has_no_error_fields():
    """正常结果不得被塞进 error_code / retryable（防过度修正）"""
    with patch.object(agent_core, "resolve", return_value=(lambda **kw: {"ok": 1, "data": [1, 2]})):
        data = _err(execute_ai_tool_v2("get_market_index", {}))
    assert "error" not in data
    assert "error_code" not in data
    assert "retryable" not in data


# ==================== 三、统一判定辅助（tool_output_error） ====================


def test_tool_output_error_returns_none_for_success():
    from utils.agent_core import tool_output_error
    assert tool_output_error(json.dumps({"a": 1}, ensure_ascii=False)) is None
    assert tool_output_error(json.dumps([1, 2, 3])) is None


def test_tool_output_error_returns_dict_for_error():
    from utils.agent_core import tool_output_error
    info = tool_output_error(json.dumps({"error": "未知工具：x", "error_code": "UNKNOWN_TOOL"},
                                       ensure_ascii=False))
    assert isinstance(info, dict)
    assert info["error_code"] == "UNKNOWN_TOOL"


def test_tool_output_error_handles_legacy_payload_without_code():
    """老格式（只有 error 文案、无 error_code）也要能判错，且给一个兜底 code"""
    from utils.agent_core import tool_output_error
    info = tool_output_error(json.dumps({"error": "工具执行出错：boom"}, ensure_ascii=False))
    assert isinstance(info, dict)
    assert info["error_code"] == "UNKNOWN_ERROR"


def test_tool_output_error_handles_non_json():
    from utils.agent_core import tool_output_error
    info = tool_output_error("not-json-at-all")
    assert isinstance(info, dict)
    assert info["error_code"] == "UNKNOWN_ERROR"


# ==================== 四、tool_end 事件带 error_code ====================


def _tool_call(name, args=None, call_id="call_1"):
    return {"type": "tool_call", "content": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _fake_llm_one_tool_then_text(messages, tools=None, model=None, temperature=0.7, thinking=False):
    if len([m for m in messages if m.get("role") == "tool"]) == 0:
        return _tool_call("get_stock_diagnosis", {"stock_code": "600519"})
    return {"type": "text", "content": "好了"}


def test_tool_end_event_carries_error_code_on_failure():
    """失败时 tool_end 必须带 error_code，供 SSE 消费方分支处理"""
    seen = []
    err_out = json.dumps({"error": "工具执行出错：boom", "error_code": "TOOL_EXCEPTION",
                          "tool": "get_stock_diagnosis", "retryable": False}, ensure_ascii=False)
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=lambda n, a: err_out):
        agent_run("x", structured_progress=True, on_progress=lambda s, d: seen.append((s, d)))
    ends = [d for s, d in seen if s == "tool_end"]
    assert ends and ends[0]["ok"] is False
    assert ends[0].get("error_code") == "TOOL_EXCEPTION"


def test_tool_end_event_has_no_error_code_on_success():
    """成功时 tool_end 不带 error_code（不制造噪声）"""
    seen = []
    ok_out = json.dumps({"name": "x", "ok": True}, ensure_ascii=False)
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=lambda n, a: ok_out):
        agent_run("x", structured_progress=True, on_progress=lambda s, d: seen.append((s, d)))
    ends = [d for s, d in seen if s == "tool_end"]
    assert ends and ends[0]["ok"] is True
    assert "error_code" not in ends[0]
