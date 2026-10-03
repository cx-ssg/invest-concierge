# -*- coding: utf-8 -*-
"""
市场行情服务（H5 · P2）：`/api/market/*` 数据源。

对应 docs/AGENT_TOOLS_PLAN.md §P2 的五 tab：指数 / 板块 / 情绪 / 资金 / 估值。
全部复用 P1 已工具化的同一批引擎函数（TOOL_REGISTRY 晚绑定目标），不新造数据逻辑；
弱网/被墙时经 fetch_with_timeout 降级为 `ok=false` + 空数据（页面显示"不可得"，
与 agent 工具"数据不可得"口径一致，绝不编造）。
"""

from utils.common import fetch_all_with_timeout, fetch_with_timeout
from services._json import to_jsonable

# 单 tab 的最大等待秒数：数据源被代理拦截时不让请求挂死（对齐 status_service 的降级思路）
MARKET_FETCH_TIMEOUT = 25.0


def _resolve(module_name, fn_name):
    """晚绑定 import（import 期不拉 akshare，服务启动要快）"""
    from importlib import import_module

    return getattr(import_module(module_name), fn_name)


def _call(module_name, fn_name, timeout=MARKET_FETCH_TIMEOUT):
    """线程池超时预取单个引擎：超时/异常 → None（页面降级，不编造）"""
    return fetch_with_timeout(_resolve(module_name, fn_name), timeout=timeout)


def _call_all(specs, timeout=MARKET_FETCH_TIMEOUT):
    """并发预取多个引擎，总等待上限 timeout（顺序与入参一致）"""
    return fetch_all_with_timeout([_resolve(m, f) for m, f in specs], timeout=timeout)


def index():
    """GET /api/market/index：主要宽基指数实时行情"""
    data = _call("data.market_api", "get_market_index", timeout=20.0)
    rows = data if isinstance(data, list) else []
    return to_jsonable({
        "ok": bool(rows),
        "indices": rows,
        "error": None if rows else "指数行情不可得",
    })


def sectors():
    """GET /api/market/sectors：今日行业板块涨幅榜（前 10）"""
    data = _call("data.market_api", "get_hot_sectors", timeout=20.0)
    rows = data if isinstance(data, list) else []
    return to_jsonable({
        "ok": bool(rows),
        "sectors": rows,
        "error": None if rows else "板块行情不可得",
    })


def sentiment():
    """GET /api/market/sentiment：涨停/跌停、最高连板、涨跌家数三指标"""
    data = _call("utils.market_sentiment_merged", "get_market_sentiment")
    if not isinstance(data, dict):
        return {"ok": False, "sentiment": None, "error": "市场情绪不可得"}
    fields = ("limit_up_down", "board_height", "breadth")
    ok = any(data.get(k) is not None for k in fields)
    return to_jsonable({
        "ok": ok,
        "sentiment": data,
        "error": None if ok else "市场情绪不可得",
    })


def moneyflow():
    """GET /api/market/moneyflow：大盘资金全景 + 行业板块资金榜（前 10）

    两个引擎并发预取（总等待 30s 上限），避免串行叠加把弱网首屏拖到 50s+。
    """
    market, sector = _call_all([
        ("data.moneyflow_api", "get_market_moneyflow"),
        ("data.moneyflow_api", "get_sector_moneyflow"),
    ], timeout=30.0)
    market = market if isinstance(market, dict) else None
    sector_rows = (sector if isinstance(sector, list) else [])[:10]

    market_fields = ("main_flow", "super_large_flow", "large_flow",
                     "medium_flow", "small_flow", "north_flow", "south_flow")
    has_market = bool(market) and any(market.get(k) is not None for k in market_fields)
    ok = has_market or bool(sector_rows)
    return to_jsonable({
        "ok": ok,
        "moneyflow": market,
        "sectors": sector_rows,
        "error": None if ok else "资金流向数据不可得",
    })


def valuation():
    """GET /api/market/valuation：主要宽基指数 PE/PB 及历史分位"""
    data = _call("data.market_api", "get_valuation_data")
    rows = data if isinstance(data, list) else []
    return to_jsonable({
        "ok": bool(rows),
        "valuation": rows,
        "error": None if rows else "指数估值数据不可得",
    })
