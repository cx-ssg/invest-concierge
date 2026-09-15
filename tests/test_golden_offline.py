# -*- coding: utf-8 -*-
"""离线评测契约（P0-3-A；见 docs/COVERAGE_DESIGN.md §11.2 P0-3）

分工：
- 本文件（离线）：校验**编排契约**（给定工具序列能否被正确执行）+ **用例表自身的合法性**
- 未来 scripts/eval_agent.py（在线）：真调模型，判 `expect_tools` 命中率与 `expect_facts`

⚠️ 离线阶段**测不了**：模型选不选对工具、回答事实是否正确 —— `expect_facts` 在此仅存档。
⚠️ 本阶段最大价值：抓出 golden set 里**写错的工具名 / 参数名**（这类错误在在线评测里会静默失效）。
"""
import importlib.util
import json
import os
import sys
from unittest.mock import patch

import pytest

from services import llm_config

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import agent_core, ai_helper                      # noqa: E402
from utils.agent_core import TOOL_REGISTRY, agent_run        # noqa: E402

# golden set 按文件路径显式加载：tests/ 下按项目惯例没有 __init__.py，
# 且环境里可能存在同名 `tests` 包 → 走包导入会踩 ModuleNotFoundError。
_GOLDEN_CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "cases.py")
_spec = importlib.util.spec_from_file_location("golden_cases", _GOLDEN_CASES)
_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_golden)
CASES = _golden.CASES
MIN_TOOL_COVERAGE = _golden.MIN_TOOL_COVERAGE


# ==================== 一、用例表结构合法性 ====================


def test_every_case_has_required_fields():
    """每条用例必须有 id / question / expect_tools / tags"""
    for c in CASES:
        for field in ("id", "question", "expect_tools", "tags"):
            assert c.get(field), "用例 %r 缺字段 %s" % (c.get("id"), field)
        assert isinstance(c["expect_tools"], list) and c["expect_tools"], \
            "用例 %s 的 expect_tools 必须是非空列表" % c["id"]


def test_case_ids_unique():
    ids = [c["id"] for c in CASES]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, "用例 id 重复：%s" % dup


def test_every_tool_name_exists_in_registry():
    """用例里的工具名必须真实存在（写错会静默失效，这是本阶段要防的头号问题）"""
    bad = []
    for c in CASES:
        for name in c["expect_tools"]:
            if name not in TOOL_REGISTRY:
                bad.append((c["id"], name))
    assert not bad, "用例引用了不存在的工具：%s" % bad


def test_every_tool_arg_is_a_real_param():
    """用例给的参数名必须是该工具真实声明的参数（写错参数名同样静默失效）"""
    bad = []
    for c in CASES:
        for name, args in (c.get("tool_args") or {}).items():
            entry = TOOL_REGISTRY.get(name)
            if entry is None:
                bad.append((c["id"], name, "工具不存在"))
                continue
            illegal = set(args) - set(entry.param_names)
            if illegal:
                bad.append((c["id"], name, sorted(illegal)))
    assert not bad, "用例参数名非法：%s" % bad


def test_required_params_are_provided_when_args_given():
    """给了参数就应覆盖该工具的必填项（否则真实调用会失败）"""
    bad = []
    for c in CASES:
        for name, args in (c.get("tool_args") or {}).items():
            entry = TOOL_REGISTRY.get(name)
            if entry is None:
                continue
            missing = set(entry.required or []) - set(args)
            if missing:
                bad.append((c["id"], name, sorted(missing)))
    assert not bad, "用例缺少必填参数：%s" % bad


def test_tool_coverage_meets_floor():
    """覆盖下限：防止用例表退化成只测少数几个工具"""
    covered = set()
    for c in CASES:
        covered.update(c["expect_tools"])
    assert len(covered) >= MIN_TOOL_COVERAGE, \
        "golden set 只覆盖 %d 个工具（下限 %d）：%s" % (
            len(covered), MIN_TOOL_COVERAGE, sorted(set(TOOL_REGISTRY) - covered))


# ==================== 二、编排契约：按 expect_tools 驱动 agent_run ====================


def _tool_call(name, args=None, call_id="call_1"):
    return {"type": "tool_call", "content": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


def _scripted_llm(seq, args_map):
    """按 seq 依次吐出工具调用，结束后返回文本（确定性，无随机）"""
    state = {"i": 0}

    def fake(messages, tools=None, model=None, temperature=0.7, thinking=False):
        i = state["i"]
        state["i"] += 1
        if i < len(seq):
            name = seq[i]
            return _tool_call(name, (args_map or {}).get(name, {}),
                              call_id="call_{}".format(i + 1))
        return {"type": "text", "content": "已根据以上数据给出结论。"}
    return fake


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case_drives_agent_run(case):
    """每条用例：agent_run 必须按 expect_tools 顺序执行，且每步都有结果回填"""
    executed = []

    def fake_execute(name, args):
        executed.append(name)
        return json.dumps({"name": name, "ok": True}, ensure_ascii=False)

    with patch.object(llm_config, "_TEST_KEY_OVERRIDE", "sk-test"), \
         patch.object(ai_helper, "call_llm",
                      side_effect=_scripted_llm(case["expect_tools"], case.get("tool_args"))), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        result = agent_run(case["question"], max_tool_rounds=8)

    assert executed == case["expect_tools"], \
        "执行顺序/数量与用例不符：实际 %s" % executed
    assert [t["name"] for t in result["tool_trace"]] == case["expect_tools"]
    assert all(t["output"] for t in result["tool_trace"]), "每步结果必须回填"
    assert result["type"] == "text" and result["content"], "必须给出最终文本回答"
