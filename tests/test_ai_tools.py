# -*- coding: utf-8 -*-
"""ai_chat 工具调用（tools）+ 演示 seed 单测：mock LLM/数据层，验证 schema、执行器与多轮循环"""
import json
from unittest.mock import patch

from utils import ai_helper


def test_ai_tools_schema_has_required_tools():
    """工具 schema 至少含 查基金/查大盘/查持仓 三个实用工具"""
    names = {t["function"]["name"] for t in ai_helper.AI_TOOLS}
    assert {"get_fund_info", "get_market_index", "load_funds"} <= names
    get_fund = next(t for t in ai_helper.AI_TOOLS
                    if t["function"]["name"] == "get_fund_info")
    assert "fund_code" in get_fund["function"]["parameters"]["properties"]
    assert get_fund["function"]["parameters"]["required"] == ["fund_code"]


def test_execute_get_fund_info_returns_json():
    with patch.object(ai_helper, "get_fund_info",
                      return_value={"name": "测试基金", "code": "000001"}):
        out = ai_helper.execute_ai_tool("get_fund_info", {"fund_code": "000001"})
    assert json.loads(out)["code"] == "000001"


def test_execute_get_fund_info_not_found():
    with patch.object(ai_helper, "get_fund_info", return_value=None):
        out = ai_helper.execute_ai_tool("get_fund_info", {"fund_code": "999999"})
    assert "未找到基金" in out


def test_execute_get_market_index_returns_json():
    with patch.object(ai_helper, "get_market_index",
                      return_value=[{"name": "上证指数", "price": 3200.0}]):
        out = ai_helper.execute_ai_tool("get_market_index", {})
    assert json.loads(out)[0]["name"] == "上证指数"


def test_execute_load_funds_demo_mode():
    """演示模式：load_funds 工具返回内置示例持仓快照（P1：{count, funds: [...]}）"""
    with patch.object(ai_helper, "_is_demo_mode", return_value=True), \
         patch.object(ai_helper, "calc_fund_metrics",
                      return_value={"returns": {"近1年": 12.3}, "max_drawdown": 5.0,
                                    "volatility": 18.0, "sharpe": 0.8}):
        out = json.loads(ai_helper.execute_ai_tool("load_funds", {}))
    assert out["count"] == "3"  # _truncate 标量统一 str()
    codes = [f["code"] for f in out["funds"]]
    assert codes == ["161725", "110022", "000961"]
    demo_row = out["funds"][0]
    assert float(demo_row["cost_nav"]) > 0 and float(demo_row["hold_shares"]) > 0
    # 快照附量化指标（净值序列已剔除）
    assert float(demo_row["metrics"]["returns"]["近1年"]) == 12.3
    assert "dates" not in demo_row["metrics"] and "values" not in demo_row["metrics"]


def test_execute_load_funds_real_mode():
    """非演示模式：load_funds 工具读真实持仓库"""
    with patch.object(ai_helper, "_is_demo_mode", return_value=False), \
         patch.object(ai_helper, "load_funds",
                      return_value={"110022": {"name": "x", "code": "110022"}}):
        out = json.loads(ai_helper.execute_ai_tool("load_funds", {}))
    assert out["count"] == "1"  # _truncate 标量统一 str()
    assert out["funds"][0]["code"] == "110022"


def test_execute_unknown_tool():
    out = ai_helper.execute_ai_tool("no_such_tool", {})
    assert "未知工具" in out


def test_seed_demo_funds_no_db_write():
    """seed_demo_funds 返回内存示例持仓 dict（不落盘）"""
    funds = ai_helper.seed_demo_funds()
    assert set(funds.keys()) == {"161725", "110022", "000961"}
    assert funds["161725"]["cost_nav"] > 0 and funds["161725"]["hold_shares"] > 0


def test_chat_with_tools_no_key_returns_guide():
    """无 Key：chat_with_tools 直接返回引导文案，不崩溃"""
    with patch.object(ai_helper, "API_KEY", ""):
        r = ai_helper.chat_with_tools([{"role": "user", "content": "hi"}])
    assert r["type"] == "text"
    assert "API Key" in r["content"]
    assert r["tool_trace"] == []


def test_chat_with_tools_runs_tool_loop():
    """有 Key：模型先请求工具，执行器回填后再出文本（完整闭环）"""
    calls = []

    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        calls.append(messages)
        if len(calls) == 1:
            return {"type": "tool_call", "content": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_market_index", "arguments": "{}"},
            }]}
        return {"type": "text", "content": "今日大盘平稳"}

    def fake_execute(name, arguments):
        return json.dumps([{"name": "上证指数", "price": 3200.0}], ensure_ascii=False)

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(ai_helper, "execute_ai_tool", side_effect=fake_execute):
        r = ai_helper.chat_with_tools([{"role": "user", "content": "今天大盘怎么样？"}])

    assert r["type"] == "text"
    assert r["content"] == "今日大盘平稳"
    assert len(calls) == 2                       # 第一轮工具 + 第二轮文本
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in calls[1])
    assert any(m.get("role") == "tool" for m in calls[1])
    assert r["tool_trace"][0]["name"] == "get_market_index"
    assert "上证指数" in r["tool_trace"][0]["output"]


def test_chat_with_tools_max_rounds_cap():
    """模型一直请求工具时，循环被 max_tool_rounds 封顶，返回提示文案"""
    def fake_call_llm(messages, tools=None, model=None, temperature=0.7):
        return {"type": "tool_call", "content": [{
            "id": "call_x",
            "type": "function",
            "function": {"name": "get_market_index", "arguments": "{}"},
        }]}

    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=fake_call_llm), \
         patch.object(ai_helper, "execute_ai_tool", return_value="[]"):
        r = ai_helper.chat_with_tools([{"role": "user", "content": "hi"}], max_tool_rounds=2)

    assert r["type"] == "text"
    assert "超限" in r["content"]
    assert len(r["tool_trace"]) == 3             # 0,1,2 共 3 轮调用后封顶


def test_build_system_prompt_uses_demo_funds_in_demo_mode():
    """演示模式下系统提示词包含示例持仓代码"""
    with patch.object(ai_helper, "_is_demo_mode", return_value=True), \
         patch.object(ai_helper, "get_fund_info",
                      return_value={"name": "白酒", "gsz": 1.5, "gszzl": 0.5}), \
         patch.object(ai_helper, "get_market_index", return_value=[]), \
         patch.object(ai_helper, "calc_market_sentiment", return_value=None):
        sp = ai_helper.build_system_prompt()
    assert "161725" in sp
    assert "成本" in sp