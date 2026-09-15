# -*- coding: utf-8 -*-
"""P0 修复回归锁（来源：2026-09-14 外部评审 + 独立复核，见 docs/COVERAGE_DESIGN.md §11）

两个 bug 的共同点：**测试绿、现实坏**（旧测试锁定了错误的契约）。

- P0-1 `agent_run(model=_reasoner_model())`：Python 默认参数在 **import 时**求值一次 →
  用户在设置页切换模型，Agent 对话链路实际不生效（配置链断点）。
- P0-2 `_ok = not output.startswith("工具执行失败")`：真实错误格式是
  `execute_ai_tool_v2` 返回的 `{"error": "工具执行出错：..."}`（agent_core.py:368），
  永远不以「工具执行失败」开头 → `tool_end.ok` 恒为 True，排障时被误导。
  （旧测试 test_m0_services.py 用「工具执行失败：数据源不可用」这个**代码从不产生**的
  字符串来测，所以才一直绿。）
"""
import json
from unittest.mock import patch

from services import llm_config

from utils import agent_core, ai_helper
from utils.agent_core import agent_run


def _tool_call(name, args=None, call_id="call_1"):
    return {"type": "tool_call", "content": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _fake_llm_one_tool_then_text(messages, tools=None, model=None, temperature=0.7, thinking=False):
    """第一轮发一次工具调用，第二轮收文本"""
    if len([m for m in messages if m.get("role") == "tool"]) == 0:
        return _tool_call("get_stock_diagnosis", {"stock_code": "600519"})
    return {"type": "text", "content": "好了"}


# ==================== P0-1：model 默认参数被冻结 ====================


def test_p0_1_default_model_reads_config_at_call_time():
    """P0-1：import 之后再改配置，agent_run 必须使用新配置的 reasoner 模型。

    旧实现：`def agent_run(..., model=_reasoner_model(), ...)` → 默认值在 import
    那一刻算死；本测试在 import 之后才改配置，因此旧实现必然断言失败。
    """
    seen_models = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen_models.append(model)
        return {"type": "text", "content": "ok"}

    real_get = llm_config.get_llm_config   # patch 前取真实实现

    def patched_get(*a, **k):
        cfg = dict(real_get(*a, **k))
        cfg["reasoner_model"] = "model-changed-after-import"
        return cfg

    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(llm_config, "get_llm_config", side_effect=patched_get):
        agent_run("hi")

    assert seen_models, "应至少调用一次 call_llm"
    assert seen_models[0] == "model-changed-after-import", (
        "默认 model 必须在调用时读取配置；实际传给 call_llm 的是 %r" % (seen_models[0],))


def test_p0_1_explicit_model_still_wins():
    """P0-1 附带：调用方显式传 model 时，必须以显式值为准（不被新逻辑覆盖）"""
    seen_models = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7, thinking=False):
        seen_models.append(model)
        return {"type": "text", "content": "ok"}

    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm):
        agent_run("hi", model="explicit-model")

    assert seen_models[0] == "explicit-model"


# ==================== P0-2：tool_end.ok 判定 ====================


def _tool_end_payload_for(output):
    """驱动一轮工具调用，返回 tool_end 事件 payload"""
    seen = []
    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=lambda n, a: output):
        agent_run("x", structured_progress=True, on_progress=lambda s, d: seen.append((s, d)))
    ends = [d for s, d in seen if s == "tool_end"]
    assert ends, "应有 tool_end 事件"
    return ends[0]


def test_p0_2_tool_end_ok_false_for_real_error_payload():
    """P0-2：真实错误格式 {"error": "工具执行出错：..."} 必须判 ok=False"""
    out = json.dumps({"error": "工具执行出错：boom"}, ensure_ascii=False)
    payload = _tool_end_payload_for(out)
    assert payload["ok"] is False, "错误返回必须标记 ok=False，实际 %r" % (payload["ok"],)


def test_p0_2_tool_end_ok_false_for_unknown_tool_payload():
    """P0-2：未知工具同为 {"error": ...} 格式，也应判 ok=False"""
    out = json.dumps({"error": "未知工具：xxx"}, ensure_ascii=False)
    payload = _tool_end_payload_for(out)
    assert payload["ok"] is False


def test_p0_2_tool_end_ok_false_for_none_error_payload():
    """P0-2：none_error 模板（如"未找到基金"）也是 {"error": ...}，应判 ok=False"""
    out = json.dumps({"error": "未找到基金：999999"}, ensure_ascii=False)
    payload = _tool_end_payload_for(out)
    assert payload["ok"] is False


def test_p0_2_tool_end_ok_true_for_normal_payload():
    """P0-2：正常结果（无 error 键）必须仍为 ok=True —— 防过度修正"""
    out = json.dumps([{"name": "x", "value": 1}], ensure_ascii=False)
    payload = _tool_end_payload_for(out)
    assert payload["ok"] is True


def test_p0_2_tool_end_ok_true_when_error_key_is_empty():
    """P0-2：数据里带空 error 字段（error=null/""）不应误判为失败"""
    out = json.dumps({"error": None, "data": [1, 2, 3]}, ensure_ascii=False)
    payload = _tool_end_payload_for(out)
    assert payload["ok"] is True


def test_p0_2_tool_end_ok_false_for_non_json_output():
    """P0-2：非 JSON 输出（异常路径兜底）按失败计，且不抛异常"""
    payload = _tool_end_payload_for("工具执行失败：数据源不可用")
    assert payload["ok"] is False
