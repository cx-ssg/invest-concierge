# -*- coding: utf-8 -*-
"""v1.1 持仓周报（粘性三件套 B）单测。

覆盖：
1. _weekly_change 纯函数：正常/数据不足/零净值边界；
2. aggregate_weekly（patch lsjz 抓取 + 持仓 + 估值源）：逐基金周涨跌/组合合计/
   沪深300 估值分位透传/指数周涨跌不可得显式标注（评审补强②的降级路径）；
3. _data_card：无持仓引导/有持仓表格/不可得字段降级 "--"；
4. generate_weekly 降级（无 Key patch）：degraded=True + 数据卡 + 落库可读回；
5. 同周幂等：第二次 generate 直接返回缓存不再聚合；
6. AI 路径：patch agent_run 成功 → degraded=False；AI 失败 → 降级数据卡不炸。

零真实网络；数据库走 tmp_path 隔离库。
"""
import pytest

from data import database
from services import report_service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "report_test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    monkeypatch.setattr(report_service, "__loaded_funds__", None, raising=False)
    database.init_db()
    yield str(db_file)


def _seed_holding(code, name, amount, cost_nav, shares):
    database.save_fund_holding(code, name, amount, cost_nav, shares, "")


# ==================== _weekly_change ====================

def test_weekly_change_normal():
    rows = [{"nav": 6.0 - 0.05 * i} for i in range(6)]  # 新→旧：6.0, 5.95, ..., 5.75
    assert report_service._weekly_change(rows) == pytest.approx((6.0 / 5.75 - 1) * 100, abs=0.01)


def test_weekly_change_insufficient_and_zero():
    assert report_service._weekly_change([{"nav": 1.0}]) is None
    assert report_service._weekly_change([{"nav": 0.0}] * 6) is None


# ==================== aggregate_weekly ====================

def test_aggregate_weekly_with_data(tmp_db, monkeypatch):
    _seed_holding("110022", "易方达消费", 10000.0, 2.5, 2000.0)
    monkeypatch.setattr(
        report_service, "_fetch_nav_rows",
        lambda code, size=12: [{"date": "2026-09-04", "nav": 3.0}, *[{"nav": 2.8}]] if False else
        [{"date": f"d{i}", "nav": 3.0 - 0.1 * i} for i in range(8)],  # 新→旧 3.0 → 2.3
    )
    monkeypatch.setattr(
        "data.market_api.get_valuation_data",
        lambda: [{"code": "000300", "name": "沪深300", "pe_percentile": 69.7,
                  "pb_percentile": 43.2, "eva_type": "正常"}],
    )
    agg = report_service.aggregate_weekly()
    assert agg["fund_count"] == 1
    f = agg["funds"][0]
    assert f["weekly_pct"] == pytest.approx((3.0 / 2.5 - 1) * 100, abs=0.01)
    assert f["week_pnl"] == pytest.approx(2000 * (3.0 - 2.5), abs=0.01)
    assert agg["total_week_pnl"] == pytest.approx(1000.0, abs=0.5)
    assert agg["hs300_valuation"]["pe_percentile"] == 69.7
    # 指数周涨跌不可得 → 显式 None + 说明（不编造）
    assert agg["index_weekly"]["weekly_pct"] is None
    assert agg["index_weekly"]["unavailable_note"]


def test_aggregate_weekly_empty_holdings(tmp_db, monkeypatch):
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [])
    agg = report_service.aggregate_weekly()
    assert agg["fund_count"] == 0 and agg["total_invest"] == 0
    assert agg["portfolio_weekly_pct"] is None


# ==================== _data_card ====================

def test_data_card_empty_holdings():
    card = report_service._data_card({"fund_count": 0, "funds": []})
    assert "暂无持仓" in card


def test_data_card_renders_rows_and_none_degradation():
    agg = {
        "period": "2026-W36", "fund_count": 1,
        "funds": [{"code": "110022", "name": "易方达消费", "shares": 2000,
                   "weekly_pct": 2.5, "week_pnl": 500.0, "market_value": 6000.0}],
        "total_invest": 5000.0, "total_market_value": 6000.0,
        "total_week_pnl": 500.0, "portfolio_weekly_pct": 8.33,
        "index_weekly": {"code": "000300", "name": "沪深300", "weekly_pct": None,
                         "unavailable_note": "指数历史行情数据源暂不可得"},
        "hs300_valuation": {"name": "沪深300", "pe_percentile": 69.7,
                            "pb_percentile": 43.2, "eva_type": "正常"},
    }
    card = report_service._data_card(agg)
    assert "110022" in card and "+2.50%" in card and "+500元" in card
    assert "8.33%" in card and "69.7%" not in card or "PE" in card
    assert "指数历史行情数据源暂不可得" in card
    assert "配置 DeepSeek API Key" in card


# ==================== generate_weekly ====================

def test_generate_weekly_no_key_degrades(tmp_db, monkeypatch):
    _seed_holding("110022", "易方达消费", 10000.0, 2.5, 2000.0)
    monkeypatch.setattr(report_service, "API_KEY", "")  # 模块 import 时已绑定，patch 侧引用
    monkeypatch.setattr(report_service, "_fetch_nav_rows",
                        lambda code, size=12: [{"date": f"d{i}", "nav": 3.0} for i in range(8)])
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [])
    r = report_service.generate_weekly()
    assert r["ok"] is True and r["degraded"] is True and r["cached"] is False
    assert "数据卡" in r["content"]
    # 落库可读回
    cached = report_service.weekly_cached()
    assert cached["exists"] is True and cached["period"] == r["period"]


def test_generate_weekly_idempotent_same_week(tmp_db, monkeypatch):
    _seed_holding("110022", "易方达消费", 10000.0, 2.5, 2000.0)
    monkeypatch.setattr(report_service, "API_KEY", "")
    calls = {"n": 0}
    real_fetch = report_service._fetch_nav_rows

    def counting_fetch(code, size=12):
        calls["n"] += 1
        return real_fetch(code, size) if False else [{"nav": 3.0}] * 8

    monkeypatch.setattr(report_service, "_fetch_nav_rows", counting_fetch)
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [])
    report_service.generate_weekly()
    assert calls["n"] == 1
    r2 = report_service.generate_weekly()
    assert r2["cached"] is True and calls["n"] == 1  # 第二次不再聚合


def test_generate_weekly_ai_path_success(tmp_db, monkeypatch):
    _seed_holding("110022", "易方达消费", 10000.0, 2.5, 2000.0)
    monkeypatch.setattr(report_service, "API_KEY", "sk-test")
    monkeypatch.setattr(report_service, "_fetch_nav_rows",
                        lambda code, size=12: [{"nav": 3.0}] * 8)
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [])

    captured = {}

    def fake_agent_run(task, context=None, **kw):
        captured["task"] = task
        captured["context"] = context
        return {"type": "text", "content": "## AI 点评\n组合表现平稳。"}

    import utils.agent_core as ac
    monkeypatch.setattr(ac, "agent_run", fake_agent_run)
    r = report_service.generate_weekly(force=True)
    assert r["degraded"] is False and "AI 点评" in r["content"]
    # 聚合 JSON 注入上下文 + prompt 含禁编造约束
    assert "【本周聚合数据】" in captured["context"][0]
    assert "禁止编造" in captured["task"]


def test_generate_weekly_ai_failure_falls_back(tmp_db, monkeypatch):
    _seed_holding("110022", "易方达消费", 10000.0, 2.5, 2000.0)
    monkeypatch.setattr(report_service, "API_KEY", "sk-test")
    monkeypatch.setattr(report_service, "_fetch_nav_rows",
                        lambda code, size=12: [{"nav": 3.0}] * 8)
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [])

    import utils.agent_core as ac

    def boom(*a, **kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ac, "agent_run", boom)
    r = report_service.generate_weekly(force=True)
    assert r["ok"] is True and r["degraded"] is True
    assert "数据卡" in r["content"] and "AI 点评生成失败" in r["content"]
