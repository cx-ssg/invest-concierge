# -*- coding: utf-8 -*-
"""
持仓服务（M0）：基金持仓 CRUD + 资产概览。

并发纪律（FRONTEND_PLAN §2.4）：load_my_funds/save 是整表读改写，
FastAPI 多线程下必须进程内 threading.Lock 序列化（防双写覆盖丢首笔——
评审发现的真实 bug 根治）。锁只在 services 层持有，data 层不感知。
"""

import threading

from data.database import (
    load_my_funds,
    save_fund_holding,
    delete_fund_holding,
    get_conn,
)
from services._json import to_jsonable

# funds 整表读改写的进程内互斥锁
_funds_lock = threading.Lock()


def list_funds():
    """GET /api/holdings/funds：全量持仓（含净值/涨跌等实时字段，尽力而为）"""
    with _funds_lock:
        funds = dict(load_my_funds())
    out = []
    for code, f in funds.items():
        item = dict(f)
        try:
            from data.fund_api import get_fund_info
            info = get_fund_info(code)
            if info:
                item["dwjz"] = info.get("dwjz")
                item["gszzl"] = info.get("gszzl")
                item["gztime"] = info.get("gztime", "")
        except Exception:
            pass
        out.append(item)
    return to_jsonable(out)


def add_fund(code, name, amount, cost_nav, hold_shares, note=""):
    """POST /api/holdings/funds：新增/覆盖一条持仓（参数校验 + 写后失效缓存）"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "基金代码不能为空"}
    try:
        amount = float(amount)
        cost_nav = float(cost_nav)
        hold_shares = float(hold_shares)
    except (TypeError, ValueError):
        return {"ok": False, "error": "金额/净值/份额必须是数字"}
    if amount <= 0 or cost_nav <= 0 or hold_shares <= 0:
        return {"ok": False, "error": "金额/净值/份额必须为正数"}

    with _funds_lock:
        save_fund_holding(code, str(name or ""), amount, cost_nav, hold_shares, note)
        try:
            from data.database import _invalidate_funds_cache
            _invalidate_funds_cache()
        except Exception:
            pass
    return {"ok": True, "code": code}


def update_fund(code, name=None, amount=None, cost_nav=None, hold_shares=None, note=None):
    """PUT /api/holdings/funds/{code}：读旧值 → 覆盖非空字段（整表读改写锁内完成）"""
    code = str(code or "").strip()
    with _funds_lock:
        funds = load_my_funds()
        old = funds.get(code)
        if not old:
            return {"ok": False, "error": "持仓不存在：{}".format(code)}
        save_fund_holding(
            code,
            name if name is not None else old.get("name", ""),
            float(amount) if amount is not None else old.get("amount", 0),
            float(cost_nav) if cost_nav is not None else old.get("cost_nav", 0),
            float(hold_shares) if hold_shares is not None else old.get("hold_shares", 0),
            note if note is not None else "",
        )
        try:
            from data.database import _invalidate_funds_cache
            _invalidate_funds_cache()
        except Exception:
            pass
    return {"ok": True, "code": code}


def delete_fund(code):
    """DELETE /api/holdings/funds/{code}"""
    code = str(code or "").strip()
    if not code:
        return {"ok": False, "error": "基金代码不能为空"}
    with _funds_lock:
        delete_fund_holding(code)
        try:
            from data.database import _invalidate_funds_cache
            _invalidate_funds_cache()
        except Exception:
            pass
    return {"ok": True, "code": code}


def summary():
    """GET /api/dashboard/summary：资产总览（纯 JSON 版 render_holdings_summary）

    只读库内字段（投入/份额/成本），不拉实时行情——行情由前端调
    /api/funds/{code}/info 补齐（P1），避免本接口弱网时 30s+。
    """
    with _funds_lock:
        funds = dict(load_my_funds())
    total_invest = 0.0
    count = 0
    for f in funds.values():
        try:
            total_invest += float(f.get("amount", 0) or 0)
            count += 1
        except (TypeError, ValueError):
            continue
    return {
        "ok": True,
        "fund_count": count,
        "total_invest": round(total_invest, 2),
        "currency": "CNY",
    }
