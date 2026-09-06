# -*- coding: utf-8 -*-
"""
周报服务（v1.1 粘性三件套 B）：持仓周度聚合 + 可选 AI 点评 + 无 Key 降级。

数据通道（实现时验证，docs/V1.1_PLAN.md B 节补强②）：
- 每只基金本周涨跌：天天基金 f10 lsjz 直连（safe_request 白名单通道）——
  akshare fund_open_fund_info_em 走东财 push 域名，本机实测反爬不可靠；
- 大盘对比拆两个独立数字：指数本周涨跌（东财 index_zh_a_hist，反爬不可得时
  显式标注"数据源暂不可得"，不编造）+ 沪深300 估值分位（蛋卷源 get_valuation_data）。
- 无 Key 降级：纯数据卡（聚合层输出），AI 点评层整体跳过。
"""

import datetime
import json

from config import API_KEY
from data.database import get_latest_report, get_report, save_report
from services._json import to_jsonable

LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
LSJZ_HEADERS = {"Referer": "https://fundf10.eastmoney.com/"}
AI_TOOLS_FOR_REPORT = ["get_market_moneyflow", "get_hot_sectors", "get_index_valuation"]


# ==================== 数据抓取 ====================

def _fetch_nav_rows(symbol, size=12):
    """天天基金 lsjz 历史净值（新→旧）：[{date, nav}]；失败返回 []。"""
    from utils.common import safe_request
    try:
        resp = safe_request(
            LSJZ_URL,
            params={"fundCode": str(symbol), "pageIndex": 1, "pageSize": int(size)},
            headers=LSJZ_HEADERS,
            timeout=12,
        )
        if resp is None:
            return []
        rows = (resp.json().get("Data") or {}).get("LSJZList") or []
        out = []
        for r in rows:
            try:
                out.append({"date": str(r.get("FSRQ", "")), "nav": float(r.get("DWJZ"))})
            except (TypeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001 - 单只失败不影响整体聚合
        return []


def _weekly_change(rows):
    """由净值序列（新→旧）算本周涨跌：最新 / 5 个交易日前 - 1。数据不足返回 None。"""
    if len(rows) < 6:
        return None
    latest, prev = rows[0]["nav"], rows[5]["nav"]
    if prev <= 0:
        return None
    return round((latest / prev - 1) * 100, 2)


# ==================== 聚合（纯数据层） ====================

def aggregate_weekly():
    """聚合本周持仓数据。所有数字可溯源；不可得字段显式 None。"""
    from data.database import load_my_funds
    from data.market_api import get_valuation_data

    funds_raw = load_my_funds() or {}
    funds = []
    total_market = 0.0
    total_week_pnl = 0.0
    total_prev_market = 0.0
    known = False

    for code, f in list(funds_raw.items())[:12]:  # 上限 12 只：网络成本封顶
        shares = float(f.get("hold_shares") or 0)
        name = str(f.get("name") or "")
        rows = _fetch_nav_rows(code, size=12)
        entry = {"code": str(code), "name": name, "shares": shares,
                 "weekly_pct": None, "week_pnl": None, "market_value": None}
        if rows and shares > 0:
            latest_nav = rows[0]["nav"]
            wk = _weekly_change(rows)
            entry["market_value"] = round(shares * latest_nav, 2)
            if wk is not None:
                entry["weekly_pct"] = wk
                prev_nav = rows[5]["nav"]
                entry["week_pnl"] = round(shares * (latest_nav - prev_nav), 2)
                total_market += shares * latest_nav
                total_prev_market += shares * prev_nav
                total_week_pnl += shares * (latest_nav - prev_nav)
                known = True
        funds.append(entry)

    total_invest = sum(float(f.get("amount") or 0) for f in funds_raw.values())

    portfolio_weekly_pct = None
    if known and total_prev_market > 0:
        portfolio_weekly_pct = round(total_week_pnl / total_prev_market * 100, 2)

    # 大盘：估值分位（蛋卷源可用）；指数周涨跌（东财历史）反爬期不可得 → 显式 None
    hs300 = None
    index_weekly_pct = None
    try:
        for item in get_valuation_data():
            if str(item.get("code")) == "000300":
                hs300 = {"name": item.get("name"), "pe_percentile": item.get("pe_percentile"),
                         "pb_percentile": item.get("pb_percentile"), "eva_type": item.get("eva_type")}
                break
    except Exception:  # noqa: BLE001
        hs300 = None

    return {
        "period": _week_key(),
        "fund_count": len(funds),
        "funds": funds,
        "total_invest": round(total_invest, 2),
        "total_market_value": round(total_market, 2) if known else None,
        "total_week_pnl": round(total_week_pnl, 2) if known else None,
        "portfolio_weekly_pct": portfolio_weekly_pct,
        "index_weekly": {"code": "000300", "name": "沪深300",
                         "weekly_pct": index_weekly_pct,
                         "unavailable_note": None if index_weekly_pct is not None
                         else "指数历史行情数据源暂不可得"},
        "hs300_valuation": hs300,
    }


def _week_key(now=None):
    now = now or datetime.date.today()
    iso = now.isocalendar()
    return "{}-W{:02d}".format(iso[0], iso[1])


# ==================== 渲染 ====================

def _data_card(agg):
    """无 Key 降级：纯数据卡 Markdown（无 AI 文字）。"""
    lines = ["## 本周持仓周报（数据卡）", ""]
    if agg["fund_count"] == 0:
        lines.append("暂无持仓——先到「我的持仓」录入，再回来生成周报。")
        return "\n".join(lines)
    lines.append("| 基金 | 本周涨跌 | 本周盈亏 | 市值 |")
    lines.append("|---|---|---|---|")
    for f in agg["funds"]:
        lines.append("| {}({}) | {} | {} | {} |".format(
            f["name"] or "--", f["code"],
            "--" if f["weekly_pct"] is None else "{:+.2f}%".format(f["weekly_pct"]),
            "--" if f["week_pnl"] is None else "{:+.0f}元".format(f["week_pnl"]),
            "--" if f["market_value"] is None else "{:.0f}元".format(f["market_value"]),
        ))
    lines.append("")
    if agg["portfolio_weekly_pct"] is not None:
        lines.append("**组合本周：{:+.2f}%（{:+.0f} 元）**".format(
            agg["portfolio_weekly_pct"], agg["total_week_pnl"]))
    else:
        lines.append("**组合本周：数据不足，无法计算（净值源不可得或无持仓）**")
    hs = agg.get("hs300_valuation")
    if hs:
        lines.append("沪深300 估值分位：PE {:.0f}% / PB {:.0f}%（{}）".format(
            hs.get("pe_percentile") or 0, hs.get("pb_percentile") or 0, hs.get("eva_type") or "--"))
    note = (agg.get("index_weekly") or {}).get("unavailable_note")
    if note:
        lines.append("> 指数本周涨跌：{}。".format(note))
    lines.append("")
    lines.append("> 配置 DeepSeek API Key 后可解锁 AI 点评。")
    return "\n".join(lines)


def _ai_prompt(agg):
    return (
        "你是用户的私人投资助理。请基于下面给出的【本周聚合数据】写一份简短周报点评，"
        "要求：\n"
        "1. 只使用数据里出现过的数字，禁止编造任何未提供的持仓/涨跌/估值数字；\n"
        "2. 结构：一段组合表现总述 + 逐条基金一句话点评 + 一段风险提示；\n"
        "3. 语气专业克制，不构成投资建议；如数据不足（None/--），如实说明；\n"
        "4. 输出 Markdown，总长不超过 300 字。\n\n"
        "可用工具（可选调用，用于补充市场环境，不得虚构结果）："
        "get_market_moneyflow / get_hot_sectors / get_index_valuation。"
    )


def generate_weekly(force=False):
    """生成（或取已缓存的）本周周报。

    流程：同周已有 → 直接返回（幂等）；否则聚合 → 有 Key 加 AI 点评 / 无 Key 纯数据卡
    → 落库返回。degraded=True 表示无 Key 降级。
    """
    period = _week_key()
    if not force:
        cached = get_report("weekly", period)
        if cached:
            return {"ok": True, "period": period, "degraded": bool(cached["degraded"]),
                    "cached": True, "content": cached["content"]}

    agg = aggregate_weekly()
    base_card = _data_card(agg)

    if not API_KEY:
        save_report("weekly", period, base_card, degraded=1)
        return {"ok": True, "period": period, "degraded": True,
                "cached": False, "content": base_card}

    try:
        from utils.agent_core import TOOL_REGISTRY, agent_run
        tools = [TOOL_REGISTRY[n].schema for n in AI_TOOLS_FOR_REPORT if n in TOOL_REGISTRY]
        res = agent_run(
            _ai_prompt(agg),
            context=["【本周聚合数据】\n" + json.dumps(to_jsonable(agg), ensure_ascii=False)],
            memory=False, tools=tools, max_tool_rounds=3,
        )
        content = str(res.get("content") or "").strip()
        if not content:
            raise RuntimeError("AI 空回复")
    except Exception as exc:  # noqa: BLE001 - AI 层失败降级数据卡
        content = base_card + "\n\n> AI 点评生成失败（{}），以上为数据卡。".format(exc)
        save_report("weekly", period, content, degraded=1)
        return {"ok": True, "period": period, "degraded": True,
                "cached": False, "content": content}

    save_report("weekly", period, content, degraded=0)
    return {"ok": True, "period": period, "degraded": False,
            "cached": False, "content": content}


def weekly_cached():
    """GET 入口：只读最近一期周报（不触发生成）。"""
    cached = get_latest_report("weekly")
    if cached is None:
        return {"ok": True, "exists": False}
    return {"ok": True, "exists": True, "period": cached["period"],
            "degraded": bool(cached["degraded"]), "content": cached["content"]}
