# -*- coding: utf-8 -*-
"""P1 工具升级单测（2026-09-04 拍板：21 页收敛为 12 个 Agent 工具 + 两刀改造）。

覆盖：
1. 12 个新工具全部注册、schema 合法、晚绑定指向真实引擎函数（离线 resolve，不真调网络）；
2. 两刀：compare_funds → compare_funds_structured（结构化 JSON）；
   load_funds → load_funds_snapshot（{count, funds:[...每只附 metrics]}）；
3. execute_ai_tool_v2 对新工具的参数过滤 / 异常包裹 / none_error 模板。

全部 mock 模块属性，零网络。
"""
import json
from unittest.mock import patch

from utils import ai_helper
from utils.agent_core import TOOL_REGISTRY, execute_ai_tool_v2, resolve

from data import fund_api, stock_api, moneyflow_api, limit_up_api, market_api

# P1 §4 清单：12 个新工具 → 晚绑定的引擎函数
P1_TOOLS = {
    "search_fund": "data.fund_api.search_fund",
    "search_stock": "data.stock_api.search_stock",
    "get_fund_metrics": "data.fund_api.calc_fund_metrics",
    "get_fund_history": "data.fund_api.get_fund_history",
    "backtest_dca": "data.fund_api.dca_result",
    "get_stock_info": "data.stock_api.get_stock_info",
    "get_stock_kline": "data.stock_api.get_stock_kline",
    "get_stock_moneyflow": "data.stock_api.get_stock_moneyflow",
    "get_market_moneyflow": "data.moneyflow_api.get_market_moneyflow",
    "get_hot_sectors": "data.market_api.get_hot_sectors",
    "get_limit_up_review": "data.limit_up_api.get_limit_up_review_data",
    "get_index_valuation": "data.market_api.get_valuation_data",
}


def test_p1_all_12_tools_registered_with_late_binding():
    """12 个新工具全部入注册表（11→23），fn_ref 精确指向引擎模块（不走 ai_helper 转发层）"""
    assert len(TOOL_REGISTRY) == 23
    for name, fn_ref in P1_TOOLS.items():
        assert name in TOOL_REGISTRY, name
        entry = TOOL_REGISTRY[name]
        assert entry.fn_ref == fn_ref
        assert callable(resolve(fn_ref)), fn_ref  # 引擎函数真实存在


def test_p1_tool_schemas_required_declared():
    """新工具 required 参数必须在 params 里声明且描述非空"""
    expected_required = {
        "search_fund": ["keyword"],
        "search_stock": ["keyword"],
        "get_fund_metrics": ["fund_code"],
        "get_fund_history": ["fund_code"],
        "backtest_dca": ["fund_code", "monthly_amount", "months"],
        "get_stock_info": ["stock_code"],
        "get_stock_kline": ["stock_code"],
        "get_stock_moneyflow": ["stock_code"],
        "get_market_moneyflow": [],
        "get_hot_sectors": [],
        "get_limit_up_review": [],
        "get_index_valuation": [],
    }
    for name, req in expected_required.items():
        entry = TOOL_REGISTRY[name]
        schema = entry.schema["function"]
        assert schema["description"].strip()
        assert schema["parameters"]["required"] == req, name
        for p in req:
            assert p in schema["parameters"]["properties"], (name, p)


def test_p1_execute_passes_kwargs_to_engine():
    """execute_v2 按注册表参数过滤并透传给引擎（晚绑定 patch 可见）"""
    with patch.object(fund_api, "search_fund",
                      return_value=[{"code": "161725", "name": "白酒LOF", "type": "指数型"}]) as mock_fn:
        out = json.loads(execute_ai_tool_v2("search_fund", {"keyword": "白酒", "junk": 1}))
        mock_fn.assert_called_once_with(keyword="白酒")
    assert out[0]["code"] == "161725"


def test_p1_execute_wraps_engine_exception():
    """引擎抛异常 → {"error": "工具执行出错：..."} 不崩"""
    with patch.object(moneyflow_api, "get_market_moneyflow", side_effect=RuntimeError("网络超时")):
        out = json.loads(execute_ai_tool_v2("get_market_moneyflow", {}))
    assert "工具执行出错" in out["error"] and "网络超时" in out["error"]


def test_p1_none_error_templates():
    """None 返回 → none_error 模板（含 fund_code 占位符格式化）"""
    with patch.object(fund_api, "calc_fund_metrics", return_value=None):
        out = json.loads(execute_ai_tool_v2("get_fund_metrics", {"fund_code": "999999"}))
    assert "999999" in out["error"]


# ==================== 两刀之一：compare_funds 切 structured ====================

def test_compare_funds_now_returns_structured_json():
    """compare_funds 工具 fn 已切 compare_funds_structured：返回 {ok, funds, metrics} 而非 Markdown"""
    assert TOOL_REGISTRY["compare_funds"].fn == "compare_funds_structured"
    with patch.object(fund_api, "get_fund_info") as mock_info:
        mock_info.side_effect = [
            {"name": "白酒", "dwjz": 1.1, "gsz": 1.11, "gszzl": 0.5, "gztime": "2026-09-04"},
            {"name": "消费", "dwjz": 3.3, "gsz": 3.31, "gszzl": -0.2, "gztime": "2026-09-04"},
        ]
        out = json.loads(execute_ai_tool_v2("compare_funds",
                                            {"fund_code1": "161725", "fund_code2": "110022"}))
    assert out["ok"] in (True, "True")  # _truncate 标量统一 str()
    assert len(out["funds"]) == 2
    keys = [m["key"] for m in out["metrics"]]
    assert keys == ["dwjz", "gsz", "gszzl", "gztime"]


def test_compare_funds_structured_error_path():
    """一端数据缺失 → ok=False + error 带缺失代码"""
    with patch.object(fund_api, "get_fund_info") as mock_info:
        mock_info.side_effect = [{"name": "白酒"}, None]
        out = json.loads(execute_ai_tool_v2("compare_funds",
                                            {"fund_code1": "161725", "fund_code2": "999999"}))
    assert out["ok"] in (False, "False")
    assert "999999" in out["error"]


# ==================== 两刀之二：load_funds 升级持仓快照 ====================

def _fake_metrics(code, days=365):
    return {"returns": {"近1年": 5.5}, "max_drawdown": 3.0, "volatility": 15.0,
            "sharpe": 0.5, "dates": ["d1", "d2"], "values": [1.0, 1.1]}


def test_load_funds_snapshot_shape_and_metrics_attached():
    """快照形态：{count, funds:[{基础字段, metrics}]}；净值序列被剔除；前 6 只带指标"""
    holdings = {"110022": {"name": "消费", "code": "110022", "amount": 1000.0,
                           "cost_nav": 3.4, "hold_shares": 294.0}}
    with patch.object(ai_helper, "_is_demo_mode", return_value=False), \
         patch.object(ai_helper, "load_funds", return_value=holdings), \
         patch.object(ai_helper, "calc_fund_metrics", side_effect=_fake_metrics) as mock_metrics:
        out = json.loads(execute_ai_tool_v2("load_funds", {}))
    mock_metrics.assert_called_once()
    assert out["count"] == "1"
    row = out["funds"][0]
    assert row["code"] == "110022" and row["cost_nav"] == "3.4"
    # dates/values 序列被剔除（对 LLM 无意义的长数组）
    assert "dates" not in row["metrics"] and "values" not in row["metrics"]
    assert row["metrics"]["max_drawdown"] == "3.0"


def test_load_funds_snapshot_caps_metrics_and_tolerates_failure():
    """>6 只只给前 6 只算指标；单只 metrics 异常不影响整体快照"""
    holdings = {str(100000 + i): {"name": f"基金{i}", "code": str(100000 + i),
                                  "amount": 100.0, "cost_nav": 1.0, "hold_shares": 100.0}
                for i in range(8)}
    calls = []

    def flaky(code, days=365):
        calls.append(code)
        if code == "100001":
            raise RuntimeError("akshare 挂了")
        return {"returns": {}, "max_drawdown": 1.0, "volatility": 5.0, "sharpe": 0.1}

    with patch.object(ai_helper, "_is_demo_mode", return_value=False), \
         patch.object(ai_helper, "load_funds", return_value=holdings), \
         patch.object(ai_helper, "calc_fund_metrics", side_effect=flaky):
        out = json.loads(execute_ai_tool_v2("load_funds", {}))
    assert len(calls) == 6  # 只算前 6 只
    assert out["count"] == "8"
    with_metrics = [f for f in out["funds"] if "metrics" in f]
    assert len(with_metrics) == 5  # 100001 抛异常被吞，其余 5 只有指标
    without = [f for f in out["funds"] if "metrics" not in f]
    assert len(without) == 3  # 第 7/8 只（限流）+ 失败那只


def test_load_funds_snapshot_demo_mode():
    """演示模式：内置示例持仓也走快照形态"""
    with patch.object(ai_helper, "_is_demo_mode", return_value=True), \
         patch.object(ai_helper, "calc_fund_metrics", return_value=None):
        out = json.loads(execute_ai_tool_v2("load_funds", {}))
    assert out["count"] == "3"
    codes = [f["code"] for f in out["funds"]]
    assert codes == ["161725", "110022", "000961"]
    # calc 返回 None → metrics 不带键，不崩
    assert all("metrics" not in f or isinstance(f["metrics"], dict) for f in out["funds"])


# ==================== 代表性新工具的 dispatch 行为 ====================

def test_get_stock_kline_truncates_long_series():
    """K 线长序列走 _truncate：列表 top-20 + 截断提示（防 prompt 撑爆）"""
    kline = {"dates": [f"2026-{i:02d}" for i in range(1, 61)],
             "closes": [10.0 + i * 0.1 for i in range(60)]}
    with patch.object(stock_api, "get_stock_kline", return_value=kline):
        out = json.loads(execute_ai_tool_v2("get_stock_kline",
                                            {"stock_code": "600519", "days": 60, "junk": "x"}))
    assert len(out["dates"]) == 21  # 20 项 + 截断提示
    assert out["dates"][-1].startswith("[已截断")


def test_get_limit_up_review_dispatch():
    """涨停复盘：零参数工具 + 大 dict 正常序列化"""
    payload = {"overview": {"涨停": 45, "跌停": 3}, "ladder": [],
               "board_distribution": [], "reason_category": {}, "first_board": [],
               "update_time": "2026-09-04"}
    with patch.object(limit_up_api, "get_limit_up_review_data", return_value=payload):
        out = json.loads(execute_ai_tool_v2("get_limit_up_review", {}))
    assert out["overview"]["涨停"] in (45, "45")


def test_backtest_dca_dispatch():
    """定投回测：三必填参数透传"""
    result = {"total_invest": 36000, "final_value": 41000,
              "profit": 5000, "return_rate": 13.9}
    with patch.object(fund_api, "dca_result", return_value=result) as mock_fn:
        out = json.loads(execute_ai_tool_v2("backtest_dca",
                                            {"fund_code": "161725",
                                             "monthly_amount": 1000,
                                             "months": 36,
                                             "extra": True}))
        mock_fn.assert_called_once_with(fund_code="161725", monthly_amount=1000, months=36)
    assert out["return_rate"] == "13.9"
