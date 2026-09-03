# -*- coding: utf-8 -*-
"""
导航服务（M0）：轨道/页面元数据唯一权威 + React 前端 /api/nav 数据源。

从 ui_components/sidebar.py 的 PAGE_META 抽出为纯数据层（无 streamlit 依赖，
服务端可独立 import）。sidebar.py 反向 import 本模块保持单一事实源，
tests/test_navigation.py 断言行为不变。
"""

# 结构顺序：基金轨 / 股票轨 / 两轨通用
_TRACK_ORDER = ("fund", "stock", "common")

TRACK_OPTIONS = ("fund", "stock")            # 顶部双轨切换的可用轨道
_TRACK_LABELS = {"fund": "📊 基金", "stock": "📈 股票"}# live 标志与轨道归属分离：轨道清单 = 全量 21 页；渲染集 = live=true 的页面
PAGE_META = {
    # 🟢 基金轨（10）
    "fund": {
        "label": "📊 基金",
        "pages": [
            {"key": "dashboard",        "label": "📊 资产总览", "live": True},
            {"key": "portfolio",        "label": "💼 我的持仓", "live": True},
            {"key": "diary",            "label": "📝 投资日记", "live": True},
            {"key": "fund_search",      "label": "🔍 基金搜索", "live": False},
            {"key": "market",           "label": "📉 市场行情", "live": False},
            {"key": "compare",          "label": "📊 对比分析", "live": False},
            {"key": "dingtou",          "label": "💰 定投管理", "live": False},
            {"key": "dingtou_calc",     "label": "📐 定投计算器", "live": False},
            {"key": "backtest",         "label": "⚡ 回测工具", "live": False},
            {"key": "analysis",         "label": "📋 持仓分析", "live": False},
        ],
    },
    # 🟢 股票轨（7）
    "stock": {
        "label": "📈 股票",
        "pages": [
            {"key": "stock_diagnosis",       "label": "🩺 综合诊断", "live": True},
            {"key": "stock_search",          "label": "🔎 股票搜索", "live": False},
            {"key": "stock_holdings",        "label": "📋 持仓股票", "live": False},
            {"key": "watchlist",             "label": "⭐ 自选股", "live": False},
            {"key": "stock_market_overview", "label": "🌡️ 市场全景", "live": False},
            {"key": "stock_deep_analysis",   "label": "🩻 深度分析", "live": False},
            {"key": "stock_tools",           "label": "🧰 投资工具箱", "live": False},
        ],
    },
    # ⚙️ 两轨通用（4）
    "common": {
        "label": "⚙️ 通用",
        "pages": [
            {"key": "ai_chat",   "label": "💬 AI 对话", "live": True},
            {"key": "settings",  "label": "⚙️ 系统设置", "live": True},
            {"key": "profile",  "label": "👤 个人中心", "live": False},
            {"key": "alert",    "label": "🔔 预警设置", "live": False},
        ],
    },
}


def get_live_pages(track=None):
    """返回 live 页面 [(key, label)]（v1.0 渲染集）。track=None 时按轨道顺序返回全部。"""
    tracks = (track,) if track else _TRACK_ORDER
    pages = []
    for t in tracks:
        for page in PAGE_META[t]["pages"]:
            if page.get("live"):
                pages.append((page["key"], page["label"]))
    return pages


def get_live_page_keys(track=None):
    """返回 live 页 key 列表（导航渲染与测试共用同一数据源）"""
    return [key for key, _ in get_live_pages(track)]


def nav_payload():
    """GET /api/nav 的响应体：各轨道 live 页（React 侧栏渲染源）"""
    return {
        "tracks": [
            {
                "track": t,
                "label": PAGE_META[t]["label"],
                "pages": [
                    {"key": p["key"], "label": p["label"]}
                    for p in PAGE_META[t]["pages"] if p.get("live")
                ],
            }
            for t in _TRACK_ORDER
        ],
    }
