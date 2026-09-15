# -*- coding: utf-8 -*-
"""工具层契约加固回归锁（P0-4；见 docs/COVERAGE_DESIGN.md §11.2）

三项：① 必填参数校验（缺参不再把空值透传给工具）② 单次调用超时 ③ 并行执行能力

设计取向（诚实标注）：
- ① 必填校验：缺参 → `error_code=INVALID_ARGS`（不可重试）。多余参数仍按旧行为静默丢弃。
- ② 超时：`TOOL_TIMEOUT_SECONDS`（默认 30s，设为 0 关闭）。超时 → `error_code=TIMEOUT`
  且 `retryable=True`。实现用线程池 `future.result(timeout)`——**不杀线程**，
  只是调用方不再被阻塞（Python 无法强杀线程，这是已知折中）。
- ③ 并行：`agent_run(parallel_tools=True)` 时多工具并发；**默认 False**
  （工具内部对缓存/SQLite 的线程安全性尚未实测，先"能力就位 + 可开关"）。
  无论并行与否，`tool_trace` 与事件顺序**仍按模型给出的调用顺序**回填。
"""
import inspect
import json
import time
from unittest.mock import patch

from services import llm_config

from utils import agent_core, ai_helper
from utils.agent_core import agent_run, execute_ai_tool_v2


# ==================== ① 必填参数校验 ====================


def test_missing_required_param_returns_invalid_args():
    """缺必填参数：返回 INVALID_ARGS 并点名缺哪个（旧实现会把空值透传给工具）"""
    data = json.loads(execute_ai_tool_v2("get_stock_diagnosis", {}))
    assert data.get("error_code") == "INVALID_ARGS"
    assert "stock_code" in data["error"]
    assert data.get("retryable") is False
    assert data.get("tool") == "get_stock_diagnosis"


def test_blank_required_param_also_rejected():
    """空串同样视为缺失"""
    data = json.loads(execute_ai_tool_v2("get_stock_diagnosis", {"stock_code": ""}))
    assert data.get("error_code") == "INVALID_ARGS"


def test_required_param_present_passes_through():
    """参数齐全时正常执行（防过度拦截）"""
    with patch.object(agent_core, "resolve", return_value=(lambda **kw: {"ok": 1})):
        data = json.loads(execute_ai_tool_v2("get_stock_diagnosis", {"stock_code": "600519"}))
    assert "error" not in data


def test_no_required_params_tool_still_works():
    """无必填项的工具（如 get_market_index）不受校验影响"""
    with patch.object(agent_core, "resolve", return_value=(lambda **kw: {"ok": 1})):
        data = json.loads(execute_ai_tool_v2("get_market_index", {}))
    assert "error" not in data


# ==================== ② 超时 ====================


def test_tool_timeout_returns_timeout_error(monkeypatch):
    monkeypatch.setattr(agent_core, "TOOL_TIMEOUT_SECONDS", 0.2)

    def slow(**kw):
        time.sleep(1.5)
        return {"ok": 1}

    with patch.object(agent_core, "resolve", return_value=slow):
        t0 = time.time()
        data = json.loads(execute_ai_tool_v2("get_market_index", {}))
        elapsed = time.time() - t0
    assert data.get("error_code") == "TIMEOUT"
    assert data.get("retryable") is True, "超时属可重试"
    assert elapsed < 1.0, "必须在超时阈值附近返回，而不是等工具跑完（实际 %.2fs）" % elapsed


def test_tool_timeout_zero_disables(monkeypatch):
    """TOOL_TIMEOUT_SECONDS=0 → 关闭超时控制（直接调用）"""
    monkeypatch.setattr(agent_core, "TOOL_TIMEOUT_SECONDS", 0)
    with patch.object(agent_core, "resolve", return_value=(lambda **kw: {"ok": 1})):
        data = json.loads(execute_ai_tool_v2("get_market_index", {}))
    assert "error" not in data


def test_tool_timeout_default_is_positive():
    assert agent_core.TOOL_TIMEOUT_SECONDS > 0


# ==================== ③ 并行能力（默认关 + 顺序不乱） ====================


def test_parallel_tools_default_off():
    """默认关闭（工具内缓存/SQLite 的线程安全性尚未实测）"""
    sig = inspect.signature(agent_run)
    assert "parallel_tools" in sig.parameters
    assert sig.parameters["parallel_tools"].default is False


def _tool_call(name, args=None, call_id="c1"):
    return {"type": "tool_call", "content": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _fake_llm_seq(seq):
    state = {"n": 0}

    def fake(messages, tools=None, model=None, temperature=0.7, thinking=False):
        i = state["n"]
        state["n"] += 1
        if i < len(seq):
            return _tool_call(seq[i], {}, call_id="c%d" % (i + 1))
        return {"type": "text", "content": "done"}
    return fake


def _run_with(mode_parallel):
    seq = ["get_market_index", "get_market_sentiment", "get_hot_sectors"]
    executed = []

    def fake_execute(name, args):
        time.sleep(0.01)
        executed.append(name)
        return json.dumps({"name": name}, ensure_ascii=False)

    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_seq(seq)), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        r = agent_run("x", parallel_tools=mode_parallel)
    return executed, [t["name"] for t in r["tool_trace"]]


def test_sequential_order_baseline():
    executed, trace = _run_with(False)
    assert executed == ["get_market_index", "get_market_sentiment", "get_hot_sectors"]
    assert trace == ["get_market_index", "get_market_sentiment", "get_hot_sectors"]


def test_parallel_executes_all_and_keeps_trace_order():
    """并行开启时：三个工具都被执行，且 tool_trace 仍按模型给的调用顺序"""
    executed, trace = _run_with(True)
    assert sorted(executed) == sorted(["get_market_index", "get_market_sentiment", "get_hot_sectors"])
    assert trace == ["get_market_index", "get_market_sentiment", "get_hot_sectors"], \
        "tool_trace 必须按调用顺序对齐（并行只影响执行，不影响顺序与回填）"
