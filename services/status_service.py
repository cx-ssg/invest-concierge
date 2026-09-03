# -*- coding: utf-8 -*-
"""
状态服务（M0）：/api/health 与 /api/status 数据源（React 状态栏）。

行情/情绪走 fetch_with_timeout（8s 上限，弱网降级——对齐 sidebar.py 的
SIDEBAR_FETCH_TIMEOUT 经验：TUN 代理拦数据源时不让状态栏拖整页）。
"""

import datetime

from config import API_KEY, DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL
from utils.common import fetch_with_timeout

VERSION = "1.0.0"

# 状态栏行情/情绪的最大等待秒数（超时降级为"不可用"，不阻塞）
STATUS_FETCH_TIMEOUT = 8


def health():
    """GET /api/health：存活探针（最轻量，无外部调用）"""
    return {
        "status": "ok",
        "api_key_configured": bool(API_KEY),
        "version": VERSION,
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def status_bar():
    """GET /api/status：引擎状态点 + 数据源健康 + 上次刷新 + 模型版本"""
    index = fetch_with_timeout(_get_market_index, timeout=STATUS_FETCH_TIMEOUT)
    sentiment = fetch_with_timeout(_get_market_sentiment, timeout=STATUS_FETCH_TIMEOUT)

    indices = []
    if isinstance(index, list):
        indices = index[:3]
    else:
        # fetch_with_timeout 超时返回 None → 数据源不可用（不崩，状态栏显示降级态）
        indices = []

    sentiment_state = ""
    if isinstance(sentiment, dict):
        sentiment_state = str(sentiment.get("state", "") or sentiment.get("level", "") or "")

    return {
        "engine": {
            "state": "ready" if API_KEY else "off",
            "api_key_configured": bool(API_KEY),
            "chat_model": DEEPSEEK_MODEL,
            "reasoner_model": DEEPSEEK_REASONER_MODEL,
        },
        "data_source": {
            "ok": bool(indices),
            "indices": indices,
            "sentiment": sentiment_state,
        },
        "last_refresh": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": VERSION,
    }


def _get_market_index():
    # 晚绑定：import 期不拉 akshare（服务进程启动要快）
    from data.market_api import get_market_index
    return get_market_index()


def _get_market_sentiment():
    from utils.market_sentiment_merged import get_market_sentiment
    return get_market_sentiment()
