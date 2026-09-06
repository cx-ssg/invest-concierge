# -*- coding: utf-8 -*-
"""v1.1 价格预警（粘性三件套 A）单测。

覆盖：
1. DB 层真 SQL（tmp_path 隔离库 + 参数绑定断言）：CRUD/事件/未读/同日去重字段；
2. 判定与交易时段纯函数：above/below 边界、None 跳过、周末/盘外跳过；
3. poll_once 集成（patch 行情函数 + 临时库）：触发落事件 + 托盘通知回调被调
   + 同自然日去重 + 行情不可得跳过；
4. 路由 API 冒烟（TestClient，INVEST_DISABLE_ALERT_SCHEDULER=1 防调度线程）。

行情函数全部 patch，零网络。
"""
import datetime
import os
import sqlite3
from unittest.mock import patch

import pytest

from data import database
from services import alert_service


# ==================== fixtures ====================

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """把 data.database.DB_FILE 指到临时库并跑 init_db——真 SQL、零真实库触碰。"""
    db_file = tmp_path / "alert_test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()
    yield str(db_file)
    # WAL 副本随 tmp_path 自动清理


@pytest.fixture(autouse=True)
def _no_scheduler_env(monkeypatch):
    """防止 create_app/TestClient 启动调度线程（poll 只在显式调用时跑）。"""
    monkeypatch.setenv("INVEST_DISABLE_ALERT_SCHEDULER", "1")


# ==================== DB 层（真 SQL） ====================

def test_alert_crud_roundtrip(tmp_db):
    assert database.list_alerts() == []
    alert_id = database.create_alert("fund", "110022", "易方达消费", "estimate_pct", "above", 3.0)
    assert alert_id is not None
    rows = database.list_alerts()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "fund" and row["symbol"] == "110022"
    assert row["metric"] == "estimate_pct" and row["op"] == "above"
    assert float(row["threshold"]) == 3.0 and row["enabled"] == 1
    assert database.update_alert_enabled(alert_id, False) is True
    assert database.list_alerts(enabled_only=True) == []
    assert database.update_alert_enabled(alert_id, True) is True
    assert len(database.list_alerts(enabled_only=True)) == 1
    assert database.delete_alert(alert_id) is True
    assert database.list_alerts() == []
    assert database.delete_alert(alert_id) is False  # 二次删除报不存在


def test_alert_events_and_unread(tmp_db):
    aid = database.create_alert("fund", "110022", "易方达消费", "estimate_pct", "below", -2.0)
    eid = database.record_alert_event(aid, "110022", "易方达消费", -2.5, -2.0, "触发消息")
    assert eid is not None
    events = database.list_alert_events()
    assert len(events) == 1 and events[0]["message"] == "触发消息"
    assert events[0]["read"] == 0 and float(events[0]["observed"]) == -2.5
    assert database.unread_alert_events_count() == 1
    database.mark_all_alert_events_read()
    assert database.unread_alert_events_count() == 0


def test_sql_parameter_binding_no_interpolation(tmp_db):
    """符号含引号/分号注入载荷：参数绑定下应作为字面量存储（安全约束）。"""
    evil = "110022'; DROP TABLE alerts; --"
    aid = database.create_alert("fund", evil, "x", "estimate_pct", "above", 1.0)
    assert aid is not None
    rows = database.list_alerts()
    assert rows[0]["symbol"] == evil
    # 表还在（未被注入破坏）
    assert database.list_alerts() == rows


# ==================== 纯函数 ====================

def test_evaluate_alert_boundaries():
    assert alert_service.evaluate_alert("above", 3.0, 3.0) is True
    assert alert_service.evaluate_alert("above", 3.0, 2.99) is False
    assert alert_service.evaluate_alert("below", -2.0, -2.0) is True
    assert alert_service.evaluate_alert("below", -2.0, -1.99) is False
    assert alert_service.evaluate_alert("above", 3.0, None) is False
    assert alert_service.evaluate_alert("sideways", 3.0, 3.0) is False


def test_is_trading_time():
    # 周三 10:00 → 是；周六 → 否；周三 8:00 / 16:00 → 否
    wed = datetime.datetime(2026, 9, 9, 10, 0)   # 2026-09-09 周三
    sat = datetime.datetime(2026, 9, 12, 10, 0)   # 周六
    early = datetime.datetime(2026, 9, 9, 8, 0)
    late = datetime.datetime(2026, 9, 9, 16, 0)
    assert alert_service.is_trading_time(wed) is True
    assert alert_service.is_trading_time(sat) is False
    assert alert_service.is_trading_time(early) is False
    assert alert_service.is_trading_time(late) is False


def test_validate_rule_pairs():
    assert alert_service.validate_rule("fund", "110022", "estimate_pct", "above", 3.0) == ""
    assert alert_service.validate_rule("stock", "600519", "price", "below", 1600) == ""
    assert "只支持" in alert_service.validate_rule("fund", "110022", "price", "above", 1)
    assert "kind" in alert_service.validate_rule("etf", "1", "price", "above", 1)
    assert "op" in alert_service.validate_rule("fund", "110022", "estimate_pct", "cross", 1)
    assert "threshold" in alert_service.validate_rule("fund", "110022", "estimate_pct", "above", "3")
    assert "symbol" in alert_service.validate_rule("fund", "  ", "estimate_pct", "above", 1)


# ==================== poll_once 集成（patch 行情 + 临时库） ====================

def _mk_alert(kind="fund", symbol="110022", op="above", threshold=3.0):
    aid = database.create_alert(kind, symbol, "测试标的", 
                                "estimate_pct" if kind == "fund" else "price", op, threshold)
    database.update_alert_last_triggered(aid, "")  # 明确未触发过
    return aid


def test_poll_once_fires_and_notifies(tmp_db):
    fired_msgs = []
    alert_service.register_notifier(fired_msgs.append)
    try:
        aid = _mk_alert()
        with patch("data.fund_api.get_fund_info", return_value={"gszzl": 3.5, "name": "易方达消费"}), \
             patch.object(alert_service, "is_trading_time", return_value=True), \
             patch.dict(alert_service._last_polled, clear=True):
            fired = alert_service.poll_once(force=True)
        assert fired == 1
        events = database.list_alert_events()
        assert len(events) == 1 and events[0]["alert_id"] == aid
        assert "按盘中估值，非最终净值" in events[0]["message"]  # 评审补强①：口径标注
        assert len(fired_msgs) == 1
        # 同自然日去重
        with patch("data.fund_api.get_fund_info", return_value={"gszzl": 9.9}), \
             patch.object(alert_service, "is_trading_time", return_value=True):
            assert alert_service.poll_once(force=True) == 0
        assert len(database.list_alert_events()) == 1
    finally:
        alert_service.register_notifier(None)


def test_poll_once_skips_when_data_unavailable(tmp_db):
    aid = _mk_alert()
    with patch("data.fund_api.get_fund_info", return_value=None), \
         patch.object(alert_service, "is_trading_time", return_value=True):
        assert alert_service.poll_once(force=True) == 0
    assert database.list_alert_events() == []
    assert database.list_alerts()[0]["id"] == aid  # 规则保留


def test_poll_once_off_hours_no_network(tmp_db):
    _mk_alert()
    with patch("data.fund_api.get_fund_info") as gi:  # 不应被调用
        assert alert_service.poll_once() == 0  # 非交易时段（CI 任意时间跑都成立）
    gi.assert_not_called()


def test_notifier_bridge_noop_without_callback():
    # 无回调（浏览器/无头模式）时 _notify 静默
    alert_service._notify("x")  # 不应抛异常


# ==================== API 冒烟（TestClient，调度器禁用） ====================

def test_alerts_api_smoke(tmp_db):
    os.environ.setdefault("INVEST_DISABLE_ALERT_SCHEDULER", "1")
    from fastapi.testclient import TestClient
    import server.main as server_main
    client = TestClient(server_main.app)

    r = client.post("/api/alerts", json={
        "kind": "fund", "symbol": "110022", "name": "易方达消费",
        "metric": "estimate_pct", "op": "above", "threshold": 3.0,
    })
    assert r.status_code == 200 and r.json()["ok"] is True
    alert_id = r.json()["id"]

    r = client.get("/api/alerts")
    assert r.status_code == 200 and len(r.json()["alerts"]) == 1

    r = client.patch(f"/api/alerts/{alert_id}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = client.get("/api/alerts/events")
    assert r.status_code == 200 and r.json()["unread"] == 0

    r = client.delete(f"/api/alerts/{alert_id}")
    assert r.status_code == 200 and r.json()["ok"] is True

    # 校验失败路径
    r = client.post("/api/alerts", json={
        "kind": "fund", "symbol": "110022", "name": "",
        "metric": "price", "op": "above", "threshold": 3.0,
    })
    assert r.status_code == 200 and r.json()["ok"] is False
