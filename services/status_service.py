# -*- coding: utf-8 -*-
"""
状态服务（M0）：/api/health 与 /api/status 数据源（React 状态栏）。

行情/情绪走 fetch_with_timeout（8s 上限，弱网降级——对齐 sidebar.py 的
SIDEBAR_FETCH_TIMEOUT 经验：TUN 代理拦数据源时不让状态栏拖整页）。
"""

import datetime

from config import DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL
from services.llm_config import get_llm_config
from utils.common import fetch_with_timeout

# 应用内版本（/api/health、/api/status、/api/settings、前端状态栏与设置页都读它）—— **唯一来源**。
# 2026-10-03 A-R2/F6：此前是 `1.0.0`，而仓库实际版本是 `1.2.0` ⇒ 同一产品两个版本号。
# 2026-10-04 交付线：对外 tag 已到 `v1.5.0`，而应用内/package.json/安装器三处仍写 `1.2.0`
# （**自洽但不反映发布版本** —— 旧锁只钉「内部一致」，钉不住「与发布版本对应」）
# ⇒ 统一升到 `1.5.0`，并把「仓库外三面」也纳入锁：CHANGELOG 顶部 / 安装器 .iss / 打包 .bat。
# `tests/test_version_sync.py` 钉死全部一致性（改一处必须改全部）。
VERSION = "1.5.0"

# 状态栏行情/情绪的最大等待秒数（超时降级为"不可用"，不阻塞）
STATUS_FETCH_TIMEOUT = 8


def health():
    """GET /api/health：存活探针（最轻量，无外部调用）"""
    return {
        "status": "ok",
        "api_key_configured": bool(get_llm_config()["api_key"]),
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
            "state": "ready" if get_llm_config()["api_key"] else "off",
            "api_key_configured": bool(get_llm_config()["api_key"]),
            "chat_model": get_llm_config()["model"] or DEEPSEEK_MODEL,
            "reasoner_model": get_llm_config()["reasoner_model"] or DEEPSEEK_REASONER_MODEL,
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
