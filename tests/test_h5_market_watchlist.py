# -*- coding: utf-8 -*-
"""H5 单测：市场行情（P2 /api/market/*）+ 自选股（P3 /api/watchlist、/api/stocks/holdings）。

覆盖：
1. market_service 五 tab：真实引擎透传 / 弱网降级 ok=false / NaN→null（非法 JSON 防线）；
2. 路由冒烟（TestClient，引擎全部 patch，零网络）；
3. watchlist / stock_holdings 服务与路由往返（tmp 隔离库 + patch 行情）；
4. stock_api 腾讯行情 fallback：解析字段位次、搜索过滤 ETF、东财快照不可用时接管。

全部离线（行情函数/HTTP 全部 mock）。
"""
import pytest

from data import database, stock_api
from services import market_service, watchlist_service


# ==================== fixtures ====================

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """把 data.database.DB_FILE 指到临时库并跑 init_db——真 SQL、零真实库触碰。"""
    db_file = tmp_path / "h5_test.db"
    monkeypatch.setattr(database, "DB_FILE", str(db_file))
    database.init_db()
    yield str(db_file)


@pytest.fixture(autouse=True)
def _no_scheduler_env(monkeypatch):
    """防止 TestClient 启动预警调度线程。"""
    monkeypatch.setenv("INVEST_DISABLE_ALERT_SCHEDULER", "1")


FAKE_QUOTE = {
    "name": "贵州茅台", "code": "600519", "market": "上海主板",
    "price": 1258.62, "change": 23.04, "change_percent": 1.86,
}


# ==================== 1. market_service ====================

def test_market_index_real_passthrough(monkeypatch):
    rows = [{"name": "上证指数", "code": "sh000001", "price": 3842.19,
             "change": 11.74, "change_percent": 0.31}]
    monkeypatch.setattr("data.market_api.get_market_index", lambda: rows)
    out = market_service.index()
    assert out["ok"] is True and out["indices"] == rows and out["error"] is None


def test_market_index_degrades_when_engine_unavailable(monkeypatch):
    monkeypatch.setattr("data.market_api.get_market_index", lambda: None)
    out = market_service.index()
    assert out["ok"] is False and out["indices"] == [] and "不可得" in out["error"]


def test_market_sectors_and_valuation_degrade(monkeypatch):
    monkeypatch.setattr("data.market_api.get_hot_sectors", lambda: [])
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: None)
    assert market_service.sectors()["ok"] is False
    assert market_service.valuation()["ok"] is False


def test_market_sentiment_partial_data_is_ok(monkeypatch):
    """三指标只到一项也算 ok（agent 工具同口径：缺失字段留 null，不编造）"""
    monkeypatch.setattr("utils.market_sentiment_merged.get_market_sentiment", lambda: {
        "limit_up_down": None, "board_height": 7, "breadth": None, "errors": [],
    })
    out = market_service.sentiment()
    assert out["ok"] is True
    assert out["sentiment"]["board_height"] == 7
    assert out["sentiment"]["limit_up_down"] is None


def test_market_sentiment_all_missing_degrades(monkeypatch):
    monkeypatch.setattr("utils.market_sentiment_merged.get_market_sentiment", lambda: {
        "limit_up_down": None, "board_height": None, "breadth": None, "errors": ["x"],
    })
    out = market_service.sentiment()
    assert out["ok"] is False and "不可得" in out["error"]


def test_market_moneyflow_nan_becomes_null(monkeypatch):
    """NaN 必须转 null——否则 FastAPI 会吐出浏览器 JSON.parse 不了的非法 JSON。"""
    monkeypatch.setattr("data.moneyflow_api.get_market_moneyflow", lambda: {
        "main_flow": None, "north_flow": float("nan"), "south_flow": 68.64,
        "update_time": "2026-10-04 00:56",
    })
    monkeypatch.setattr("data.moneyflow_api.get_sector_moneyflow", lambda: [])
    out = market_service.moneyflow()
    assert out["ok"] is True
    assert out["moneyflow"]["north_flow"] is None
    assert out["moneyflow"]["south_flow"] == 68.64


def test_market_moneyflow_sector_only_is_ok(monkeypatch):
    monkeypatch.setattr("data.moneyflow_api.get_market_moneyflow", lambda: None)
    monkeypatch.setattr("data.moneyflow_api.get_sector_moneyflow", lambda: [
        {"name": "银行", "change": 1.2, "main_flow": 3.4, "main_flow_ratio": 2.1}])
    out = market_service.moneyflow()
    assert out["ok"] is True and len(out["sectors"]) == 1 and out["moneyflow"] is None


# ==================== 2. 路由冒烟（TestClient，引擎 patch） ====================

def test_market_routes_smoke(monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as server_main

    monkeypatch.setattr("data.market_api.get_market_index", lambda: [
        {"name": "上证指数", "code": "sh000001", "price": 1.0, "change": 0.1, "change_percent": 0.01}])
    monkeypatch.setattr("data.market_api.get_hot_sectors", lambda: [
        {"name": "银行", "code": "BK0475", "change": 1.5}])
    monkeypatch.setattr("utils.market_sentiment_merged.get_market_sentiment", lambda: {
        "limit_up_down": {"limit_up": 30, "limit_down": 5}, "board_height": 4,
        "breadth": {"up_count": 3000, "down_count": 1800, "total": 5000, "up_ratio": 60.0},
        "errors": []})
    monkeypatch.setattr("data.moneyflow_api.get_market_moneyflow", lambda: {
        "main_flow": -12.5, "super_large_flow": 0, "large_flow": 0, "medium_flow": 0,
        "small_flow": 0, "north_flow": None, "south_flow": 68.64, "update_time": "t"})
    monkeypatch.setattr("data.moneyflow_api.get_sector_moneyflow", lambda: [])
    monkeypatch.setattr("data.market_api.get_valuation_data", lambda: [
        {"name": "沪深300", "code": "000300", "pe": 13.18, "pe_percentile": 60.6,
         "pb": 1.4, "pb_percentile": 33.6, "eva_type": "正常", "eva_type_int": 1,
         "update_date": "2026-10-04"}])

    c = TestClient(server_main.app)
    for path, key in (("/api/market/index", "indices"), ("/api/market/sectors", "sectors"),
                      ("/api/market/sentiment", "sentiment"), ("/api/market/moneyflow", "moneyflow"),
                      ("/api/market/valuation", "valuation")):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.json()["ok"] is True and key in r.json(), path


# ==================== 3. 自选股 / 股票持仓 ====================

def test_watchlist_service_roundtrip(tmp_db, monkeypatch):
    monkeypatch.setattr("data.stock_api.get_stock_info", lambda code: dict(FAKE_QUOTE, code=code))

    assert watchlist_service.list_watchlist()["items"] == []

    r = watchlist_service.add_watchlist("600519")
    assert r["ok"] is True and r["name"] == "贵州茅台"

    items = watchlist_service.list_watchlist()["items"]
    assert len(items) == 1
    assert items[0]["code"] == "600519" and items[0]["price"] == 1258.62
    assert items[0]["change_percent"] == 1.86

    # 重复加入 → 明确失败（不静默）
    dup = watchlist_service.add_watchlist("600519")
    assert dup["ok"] is False and "已在自选" in dup["error"]

    assert watchlist_service.remove_watchlist("600519")["ok"] is True
    assert watchlist_service.remove_watchlist("600519")["ok"] is False
    assert watchlist_service.list_watchlist()["items"] == []


def test_watchlist_add_rejects_unknown_code(tmp_db, monkeypatch):
    monkeypatch.setattr("data.stock_api.get_stock_info", lambda code: None)
    r = watchlist_service.add_watchlist("999999")
    assert r["ok"] is False and "未找到股票" in r["error"]
    assert database.get_watchlist() == []


def test_watchlist_quote_failure_degrades_to_null(tmp_db, monkeypatch):
    """行情取不到时列表仍可用（price/change_percent 为 None），不报错、不编造。"""
    monkeypatch.setattr("data.stock_api.get_stock_info", lambda code: None)
    database.add_watchlist_stock("600519", "贵州茅台", "上海主板")
    items = watchlist_service.list_watchlist()["items"]
    assert items[0]["name"] == "贵州茅台"
    assert items[0]["price"] is None and items[0]["change_percent"] is None


def test_watchlist_search_uses_engine(tmp_db, monkeypatch):
    monkeypatch.setattr("data.stock_api.search_stock",
                        lambda kw: [{"code": "600519", "name": "贵州茅台", "type": "股票"}])
    out = watchlist_service.search_stock("茅台")
    assert out["ok"] is True and out["results"][0]["code"] == "600519"
    assert watchlist_service.search_stock("  ")["results"] == []


def test_stock_holdings_service_market_value_and_pnl(tmp_db, monkeypatch):
    monkeypatch.setattr("data.stock_api.get_stock_info", lambda code: dict(FAKE_QUOTE, code=code))

    r = watchlist_service.add_stock_holding("600519", quantity=100, cost_price=1250.0)
    assert r["ok"] is True

    items = watchlist_service.list_stock_holdings()["items"]
    assert len(items) == 1
    assert items[0]["name"] == "贵州茅台"
    assert items[0]["market_value"] == pytest.approx(125862.0)
    assert items[0]["pnl"] == pytest.approx(862.0)
    assert items[0]["pnl_percent"] == pytest.approx(0.69, abs=0.01)

    # 加仓 → 加权平均成本
    watchlist_service.add_stock_holding("600519", quantity=100, cost_price=1150.0)
    row = database.get_stock_holding("600519")
    assert row["quantity"] == 200 and row["cost_price"] == pytest.approx(1200.0)

    assert watchlist_service.remove_stock_holding("600519")["ok"] is True
    assert watchlist_service.list_stock_holdings()["items"] == []


def test_stock_holding_validation(tmp_db):
    assert watchlist_service.add_stock_holding("600519", quantity=0, cost_price=10)["ok"] is False
    assert watchlist_service.add_stock_holding("600519", quantity=1, cost_price=-1)["ok"] is False
    assert watchlist_service.add_stock_holding("", quantity=1, cost_price=10)["ok"] is False


def test_watchlist_routes_smoke(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    import server.main as server_main

    monkeypatch.setattr("data.stock_api.get_stock_info", lambda code: dict(FAKE_QUOTE, code=code))
    monkeypatch.setattr("data.stock_api.search_stock",
                        lambda kw: [{"code": "600519", "name": "贵州茅台", "type": "股票"}])
    c = TestClient(server_main.app)

    r = c.get("/api/watchlist")
    assert r.status_code == 200 and r.json()["items"] == []

    r = c.get("/api/watchlist/search", params={"q": "茅台"})
    assert r.status_code == 200 and r.json()["results"][0]["name"] == "贵州茅台"

    r = c.post("/api/watchlist", json={"code": "600519"})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = c.get("/api/watchlist")
    assert len(r.json()["items"]) == 1 and r.json()["items"][0]["price"] == 1258.62

    r = c.post("/api/stocks/holdings", json={"code": "600519", "quantity": 100, "cost_price": 1250})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = c.get("/api/stocks/holdings")
    assert len(r.json()["items"]) == 1 and r.json()["items"][0]["market_value"] == pytest.approx(125862.0)

    # pydantic 校验：数量必须为正
    r = c.post("/api/stocks/holdings", json={"code": "600519", "quantity": 0, "cost_price": 1250})
    assert r.status_code == 422

    assert c.delete("/api/stocks/holdings/600519").json()["ok"] is True
    assert c.delete("/api/watchlist/600519").json()["ok"] is True
    assert c.get("/api/watchlist").json()["items"] == []


# ==================== 4. stock_api 腾讯 fallback ====================

class _FakeResp:
    status_code = 200

    def __init__(self, text):
        self.text = text
        self.encoding = None


def _tencent_quote_raw():
    """最小可用字段集（88 段真机响应裁剪；位次与 2026-10-04 实测一致）。"""
    parts = [""] * 88
    parts[0], parts[1], parts[2] = "1", "贵州茅台", "600519"
    parts[3], parts[4], parts[5] = "1258.62", "1235.58", "1239.53"
    parts[31], parts[32], parts[33], parts[34] = "23.04", "1.86", "1268.00", "1236.05"
    parts[36], parts[37], parts[38], parts[39] = "38331", "479725", "0.31", "19.32"
    parts[43], parts[44], parts[45], parts[46] = "2.59", "15733.78", "15733.78", "6.26"
    return 'v_sh600519="' + "~".join(parts) + '";'


def test_tencent_secid_mapping():
    assert stock_api._tencent_secid("600519") == "sh600519"
    assert stock_api._tencent_secid("000001") == "sz000001"
    assert stock_api._tencent_secid("300750") == "sz300750"
    assert stock_api._tencent_secid("830799") == "bj830799"


def test_tencent_quote_field_parsing(monkeypatch):
    monkeypatch.setattr(stock_api, "safe_request", lambda *a, **k: _FakeResp(_tencent_quote_raw()))
    info = stock_api._get_stock_info_tencent("600519")
    assert info["name"] == "贵州茅台"
    assert info["price"] == 1258.62
    assert info["prev_close"] == 1235.58
    assert info["change"] == 23.04 and info["change_percent"] == 1.86
    assert info["high"] == 1268.0 and info["low"] == 1236.05
    assert info["volume"] == 38331.0
    assert info["amount"] == 479725.0 * 10000
    assert info["turnover_rate"] == 0.31 and info["pe"] == 19.32 and info["pb"] == 6.26
    assert info["market_cap"] == 15733.78 * 100000000
    assert info["source"] == "tencent"


def test_get_stock_info_falls_back_when_spot_unavailable(monkeypatch):
    """东财全市场快照不可用 → 直接接管腾讯行情（不再返回 None）。"""
    monkeypatch.setattr(stock_api, "_get_akshare_spot_df", lambda: None)
    monkeypatch.setattr(stock_api, "_get_stock_info_tencent",
                        lambda code: dict(FAKE_QUOTE, code=code, source="tencent"))
    info = stock_api.get_stock_info.__wrapped__("600519")  # 绕过缓存，避免跨用例污染
    assert info["price"] == 1258.62 and info["source"] == "tencent"


def test_search_stock_falls_back_to_tencent_and_filters_etf(monkeypatch):
    raw = ('v_hint="sh~600519~\\u8d35\\u5dde\\u8305\\u53f0~gzmt~GP-A'
           '^sh~512800~\\u94f6\\u884cETF\\u534e\\u5b9d~yhetfhb~ETF";')
    monkeypatch.setattr(stock_api, "_get_akshare_spot_df", lambda: None)
    monkeypatch.setattr(stock_api, "safe_request", lambda *a, **k: _FakeResp(raw))
    out = stock_api.search_stock.__wrapped__("茅台")
    assert out == [{"code": "600519", "name": "贵州茅台", "type": "股票"}]
