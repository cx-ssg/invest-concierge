# -*- coding: utf-8 -*-
"""Tool Registry 单测：注册表规模/schema 合法/晚绑定/截断/兼容别名（离线 mock，无网络）"""
import json
from unittest.mock import patch

from utils import agent_core, ai_helper
from data import stock_valuation

from utils.agent_core import TOOL_REGISTRY, execute_ai_tool_v2, _truncate


def test_registry_has_at_least_10_tools():
    """验收：TOOL_REGISTRY ≥10 工具，且 11 个真名齐备"""
    assert len(TOOL_REGISTRY) >= 10
    expected = {"get_fund_info", "get_market_index", "load_funds",
                "get_stock_diagnosis", "get_stock_valuation", "get_stock_minefield",
                "get_stock_moat", "compare_funds", "get_market_sentiment",
                "add_diary", "get_diary"}
    assert expected <= set(TOOL_REGISTRY.keys())


def test_all_schemas_are_valid():
    """每个工具 schema 必须含 name/description/parameters，且晚绑定 fn_ref 为 '模块.函数'"""
    for name, entry in TOOL_REGISTRY.items():
        fn = entry.schema["function"]
        assert fn["name"] == name
        assert fn["description"].strip()
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        for req in params.get("required", []):
            assert req in params["properties"]
        # 晚绑定：fn 只存字符串引用，不存函数对象
        assert isinstance(entry.fn_ref, str) and "." in entry.fn_ref
        assert entry.module and entry.fn


def test_legacy_three_schemas_unchanged():
    """存量兼容：3 旧工具 schema 与旧 AI_TOOLS 完全一致（fund_code required 断言）"""
    names = {t["function"]["name"] for t in ai_helper.AI_TOOLS}
    assert {"get_fund_info", "get_market_index", "load_funds"} <= names
    assert len(ai_helper.AI_TOOLS) == len(TOOL_REGISTRY)  # 全量派生，无遗漏
    get_fund = next(t for t in ai_helper.AI_TOOLS
                    if t["function"]["name"] == "get_fund_info")
    assert "fund_code" in get_fund["function"]["parameters"]["properties"]
    assert get_fund["function"]["parameters"]["required"] == ["fund_code"]


def test_execute_v2_unknown_tool_returns_error():
    out = json.loads(execute_ai_tool_v2("no_such_tool", {}))
    assert "error" in out and "未知工具" in out["error"]


def test_execute_v2_late_binding_sees_module_patch():
    """晚绑定验证：调用时才解析 'data.stock_valuation.get_valuation_data'，
    patch 模块属性后 execute_v2 必须看到 mock 返回值（标量经 _truncate 转 str）"""
    with patch.object(stock_valuation, "get_valuation_data",
                      return_value={"pe": 20.5, "stock_name": "测试股", "price": 1500.0}):
        out = json.loads(execute_ai_tool_v2("get_stock_valuation", {"stock_code": "600519"}))
    assert out["stock_name"] == "测试股"
    assert float(out["pe"]) == 20.5
    assert float(out["price"]) == 1500.0


def test_execute_v2_legacy_none_error_text():
    """旧错误文案保持：get_fund_info 返回 None → "未找到基金"（存量测试断言的文案）"""
    with patch.object(ai_helper, "get_fund_info", return_value=None):
        out = execute_ai_tool_v2("get_fund_info", {"fund_code": "999999"})
    assert "未找到基金" in out and "999999" in out


def test_execute_v2_args_filtered_by_param_names():
    """只透传注册表声明的参数（多余参数忽略，不报错）"""
    with patch.object(ai_helper, "get_fund_info",
                      return_value={"name": "测试基金", "code": "000001"}) as mock_fn:
        execute_ai_tool_v2("get_fund_info", {"fund_code": "000001", "evil": "x"})
        mock_fn.assert_called_once_with(fund_code="000001")


def test_execute_v2_exception_wrapped_as_error():
    """工具内部抛异常 → {"error": "工具执行出错：..."}，不崩"""
    with patch.object(stock_valuation, "get_valuation_data",
                      side_effect=RuntimeError("boom")):
        out = json.loads(execute_ai_tool_v2("get_stock_valuation", {"stock_code": "600519"}))
    assert "error" in out and "工具执行出错" in out["error"]


def test_truncate_long_list_top_20():
    """长列表 top-20 截断 + 末尾提示"""
    out = _truncate(list(range(100)))
    assert len(out) == 21  # 20 项 + 1 条截断提示
    assert out[-1].startswith("[已截断，共 100 项")


def test_truncate_long_string_8000_cap():
    """超长字符串 8000 截断，防 prompt 撑爆（8000 内容 + 截断提示）"""
    out = _truncate("x" * 9000)
    assert isinstance(out, str)
    assert out.startswith("x" * 8000)
    assert "截断" in out
    assert len(out) <= 8020


def test_truncate_nested_dict_and_none():
    d = {"a": list(range(50)), "b": None, "c": {"d": "y" * 9000}}
    out = _truncate(d)
    assert len(out["a"]) == 21
    assert out["b"] is None
    assert "截断" in out["c"]["d"]


def test_compat_alias_execute_ai_tool_is_legacy_executor():
    """兼容别名：execute_ai_tool 即 execute_ai_tool_v2，且对 3 旧工具走注册表"""
    assert agent_core.execute_ai_tool is execute_ai_tool_v2
    with patch.object(ai_helper, "get_fund_info",
                      return_value={"name": "测试基金", "code": "000001"}):
        out = json.loads(ai_helper.execute_ai_tool("get_fund_info", {"fund_code": "000001"}))
    assert out["code"] == "000001"