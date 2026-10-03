# -*- coding: utf-8 -*-
"""
自选股 / 股票持仓服务（H5 · P3）：`/api/watchlist` 与 `/api/stocks/holdings`。

接线说明（H5 先定因结论）：
- **已有的**：`data/database` 的 watchlist / stock_holdings CRUD 早已存在（Streamlit 时代的
  库表 + 函数），`data/stock_api.get_stock_info` 提供个股行情（东财不可达时 stock_api
  内自带腾讯 fallback）。缺的只是 **HTTP 层 + React 页面**——本模块 + routers 即补齐。
- 页面用的就是这一套，不另起数据通路；行情逐只尽力补齐，取不到就留空（前端显示 "--"）。
"""

import threading

from data.database import (
    add_stock_holding as db_add_stock_holding,
    add_watchlist_stock,
    delete_stock_holding,
    get_all_stock_holdings,
    get_watchlist,
    remove_watchlist_stock,
)
from services._json import to_jsonable

# 股票持仓是"读旧值 → 加权平均 → 写回"，多线程下需进程内互斥（对齐 holdings_service）
_holdings_lock = threading.Lock()

# 单只行情的最大等待：东财不可达时 get_stock_info 内部要先等 AkShare 失败（实测 5-10s）
# 再走腾讯 fallback，超时给太紧会把 fallback 一起掐掉（H5 实测 8s 时 add 恒失败）⇒ 放宽到 20s。
# 失败会进 stock_api 的 5 分钟负缓存，之后同进程内逐只请求都是毫秒级。
QUOTE_TIMEOUT = 20.0


def _quote(code):
    """逐只行情（尽力而为）：失败返回 {}，调用方留空降级。"""
    from utils.common import fetch_with_timeout

    try:
        from data.stock_api import get_stock_info

        info = fetch_with_timeout(get_stock_info, QUOTE_TIMEOUT, code)
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


def _with_quote(row):
    """库内一行 + 实时行情字段（price/change_percent/…），行情不可得则这些键为 None。"""
    q = _quote(row.get("code", ""))
    return {
        **row,
        "name": row.get("name") or q.get("name", ""),
        "market": row.get("market") or q.get("market", ""),
        "price": q.get("price"),
        "change_percent": q.get("change_percent"),
        "change": q.get("change"),
    }


# ==================== 自选股 ====================

def list_watchlist():
    """GET /api/watchlist：自选列表（按加入时间倒序）+ 实时行情"""
    return to_jsonable({"ok": True, "items": [_with_quote(r) for r in get_watchlist()]})


def add_watchlist(code, name="", market=""):
    """POST /api/watchlist：加入自选（名称缺省用行情补全；重复加入返回明确错误）"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "股票代码不能为空"}

    q = _quote(code)
    if not q:
        return {"ok": False, "error": "未找到股票 {}（代码有误或行情不可得）".format(code)}

    resolved_name = str(name or "").strip() or q.get("name", "")
    added = add_watchlist_stock(code, resolved_name, str(market or "").strip() or q.get("market", ""))
    if not added:
        return {"ok": False, "error": "{} 已在自选中".format(resolved_name or code)}
    return {"ok": True, "code": code, "name": resolved_name}


def remove_watchlist(code):
    """DELETE /api/watchlist/{code}"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "股票代码不能为空"}
    if not remove_watchlist_stock(code):
        return {"ok": False, "error": "{} 不在自选中".format(code)}
    return {"ok": True, "code": code}


def search_stock(keyword):
    """GET /api/watchlist/search：按代码/名称搜 A 股（东财不可达时腾讯 smartbox 兜底）"""
    keyword = str(keyword or "").strip()
    if not keyword:
        return {"ok": True, "results": []}

    from utils.common import fetch_with_timeout

    try:
        from data.stock_api import search_stock as _search

        rows = fetch_with_timeout(_search, QUOTE_TIMEOUT, keyword)
    except Exception:
        rows = None
    return to_jsonable({"ok": True, "results": rows if isinstance(rows, list) else []})


# ==================== 股票持仓（自选页的第二个 tab） ====================

def list_stock_holdings():
    """GET /api/stocks/holdings：持仓列表 + 市值/浮动盈亏（行情不可得时留空）"""
    items = []
    for row in get_all_stock_holdings():
        q = _quote(row.get("code", ""))
        price = q.get("price")
        quantity = row.get("quantity") or 0
        cost_price = row.get("cost_price") or 0
        try:
            market_value = round(float(price) * float(quantity), 2) if price else None
            pnl = round((float(price) - float(cost_price)) * float(quantity), 2) if price else None
            pnl_percent = round((float(price) / float(cost_price) - 1) * 100, 2) if price and cost_price else None
        except (TypeError, ValueError):
            market_value, pnl, pnl_percent = None, None, None
        items.append({
            **row,
            "name": row.get("name") or q.get("name", ""),
            "price": price,
            "change_percent": q.get("change_percent"),
            "market_value": market_value,
            "pnl": pnl,
            "pnl_percent": pnl_percent,
        })
    return to_jsonable({"ok": True, "items": items})


def add_stock_holding(code, name="", quantity=0, cost_price=0):
    """POST /api/stocks/holdings：新增/加仓（加权平均成本，库函数已有语义）"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "股票代码不能为空"}
    try:
        quantity = float(quantity)
        cost_price = float(cost_price)
    except (TypeError, ValueError):
        return {"ok": False, "error": "数量/成本价必须是数字"}
    if quantity <= 0 or cost_price <= 0:
        return {"ok": False, "error": "数量/成本价必须为正数"}

    resolved_name = str(name or "").strip()
    if not resolved_name:
        resolved_name = _quote(code).get("name", "")

    with _holdings_lock:
        ok = db_add_stock_holding(code, resolved_name, quantity, cost_price)
    if not ok:
        return {"ok": False, "error": "写入股票持仓失败"}
    return {"ok": True, "code": code}


def remove_stock_holding(code):
    """DELETE /api/stocks/holdings/{code}"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "股票代码不能为空"}
    with _holdings_lock:
        removed = delete_stock_holding(code)
    if not removed:
        return {"ok": False, "error": "{} 不在持仓中".format(code)}
    return {"ok": True, "code": code}
