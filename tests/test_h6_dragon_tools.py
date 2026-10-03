# -*- coding: utf-8 -*-
"""H6（P4）龙虎/打板工具族回归锁 —— data/dragon_api 12 个业务函数 → 7 个 agent 工具。

依据 docs/AGENT_TOOLS_PLAN.md §3.2：这族「游资向」功能**只做 agent 工具**，
不做页面、UI 不主动推荐（本文件末尾的边界用例把这条锁住）。

全部用例**离线**（patch 数据源函数，无网络），覆盖四件事：
1. 注册表条目（真名 / 晚绑定 / 必填 / 合规文案）；
2. 适配层的合并与归一（DataFrame→records、空→None⇒NOT_FOUND、合并板块清单、龙头载荷裁剪投影）；
3. 真实错误契约（error_code / tool / retryable 三件套）；
4. 边界（工具名不得出现在 pages/ 与 frontend/，防「顺手做个页面」）。
"""
import importlib.util
import json
import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from data import dragon_api
from utils.agent_core import TOOL_REGISTRY, execute_ai_tool_v2

_DRAGON_TOOLS = {
    "get_limit_up_pool": "agent_limit_up_pool",
    "get_limit_up_detail": "agent_limit_up_detail",
    "get_lhb_stats": "agent_lhb_stats",
    "get_dragon_stocks": "agent_dragon_stocks",
    "get_board_list": "agent_board_list",
    "get_board_members": "agent_board_members",
    "get_stock_boards": "agent_stock_boards",
}


def _call(name, args=None):
    return json.loads(execute_ai_tool_v2(name, args or {}))


# ==================== 一、注册表契约 ====================


def test_seven_dragon_tools_registered_and_late_bound():
    """7 个工具全部在册，且晚绑定到 data.dragon_api 的适配函数"""
    for tool, fn in _DRAGON_TOOLS.items():
        assert tool in TOOL_REGISTRY, "%s 未注册" % tool
        entry = TOOL_REGISTRY[tool]
        assert entry.fn == fn
        assert entry.module == "data.dragon_api"
        assert entry.fn_ref == "data.dragon_api.%s" % fn


def test_dragon_tool_descriptions_carry_source_and_risk():
    """合规：游资向数据的描述必须写明来源与「不构成投资建议」"""
    for tool in _DRAGON_TOOLS:
        desc = TOOL_REGISTRY[tool].description
        assert "不构成投资建议" in desc, "%s 缺风险提示" % tool
        assert ("东方财富" in desc) or ("行业板块" in desc), "%s 缺来源标注" % tool


def test_twelve_business_functions_all_reachable():
    """12 个业务函数要么直接注册，要么被 get_dragon_stocks / get_board_list 合并覆盖

    锁定方式：适配层注释里必须逐个列出 12 个函数名（映射表与代码同文件同段），
    同时这些名字必须真实存在于 data/dragon_api.py（改名即红）。
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "dragon_api.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "Agent 工具适配层" in src
    adapter_section = src.split("Agent 工具适配层", 1)[1]
    for fn in ("get_limit_up_stocks", "get_lhb_stats", "get_stock_board_info",
               "get_stock_board_members", "get_stock_board_name_by_stock",
               "get_stock_concept_boards", "get_limit_up_detail", "calc_board_score",
               "judge_stage", "get_board_limit_up_count", "identify_dragon_stocks",
               "get_board_concept_stocks"):
        assert "\ndef %s(" % fn in src, "业务函数 %s 不存在（改名了？）" % fn
        assert fn in adapter_section, "业务函数 %s 未出现在适配层映射说明里" % fn


def test_required_params_declared():
    assert TOOL_REGISTRY["get_limit_up_detail"].required == ["stock_code"]
    assert TOOL_REGISTRY["get_board_members"].required == ["board_code"]
    assert TOOL_REGISTRY["get_stock_boards"].required == ["stock_code"]
    for tool in ("get_limit_up_pool", "get_lhb_stats", "get_dragon_stocks"):
        assert TOOL_REGISTRY[tool].required == []
        assert TOOL_REGISTRY[tool].param_names == set()
    assert TOOL_REGISTRY["get_board_list"].required == []
    assert TOOL_REGISTRY["get_board_list"].param_names == {"board_type"}


def test_late_binding_sees_module_patch():
    """晚绑定：patch dragon_api 的数据源函数后，工具调用必须看到 mock（不是 import 时快照）

    ⚠️ 载荷里的标量经 agent_core._truncate 统一 str()（既有契约，见 test_tool_registry
    的 late-binding 用例），故断言按字符串比较。
    """
    fake = [{"code": "600000", "name": "测试股", "change": 10.0}]
    with patch.object(dragon_api, "get_limit_up_stocks", return_value=fake):
        out = _call("get_limit_up_pool")
    assert out["count"] == "1"
    assert out["data"][0]["code"] == "600000"
    assert "不构成投资建议" in out["risk_note"]


# ==================== 二、空值 → None → NOT_FOUND 契约 ====================


@pytest.mark.parametrize("tool,args,target,empty", [
    ("get_limit_up_pool", {}, "get_limit_up_stocks", []),
    ("get_limit_up_detail", {"stock_code": "600519"}, "get_limit_up_detail", None),
    ("get_lhb_stats", {}, "get_lhb_stats", []),
    ("get_dragon_stocks", {}, "identify_dragon_stocks", None),
    ("get_board_list", {}, "get_stock_board_info", []),
    ("get_board_members", {"board_code": "BK0475"}, "get_stock_board_members", []),
    ("get_stock_boards", {"stock_code": "600519"}, "get_stock_board_name_by_stock", []),
])
def test_empty_source_maps_to_not_found(tool, args, target, empty):
    """数据源返回空 → 适配层归一为 None → 注册表出 NOT_FOUND（模型可判「数据不可得」）"""
    with patch.object(dragon_api, target, return_value=empty):
        out = _call(tool, args)
    assert out["error_code"] == "NOT_FOUND"
    assert out["tool"] == tool
    assert out["retryable"] is False
    assert "不可得" in out["error"] or "未" in out["error"]


def test_empty_dataframe_maps_to_not_found():
    with patch.object(dragon_api, "get_lhb_stats", return_value=pd.DataFrame()):
        out = _call("get_lhb_stats")
    assert out["error_code"] == "NOT_FOUND"


# ==================== 三、适配层：形状归一 ====================


def test_lhb_dataframe_becomes_json_records():
    """DataFrame → records（适配层的核心依据）：493 行表不再被 str(df) 腰斩"""
    df = pd.DataFrame([
        {"序号": np.int64(1), "代码": "600127", "名称": "金健米业",
         "上榜次数": np.int64(16), "龙虎榜净买额": np.float64(217989631.89)},
        {"序号": np.int64(2), "代码": "600519", "名称": "贵州茅台",
         "上榜次数": np.int64(3), "龙虎榜净买额": np.float64(-1234.5)},
    ])
    with patch.object(dragon_api, "get_lhb_stats", return_value=df):
        out = _call("get_lhb_stats")
    assert out["count"] == "2"
    row = out["data"][0]
    assert set(row) == {"序号", "代码", "名称", "上榜次数", "龙虎榜净买额"}
    assert row["代码"] == "600127" and row["上榜次数"] == "16"
    assert float(row["龙虎榜净买额"]) == 217989631.89
    assert out["data"][1]["名称"] == "贵州茅台"
    assert "东方财富" in out["source"]


def test_long_list_capped_at_20_with_true_count():
    """列表统一裁到 20 条（与 _truncate 的 list_top_n 同口径），真实总数在 count"""
    fake = [{"code": "%06d" % i, "name": "股%d" % i} for i in range(52)]
    with patch.object(dragon_api, "get_limit_up_stocks", return_value=fake):
        out = _call("get_limit_up_pool")
    assert out["count"] == "52"
    assert len(out["data"]) == 20
    assert out["data"][0]["code"] == "000000"


def test_board_list_merges_industry_and_concept():
    """合并依据：行业/概念两个「板块清单」→ 一个 list_boards(board_type=...)；
    概念分支保留信息量更大的 get_board_concept_stocks（带 code/涨幅）"""
    industry = [{"code": "BK0475", "name": "银行"}]
    concept = [{"code": "BK0816", "name": "白酒概念", "change": 2.1, "amount": 1e9}]
    with patch.object(dragon_api, "get_stock_board_info", return_value=industry), \
         patch.object(dragon_api, "get_board_concept_stocks",
                      side_effect=AssertionError("concept 分支不该被调用")):
        out = _call("get_board_list")
    assert out["board_type"] == "industry" and out["data"][0]["code"] == "BK0475"

    with patch.object(dragon_api, "get_stock_board_info",
                      side_effect=AssertionError("industry 分支不该被调用")), \
         patch.object(dragon_api, "get_stock_concept_boards",
                      side_effect=AssertionError("退化投影不该被调用")), \
         patch.object(dragon_api, "get_board_concept_stocks", return_value=concept):
        out = _call("get_board_list", {"board_type": "concept"})
    assert out["board_type"] == "concept"
    assert out["data"][0] == {"code": "BK0816", "name": "白酒概念",
                              "change": "2.1", "amount": "1000000000.0"}


def test_board_members_returns_codes_and_count():
    with patch.object(dragon_api, "get_stock_board_members",
                      return_value=["600000", "600036"]):
        out = _call("get_board_members", {"board_code": "BK0475"})
    assert out["count"] == "2" and out["board_code"] == "BK0475"
    assert out["data"] == ["600000", "600036"]


def test_dragon_payload_is_projected_not_raw():
    """identify_dragon_stocks 原始载荷（同一记录重复 3 处 + 全量涨停明细）被裁剪投影"""
    raw = {
        "hot_boards": [{"name": "银行", "limit_up_count": 3,
                        "top_dragon": {"stock": {"code": "600036", "name": "招商银行"},
                                       "total_score": 88}}],
        "dragon_stocks": [
            {"stock": {"code": "6000%02d" % i, "name": "股%d" % i, "change": 10.0,
                       "turnover": 5.0, "amount": 1e8, "board_count": 2},
             "board": "银行", "all_boards": ["银行"],
             "board_limit_up_count": 3, "scores": {"涨幅排名": 25},
             "total_score": 80, "stage": {"stage": "主升期", "advice": "积极参与、持有",
                                          "description": "连板加速"}}
            for i in range(25)
        ],
        "limit_up_stocks": [{"code": "%06d" % i} for i in range(52)],
        "board_stocks": {"银行": [{"code": "600036"}]},
        "update_time": "2026-10-04 15:00:00",
    }
    with patch.object(dragon_api, "identify_dragon_stocks", return_value=raw):
        out = _call("get_dragon_stocks")
    assert out["count"] == "25"
    assert out["limit_up_total"] == "52"
    assert out["board_group_count"] == "1"
    assert len(out["data"]["dragon_stocks"]) == 20          # 裁剪到 20
    assert out["data"]["hot_boards"][0]["top_dragon_code"] == "600036"
    top = out["data"]["dragon_stocks"][0]
    assert top["code"] == "600000" and top["stage"] == "主升期"
    assert top["scores"] == {"涨幅排名": "25"}               # 六维明细保留
    assert "limit_up_stocks" not in out["data"]              # 原始重复段不带进来
    assert "board_stocks" not in out["data"]


def test_extra_arguments_are_filtered():
    """多余参数被注册表过滤（不传给工具函数）"""
    with patch.object(dragon_api, "get_stock_board_name_by_stock",
                      return_value=["银行"]) as mock_fn:
        _call("get_stock_boards", {"stock_code": "600519", "evil": "x"})
    mock_fn.assert_called_once_with("600519")


def test_missing_required_param_is_invalid_args():
    out = _call("get_board_members", {})
    assert out["error_code"] == "INVALID_ARGS" and out["tool"] == "get_board_members"


# ==================== 四、边界：只做工具，不做页面 ====================


def test_dragon_family_never_surfaced_in_ui():
    """H6 硬边界（AGENT_TOOLS_PLAN §3.2）：不加页面、UI 不主动推荐"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    needles = list(_DRAGON_TOOLS) + ["data.dragon_api", "dragon_api"]
    hits = []
    for base, exts in ((os.path.join(root, "pages"), (".py",)),
                       (os.path.join(root, "ui_components"), (".py",)),
                       (os.path.join(root, "frontend", "src"), (".ts", ".tsx", ".js", ".jsx"))):
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if not name.endswith(exts):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except OSError:
                    continue
                for needle in needles:
                    if needle in text:
                        hits.append((os.path.relpath(path, root), needle))
    assert not hits, "龙虎/打板族不得进入页面或前端：%s" % hits


def test_golden_floor_equals_registry_size():
    """加了工具就必须加 golden 用例（MIN_TOOL_COVERAGE 与注册表等值）"""
    cases_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "cases.py")
    spec = importlib.util.spec_from_file_location("h6_golden_cases", cases_path)
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    covered = set()
    for case in golden.CASES:
        covered.update(case["expect_tools"])
    assert len(covered) >= golden.MIN_TOOL_COVERAGE
    assert golden.MIN_TOOL_COVERAGE == len(TOOL_REGISTRY)
    assert set(TOOL_REGISTRY) - covered == set()
    assert set(_DRAGON_TOOLS) <= covered
