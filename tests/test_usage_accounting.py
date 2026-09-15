# -*- coding: utf-8 -*-
"""usage / token 记账回归锁（P0-3-B；见 docs/COVERAGE_DESIGN.md §11.2 P0-3）

问题：`call_llm` 从不读 `response.usage`（全仓无 `.usage` 引用）→ 没有 token 账，
成本不可观测（§10.3 引的行业调查：89% 有可观测、仅 52% 有 eval）。

目标契约：
- `call_llm` 把上游 usage 原样带回：`{"usage": {"prompt_tokens","completion_tokens","total_tokens"}}`
  （上游没给就 `usage=None`，不抛异常）
- `agent_run` 把多轮 usage **累加**到返回值：`{"usage": {"...", "calls": N}}`
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from services import llm_config

from utils import agent_core, ai_helper
from utils.agent_core import agent_run


# ==================== 工具：伪造 OpenAI 客户端 ====================


def _resp(content="hi", tool_calls=None, usage=(10, 5, 15)):
    """构造一个形状与 openai SDK 响应一致的假响应"""
    message = SimpleNamespace(content=content, reasoning_content="", tool_calls=tool_calls)
    choice = SimpleNamespace(message=message,
                             finish_reason="tool_calls" if tool_calls else "stop")
    u = None if usage is None else SimpleNamespace(prompt_tokens=usage[0],
                                                   completion_tokens=usage[1],
                                                   total_tokens=usage[2])
    return SimpleNamespace(choices=[choice], usage=u)


class _FakeCompletions:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def _fake_client(resp):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(resp)))


def _tc(name, args=None, call_id="c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(
        name=name, arguments=json.dumps(args or {}, ensure_ascii=False)))


def _call_llm_with(resp, **kw):
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "_get_client", return_value=_fake_client(resp)):
        return ai_helper.call_llm([{"role": "user", "content": "x"}], **kw)


# ==================== 一、call_llm 带回 usage ====================


def test_call_llm_returns_usage_in_text_mode():
    r = _call_llm_with(_resp(content="hello"))
    assert r["type"] == "text"
    assert r["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_call_llm_returns_usage_in_tool_call_mode():
    r = _call_llm_with(_resp(tool_calls=[_tc("get_market_index")]), tools=[{"type": "function"}])
    assert r["type"] == "tool_call"
    assert r["usage"]["total_tokens"] == 15


def test_call_llm_usage_is_none_when_upstream_missing():
    """上游没返回 usage（部分兼容端点）→ usage=None，不能抛异常"""
    r = _call_llm_with(_resp(content="hi", usage=None))
    assert r["usage"] is None


# ==================== 二、agent_run 累加多轮 usage ====================


def _scripted_llm_with_usage():
    """第 1 轮：工具调用（usage 110）；第 2 轮：文本（usage 70）"""
    state = {"n": 0}

    def fake(messages, tools=None, model=None, temperature=0.7, thinking=False):
        state["n"] += 1
        if state["n"] == 1:
            return {"type": "tool_call",
                    "content": [{"id": "c1", "type": "function",
                                 "function": {"name": "get_stock_diagnosis",
                                              "arguments": json.dumps({"stock_code": "600519"})}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}
        return {"type": "text", "content": "done",
                "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}}
    return fake


def test_agent_run_accumulates_usage_across_rounds():
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_scripted_llm_with_usage()), \
         patch.object(agent_core, "execute_ai_tool_v2",
                      side_effect=lambda n, a: json.dumps({"ok": True}, ensure_ascii=False)):
        r = agent_run("x")
    assert r["usage"] == {"prompt_tokens": 150, "completion_tokens": 30,
                          "total_tokens": 180, "calls": 2}


def test_agent_run_usage_handles_missing_upstream_usage():
    """某一轮没 usage → 只累加有值的轮次，calls 仍计数，不抛异常"""
    state = {"n": 0}

    def fake(messages, tools=None, model=None, temperature=0.7, thinking=False):
        state["n"] += 1
        if state["n"] == 1:
            return {"type": "text", "content": "done", "usage": None}
        return {"type": "text", "content": "done"}
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake):
        r = agent_run("x")
    assert r["usage"]["calls"] == 1
    assert r["usage"]["total_tokens"] == 0


def test_agent_run_still_returns_existing_keys():
    """向后兼容：原有返回键不得缺失（usage 是新增字段）"""
    def fake(messages, tools=None, model=None, temperature=0.7, thinking=False):
        return {"type": "text", "content": "ok", "usage": {"prompt_tokens": 1,
                                                           "completion_tokens": 1,
                                                           "total_tokens": 2}}
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake):
        r = agent_run("x")
    for k in ("type", "content", "tool_trace", "session_id", "usage"):
        assert k in r, "缺少返回键 %s" % k
